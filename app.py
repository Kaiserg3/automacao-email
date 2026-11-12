# =========== 1. IMPORTAÇÕES E CONFIGURAÇÕES INICIAIS ===========
# 1.1 Bibliotecas e recursos compartilhados
import mimetypes
import os
import smtplib
import ssl
import time
from contextlib import closing
from email.message import EmailMessage
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

from storage import connect, import_legacy, get_setting, set_setting, now, render
from tracking import (
    base_url, validate_public_url, create_token,
    attach_pixel, mark_accepted,
)
from formatting import body_plain, has_formatting, set_message_body
from visual import (
    aplicar_tema, barra_superior, cabecalho, indicadores,
    item_lista, marca, tabela, texto_template, vazio,
)


# 1.2 Identidade e preferências da sessão
st.set_page_config(
    page_title='GAMA | Automação de emails',
    layout='wide',
    initial_sidebar_state='expanded',
)
st.session_state.setdefault('modo_escuro', True)
st.session_state.setdefault('gama_fonte_base', 22)
load_dotenv(Path(__file__).parent / '.env')
aplicar_tema(st.session_state['modo_escuro'])


# =========== 2. NAVEGAÇÃO E CONFIRMAÇÕES ===========
# 2.1 Callbacks executados antes da criação do menu lateral
def navegar(destino):
    st.session_state['pagina'] = destino


def confirmar(mensagem):
    st.session_state['confirmacao'] = mensagem
    st.rerun()


