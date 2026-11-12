"""Editor de modelos isolado da navegação e das outras páginas do GAMA."""

# =========== 1. IMPORTAÇÕES E ESTADO DO EDITOR ===========
# 1.1 Nenhum componente de interface é desenhado durante a importação
import hashlib
import sqlite3
from contextlib import closing
from html import escape
from pathlib import Path

import streamlit as st

from storage import (
    attachment_data, connect, delete_template, export_template,
    import_template, render, save_template,
)
from visual import cabecalho, texto_template, vazio
from componentes_ui import editor_formatado


# 1.2 Cada criação usa um rascunho separado
def preparar_novo_modelo(preservar_retorno=False):
    if not preservar_retorno:
        st.session_state.pop('gama_modelo_retorno_campanha', None)
    st.session_state['modelo_edicao'] = None
    st.session_state['gama_modelo_rascunho'] = True
    st.session_state['gama_modelo_geracao'] = (
        st.session_state.get('gama_modelo_geracao', 0) + 1
    )


def selecionar_modelo_salvo():
    st.session_state['gama_modelo_rascunho'] = False


def cancelar_criacao_modelo():
    selecionar_modelo_salvo()
    if st.session_state.pop('gama_modelo_retorno_campanha', False):
        st.session_state['pagina'] = 'Campanhas'


# 1.3 Aplicar a seleção antes de criar o widget
def _concluir_modelo(modelo_id, mensagem, retornar=False):
    st.session_state['gama_modelo_selecionar'] = modelo_id
    if retornar and st.session_state.pop('gama_modelo_retorno_campanha', False):
        st.session_state['campaign_v3_created_model_id'] = modelo_id
        st.session_state['gama_pagina_pendente'] = 'Campanhas'
    st.session_state['confirmacao'] = mensagem
    st.rerun()


def _preparar_estado(ids_modelos):
    if st.session_state.pop('gama_novo_modelo', False):
        preparar_novo_modelo(preservar_retorno=True)
    if 'gama_modelo_selecionar' in st.session_state:
        st.session_state['modelo_edicao'] = st.session_state.pop('gama_modelo_selecionar')
        st.session_state['gama_modelo_rascunho'] = False
    if st.session_state.get('modelo_edicao') not in ids_modelos:
        st.session_state['modelo_edicao'] = None


# =========== 2. DADOS AUXILIARES E ANEXOS ===========
# 2.1 Limitar somente a amostra da prévia
def _dados_previa(db):
    contatos = [dict(r) for r in db.execute(
        'SELECT * FROM contacts ORDER BY name,email,id LIMIT 100'
    )]
    campos = {'nome', 'email', 'empresa', 'telefone'}
    campos.update(r[0] for r in db.execute('''
        SELECT DISTINCT j.key FROM contacts c,
        json_each(CASE WHEN json_valid(c.extra) THEN c.extra ELSE '{}' END) j
    '''))
    total = db.execute('SELECT COUNT(*) FROM contacts').fetchone()[0]
    return contatos, sorted(campos), total


# 2.2 Evitar reconversões do mesmo arquivo
def _preparar_anexo(nome, dados, converter, chave_modelo):
    assinatura = (nome, hashlib.sha256(dados).hexdigest(), bool(converter))
    chave = f'gama_anexo_preparado_{chave_modelo}'
    preparado = st.session_state.get(chave)
    if preparado is None or preparado['assinatura'] != assinatura:
        nome_final, conteudo = attachment_data(nome, dados, converter)
        preparado = {
            'assinatura': assinatura,
            'nome': nome_final,
            'conteudo': conteudo,
        }
        st.session_state[chave] = preparado
    return preparado['nome'], preparado['conteudo']


