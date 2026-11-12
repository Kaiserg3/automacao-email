"""Criação, edição e revisão das campanhas do GAMA."""

# =========== 1. IMPORTAÇÕES E ESTADO DA INTERFACE ===========
# 1.1 A conexão existe somente durante a execução atual da página
import os
from contextlib import closing

import streamlit as st

from componentes_ui import tabela_selecionavel
from storage import connect, contacts_in_campaign, delete_campaign, now, render, set_campaign_contacts
from tracking import base_url
from visual import cabecalho, indicadores, texto_template


# 1.2 Uma nova revisão reinicializa o formulário e sua confirmação de envio
def _nova_campanha():
    st.session_state['campaign_v3_selected'] = None
    st.session_state['campaign_v3_revision'] = st.session_state.get('campaign_v3_revision', 0) + 1


def _trocar_campanha():
    st.session_state['campaign_v3_revision'] = st.session_state.get('campaign_v3_revision', 0) + 1


# 1.3 O callback roda antes da próxima execução do menu lateral
def _novo_modelo(prefixo):
    campos = {'nome', 'modelo', 'lista', 'ordem', 'intervalo', 'lote', 'pausa'}
    valores = {}
    for chave in list(st.session_state):
        if chave.startswith(prefixo + '_'):
            sufixo = chave[len(prefixo) + 1:]
            if sufixo in campos or sufixo.startswith('membros_'):
                valores[chave] = st.session_state[chave]
    st.session_state['campaign_v3_draft_return'] = {
        'selected': st.session_state.get('campaign_v3_selected'),
        'revision': st.session_state.get('campaign_v3_revision', 0),
        'prefixo': prefixo,
        'widgets': valores,
    }
    st.session_state['gama_modelo_retorno_campanha'] = True
    st.session_state['gama_novo_modelo'] = True
    st.session_state['pagina'] = 'Templates de campanha'


# 1.4 A seleção global usa IDs; a tabela recebe índices da página atual
def _revisar_participantes(chave, reiniciar_pagina=False):
    st.session_state[f'{chave}_revisao'] = st.session_state.get(f'{chave}_revisao', 0) + 1
    if reiniciar_pagina:
        st.session_state[f'{chave}_pagina'] = 1
    st.session_state.pop(f'{chave}_erro', None)


def _alterar_participantes(chave, identificadores, acao):
    atuais = set(st.session_state.get(chave, []))
    if acao == 'limpar':
        atuais.clear()
    elif acao == 'substituir':
        atuais = set(identificadores)
    else:
        atuais.update(identificadores)
    st.session_state[chave] = sorted(atuais)
    _revisar_participantes(chave)


def _aplicar_intervalo(chave, identificadores):
    inicio = int(st.session_state.get(f'{chave}_de', 1))
    fim = int(st.session_state.get(f'{chave}_ate', len(identificadores)))
    if not 1 <= inicio <= fim <= len(identificadores):
        st.session_state[f'{chave}_erro'] = 'Informe um intervalo válido: o primeiro contato deve vir antes do último.'
        return
    acao = 'adicionar' if st.session_state.get(f'{chave}_adicionar', False) else 'substituir'
    _alterar_participantes(chave, identificadores[inicio - 1:fim], acao)


def _mudar_pagina_participantes(chave, deslocamento, paginas):
    atual = st.session_state.get(f'{chave}_pagina', 1)
    st.session_state[f'{chave}_pagina'] = min(paginas, max(1, atual + deslocamento))
    _revisar_participantes(chave)


