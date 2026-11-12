"""Contatos com barra de ações, pesquisa sobreposta e edição local."""

# =========== 1. IMPORTAÇÕES E ESTADO DA INTERFACE ===========
# 1.1 Conexões próprias mantêm os diálogos independentes dos reruns
import csv
import io
import json
import sqlite3
from contextlib import closing

import streamlit as st

from storage import (
    connect, save_contact, delete_contact, read_contact_rows,
    contacts_in_campaign, delete_contacts, update_campaign_members,
)
from visual import cabecalho, indicadores
from componentes_ui import tabela_selecionavel


# 1.2 Filtros confirmados ficam separados do rascunho da janela
def _filtros_padrao():
    return {
        'busca': '', 'campo': 'Todos os campos', 'campanha': None,
        'lista': None, 'ordem': 'Nome',
    }


def _estilo_dialogo():
    st.markdown('''<style>
    [role="dialog"] {
        background:var(--gama-surface)!important;
        color:var(--gama-text)!important;
    }
    [data-testid="stDialog"] [aria-modal="true"] {
        border:1px solid var(--gama-line);
        border-radius:18px;
        box-shadow:0 24px 80px #0006;
    }
    [role="dialog"] h2,[role="dialog"] h3 {
        color:var(--gama-text)!important;
    }
    </style>''', unsafe_allow_html=True)


def _concluir(mensagem):
    st.session_state['gama_contatos_aviso'] = mensagem
    _definir_selecao([])
    st.rerun()


# =========== 2. PESQUISA, TABELA E EXPORTAÇÃO ===========
# 2.1 Etiquetas importadas também podem ser pesquisadas
def _campos_disponiveis(contatos):
    etiquetas = sorted({k for c in contatos for k in json.loads(c['extra'] or '{}')})
    return list(dict.fromkeys(
        ['Todos os campos', 'nome', 'email', 'empresa', 'telefone'] + etiquetas
    ))


def _normalizar_filtros(filtros, campos, campanhas, listas):
    resultado = {**_filtros_padrao(), **filtros}
    if resultado['campo'] not in campos:
        resultado['campo'] = 'Todos os campos'
    if resultado['campanha'] not in {c['id'] for c in campanhas}:
        resultado['campanha'] = None
    if resultado['lista'] not in listas:
        resultado['lista'] = None
    if resultado['ordem'] not in {'Nome', 'Empresa', 'Email'}:
        resultado['ordem'] = 'Nome'
    resultado['busca'] = str(resultado['busca'] or '')
    return resultado


def _filtrar_contatos(db, contatos, filtros):
    membros = None if filtros['campanha'] is None else {
        c['id'] for c in contacts_in_campaign(db, filtros['campanha'])
    }
    encontrados = []
    for contato in contatos:
        valores = {
            **json.loads(contato['extra'] or '{}'),
            'nome': contato['name'] or '',
            'email': contato['email'] or '',
            'empresa': contato['company'] or '',
            'telefone': contato['phone'] or '',
        }
        texto = (
            ' '.join(str(v) for v in valores.values())
            if filtros['campo'] == 'Todos os campos'
            else str(valores.get(filtros['campo'], ''))
        )
        if (
            filtros['lista'] is None or contato['list_name'] == filtros['lista']
        ) and (
            membros is None or contato['id'] in membros
        ) and filtros['busca'].strip().casefold() in texto.casefold():
            encontrados.append(contato)
    chave = {'Nome': 'name', 'Empresa': 'company', 'Email': 'email'}[filtros['ordem']]
    return sorted(encontrados, key=lambda c: str(c[chave] or '').casefold())


# 2.2 Consultas parametrizadas mantêm somente a página atual em memória
def _consulta_contatos(db, filtros):
    db.create_function(
        'gama_casefold', 1,
        lambda valor: '' if valor is None else str(valor).casefold(),
        deterministic=True,
    )
    condicoes, parametros = [], []
    if filtros['lista'] is not None:
        condicoes.append('c.list_name=?')
        parametros.append(filtros['lista'])
    if filtros['campanha'] is not None:
        condicoes.append('''EXISTS (
            SELECT 1 FROM campaigns ca WHERE ca.id=? AND (
                (COALESCE(ca.members_custom,0)=0 AND ca.list_name=c.list_name)
                OR (ca.members_custom=1 AND EXISTS (
                    SELECT 1 FROM campaign_contacts cc
                    WHERE cc.campaign_id=ca.id AND cc.contact_id=c.id
                ))
            )
        )''')
        parametros.append(filtros['campanha'])

    busca = filtros['busca'].strip().casefold()
    colunas = {
        'nome': 'name', 'email': 'email',
        'empresa': 'company', 'telefone': 'phone',
    }
    if busca:
        if filtros['campo'] == 'Todos os campos':
            condicoes.append('''(
                instr(gama_casefold(COALESCE(c.name,'') || ' ' || COALESCE(c.email,'') || ' '
                    || COALESCE(c.company,'') || ' ' || COALESCE(c.phone,'')), ?) > 0
                OR EXISTS (
                    SELECT 1 FROM json_each(CASE WHEN json_valid(c.extra) THEN c.extra ELSE '{}' END) etiqueta
                    WHERE instr(gama_casefold(etiqueta.value), ?) > 0
                )
            )''')
            parametros.extend([busca, busca])
        elif filtros['campo'] in colunas:
            coluna = colunas[filtros['campo']]
            condicoes.append(f'instr(gama_casefold(c.{coluna}), ?) > 0')
            parametros.append(busca)
        else:
            condicoes.append('''EXISTS (
                SELECT 1 FROM json_each(CASE WHEN json_valid(c.extra) THEN c.extra ELSE '{}' END) etiqueta
                WHERE etiqueta.key=? AND instr(gama_casefold(etiqueta.value), ?) > 0
            )''')
            parametros.extend([filtros['campo'], busca])

    where = ' WHERE ' + ' AND '.join(condicoes) if condicoes else ''
    ordem = {'Nome': 'name', 'Empresa': 'company', 'Email': 'email'}[filtros['ordem']]
    return where, parametros, f'gama_casefold(c.{ordem}),c.id'