# =========== 3. ENVIO E REGISTRO DAS MENSAGENS ===========
# 3.1 Somente a confirmação de envio chama esta função
def enviar_campanha(banco, campanha, modelo, destinatarios):
    remetente = os.getenv('EMAIL_REMETENTE')
    senha = os.getenv('SENHA_APP')
    url_pixel = base_url(banco)
    enviados = falhas = 0
    interrompido = False
    barra = st.progress(0)
    resumo = st.empty()
    log = st.container(height=250, border=True)

    try:
        with smtplib.SMTP('smtp.gmail.com', 587, timeout=30) as servidor:
            servidor.starttls(context=ssl.create_default_context())
            servidor.login(remetente, senha)
            banco.execute(
                "UPDATE campaigns SET status='Em envio' WHERE id=?",
                (campanha['id'],),
            )
            banco.commit()

            for indice, contato in enumerate(destinatarios):
                assunto, corpo = render(modelo, contato)
                mensagem = EmailMessage()
                mensagem['From'] = remetente
                mensagem['To'] = contato['email']
                mensagem['Subject'] = assunto

                # 3.2 Formatação funciona também sem pixel
                if has_formatting(corpo):
                    set_message_body(mensagem, corpo)
                else:
                    mensagem.set_content(body_plain(corpo))

                token_pixel = None
                if url_pixel:
                    token_pixel = create_token(
                        banco, campanha['id'],
                        contato['id'], contato['email'],
                    )
                    attach_pixel(
                        mensagem, corpo, url_pixel, token_pixel,
                    )

                # 3.3 Anexo salvo no modelo
                if modelo['attachment'] is not None:
                    mime = (
                        mimetypes.guess_type(
                            modelo['attachment_name'] or ''
                        )[0]
                        or 'application/octet-stream'
                    )
                    principal, subtipo = mime.split('/', 1)
                    mensagem.add_attachment(
                        modelo['attachment'],
                        maintype=principal,
                        subtype=subtipo,
                        filename=modelo['attachment_name'],
                    )

                # 3.4 Registrar a tentativa antes do SMTP
                banco.execute('''
                    INSERT INTO deliveries(
                        campaign_id,contact_id,email,status,
                        detail,created,tracking_token
                    )
                    VALUES(?,?,?,?,?,?,?)
                    ON CONFLICT(campaign_id,contact_id) DO UPDATE SET
                        email=excluded.email,
                        status=excluded.status,
                        detail=excluded.detail,
                        created=excluded.created,
                        tracking_token=excluded.tracking_token
                ''', (
                    campanha['id'],
                    contato['id'],
                    contato['email'],
                    'Incerto',
                    'Processamento iniciado; confira a conta antes de repetir.',
                    now(),
                    token_pixel,
                ))
                banco.commit()

                try:
                    recusados = servidor.send_message(mensagem)
                    if recusados:
                        raise smtplib.SMTPRecipientsRefused(recusados)
                    status = 'Enviado'
                    detalhe = 'Aceito pelo servidor SMTP'
                    enviados += 1
                except (
                    smtplib.SMTPRecipientsRefused,
                    smtplib.SMTPDataError,
                    smtplib.SMTPSenderRefused,
                ):
                    status = 'Falhou'
                    detalhe = 'Mensagem recusada pelo servidor SMTP'
                    falhas += 1
                except (OSError, smtplib.SMTPException):
                    status = 'Incerto'
                    detalhe = (
                        'Conexão interrompida; confirme o resultado '
                        'na conta de envio.'
                    )
                    interrompido = True

                banco.execute('''
                    UPDATE deliveries SET status=?,detail=?,created=?
                    WHERE campaign_id=? AND contact_id=?
                ''', (
                    status, detalhe, now(),
                    campanha['id'], contato['id'],
                ))
                if token_pixel and status == 'Enviado':
                    mark_accepted(banco, token_pixel)
                banco.commit()

                log.write(f"{contato['email']} — {status}")
                barra.progress((indice + 1) / len(destinatarios))
                resumo.info(
                    f'Enviados: {enviados} | Falhas: {falhas}'
                )
                if interrompido:
                    break

                # 3.5 Intervalos e pausas entre lotes
                if indice + 1 < len(destinatarios):
                    time.sleep(campanha['interval_seconds'])
                    if (
                        (indice + 1) % campanha['batch_size'] == 0
                        and campanha['batch_pause']
                    ):
                        time.sleep(campanha['batch_pause'] * 60)

    except (OSError, smtplib.SMTPException):
        interrompido = True
        st.error(
            'Não foi possível concluir a conexão SMTP. Confira a rede, '
            'as credenciais e os resultados salvos antes de retomar.'
        )
    finally:
        status = (
            'Interrompida' if interrompido
            else 'Com falhas' if falhas
            else 'Finalizada'
        )
        banco.execute(
            'UPDATE campaigns SET status=? WHERE id=?',
            (status, campanha['id']),
        )
        banco.commit()

    if interrompido:
        st.warning(
            'Processamento interrompido. Resultados incertos ficam '
            'bloqueados até sua conferência em Desempenho.'
        )
    elif falhas:
        st.warning(
            f'Processamento concluído: {enviados} enviados '
            f'e {falhas} falhas.'
        )
    else:
        st.success(
            f'Processamento concluído: {enviados} '
            'mensagens aceitas pelo SMTP.'
        )