# 1.5 Preservar a seleção entre páginas, origens e editor de modelos
def _selecao_participantes(contatos, membros_padrao, prefixo, lista, restaurados=None):
    restaurados = restaurados or {}
    chave = f'{prefixo}_membros_{lista}'
    ids_validos = {contato['id'] for contato in contatos}
    if chave not in st.session_state:
        st.session_state[chave] = list(membros_padrao)
    st.session_state[chave] = sorted(set(st.session_state[chave]) & ids_validos)
    for sufixo in ('pagina', 'revisao'):
        controle = f'{chave}_{sufixo}'
        if controle not in st.session_state and controle in restaurados:
            st.session_state[controle] = restaurados[controle]

    origem_col, ordem_col, tamanho_col = st.columns([2, 2, 1])
    origens = ['Lista da campanha', 'Todas as listas']
    ordens = ['Nome', 'Empresa', 'Email']
    tamanhos = [25, 50, 100]
    origem = origem_col.selectbox(
        'Mostrar contatos de', origens,
        index=origens.index(restaurados.get(f'{chave}_origem', origens[0])),
        key=f'{chave}_origem', on_change=_revisar_participantes, args=(chave, True),
    )
    ordenacao = ordem_col.selectbox(
        'Ordenar contatos', ordens,
        index=ordens.index(restaurados.get(f'{chave}_ordem', ordens[0])),
        key=f'{chave}_ordem', on_change=_revisar_participantes, args=(chave, True),
    )
    tamanho = tamanho_col.selectbox(
        'Contatos por página', tamanhos,
        index=tamanhos.index(restaurados.get(f'{chave}_tamanho', 50)),
        key=f'{chave}_tamanho', on_change=_revisar_participantes, args=(chave, True),
    )
    campo_ordem = {'Nome': 'name', 'Empresa': 'company', 'Email': 'email'}[ordenacao]
    candidatos = sorted(
        (
            contato for contato in contatos
            if origem == 'Todas as listas' or contato['list_name'] == lista
        ),
        key=lambda contato: (str(contato[campo_ordem] or '').casefold(), contato['id']),
    )
    ids_origem = tuple(contato['id'] for contato in candidatos)
    selecionar, limpar = st.columns(2)
    selecionar.button(
        'Selecionar lista' if origem == 'Lista da campanha' else 'Selecionar todos desta origem',
        key=f'{chave}_selecionar', disabled=not candidatos,
        on_click=_alterar_participantes, args=(chave, ids_origem, 'adicionar'),
    )
    limpar.button(
        'Limpar seleção', key=f'{chave}_limpar', disabled=not contatos,
        on_click=_alterar_participantes, args=(chave, (), 'limpar'),
    )

    # 1.6 Intervalo inclusivo, mesmo entre páginas diferentes
    with st.expander('Selecionar um intervalo de contatos'):
        st.caption('Use as posições da origem e da ordenação acima. O intervalo inclui o primeiro e o último contato, mesmo em páginas diferentes.')
        inicio_col, fim_col, aplicar_col = st.columns(
            [1, 1, 2], vertical_alignment='bottom',
        )
        limite = max(1, len(candidatos))
        for sufixo in ('de', 'ate'):
            controle = f'{chave}_{sufixo}'
            if controle in st.session_state:
                ajustado = min(limite, max(1, int(st.session_state[controle])))
                if ajustado != st.session_state[controle]:
                    st.session_state[controle] = ajustado
        inicio_padrao = min(limite, max(1, int(restaurados.get(f'{chave}_de', 1))))
        fim_padrao = min(limite, max(1, int(restaurados.get(f'{chave}_ate', limite))))
        inicio_col.number_input(
            'Do contato', 1, limite, inicio_padrao,
            key=f'{chave}_de', disabled=not candidatos,
        )
        fim_col.number_input(
            'Até o contato', 1, limite, fim_padrao,
            key=f'{chave}_ate', disabled=not candidatos,
        )
        aplicar_col.button(
            'Aplicar intervalo', key=f'{chave}_intervalo', disabled=not candidatos,
            on_click=_aplicar_intervalo, args=(chave, ids_origem),
        )
        st.checkbox(
            'Adicionar à seleção atual',
            value=bool(restaurados.get(f'{chave}_adicionar', False)),
            key=f'{chave}_adicionar',
        )
        st.caption('Desmarcado: selecionar somente o intervalo. Marcado: manter os participantes atuais e adicionar o intervalo.')
        if erro := st.session_state.get(f'{chave}_erro'):
            st.error(erro)

    # 1.7 Tabela paginada e seleção global
    paginas = max(1, (len(candidatos) + tamanho - 1) // tamanho)
    pagina_chave = f'{chave}_pagina'
    st.session_state[pagina_chave] = min(
        paginas, max(1, int(st.session_state.get(pagina_chave, 1))),
    )
    pagina = st.session_state[pagina_chave]
    inicio = (pagina - 1) * tamanho
    contatos_pagina = candidatos[inicio:inicio + tamanho]
    selecionados = set(st.session_state[chave])
    linhas = [{
        'Posição': inicio + indice + 1,
        'Nome': contato['name'] or '',
        'Email': contato['email'],
        'Empresa': contato['company'] or '',
        'Lista': contato['list_name'],
        'Recebe emails': 'Não' if contato['unsubscribed'] else 'Sim',
    } for indice, contato in enumerate(contatos_pagina)]
    indices = tabela_selecionavel(
        linhas,
        key=f'{chave}_tabela_{st.session_state.get(f"{chave}_revisao", 0)}_{pagina}_{tamanho}_{tuple(c["id"] for c in contatos_pagina)}',
        modo='multi',
        selecao_inicial=[
            indice for indice, contato in enumerate(contatos_pagina)
            if contato['id'] in selecionados
        ],
    )
    ids_pagina = {contato['id'] for contato in contatos_pagina}
    selecionados.difference_update(ids_pagina)
    selecionados.update(
        contatos_pagina[indice]['id'] for indice in indices
        if type(indice) is int and 0 <= indice < len(contatos_pagina)
    )
    st.session_state[chave] = sorted(selecionados)
    st.caption(f'{len(selecionados)} participantes selecionados no total. {len(candidatos)} contatos nesta origem. Clique nas linhas ou use Shift para selecionar uma sequência na página.')

    anterior, numero, proxima = st.columns([1, 2, 1], vertical_alignment='bottom')
    anterior.button(
        'Anterior', key=f'{chave}_anterior', disabled=pagina <= 1,
        on_click=_mudar_pagina_participantes, args=(chave, -1, paginas),
    )
    numero.number_input(
        'Página de contatos', 1, paginas, key=pagina_chave,
        on_change=_revisar_participantes, args=(chave,),
    )
    proxima.button(
        'Próxima', key=f'{chave}_proxima', disabled=pagina >= paginas,
        on_click=_mudar_pagina_participantes, args=(chave, 1, paginas),
    )
    if candidatos:
        st.caption(
            f'Mostrando {inicio + 1}–{inicio + len(contatos_pagina)} '
            f'de {len(candidatos)} · Página {pagina} de {paginas}'
        )
    st.caption('Os participantes ficam salvos nesta campanha. A origem limita a tabela; a seleção pode reunir outras listas. Descadastros sempre impedem o envio.')
    return st.session_state[chave]


# =========== 2. VALIDAÇÃO E PARTICIPANTES ELEGÍVEIS ===========
# 2.1 Comparar configurações independentemente da ordem dos contatos
def _configuracao(nome, modelo_id, lista, ordem, intervalo, lote, pausa, membros):
    return (
        nome.strip(), modelo_id, lista or '', ordem,
        int(intervalo), int(lote), int(pausa), tuple(sorted(membros)),
    )


# 2.2 Proteções para campanhas novas e retomadas
def _elegiveis(contatos, historico, legados, campanha_id, pular_anteriores):
    emails_bloqueados = {
        r['email'].casefold() for r in historico
        if r['status'] == 'Incerto'
        or (
            r['status'] == 'Enviado'
            and (pular_anteriores or r['campaign_id'] == campanha_id)
        )
    }
    ids_bloqueados = {
        r['contact_id'] for r in historico
        if r['status'] == 'Incerto'
        or (r['status'] == 'Enviado' and r['campaign_id'] == campanha_id)
    }
    if pular_anteriores:
        emails_bloqueados |= {email.casefold() for email in legados}
    return [
        contato for contato in contatos
        if not contato['unsubscribed']
        and contato['id'] not in ids_bloqueados
        and contato['email'].casefold() not in emails_bloqueados
    ]


# 2.3 Persistir configuração e participantes na mesma transação
def _salvar(db, campanha_id, configuracao):
    nome, modelo_id, lista, ordem, intervalo, lote, pausa, membros = configuracao
    if not nome:
        raise ValueError('Informe um nome para a campanha.')
    if modelo_id is not None and db.execute(
        'SELECT 1 FROM templates WHERE id=?', (modelo_id,),
    ).fetchone() is None:
        raise ValueError('Mensagem: o modelo escolhido não está mais disponível. Selecione outro modelo.')
    with db:
        if campanha_id is None:
            resultado = db.execute('''INSERT INTO campaigns(
                id,name,template_id,list_name,ordering,interval_seconds,batch_size,batch_pause,status,created
                ) SELECT COALESCE(MAX(identificador),0)+1,?,?,?,?,?,?,?,?,? FROM (
                    SELECT id AS identificador FROM campaigns
                    UNION ALL SELECT campaign_id FROM deliveries
                    UNION ALL SELECT campaign_id FROM tracking_messages
                    UNION ALL SELECT campaign_id FROM campaign_contacts
                )''',
                (nome, modelo_id, lista, ordem, intervalo, lote, pausa, 'Rascunho', now()))
            campanha_id = resultado.lastrowid
        else:
            atual = db.execute(
                'SELECT status FROM campaigns WHERE id=?', (campanha_id,),
            ).fetchone()
            if atual is None:
                raise ValueError('Esta campanha não está mais disponível.')
            if atual['status'] == 'Em envio':
                raise ValueError('Aguarde o processamento terminar antes de editar esta campanha.')
            db.execute('''UPDATE campaigns SET name=?,template_id=?,list_name=?,ordering=?,
                interval_seconds=?,batch_size=?,batch_pause=?,status='Rascunho' WHERE id=?''',
                (nome, modelo_id, lista, ordem, intervalo, lote, pausa, campanha_id))
        set_campaign_contacts(db, campanha_id, list(membros))
    return campanha_id


# =========== 3. TELA PRINCIPAL DAS CAMPANHAS ===========
# 3.1 Receber a função de envio do aplicativo
def mostrar_campanhas(enviar_callback):
    with closing(connect()) as db:
        campanhas = [dict(r) for r in db.execute('SELECT * FROM campaigns ORDER BY id DESC')]
        modelos = [dict(r) for r in db.execute('SELECT * FROM templates ORDER BY name,id')]
        contatos = [dict(r) for r in db.execute('SELECT * FROM contacts ORDER BY name,email')]
        historico = [dict(r) for r in db.execute('SELECT * FROM deliveries')]
        legados = {r[0] for r in db.execute('SELECT email FROM legacy_deliveries')}
        ids_campanhas = [c['id'] for c in campanhas]
        nomes_campanhas = {c['id']: c['name'] for c in campanhas}
        nomes_modelos = {m['id']: m['name'] for m in modelos}

        # 3.2 Restaurar o rascunho ao voltar da criação de um modelo
        retorno = st.session_state.pop('campaign_v3_draft_return', None)
        restaurados = {}
        modelo_retornado = None
        novo_modelo_id = st.session_state.pop('campaign_v3_created_model_id', None)
        if retorno and (
            retorno['selected'] is None or retorno['selected'] in ids_campanhas
        ):
            st.session_state['campaign_v3_selected'] = retorno['selected']
            st.session_state['campaign_v3_revision'] = retorno['revision']
            restaurados = retorno['widgets']
            if novo_modelo_id in nomes_modelos:
                modelo_retornado = novo_modelo_id
            st.session_state.pop('gama_modelo_retorno_campanha', None)

        # 3.3 Seleção de campanha salva
        pendente = st.session_state.pop('campaign_v3_pending_id', None)
        if pendente is not None:
            st.session_state['campaign_v3_selected'] = pendente
        if st.session_state.pop('campaign_v3_reset_selection', False):
            st.session_state['campaign_v3_selected'] = None
        if 'campaign_v3_selected' not in st.session_state:
            st.session_state['campaign_v3_selected'] = (
                ids_campanhas[0] if ids_campanhas else None
            )
        elif (
            st.session_state['campaign_v3_selected'] not in ids_campanhas
            and st.session_state['campaign_v3_selected'] is not None
        ):
            st.session_state['campaign_v3_selected'] = (
                ids_campanhas[0] if ids_campanhas else None
            )

        cabecalho(
            'Campanhas',
            'Escolha uma campanha ou crie uma nova. Revise a mensagem, os contatos e o ritmo de envio.',
            'Sua comunicação',
        )
        if aviso := st.session_state.pop('campaign_v3_message', None):
            st.success(aviso)
        seletor, novo = st.columns([8, 1], vertical_alignment='bottom')
        campanha_id = seletor.selectbox(
            'Campanha salva', ids_campanhas, index=None,
            format_func=lambda v: nomes_campanhas[v],
            placeholder='Selecione uma campanha',
            disabled=not campanhas, key='campaign_v3_selected',
            on_change=_trocar_campanha,
        )
        novo.button(
            '+', type='primary', help='Nova campanha',
            key='campaign_v3_new_button', on_click=_nova_campanha,
            width='stretch',
        )
        campanha = next((c for c in campanhas if c['id'] == campanha_id), None)
        revisao = st.session_state.get('campaign_v3_revision', 0)
        prefixo = f'campaign_v3_{campanha_id or "nova"}_{revisao}'
        if campanha is None:
            st.caption('Nova campanha · rascunho ainda não salvo')
        else:
            st.caption(f"{campanha['name']} · {campanha['status']}")

        # 3.4 Dados iniciais
        padrao = campanha or {
            'name': '', 'template_id': None, 'list_name': '',
            'ordering': 'Nome', 'interval_seconds': 5,
            'batch_size': 20, 'batch_pause': 0,
        }
        listas = sorted(
            {c['list_name'] for c in contatos}
            | ({padrao['list_name']} if padrao['list_name'] else set())
        )
        membros_salvos = (
            contacts_in_campaign(db, campanha_id)
            if campanha_id is not None else []
        )
        ids_salvos = [c['id'] for c in membros_salvos]
        nomes_contatos = {
            c['id']: f"{c['name'] or c['email']} — {c['email']}"
            for c in contatos
        }

        # 3.5 Identificação e ação principal
        acoes = st.container(border=True, key='painel_campanha_identificacao')
        with acoes:
            nome = st.text_input(
                'Nome da campanha',
                restaurados.get(f'{prefixo}_nome', padrao['name']),
                key=f'{prefixo}_nome',
            )
            if campanha is None:
                st.caption('Informe o nome para criar um rascunho. Você pode completar a mensagem e os destinatários depois.')
        mensagem_tab, contatos_tab, envio_tab = st.tabs([
            'Mensagem', 'Destinatários', 'Envio',
        ])

        # =========== 4. MENSAGEM DA CAMPANHA ===========
        # 4.1 Escolher modelo ou abrir a criação pelo +
        with mensagem_tab:
            with st.container(border=True, key='painel_campanha_mensagem'):
                modelo_col, novo_modelo_col = st.columns(
                    [8, 1], vertical_alignment='bottom',
                )
                ids_modelos = list(nomes_modelos)
                modelo_padrao = modelo_retornado or restaurados.get(
                    f'{prefixo}_modelo', padrao['template_id'],
                )
                if campanha is None and modelo_padrao is None and ids_modelos:
                    modelo_padrao = ids_modelos[0]
                indice_modelo = (
                    ids_modelos.index(modelo_padrao)
                    if modelo_padrao in ids_modelos else None
                )
                modelo_id = modelo_col.selectbox(
                    'Modelo salvo', ids_modelos, index=indice_modelo,
                    placeholder='Selecione um modelo',
                    format_func=lambda v: nomes_modelos[v],
                    key=f'{prefixo}_modelo', disabled=not modelos,
                )
                novo_modelo_col.button(
                    '+', type='primary', help='Criar novo modelo',
                    key=f'{prefixo}_novo_modelo', on_click=_novo_modelo,
                    args=(prefixo,), width='stretch',
                )
                if not modelos:
                    st.info('Você pode criar o rascunho agora. Use + para adicionar o modelo de mensagem antes de enviar.')
                st.caption('A campanha usa a última versão salva do modelo, incluindo seu anexo.')

        # =========== 5. DESTINATÁRIOS DA CAMPANHA ===========
        # 5.1 A seleção salva pode reunir várias listas
        with contatos_tab:
            with st.container(border=True, key='painel_campanha_destinatarios'):
                lista_padrao = restaurados.get(
                    f'{prefixo}_lista', padrao['list_name'],
                )
                indice_lista = (
                    listas.index(lista_padrao) if lista_padrao in listas
                    else (0 if listas else None)
                )
                lista = st.selectbox(
                    'Lista da campanha', listas, index=indice_lista,
                    placeholder='Selecione uma lista',
                    key=f'{prefixo}_lista', disabled=not listas,
                )
                if campanha is not None and lista == campanha['list_name']:
                    membros_padrao = ids_salvos
                else:
                    membros_padrao = [
                        c['id'] for c in contatos if c['list_name'] == lista
                    ]
                membros_padrao = restaurados.get(
                    f'{prefixo}_membros_{lista}', membros_padrao,
                )
                membros_padrao = [
                    identificador for identificador in membros_padrao
                    if identificador in nomes_contatos
                ]
                membros = _selecao_participantes(
                    contatos, membros_padrao, prefixo, lista, restaurados,
                )
                if not contatos:
                    st.info('Cadastre ou importe contatos na aba Contatos para escolher os destinatários.')

        # =========== 6. RITMO E ORDEM DE ENVIO ===========
        # 6.1 Todos os controles participam da configuração salva
        with envio_tab:
            with st.container(border=True, key='painel_campanha_ritmo'):
                ordem = st.selectbox(
                    'Ordem de envio', ['Nome', 'Empresa', 'Email'],
                    index=['Nome', 'Empresa', 'Email'].index(
                        restaurados.get(f'{prefixo}_ordem', padrao['ordering'])
                    ),
                    key=f'{prefixo}_ordem',
                )
                a, b, c = st.columns(3)
                intervalo = a.number_input(
                    'Segundos entre cada email', 1, 300,
                    int(restaurados.get(
                        f'{prefixo}_intervalo', padrao['interval_seconds'],
                    )),
                    key=f'{prefixo}_intervalo',
                )
                lote = b.number_input(
                    'Emails por lote', 1, 1000,
                    int(restaurados.get(f'{prefixo}_lote', padrao['batch_size'])),
                    key=f'{prefixo}_lote',
                )
                pausa = c.number_input(
                    'Pausa entre lotes (minutos)', 0, 180,
                    int(restaurados.get(f'{prefixo}_pausa', padrao['batch_pause'])),
                    key=f'{prefixo}_pausa',
                )
                st.caption('Os intervalos valem entre mensagens. A pausa adicional ocorre ao concluir cada lote, exceto no fim da campanha.')

        # 6.2 Salvar antes de enviar
        configuracao = _configuracao(
            nome, modelo_id, lista, ordem, intervalo, lote, pausa, membros,
        )
        original = _configuracao(
            padrao['name'], padrao['template_id'], padrao['list_name'],
            padrao['ordering'], padrao['interval_seconds'],
            padrao['batch_size'], padrao['batch_pause'], ids_salvos,
        )
        alterada = campanha is None or configuracao != original
        em_envio = bool(campanha and campanha['status'] == 'Em envio')
        with acoes:
            if alterada and campanha is not None:
                st.info('Há alterações ainda não salvas. Use Salvar alterações antes de enviar.')
            rotulo_salvar = (
                'Criar campanha' if campanha is None else 'Salvar alterações'
            )
            if st.button(
                rotulo_salvar, type='primary',
                key=f'{prefixo}_salvar', disabled=em_envio,
            ):
                try:
                    salvo_id = _salvar(db, campanha_id, configuracao)
                except ValueError as erro:
                    st.error(str(erro))
                else:
                    st.session_state['campaign_v3_pending_id'] = salvo_id
                    st.session_state['campaign_v3_revision'] = revisao + 1
                    st.session_state['campaign_v3_message'] = (
                        'Campanha salva como rascunho. Complete as etapas '
                        'e confira a revisão antes de enviar.'
                    )
                    st.rerun()
            pendencias = []
            if modelo_id is None:
                pendencias.append('Mensagem: escolha ou crie um modelo salvo.')
            if not membros:
                pendencias.append('Destinatários: escolha uma lista e seus participantes.')
            if pendencias:
                st.info(
                    'O rascunho pode ser salvo. Para enviar, complete:\n'
                    + '\n'.join(pendencias)
                )

        # =========== 7. PRÉVIA E REVISÃO ANTES DO ENVIO ===========
        modelo = next((m for m in modelos if m['id'] == modelo_id), None)
        participantes = [c for c in contatos if c['id'] in set(membros)]
        with mensagem_tab:
            if modelo:
                with st.expander('Prévia da mensagem', expanded=True):
                    if participantes:
                        amostra_previa = participantes[:100]
                        previa_id = st.selectbox(
                            'Contato da prévia',
                            [c['id'] for c in amostra_previa],
                            format_func=lambda v: nomes_contatos[v],
                            key=f'{prefixo}_previa',
                        )
                        if len(participantes) > len(amostra_previa):
                            st.caption(
                                f'A prévia mostra os primeiros {len(amostra_previa)} participantes. '
                                f'Todos os {len(participantes)} contatos continuam na seleção da campanha.'
                            )
                        try:
                            assunto, corpo = render(
                                modelo,
                                next(c for c in amostra_previa if c['id'] == previa_id),
                            )
                            st.write(assunto)
                            texto_template(corpo)
                        except (
                            KeyError, ValueError, IndexError,
                            AttributeError, TypeError,
                        ) as erro:
                            st.warning(f'Revise as variáveis do modelo: {erro}')
                    else:
                        st.write(modelo['subject'])
                        texto_template(modelo['body'])
                    if modelo['attachment_name']:
                        st.caption('Anexo: ' + modelo['attachment_name'])

        # 7.1 Destinatários elegíveis e proteções de retomada
        with envio_tab:
            st.subheader('Revisão do envio')
            pular_anteriores = st.checkbox(
                'Pular contatos que já receberam emails em outras campanhas ou no histórico antigo',
                value=True, key=f'campaign_v3_skip_{campanha_id or "nova"}',
            )
            elegiveis = _elegiveis(
                participantes, historico, legados,
                campanha_id, pular_anteriores,
            )
            chave_ordem = {'Nome': 'name', 'Empresa': 'company', 'Email': 'email'}[ordem]
            elegiveis.sort(key=lambda c: str(c[chave_ordem] or '').casefold())
            st.caption(
                f'{len(elegiveis)} elegíveis; '
                f'{len(participantes) - len(elegiveis)} excluídos por descadastro ou histórico.'
            )
            st.caption('Para repetir um teste, remova o registro correspondente em Desempenho → Histórico de envios. Contatos descadastrados continuam impedidos de receber emails.')
            indicadores([
                ('Participantes', len(participantes), 'Contatos configurados nesta campanha', 'contatos'),
                ('Elegíveis', len(elegiveis), 'Podem receber uma nova mensagem', 'email'),
                ('Protegidos', len(participantes) - len(elegiveis), 'Descadastro ou histórico de envio', 'modelo'),
                ('Intervalo', f'{intervalo} s', 'Tempo entre mensagens', 'campanha'),
            ])
            linhas = [{
                'Selecionado': True, 'id': c['id'], 'nome': c['name'],
                'email': c['email'], 'empresa': c['company'],
            } for c in elegiveis]
            if linhas:
                edicao = st.data_editor(
                    linhas, row_height=46,
                    disabled=['id', 'nome', 'email', 'empresa'],
                    hide_index=True,
                    column_order=['Selecionado', 'nome', 'email', 'empresa'],
                    width='stretch',
                    key=f'destinatarios_{campanha_id}_{pular_anteriores}_{tuple(c["id"] for c in elegiveis)}_{revisao}_{st.session_state.get("gama_historico_revisao", 0)}',
                )
                ids_selecionados = {
                    r['id'] for r in edicao if r['Selecionado']
                }
                selecionados = [
                    c for c in elegiveis if c['id'] in ids_selecionados
                ]
            else:
                selecionados = []
                st.info('Nenhum destinatário elegível para esta campanha.')
            st.write(f'**{len(selecionados)}** contatos selecionados para envio.')
            duracao = (
                max(len(selecionados) - 1, 0) * intervalo
                + (max(len(selecionados) - 1, 0) // lote) * pausa * 60
            )
            if selecionados:
                st.caption(
                    f'Estimativa de intervalos: {duracao // 60} min '
                    f'e {duracao % 60} s, sem considerar o tempo de conexão SMTP.'
                )

            # 7.2 Validação e confirmação do envio real
            problemas = []
            if modelo is None:
                problemas.append('Mensagem: escolha um modelo salvo.')
            if not membros:
                problemas.append('Destinatários: escolha uma lista e seus participantes.')
            if modelo is not None:
                for contato in selecionados:
                    try:
                        assunto, _ = render(modelo, contato)
                        if '\r' in assunto or '\n' in assunto:
                            raise ValueError('O assunto não pode conter quebras de linha.')
                    except (
                        KeyError, ValueError, IndexError,
                        AttributeError, TypeError,
                    ) as erro:
                        problemas.append(f"{contato['email']}: {erro}")
            try:
                endereco_pixel = base_url(db)
            except ValueError as erro:
                problemas.append(
                    'Revise o endereço do pixel em Configurações: ' + str(erro)
                )
                endereco_pixel = ''
            if problemas:
                st.error('Corrija antes de enviar:\n' + '\n'.join(problemas))
            if endereco_pixel:
                st.caption('Novas mensagens incluirão o pixel configurado. O serviço público precisa estar disponível para receber aberturas.')
            else:
                st.caption('Pixel não configurado: novas mensagens serão identificadas como não rastreadas.')

            credenciais = bool(
                os.getenv('EMAIL_REMETENTE') and os.getenv('SENHA_APP')
            )
            if not credenciais:
                st.info('Configure EMAIL_REMETENTE e SENHA_APP para habilitar o envio real pelo Gmail.')
            if em_envio:
                st.warning('Esta campanha está em processamento. Aguarde antes de iniciar outro envio.')
            confirmado = st.checkbox(
                'Confirmo o envio real para os contatos selecionados e tenho autorização para contatá-los.',
                key=f'{prefixo}_confirmado',
            )
            st.caption('Mantenha a sessão ativa. Cada resultado será salvo; salvar a configuração nunca dispara mensagens.')
            pronto = bool(
                campanha and not alterada and not em_envio
                and selecionados and modelo and not problemas
                and credenciais and confirmado
            )
            if st.button(
                'Iniciar envio de emails', type='primary',
                disabled=not pronto, key=f'{prefixo}_enviar',
            ):
                enviar_callback(db, campanha, modelo, selecionados)
                st.button('Atualizar resultados', key=f'{prefixo}_atualizar')

        # =========== 8. EXCLUSÃO DA CAMPANHA ===========
        # 8.1 Contatos e modelos são preservados
        if campanha is not None:
            with st.expander('Excluir campanha'):
                st.write('A campanha e sua seleção de participantes serão excluídas. Seus contatos, listas e modelos permanecem disponíveis.')
                apagar_historico = st.checkbox(
                    'Apagar também o histórico desta campanha',
                    value=False, key=f'{prefixo}_excluir_historico',
                    disabled=em_envio,
                )
                if apagar_historico:
                    st.caption('Os registros de envio e as aberturas desta campanha serão removidos. Isso permite repetir os testes para seus destinatários.')
                else:
                    st.caption('O histórico de envios e de aberturas será preservado em Desempenho.')
                excluir_confirmado = st.checkbox(
                    'Confirmo a exclusão desta campanha',
                    key=f'{prefixo}_excluir_confirmado', disabled=em_envio,
                )
                if st.button(
                    'Excluir campanha', key=f'{prefixo}_excluir',
                    disabled=em_envio or not excluir_confirmado,
                ):
                    try:
                        delete_campaign(
                            db, campanha_id, remove_history=apagar_historico,
                        )
                    except ValueError as erro:
                        st.error(str(erro))
                    else:
                        st.session_state['campaign_v3_reset_selection'] = True
                        st.session_state['campaign_v3_revision'] = revisao + 1
                        st.session_state['campaign_v3_message'] = (
                            'Campanha excluída. Contatos e modelos foram preservados.'
                        )
                        st.rerun()