def _contar_contatos(db, filtros):
    where, parametros, _ = _consulta_contatos(db, filtros)
    return db.execute(
        'SELECT COUNT(*) FROM contacts c' + where, parametros,
    ).fetchone()[0]


def _pagina_contatos(db, filtros, pagina, tamanho):
    where, parametros, ordem = _consulta_contatos(db, filtros)
    return [dict(registro) for registro in db.execute(
        'SELECT c.* FROM contacts c' + where + f' ORDER BY {ordem} LIMIT ? OFFSET ?',
        [*parametros, tamanho, (pagina - 1) * tamanho],
    )]


# 2.3 A seleção usa IDs; intervalos seguem a ordem visível
def _ids_filtrados(db, filtros, inicio=None, fim=None):
    where, parametros, ordem = _consulta_contatos(db, filtros)
    sql = 'SELECT c.id FROM contacts c' + where + f' ORDER BY {ordem}'
    if inicio is not None or fim is not None:
        quantidade = _contar_contatos(db, filtros)
        if (
            not isinstance(inicio, int) or isinstance(inicio, bool)
            or not isinstance(fim, int) or isinstance(fim, bool)
            or not 1 <= inicio <= fim <= quantidade
        ):
            raise ValueError('Use um intervalo válido: 1 ≤ De ≤ Até ≤ contatos encontrados.')
        sql += ' LIMIT ? OFFSET ?'
        parametros = [*parametros, fim - inicio + 1, inicio - 1]
    return [registro[0] for registro in db.execute(sql, parametros)]


def _definir_selecao(contato_ids):
    st.session_state['gama_contatos_ids_selecionados'] = sorted(set(contato_ids))
    _limpar_selecao_tabela()


def _selecionar_resultados(filtros):
    with closing(connect()) as db:
        _definir_selecao(_ids_filtrados(db, filtros))


def _selecionar_intervalo(filtros):
    try:
        with closing(connect()) as db:
            ids = _ids_filtrados(
                db, filtros,
                st.session_state['gama_contatos_intervalo_de'],
                st.session_state['gama_contatos_intervalo_ate'],
            )
        anteriores = (
            st.session_state.get('gama_contatos_ids_selecionados', [])
            if st.session_state.get('gama_contatos_intervalo_adicionar', False)
            else []
        )
        _definir_selecao([*anteriores, *ids])
        st.session_state.pop('gama_contatos_selecao_erro', None)
    except ValueError as erro:
        st.session_state['gama_contatos_selecao_erro'] = str(erro)


def _escolher_campanha():
    filtros = st.session_state.get(
        'gama_filtros_contatos', _filtros_padrao(),
    ).copy()
    filtros['campanha'] = st.session_state['gama_contatos_campanha_visivel']
    st.session_state['gama_filtros_contatos'] = filtros


# 2.4 A exportação completa é preparada somente ao clicar
def _exportar_filtrados(filtros):
    with closing(connect()) as db:
        where, parametros, ordem = _consulta_contatos(db, filtros)
        registros = db.execute(
            'SELECT c.* FROM contacts c' + where + f' ORDER BY {ordem}',
            parametros,
        )
        return _exportar_csv(dict(registro) for registro in registros)


# 2.5 A revisão atualiza a tabela sem perder IDs de outras páginas
def _limpar_selecao_tabela():
    st.session_state['gama_contatos_revisao'] = (
        st.session_state.get('gama_contatos_revisao', 0) + 1
    )


def _reiniciar_paginacao():
    st.session_state['gama_contatos_pagina'] = 1
    _limpar_selecao_tabela()


def _mudar_pagina(pagina):
    st.session_state['gama_contatos_pagina'] = pagina
    _limpar_selecao_tabela()


def _contato_da_selecao(contatos, linhas):
    if len(linhas) != 1:
        return None
    indice = linhas[0]
    if (
        not isinstance(indice, int) or isinstance(indice, bool)
        or not 0 <= indice < len(contatos)
    ):
        return None
    return contatos[indice]['id']