# =========== 4. TELA: DASHBOARD ===========
# 4.1 Consultar contagens e resumos
def mostrar_dashboard():
    from visual import atividade_recente, resumo_envios

    with closing(connect()) as db:
        total_contatos = db.execute(
            'SELECT COUNT(*) FROM contacts'
        ).fetchone()[0]
        total_listas = db.execute(
            'SELECT COUNT(DISTINCT list_name) FROM contacts'
        ).fetchone()[0]
        total_modelos = db.execute(
            'SELECT COUNT(*) FROM templates'
        ).fetchone()[0]
        total_campanhas = db.execute(
            'SELECT COUNT(*) FROM campaigns'
        ).fetchone()[0]
        total_legados = db.execute(
            'SELECT COUNT(*) FROM legacy_deliveries'
        ).fetchone()[0]
        envios = {
            r['status']: r['total']
            for r in db.execute(
                'SELECT status,COUNT(*) AS total '
                'FROM deliveries GROUP BY status'
            )
        }
        situacoes = {
            r['status']: r['total']
            for r in db.execute(
                'SELECT status,COUNT(*) AS total '
                'FROM campaigns GROUP BY status'
            )
        }
        listas = [dict(r) for r in db.execute('''
            SELECT list_name,COUNT(*) AS total
            FROM contacts
            GROUP BY list_name
            ORDER BY total DESC,list_name
            LIMIT 5
        ''')]
        campanhas = [dict(r) for r in db.execute('''
            SELECT name,list_name,status
            FROM campaigns ORDER BY id DESC LIMIT 5
        ''')]
        atividades = [dict(r) for r in db.execute('''
            SELECT d.email,d.status,d.detail,d.created,
                c.name AS campanha
            FROM deliveries d
            LEFT JOIN campaigns c ON c.id=d.campaign_id
            ORDER BY d.created DESC,d.id DESC LIMIT 6
        ''')]

    # 4.2 Atalho para um novo rascunho
    def abrir_nova_campanha():
        st.session_state['campaign_v3_selected'] = None
        st.session_state['campaign_v3_revision'] = (
            st.session_state.get('campaign_v3_revision', 0) + 1
        )
        navegar('Campanhas')

    titulo_col, acoes_col = st.columns(
        [3, 2.5], vertical_alignment='bottom',
    )
    with titulo_col:
        cabecalho(
            'Visão geral',
            'Acompanhe sua base, organize campanhas e confira os resultados.',
            'Dashboard',
        )
    with acoes_col:
        criar_col, desempenho_col = st.columns(
            [1, 1.35], gap='small',
        )
        criar_col.button(
            'Criar campanha', type='primary',
            icon=':material/add:',
            on_click=abrir_nova_campanha,
            key='gama_dashboard_criar_campanha',
            width='stretch',
        )
        desempenho_col.button(
            'Consultar desempenho', icon=':material/monitoring:',
            on_click=navegar, args=('Desempenho',),
            key='gama_dashboard_desempenho',
            width='stretch',
        )

    # 4.3 Indicadores reais do banco
    indicadores([
        (
            'Contatos salvos', total_contatos,
            f'{total_listas} listas organizadas', 'contatos',
        ),
        (
            'Emails enviados', envios.get('Enviado', 0),
            'Mensagens aceitas pelo servidor', 'email',
        ),
        (
            'Templates', total_modelos,
            'Mensagens prontas para personalizar', 'modelo',
        ),
        (
            'Campanhas', total_campanhas,
            'Sua comunicação em movimento', 'campanha',
        ),
    ])

    # 4.4 Resultados e atividade recente
    esquerda, direita = st.columns([1.15, 1], gap='medium')
    with esquerda:
        with st.container(border=True, key='painel_visao_envios'):
            st.subheader('Visão dos envios')
            st.caption('Resultado mais recente de cada contato por campanha.')
            resumo_envios([
                ('Enviados', envios.get('Enviado', 0), 'Enviado'),
                (
                    'Falhas registradas',
                    envios.get('Falhou', 0), 'Falhou',
                ),
                (
                    'Resultados incertos',
                    envios.get('Incerto', 0), 'Incerto',
                ),
            ])
            st.caption(
                'Enviado significa aceito pelo servidor SMTP. '
                'As aberturas detectadas ficam em Desempenho.'
            )
            if total_legados:
                st.caption(
                    f'Histórico antigo preservado: {total_legados} '
                    'destinatários, sem campanha identificada.'
                )

    with direita:
        with st.container(border=True, key='painel_atividade_dashboard'):
            st.subheader('Atividade recente')
            st.caption('Últimos resultados registrados nos envios.')
            atividade_recente([{
                'titulo': registro['email'],
                'descricao': registro['campanha'] or 'Campanha indisponível',
                'estado': registro['status'],
                'data': registro['created'],
            } for registro in atividades])

    # 4.5 Resumos de campanhas e listas
    campanhas_col, listas_col = st.columns([1.65, 1], gap='medium')
    with campanhas_col:
        with st.container(border=True, key='painel_campanhas_dashboard'):
            st.subheader('Campanhas recentes')
            st.caption(
                f"{situacoes.get('Rascunho', 0)} rascunhos · "
                f"{situacoes.get('Em envio', 0)} em envio · "
                f"{total_campanhas} no total"
            )
            if campanhas:
                tabela([{
                    'Campanha': c['name'],
                    'Lista': c['list_name'] or 'A definir',
                    'Status': c['status'],
                } for c in campanhas])
                st.button(
                    'Gerenciar campanhas',
                    on_click=navegar, args=('Campanhas',),
                    key='gama_dashboard_gerenciar_campanhas',
                )
            else:
                vazio(
                    'Sua primeira campanha começa aqui.',
                    'Use Criar campanha para salvar um rascunho e completar as etapas.',
                    'campanha',
                )

    with listas_col:
        with st.container(border=True, key='painel_listas_dashboard'):
            st.subheader('Listas de contatos')
            st.caption(
                f'{total_listas} listas · '
                f'{total_contatos} contatos cadastrados'
            )
            if listas:
                for lista in listas:
                    item_lista(lista['list_name'], lista['total'])
                st.button(
                    'Gerenciar contatos',
                    on_click=navegar, args=('Contatos',),
                    key='gama_dashboard_contatos',
                )
            else:
                vazio(
                    'Organize sua base.',
                    'Importe uma planilha ou cadastre o primeiro contato.',
                    'contatos',
                )