# =========== 3. TELA DE TEMPLATES DE CAMPANHA ===========
# 3.1 Desenhar o editor somente nesta página
def mostrar_templates():
    with closing(connect()) as db:
        modelos = [dict(r) for r in db.execute('SELECT * FROM templates ORDER BY name,id')]
        ids_modelos = [m['id'] for m in modelos]
        nomes_modelos = {m['id']: m['name'] for m in modelos}
        _preparar_estado(ids_modelos)

        cabecalho(
            'Templates de campanha',
            'Importe, edite e guarde textos e anexos para suas campanhas.',
            'Suas mensagens',
        )

        # 3.2 Importar JSON completo ou texto TXT
        with st.expander('Importar template'):
            arquivo_modelo = st.file_uploader(
                'Arquivo do template', type=['json', 'txt'],
                key='importar_template',
            )
            assunto_importado = st.text_input(
                'Assunto para arquivo TXT', 'Mensagem para sua equipe',
                key='gama_template_assunto_importado',
            )
            if st.button(
                'Importar template', disabled=arquivo_modelo is None,
                key='gama_template_importar',
            ):
                try:
                    if arquivo_modelo.name.lower().endswith('.json'):
                        novo_id = import_template(db, arquivo_modelo.getvalue())
                    else:
                        corpo_importado = arquivo_modelo.getvalue().decode('utf-8-sig')
                        novo_id = save_template(
                            db, Path(arquivo_modelo.name).stem,
                            assunto_importado, corpo_importado,
                        )
                    _concluir_modelo(
                        novo_id, 'Template importado com sucesso.', retornar=True,
                    )
                except (ValueError, sqlite3.IntegrityError) as erro:
                    st.error(f'Não foi possível importar: {erro}')
            st.caption('JSON preserva nome, assunto, mensagem e anexo. TXT importa o corpo da mensagem em UTF-8.')

        # 3.3 Escolher um modelo existente ou criar pelo +
        with st.container(border=True, key='painel_seletor_modelo'):
            coluna_modelo, coluna_novo = st.columns(
                [9, 1], vertical_alignment='bottom',
            )
            modelo_id = coluna_modelo.selectbox(
                'Modelo salvo', ids_modelos, index=None,
                placeholder='Selecione um modelo salvo',
                format_func=lambda valor: nomes_modelos[valor],
                key='modelo_edicao', on_change=selecionar_modelo_salvo,
            )
            with coluna_novo:
                with st.container(key='gama_adicionar_modelo'):
                    st.button(
                        '+', help='Criar novo modelo',
                        key='gama_modelo_adicionar', type='primary',
                        on_click=preparar_novo_modelo,
                        args=(bool(st.session_state.get('gama_modelo_retorno_campanha')),),
                        width='stretch',
                    )

        em_criacao = st.session_state.get('gama_modelo_rascunho', False)
        modelo_atual = next((m for m in modelos if m['id'] == modelo_id), {})
        if modelo_id is None and not em_criacao:
            vazio(
                'Escolha uma mensagem para continuar.',
                'Clique em Modelo salvo para ver seus modelos. Use o botão + ao lado para criar um novo.',
                'modelo',
            )
            return

        contatos, campos, total_contatos = _dados_previa(db)
        chave_modelo = (
            f"novo_{st.session_state.get('gama_modelo_geracao', 0)}"
            if em_criacao else str(modelo_id)
        )

        # 3.4 Texto e anexo lado a lado
        coluna_texto, coluna_anexo = st.columns([1.5, 1], gap='large')
        with coluna_texto:
            # =========== 4. TEXTO DO MODELO ===========
            # 4.1 Campos e editor com formatação
            with st.container(border=True, key='painel_template'):
                st.subheader('Novo modelo' if em_criacao else 'Editar modelo')
                badges = ''.join(
                    '<span class="var-badge">{' + escape(c) + '}</span>'
                    for c in campos
                )
                st.markdown('Campos disponíveis: ' + badges, unsafe_allow_html=True)
                st.caption('Também são aceitas variantes como {Nome} e {NOME}.')
                nome_modelo = st.text_input(
                    'Nome do modelo', modelo_atual.get('name', ''),
                    key=f'nome_modelo_{chave_modelo}',
                )
                assunto = st.text_input(
                    'Assunto do email',
                    modelo_atual.get('subject', 'Mensagem para sua equipe'),
                    key=f'assunto_{chave_modelo}',
                )
                texto_modelo = editor_formatado(
                    modelo_atual.get('body', ''),
                    key=f'gama_editor_corpo_{chave_modelo}', altura=340,
                )

        with coluna_anexo:
            # =========== 5. ANEXO E GRAVAÇÃO ===========
            # 5.1 Preparar o anexo somente nesta página
            with st.container(border=True, key='painel_anexo'):
                st.subheader('Anexo da mensagem')
                novo_anexo = st.file_uploader(
                    'Selecione um arquivo', key=f'anexo_{chave_modelo}',
                )
                remover_anexo = st.checkbox(
                    'Remover anexo salvo', key=f'remover_anexo_{chave_modelo}',
                )
                nome_anexo = None if remover_anexo else modelo_atual.get('attachment_name')
                dados_anexo = None if remover_anexo else modelo_atual.get('attachment')
                anexo_valido = True
                if novo_anexo is not None and not remover_anexo:
                    converter = False
                    if novo_anexo.name.lower().endswith('.pdf'):
                        converter = st.checkbox(
                            'Converter primeira página do PDF para PNG',
                            key=f'converter_{chave_modelo}',
                        )
                        st.caption('Ao converter, somente a primeira página será anexada como imagem. Deixe desmarcado para enviar o PDF completo.')
                    try:
                        nome_anexo, dados_anexo = _preparar_anexo(
                            novo_anexo.name, novo_anexo.getvalue(),
                            converter, chave_modelo,
                        )
                    except Exception:
                        anexo_valido = False
                        st.error('Não foi possível preparar o anexo. Verifique o arquivo ou mantenha o PDF original.')

                if nome_anexo and dados_anexo is not None:
                    st.caption(f'{nome_anexo} ({len(dados_anexo) / 1024:.1f} KB)')
                    if nome_anexo.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp')):
                        try:
                            st.image(dados_anexo, width=300)
                        except Exception:
                            st.warning('Prévia indisponível; verifique se a imagem é válida.')
                    st.download_button(
                        'Baixar anexo para conferir', dados_anexo, nome_anexo,
                        key=f'baixar_anexo_{chave_modelo}',
                    )

        # 5.2 Salvar o texto e o anexo juntos
        with st.container(border=True, key='painel_acoes_template'):
            orientacao_col, salvar_col, cancelar_col = st.columns(
                [3, 1.3, 1.2], vertical_alignment='center',
            )
            orientacao_col.caption('Salva o texto e o anexo deste modelo.')
            with salvar_col:
                if st.button(
                    'Salvar modelo', type='primary', disabled=not anexo_valido,
                    key=f'gama_template_salvar_{chave_modelo}', width='stretch',
                ):
                    try:
                        salvo_id = save_template(
                            db, nome_modelo, assunto, texto_modelo,
                            nome_anexo, dados_anexo, template_id=modelo_id,
                        )
                        _concluir_modelo(
                            salvo_id, 'Modelo e anexo salvos localmente.',
                            retornar=em_criacao,
                        )
                    except (ValueError, sqlite3.IntegrityError) as erro:
                        st.error(str(erro))
            with cancelar_col:
                if em_criacao:
                    st.button(
                        'Cancelar criação', on_click=cancelar_criacao_modelo,
                        key='gama_template_cancelar_criacao', width='stretch',
                    )

        # =========== 6. PRÉVIA, EXPORTAÇÃO E EXCLUSÃO ===========
        # 6.1 Visualizar a personalização
        if contatos and texto_modelo:
            with st.expander('Pré-visualização do email', expanded=True):
                contato_previa = st.selectbox(
                    'Contato da prévia', [c['id'] for c in contatos],
                    format_func=lambda valor: next(
                        c['email'] for c in contatos if c['id'] == valor
                    ),
                    key=f'gama_template_previa_{chave_modelo}',
                )
                if total_contatos > len(contatos):
                    st.caption(f'A prévia mostra os primeiros {len(contatos)} contatos da base.')
                try:
                    assunto_previa, corpo_previa = render(
                        {'subject': assunto, 'body': texto_modelo},
                        next(c for c in contatos if c['id'] == contato_previa),
                    )
                    st.write(assunto_previa)
                    texto_template(corpo_previa)
                except (
                    KeyError, ValueError, IndexError, AttributeError, TypeError,
                ) as erro:
                    st.warning(f'Revise as variáveis ou chaves do template: {erro}')

        # 6.2 Exportar a versão salva ou excluir
        if modelo_id is not None:
            with st.container(border=True, key='painel_exportar_template'):
                st.subheader('Exportar ou excluir template')
                st.download_button(
                    'Exportar template completo', export_template(modelo_atual),
                    f'template-{modelo_id}.json', 'application/json',
                )
                st.download_button(
                    'Exportar texto', modelo_atual['body'].encode('utf-8'),
                    f'template-{modelo_id}.txt', 'text/plain',
                )
                st.caption('A exportação usa a última versão salva. O JSON completo inclui o anexo em seu formato original.')
                exclusao = st.checkbox(
                    'Confirmo a exclusão deste template',
                    key=f'exclusao_modelo_{modelo_id}',
                )
                if st.button(
                    'Excluir template', disabled=not exclusao,
                    key=f'gama_template_excluir_{modelo_id}',
                ):
                    try:
                        delete_template(db, modelo_id)
                        _concluir_modelo(None, 'Template excluído.')
                    except ValueError as erro:
                        st.error(str(erro))