# 2.6 Exportação com os mesmos cabeçalhos da importação
def _exportar_csv(contatos):
    linhas = []
    for contato in contatos:
        linha = {
            'nome': contato['name'],
            'email': contato['email'],
            'empresa': contato['company'],
            'telefone': contato['phone'],
            'lista': contato['list_name'],
            'descadastrado': bool(contato['unsubscribed']),
        }
        for chave, valor in json.loads(contato['extra'] or '{}').items():
            if chave not in linha:
                linha[chave] = valor
        linhas.append(linha)
    saida = io.StringIO()
    if linhas:
        colunas = list(dict.fromkeys(k for linha in linhas for k in linha))
        gravador = csv.DictWriter(saida, fieldnames=colunas)
        gravador.writeheader()
        gravador.writerows(linhas)
    return saida.getvalue().encode('utf-8-sig')


def _linhas_tabela(contatos):
    linhas = []
    for contato in contatos:
        linha = {
            'Nome': contato['name'],
            'Empresa': contato['company'],
            'Email': contato['email'],
            'Telefone': contato['phone'],
            'Lista': contato['list_name'],
        }
        for chave, valor in json.loads(contato['extra'] or '{}').items():
            if chave not in linha:
                linha[chave] = valor
        linha['Recebe emails'] = 'Não' if contato['unsubscribed'] else 'Sim'
        linhas.append(linha)
    return linhas


# =========== 3. JANELA SOBREPOSTA DE PESQUISA ===========
# 3.1 Fechar ou cancelar não altera os filtros aplicados
@st.dialog('Pesquisar contatos', width='large')
def _dialogo_pesquisa(campos, campanhas, listas):
    _estilo_dialogo()
    st.caption('Pesquise sua base e escolha os filtros. A lista será atualizada ao aplicar.')
    busca_col, filtros_col = st.columns([4, 1.2], vertical_alignment='bottom')
    with busca_col:
        busca = st.text_input(
            'Buscar contato', placeholder='Nome, empresa, telefone ou etiqueta',
            key='gama_pesquisa_busca', icon=':material/search:',
        )
    with filtros_col:
        with st.popover('Filtros', icon=':material/filter_alt:'):
            campo = st.selectbox('Pesquisar em', campos, key='gama_pesquisa_campo')
            campanha = st.selectbox(
                'Campanha', [None] + [c['id'] for c in campanhas],
                format_func=lambda v: (
                    'Todas as campanhas' if v is None
                    else next(c['name'] for c in campanhas if c['id'] == v)
                ),
                key='gama_pesquisa_campanha',
            )
            lista = st.selectbox(
                'Lista', [None] + listas,
                format_func=lambda v: 'Todas as listas' if v is None else v,
                key='gama_pesquisa_lista',
            )
            ordem = st.selectbox(
                'Ordenar por', ['Nome', 'Empresa', 'Email'],
                key='gama_pesquisa_ordem',
            )
    st.divider()
    aplicar, limpar, cancelar = st.columns(3)
    if aplicar.button('Aplicar pesquisa', type='primary'):
        st.session_state['gama_filtros_contatos'] = {
            'busca': busca, 'campo': campo, 'campanha': campanha,
            'lista': lista, 'ordem': ordem,
        }
        st.rerun()
    if limpar.button('Limpar filtros', key='gama_dialogo_limpar'):
        st.session_state['gama_filtros_contatos'] = _filtros_padrao()
        st.rerun()
    if cancelar.button('Cancelar'):
        st.rerun()


# =========== 4. JANELA DE CADASTRO E EDIÇÃO ===========
# 4.1 Reutilizar listas equivalentes
def _resolver_nome_lista(nome, listas):
    nome = ' '.join(str(nome or '').split())
    if not nome:
        raise ValueError('Informe o nome da nova lista.')
    for existente in listas:
        if ' '.join(existente.split()).casefold() == nome.casefold():
            return existente
    return nome


# 4.2 A troca de modo preserva os outros dados
def _alternar_modo_lista(chave, criar):
    st.session_state[chave] = criar