# =========== 5. TELA: CONFIGURAÇÕES ===========
# 5.1 Pixel, aparência e armazenamento
def mostrar_configuracoes():
    with closing(connect()) as db:
        try:
            base_url(db)
            erro_pixel = ''
        except ValueError as erro:
            erro_pixel = str(erro)

        cabecalho(
            'Do seu jeito.',
            'Ajuste sua experiência e confira as configurações do seu espaço.',
            'Configurações',
        )

        with st.container(border=True, key='painel_pixel'):
            st.subheader('Pixel de abertura')
            st.caption(
                'O endereço precisa ser HTTPS público e encaminhar '
                'as consultas ao serviço tracking.py. O serviço e '
                'este aplicativo devem usar o mesmo banco SQLite.'
            )
            url_digitada = st.text_input(
                'Endereço público do pixel',
                get_setting(db, 'tracking_url'),
                placeholder='https://seu-dominio.com',
            )
            if st.button('Salvar endereço do pixel'):
                try:
                    url_validada = (
                        validate_public_url(url_digitada)
                        if url_digitada.strip() else ''
                    )
                    set_setting(db, 'tracking_url', url_validada)
                    confirmar(
                        'Configuração do pixel salva. '
                        'Novos envios usarão essa configuração.'
                    )
                except ValueError as erro:
                    st.error(str(erro))
            st.caption(
                'Sem endereço público, o serviço pode ser testado '
                'localmente, mas ainda não recebe aberturas de '
                'destinatários. GAMA_TRACKING_URL no ambiente tem '
                'prioridade sobre o endereço salvo.'
            )
            if erro_pixel:
                st.error('Endereço configurado inválido: ' + erro_pixel)

        esquerda, direita = st.columns(2, gap='large')

        # 5.2 Preferências e credenciais
        with esquerda:
            with st.container(border=True, key='painel_preferencias'):
                st.subheader('Sua experiência')
                st.write('Uma interface para acompanhar o seu ritmo.')
                st.caption(
                    'Use a opção Modo escuro na barra lateral '
                    'para alternar a aparência.'
                )
                st.select_slider(
                    'Tamanho da fonte',
                    options=[18, 20, 22, 24],
                    key='gama_fonte_base',
                    help='Ajusta textos, controles e opções dos seletores.',
                )
            with st.container(border=True, key='painel_conta'):
                st.subheader('Conta de envio')
                for variavel in ['EMAIL_REMETENTE', 'SENHA_APP']:
                    situacao = (
                        'configurada'
                        if os.getenv(variavel)
                        else 'não configurada'
                    )
                    st.write(f'{variavel}: {situacao}')
                st.caption(
                    'Configure as variáveis no processo ou no arquivo '
                    '.env local. Use uma senha de aplicativo Gmail. '
                    'O envio exige acesso a smtp.gmail.com:587 com STARTTLS.'
                )

        # 5.3 Persistência local
        with direita:
            with st.container(border=True, key='painel_armazenamento'):
                st.subheader('Seus dados, no seu espaço')
                st.write(
                    'Contatos, mensagens, anexos e histórico '
                    'ficam salvos no banco local.'
                )
                st.caption(
                    'O arquivo fica em data/email.sqlite3. '
                    'AUTOMACAO_DATA_DIR permite escolher outro diretório. '
                    'Faça backup com o sistema parado.'
                )
                st.divider()
                st.caption(
                    'templates.json e enviados.txt são importados sem '
                    'alterar os originais. Modelos já salvos são preservados.'
                )
                st.caption(
                    'Contatos que estavam apenas na sessão da versão '
                    'antiga precisam ser importados ou cadastrados.'
                )
            st.caption(
                'GAMA é o nome provisório desta versão. Login e perfis '
                'de vários usuários ainda não estão disponíveis.'
            )