# 4.3 Uma versão por abertura evita recuperar rascunhos cancelados
@st.dialog('Gerenciar contato', width='large')
def _dialogo_contato(contato_id=None, versao=0):
    _estilo_dialogo()
    atual = {}
    with closing(connect()) as db:
        listas = sorted({
            r[0] for r in db.execute('SELECT DISTINCT list_name FROM contacts')
            if r[0] and r[0].strip()
        }, key=str.casefold)
        if contato_id is not None:
            registro = db.execute(
                'SELECT * FROM contacts WHERE id=?', (contato_id,),
            ).fetchone()
            if registro is None:
                st.warning('Este contato não está mais disponível.')
                return
            atual = dict(registro)
    st.caption(
        'Novo contato' if contato_id is None
        else 'Edite os dados do contato selecionado.'
    )
    identificador = f'gama_contato_{contato_id}_{versao}'

    # 4.4 Campos básicos
    esquerda, direita = st.columns(2)
    nome = esquerda.text_input(
        'Nome', atual.get('name') or '', key=f'{identificador}_nome',
    )
    email = direita.text_input(
        'Email', atual.get('email') or '', key=f'{identificador}_email',
    )
    empresa = esquerda.text_input(
        'Empresa', atual.get('company') or '', key=f'{identificador}_empresa',
    )
    telefone = direita.text_input(
        'Telefone', atual.get('phone') or '', key=f'{identificador}_telefone',
    )

    # 4.5 Lista salva ou criação pelo botão +
    chave_modo = f'{identificador}_lista_nova'
    criando_lista = st.session_state.setdefault(chave_modo, not listas)
    with st.container(border=True):
        selecionar_col, criar_col = st.columns([8, 1], vertical_alignment='bottom')
        lista_atual = atual.get('list_name')
        indice = listas.index(lista_atual) if lista_atual in listas else (
            0 if listas else None
        )
        lista_existente = selecionar_col.selectbox(
            'Lista existente', listas, index=indice,
            placeholder='Selecione uma lista',
            key=f'{identificador}_lista_existente',
            disabled=criando_lista or not listas,
        )
        criar_col.button(
            '+', type='primary', help='Criar nova lista', width='stretch',
            key=f'{identificador}_criar_lista', disabled=criando_lista,
            on_click=_alternar_modo_lista, args=(chave_modo, True),
        )
        nova_lista = ''
        if criando_lista:
            nova_lista = st.text_input(
                'Nome da nova lista',
                placeholder='Ex.: Clientes, Parceiros ou Prospecção',
                key=f'{identificador}_nova_lista_nome',
            )
            st.caption('A nova lista será criada ao salvar o contato.')
            if nova_lista.strip():
                nome_resolvido = _resolver_nome_lista(nova_lista, listas)
                if nome_resolvido in listas:
                    st.info(f'A lista "{nome_resolvido}" já existe e será utilizada.')
            if listas:
                st.button(
                    'Usar lista existente', key=f'{identificador}_usar_lista',
                    on_click=_alternar_modo_lista, args=(chave_modo, False),
                )
        else:
            st.caption('Escolha uma lista existente ou use + para criar outra.')

    # 4.6 Etiquetas e preferências de recebimento
    st.caption('Use etiquetas para informações como cargo, cidade, segmento ou idade.')
    etiquetas = [
        {'campo': k, 'valor': str(v)}
        for k, v in json.loads(atual.get('extra') or '{}').items()
    ]
    extras = st.data_editor(
        etiquetas or [{'campo': '', 'valor': ''}],
        num_rows='dynamic', hide_index=True, row_height=46,
        column_config={'campo': 'Etiqueta', 'valor': 'Valor'},
        key=f'{identificador}_etiquetas', width='stretch',
    )
    descadastrado = st.checkbox(
        'Não receber emails', bool(atual.get('unsubscribed')),
        key=f'{identificador}_descadastrado',
    )
    salvar = st.button(
        'Salvar contato', type='primary', key=f'{identificador}_salvar',
    )

    # 4.7 Validar e persistir somente ao salvar
    if salvar:
        try:
            lista = (
                _resolver_nome_lista(nova_lista, listas)
                if criando_lista else lista_existente
            )
            if not lista or lista not in listas and not criando_lista:
                raise ValueError('Escolha uma lista existente ou crie uma nova pelo botão +.')
            campos = {}
            for linha in extras:
                chave = str(linha.get('campo') or '').strip().lower()
                if not chave:
                    continue
                if chave in {
                    'nome', 'email', 'empresa', 'telefone',
                    'lista', 'descadastrado',
                }:
                    raise ValueError(f'O campo {chave} já possui um controle próprio.')
                if chave in campos:
                    raise ValueError(f'A etiqueta {chave} está repetida.')
                valor = linha.get('valor')
                campos[chave] = '' if valor is None else str(valor)
            with closing(connect()) as db:
                save_contact(
                    db, email, nome, empresa, telefone, lista, campos,
                    descadastrado, contato_id,
                )
            _concluir('Contato salvo.')
        except sqlite3.IntegrityError:
            st.error('Já existe um contato com esse email.')
        except ValueError as erro:
            st.error(str(erro))

    if contato_id is not None:
        st.divider()
        excluir = st.checkbox(
            'Confirmo a exclusão deste contato',
            key=f'{identificador}_excluir',
        )
        if st.button('Excluir contato', disabled=not excluir):
            try:
                with closing(connect()) as db:
                    delete_contact(db, contato_id)
                _concluir('Contato excluído. O histórico foi preservado.')
            except ValueError as erro:
                st.error(str(erro))
    if st.button('Cancelar', key=f'{identificador}_cancelar'):
        st.rerun()


# =========== 5. JANELA DE IMPORTAÇÃO ===========
# 5.1 Resultados ficam disponíveis depois de fechar a janela
@st.dialog('Importar contatos', width='large')
def _dialogo_importacao(versao=0):
    _estilo_dialogo()
    st.caption('Use XLSX ou CSV UTF-8 com a coluna email. As demais colunas serão preservadas.')
    lista = st.text_input(
        'Lista de destino', 'Principal',
        key=f'gama_importacao_lista_{versao}',
    )
    arquivo = st.file_uploader(
        'Arquivo de contatos', type=['xlsx', 'csv'],
        key=f'gama_importacao_arquivo_{versao}',
    )
    if st.button('Importar', type='primary', disabled=arquivo is None):
        try:
            linhas = read_contact_rows(arquivo.name, arquivo.getvalue())
            importados, problemas = 0, []
            with closing(connect()) as db:
                for numero, linha in enumerate(linhas, start=2):
                    try:
                        campos = {
                            k: v for k, v in linha.items()
                            if k not in {
                                'nome', 'email', 'empresa', 'telefone',
                                'lista', 'descadastrado',
                            }
                        }
                        descadastrado = str(
                            linha.get('descadastrado', '')
                        ).strip().casefold() in {'true', '1', 'sim', 'yes'}
                        save_contact(
                            db, linha.get('email', ''), linha.get('nome', ''),
                            linha.get('empresa', ''), linha.get('telefone', ''),
                            lista, campos, descadastrado,
                        )
                        importados += 1
                    except sqlite3.IntegrityError:
                        problemas.append(f'Linha {numero}: email já cadastrado.')
                    except ValueError:
                        problemas.append(f'Linha {numero}: email inválido.')
            st.session_state['gama_contatos_erros'] = problemas
            _concluir(f'{importados} contatos importados; {len(problemas)} linhas ignoradas.')
        except (ValueError, OSError) as erro:
            st.error(str(erro))
    if st.button('Cancelar'):
        st.rerun()


# =========== 6. JANELA DE PARTICIPANTES POR CAMPANHA ===========
# 6.1 Vínculos independentes da lista de origem
@st.dialog('Organizar campanhas', width='large')
def _dialogo_campanhas(versao=0, contato_ids=None):
    _estilo_dialogo()
    selecionados = list(dict.fromkeys(contato_ids or []))
    with closing(connect()) as db:
        campanhas = [
            dict(r) for r in db.execute(
                'SELECT * FROM campaigns ORDER BY name,id'
            )
        ]
        exemplos = []
        for contato_id in selecionados[:5]:
            contato = db.execute(
                'SELECT email FROM contacts WHERE id=?', (contato_id,),
            ).fetchone()
            if contato:
                exemplos.append(contato['email'])
    if not campanhas:
        st.info('Crie uma campanha na aba Campanhas para organizar seus participantes.')
        return
    if not selecionados:
        st.info('Selecione os contatos na tabela antes de organizar as campanhas.')
        return

    st.subheader(f'{len(selecionados)} contatos selecionados')
    st.caption(
        ' · '.join(exemplos)
        + (' · …' if len(selecionados) > len(exemplos) else '')
    )
    campanha_atual = st.session_state.get(
        'gama_filtros_contatos', {},
    ).get('campanha')
    ids_campanhas = [c['id'] for c in campanhas]
    campanha_id = st.selectbox(
        'Campanha', ids_campanhas,
        index=(
            ids_campanhas.index(campanha_atual)
            if campanha_atual in ids_campanhas else 0
        ),
        format_func=lambda v: next(
            c['name'] for c in campanhas if c['id'] == v
        ),
        key=f'gama_organizar_campanha_{versao}',
    )
    operacao = st.radio(
        'Como atualizar a campanha',
        ['Adicionar selecionados', 'Remover selecionados', 'Substituir participantes'],
        key=f'gama_organizar_acao_{versao}',
    )
    substituir_confirmado = True
    if operacao == 'Substituir participantes':
        st.warning('Os participantes atuais serão substituídos pelos contatos selecionados na tabela.')
        substituir_confirmado = st.checkbox(
            'Confirmo a substituição dos participantes',
            key=f'gama_organizar_substituir_{versao}',
        )
    st.caption('O mesmo contato pode participar de várias campanhas. Descadastros continuam sendo respeitados.')
    if st.button(
        'Salvar participantes', type='primary',
        disabled=not substituir_confirmado,
    ):
        try:
            acao = {
                'Adicionar selecionados': 'add',
                'Remover selecionados': 'remove',
                'Substituir participantes': 'replace',
            }[operacao]
            with closing(connect()) as db:
                update_campaign_members(
                    db, campanha_id, selecionados, action=acao,
                )
            _concluir('Participantes da campanha atualizados.')
        except ValueError as erro:
            st.error(str(erro))
    if st.button('Cancelar'):
        st.rerun()


# 6.2 Exclusão com confirmação e alcance explícitos
@st.dialog('Excluir contatos', width='large')
def _dialogo_exclusao(contato_ids=None, todos=False, versao=0):
    _estilo_dialogo()
    selecionados = list(dict.fromkeys(contato_ids or []))
    with closing(connect()) as db:
        quantidade = (
            db.execute('SELECT COUNT(*) FROM contacts').fetchone()[0]
            if todos else len(selecionados)
        )
    if not quantidade:
        st.info('Não há contatos para excluir.')
        return
    st.subheader(
        f'Excluir {quantidade} '
        f'{"contato" if quantidade == 1 else "contatos"}?'
    )
    if todos:
        st.warning('Esta ação exclui TODOS os contatos da base, inclusive de outras campanhas e listas. Os filtros atuais não limitam a exclusão.')
    else:
        st.warning('Esta ação exclui os contatos selecionados da base e de todas as campanhas das quais participam.')
    st.caption('Os registros de envio e as métricas já registradas serão preservados. Aguarde o término das campanhas em envio antes de excluir seus participantes.')
    confirmar = st.checkbox(
        'Confirmo a exclusão dos contatos',
        key=f'gama_excluir_confirmar_{versao}',
    )
    excluir_col, cancelar_col = st.columns(2)
    if excluir_col.button(
        'Excluir contatos', type='primary', disabled=not confirmar,
        key=f'gama_excluir_confirmado_{versao}',
    ):
        try:
            with closing(connect()) as db:
                excluidos = delete_contacts(
                    db, contact_ids=selecionados, all_contacts=todos,
                )
            _concluir(f'{excluidos} contatos excluídos. O histórico de envios foi preservado.')
        except ValueError as erro:
            st.error(str(erro))
    if cancelar_col.button(
        'Cancelar', key=f'gama_excluir_cancelar_{versao}',
    ):
        st.rerun()