# =========== 6. ESTRUTURA PRINCIPAL E ROTEAMENTO ===========
# 6.1 Importação compatível com a instalação anterior
with closing(connect()) as banco_inicial:
    avisos_importacao = import_legacy(
        banco_inicial, Path(__file__).parent,
    )

paginas = [
    'Dashboard',
    'Contatos',
    'Templates de campanha',
    'Campanhas',
    'Desempenho',
    'Configurações',
]
pendente = st.session_state.pop('gama_pagina_pendente', None)
if pendente in paginas:
    st.session_state['pagina'] = pendente
if st.session_state.get('pagina') not in paginas:
    st.session_state['pagina'] = 'Dashboard'

# 6.2 Menu lateral
with st.sidebar:
    marca()
    pagina = st.radio(
        'Navegação', paginas,
        label_visibility='collapsed', key='pagina',
    )
    st.divider()
    st.toggle('Modo escuro', key='modo_escuro')
    st.markdown(
        '<div class="gama-sidebar-footer">'
        '<strong>Feito para conectar.</strong><br>'
        'Seu espaço de comunicação.</div>',
        unsafe_allow_html=True,
    )

if pagina != 'Templates de campanha':
    st.session_state.pop('gama_modelo_retorno_campanha', None)

# 6.3 Limpar o conteúdo da página anterior
conteudo = st.empty()
conteudo.empty()

with conteudo.container():
    # 6.4 Contexto e atualização
    with st.container(key='gama_barra_app'):
        contexto_col, atualizar_col = st.columns(
            [5, 1.2], vertical_alignment='center',
        )
        with contexto_col:
            barra_superior(pagina)
        atualizar_col.button(
            'Atualizar',
            icon=':material/refresh:',
            key='gama_atualizar_pagina',
            width='stretch',
        )

    for aviso in avisos_importacao:
        st.warning(aviso)

    if mensagem := st.session_state.pop('confirmacao', None):
        st.success(mensagem)

    # 6.5 Abrir somente a página selecionada
    with st.container(key='gama_pagina_' + str(paginas.index(pagina))):
        if pagina == 'Dashboard':
            mostrar_dashboard()
        elif pagina == 'Contatos':
            from contatos_ui import mostrar_contatos
            mostrar_contatos()
        elif pagina == 'Templates de campanha':
            from templates_ui import mostrar_templates
            mostrar_templates()
        elif pagina == 'Campanhas':
            from campanhas_ui import mostrar_campanhas
            mostrar_campanhas(enviar_campanha)
        elif pagina == 'Desempenho':
            from desempenho_ui import mostrar_desempenho
            mostrar_desempenho()
        else:
            mostrar_configuracoes()