# =========== 7. TELA PRINCIPAL DE CONTATOS ===========
# 7.1 Consultar metadados sem carregar toda a base
def mostrar_contatos():
    with closing(connect()) as db:
        contagens = db.execute('''SELECT COUNT(*) AS total,
            COUNT(CASE WHEN COALESCE(unsubscribed,0)=0 THEN 1 END) AS recebem,
            COUNT(CASE WHEN COALESCE(unsubscribed,0)<>0 THEN 1 END) AS nao_recebem,
            COUNT(DISTINCT list_name) AS listas FROM contacts''').fetchone()
        total = contagens['total']
        campanhas = [
            dict(r) for r in db.execute(
                'SELECT id,name FROM campaigns ORDER BY name,id'
            )
        ]
        listas = sorted({
            r[0] for r in db.execute('SELECT DISTINCT list_name FROM contacts')
        }, key=str.casefold)
        etiquetas = [r[0] for r in db.execute("""
            SELECT DISTINCT etiqueta.key
            FROM contacts c,
                json_each(CASE WHEN json_valid(c.extra) THEN c.extra ELSE '{}' END) etiqueta
            ORDER BY etiqueta.key
        """)]
        campos = list(dict.fromkeys(
            ['Todos os campos', 'nome', 'email', 'empresa', 'telefone']
            + etiquetas
        ))
        filtros = _normalizar_filtros(
            st.session_state.get('gama_filtros_contatos', _filtros_padrao()),
            campos, campanhas, listas,
        )
        st.session_state['gama_filtros_contatos'] = filtros.copy()
        quantidade = _contar_contatos(db, filtros)

    # 7.2 Mudar filtros reinicia a seleção; trocar de página preserva
    contexto = json.dumps(filtros, sort_keys=True, ensure_ascii=False)
    if st.session_state.get('gama_contatos_contexto') != contexto:
        st.session_state['gama_contatos_contexto'] = contexto
        _definir_selecao([])
        st.session_state.pop('gama_contatos_selecao_erro', None)
        st.session_state['gama_contatos_intervalo_de'] = 1
        st.session_state['gama_contatos_intervalo_ate'] = 1
        _reiniciar_paginacao()
    if st.session_state.get('gama_contatos_tamanho', 50) not in (25, 50, 100):
        st.session_state['gama_contatos_tamanho'] = 50
    tamanho = st.session_state.get('gama_contatos_tamanho', 50)
    paginas = max(1, (quantidade + tamanho - 1) // tamanho)
    st.session_state['gama_contatos_pagina'] = max(
        1, min(paginas, st.session_state.get('gama_contatos_pagina', 1)),
    )

    # 7.3 Cabeçalho e cadastro principal
    titulo_col, novo_col = st.columns([4, 1.3], vertical_alignment='center')
    with titulo_col:
        cabecalho(
            'Contatos',
            'Organize os participantes de cada campanha. Selecione contatos, intervalos ou todos os resultados.',
            'Sua base',
        )
    novo = novo_col.button(
        'Novo contato', type='primary', icon=':material/person_add:',
        key='gama_novo_contato', width='stretch',
    )
    indicadores([
        ('Contatos', total, 'Pessoas cadastradas na sua base', 'contatos'),
        ('Listas', contagens['listas'], 'Bases organizadas para suas campanhas', 'lista'),
        ('Recebem emails', contagens['recebem'], 'Contatos habilitados para receber', 'email'),
        ('Não recebem', contagens['nao_recebem'], 'Descadastros registrados', 'modelo'),
    ])
    aviso = st.session_state.pop('gama_contatos_aviso', None)
    if aviso:
        st.success(aviso)

    with st.container(border=True, key='painel_contatos'):
        # 7.4 Visualização separada por campanha
        st.session_state['gama_contatos_campanha_visivel'] = filtros['campanha']
        st.selectbox(
            'Campanha dos contatos',
            [None] + [c['id'] for c in campanhas],
            format_func=lambda valor: (
                'Todos os contatos' if valor is None
                else next(c['name'] for c in campanhas if c['id'] == valor)
            ),
            placeholder='Todos os contatos',
            key='gama_contatos_campanha_visivel',
            on_change=_escolher_campanha,
        )

        # 7.5 Barra de ações
        importar_col, exportar_col, campanhas_col, pesquisa_col, limpar_col = st.columns(
            [1, 1.2, 1.7, 1.7, 1.3],
            gap='small', vertical_alignment='bottom',
        )
        importar = importar_col.button(
            'Importar', icon=':material/upload:',
            key='gama_importar_contatos', width='stretch',
        )
        exportar_col.download_button(
            'Exportar CSV',
            lambda filtros=filtros.copy(): _exportar_filtrados(filtros),
            'contatos.csv', 'text/csv', disabled=not quantidade,
            icon=':material/download:',
            help='Exporta todos os contatos encontrados pelos filtros atuais.',
            key='gama_exportar_contatos', width='stretch',
        )
        pesquisar = pesquisa_col.button(
            'Pesquisar e filtrar', icon=':material/search:',
            key='gama_abrir_pesquisa', width='stretch',
        )
        if limpar_col.button(
            'Limpar filtros', disabled=filtros == _filtros_padrao(),
            key='gama_limpar_filtros', width='stretch',
        ):
            st.session_state['gama_filtros_contatos'] = _filtros_padrao()
            st.rerun()
        st.divider()

        # 7.6 Cabeçalho da tabela
        titulo_tabela_col, editar_col = st.columns(
            [3.5, 1.5], vertical_alignment='center',
        )
        with titulo_tabela_col:
            st.subheader('Base de contatos')
            st.caption(
                f"{quantidade} "
                f"{'contato encontrado' if quantidade == 1 else 'contatos encontrados'} "
                f"de {total} cadastrados."
            )
        partes = []
        if filtros['busca']:
            partes.append(f"Busca: {filtros['busca']}")
        if filtros['campo'] != 'Todos os campos':
            partes.append(f"Campo: {filtros['campo']}")
        if filtros['campanha'] is not None:
            partes.append(
                'Campanha: '
                + next(c['name'] for c in campanhas if c['id'] == filtros['campanha'])
            )
        if filtros['lista'] is not None:
            partes.append(f"Lista: {filtros['lista']}")
        partes.append(f"Ordem: {filtros['ordem']}")
        st.caption(' · '.join(partes))

        # 7.7 Seleção completa ou por intervalo
        todos_col, limpar_selecao_col, de_col, ate_col, intervalo_col = st.columns(
            [2.1, 1.3, .8, .8, 1.5], vertical_alignment='bottom',
        )
        todos_col.button(
            'Selecionar todos os resultados', disabled=not quantidade,
            on_click=_selecionar_resultados, args=(filtros.copy(),),
            key='gama_contatos_selecionar_todos', width='stretch',
            help='Seleciona todos os contatos encontrados, inclusive em outras páginas.',
        )
        limpar_selecao_col.button(
            'Limpar seleção', on_click=_definir_selecao, args=([],),
            key='gama_contatos_limpar_selecao', width='stretch',
        )
        for chave in (
            'gama_contatos_intervalo_de',
            'gama_contatos_intervalo_ate',
        ):
            st.session_state[chave] = max(
                1, min(max(1, quantidade), st.session_state.get(chave, 1)),
            )
        de_col.number_input(
            'De', min_value=1, max_value=max(1, quantidade), step=1,
            key='gama_contatos_intervalo_de', disabled=not quantidade,
        )
        ate_col.number_input(
            'Até', min_value=1, max_value=max(1, quantidade), step=1,
            key='gama_contatos_intervalo_ate', disabled=not quantidade,
        )
        intervalo_col.button(
            'Aplicar intervalo', disabled=not quantidade,
            on_click=_selecionar_intervalo, args=(filtros.copy(),),
            key='gama_contatos_selecionar_intervalo', width='stretch',
        )
        st.checkbox(
            'Adicionar à seleção atual',
            key='gama_contatos_intervalo_adicionar',
            help='Desmarcado: o intervalo substitui a seleção. Marcado: os contatos do intervalo são adicionados.',
        )
        st.caption('De e Até correspondem à coluna Posição na ordem atual. Aplicar intervalo substitui a seleção, salvo quando a opção acima estiver marcada.')
        erro_selecao = st.session_state.get('gama_contatos_selecao_erro')
        if erro_selecao:
            st.error(erro_selecao)

        # 7.8 Consultar os registros da página atual
        pagina = st.session_state['gama_contatos_pagina']
        with closing(connect()) as db:
            encontrados = _pagina_contatos(db, filtros, pagina, tamanho)

        # 7.9 Seleção global por ID
        revisao = st.session_state.get('gama_contatos_revisao', 0)
        chave_tabela = (
            f'gama_tabela_contatos_{revisao}_{pagina}_{tamanho}_'
            f'{tuple(c["id"] for c in encontrados)}'
        )
        ids_selecionados = set(
            st.session_state.get('gama_contatos_ids_selecionados', [])
        )
        ids_pagina = {c['id'] for c in encontrados}
        selecao_inicial = [
            indice for indice, contato in enumerate(encontrados)
            if contato['id'] in ids_selecionados
        ]
        dados_tabela = [
            {'Posição': (pagina - 1) * tamanho + indice + 1, **linha}
            for indice, linha in enumerate(_linhas_tabela(encontrados))
        ]
        linhas_selecionadas = tabela_selecionavel(
            dados_tabela, key=chave_tabela,
            modo='multi', selecao_inicial=selecao_inicial,
        )
        indices_validos = [
            indice for indice in linhas_selecionadas
            if isinstance(indice, int) and not isinstance(indice, bool)
            and 0 <= indice < len(encontrados)
        ]
        ids_selecionados = (
            ids_selecionados - ids_pagina
        ) | {encontrados[i]['id'] for i in indices_validos}
        st.session_state['gama_contatos_ids_selecionados'] = sorted(ids_selecionados)

        contato_edicao = (
            next(iter(ids_selecionados))
            if len(ids_selecionados) == 1 else None
        )
        selecionado = None
        if contato_edicao is not None:
            with closing(connect()) as db:
                selecionado = db.execute(
                    'SELECT name,email FROM contacts WHERE id=?',
                    (contato_edicao,),
                ).fetchone()
            if selecionado is None:
                contato_edicao = None

        st.caption(
            f'{len(ids_selecionados)} '
            f'{"contato selecionado" if len(ids_selecionados) == 1 else "contatos selecionados"} '
            'em todas as páginas.'
        )
        if selecionado:
            st.caption(
                f"Selecionado: {selecionado['name'] or selecionado['email']} "
                f"· {selecionado['email']}"
            )
        else:
            st.caption('Marque as linhas para selecionar vários contatos. Para editar, mantenha apenas um contato selecionado.')

        editar = editar_col.button(
            'Editar contato selecionado', disabled=contato_edicao is None,
            icon=':material/edit:', key='gama_editar_contato',
            width='stretch',
        )
        organizar = campanhas_col.button(
            'Organizar campanhas',
            disabled=not campanhas or not ids_selecionados,
            icon=':material/folder_open:',
            help='Adicione ou remova os contatos selecionados de uma campanha.',
            key='gama_organizar_contatos', width='stretch',
        )
        excluir_selecao_col, excluir_todos_col = st.columns(2)
        excluir_selecionados = excluir_selecao_col.button(
            'Excluir selecionados', disabled=not ids_selecionados,
            icon=':material/delete:',
            key='gama_contatos_excluir_selecionados',
        )
        excluir_todos = excluir_todos_col.button(
            'Excluir todos os contatos', disabled=not total,
            icon=':material/delete_sweep:', key='gama_contatos_excluir_todos',
            help='Exclui a base inteira, inclusive outras campanhas e listas. Exige confirmação.',
        )

        # 7.10 Paginação sem perder a seleção
        st.divider()
        inicio = (pagina - 1) * tamanho + 1 if encontrados else 0
        fim = (pagina - 1) * tamanho + len(encontrados) if encontrados else 0
        st.caption(
            f'Mostrando {inicio}–{fim} de {quantidade} '
            f'· Página {pagina} de {paginas}'
        )
        anterior_col, pagina_col, proxima_col, tamanho_col = st.columns(
            [1, 1, 1, 1.4], vertical_alignment='bottom',
        )
        anterior_col.button(
            'Anterior', disabled=pagina <= 1, on_click=_mudar_pagina,
            args=(pagina - 1,), key='gama_contatos_anterior',
        )
        pagina_col.number_input(
            'Página', min_value=1, max_value=paginas, step=1,
            key='gama_contatos_pagina', on_change=_limpar_selecao_tabela,
        )
        proxima_col.button(
            'Próxima', disabled=pagina >= paginas, on_click=_mudar_pagina,
            args=(pagina + 1,), key='gama_contatos_proxima',
        )
        tamanho_col.selectbox(
            'Contatos por página', [25, 50, 100], index=1,
            key='gama_contatos_tamanho', on_change=_reiniciar_paginacao,
        )

    problemas = st.session_state.pop('gama_contatos_erros', [])
    if problemas:
        with st.expander('Ver linhas ignoradas na importação'):
            st.text('\n'.join(problemas))

    # 7.11 Abrir somente uma janela por execução
    if (
        novo or importar or organizar or pesquisar or editar
        or excluir_selecionados or excluir_todos
    ):
        versao = st.session_state.get('gama_contatos_abertura', 0) + 1
        st.session_state['gama_contatos_abertura'] = versao
        if novo:
            _dialogo_contato(contato_id=None, versao=versao)
        elif importar:
            _dialogo_importacao(versao=versao)
        elif organizar:
            _dialogo_campanhas(
                versao=versao, contato_ids=sorted(ids_selecionados),
            )
        elif pesquisar:
            for nome in ('busca', 'campo', 'campanha', 'lista', 'ordem'):
                st.session_state[f'gama_pesquisa_{nome}'] = filtros[nome]
            _dialogo_pesquisa(campos, campanhas, listas)
        elif editar:
            _dialogo_contato(contato_id=contato_edicao, versao=versao)
        elif excluir_selecionados or excluir_todos:
            _dialogo_exclusao(
                contato_ids=sorted(ids_selecionados) if not excluir_todos else [],
                todos=excluir_todos, versao=versao,
            )