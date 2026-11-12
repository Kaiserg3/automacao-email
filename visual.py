"""Identidade visual do GAMA, independente da lógica de envio."""

# =========== 1. IMPORTAÇÕES E ÍCONES VETORIAIS ===========
# 1.1 Recursos locais, sem bibliotecas ou fontes externas
from html import escape
from pathlib import Path

import streamlit as st


# 1.2 Ícones desenhados em SVG
ICONS = {
    'marca': '<path d="M5 19V5h14v4H9v10z"/>',
    'contatos': '<circle cx="9" cy="8" r="3"/><path d="M3 20v-2a6 6 0 0 1 12 0v2M16 5a3 3 0 0 1 0 6m2 3a5 5 0 0 1 3 5"/>',
    'email': '<rect x="3" y="5" width="18" height="14" rx="3"/><path d="m3 7 9 6 9-6"/>',
    'modelo': '<rect x="5" y="3" width="14" height="18" rx="2"/><path d="M9 8h6M9 12h6M9 16h3"/>',
    'campanha': '<path d="m3 11 18-8-8 18-2-8-8-2zM11 13l5-5"/>',
    'lista': '<rect x="3" y="3" width="18" height="18" rx="3"/><path d="M8 8h9M8 12h9M8 16h9M6 8h.01M6 12h.01M6 16h.01"/>',
}


def icone(nome):
    desenho = ICONS.get(nome, ICONS['email'])
    return (
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" '
        f'aria-hidden="true">{desenho}</svg>'
    )


# =========== 2. PALETA E APLICAÇÃO DO TEMA ===========
# 2.1 Cores e tamanhos de fonte
def aplicar_tema(escuro=True):
    tamanho = max(18, min(24, int(st.session_state.get('gama_fonte_base', 22))))
    comuns = {
        'font-base': f'{tamanho}px',
        'font-support': f'{tamanho - 2}px',
        'font-small': f'{max(16, tamanho - 4)}px',
        'button': '#7052cf',
        'accent-line': '#8b72df55',
        'accent-soft': '#8b72df14',
        'red-soft': '#ef77771a',
        'green-soft': '#55c6a51a',
    }
    if escuro:
        cores = {
            'bg': '#10131d',
            'surface': '#181d2b',
            'sidebar': '#151925',
            'text': '#edf0f7',
            'muted': '#adb5c8',
            'line': '#30384b',
            'inset': '#131826',
            'hover': '#222a3c',
            'accent': '#c1afff',
            'green': '#78d9b7',
            'red': '#f49b9b',
            'scheme': 'dark',
            'amber': '#f1c578',
            'amber-soft': '#f1c57815',
            'shadow': '0 4px 18px #00000012',
            'shadow-hover': '0 14px 32px #00000035',
        }
    else:
        cores = {
            'bg': '#f5f6fa',
            'surface': '#ffffff',
            'sidebar': '#ffffff',
            'text': '#242339',
            'muted': '#606b80',
            'line': '#dce0ea',
            'inset': '#f7f8fb',
            'hover': '#f0ecfa',
            'accent': '#6241b8',
            'green': '#21725a',
            'red': '#b04444',
            'scheme': 'light',
            'amber': '#926014',
            'amber-soft': '#fff5df',
            'shadow': '0 4px 18px #24335a05',
            'shadow-hover': '0 14px 32px #24335a12',
        }
    variaveis = ';'.join(
        f'--gama-{nome}:{valor}'
        for nome, valor in {**comuns, **cores}.items()
    )
    css = Path(__file__).with_name('estilos.css').read_text(encoding='utf-8')
    st.markdown(
        f'<style>:root{{{variaveis}}}{css}</style>',
        unsafe_allow_html=True,
    )


# =========== 3. MARCA E HIERARQUIA DE PÁGINAS ===========
# 3.1 Marca e indicação do espaço de trabalho
def marca():
    st.markdown(
        f'<div class="gama-brand"><span class="gama-monogram">{icone("marca")}</span>'
        '<div><div class="gama-brand-name">GAMA</div>'
        '<div class="gama-brand-sub">Email & relacionamentos</div></div></div>'
        f'<div class="gama-workspace">{icone("lista")}<div><strong>Meu espaço</strong>'
        'Organização local</div></div><div class="gama-nav-label">Principal</div>',
        unsafe_allow_html=True,
    )


# 3.2 Contexto da página
def barra_superior(pagina):
    st.markdown(
        '<div class="gama-topbar"><div class="gama-topbar-path">'
        '<strong>GAMA</strong><span aria-hidden="true">/</span>'
        f'<span>{escape(pagina)}</span></div>'
        '<span class="gama-local"><span aria-hidden="true"></span>Espaço local</span></div>',
        unsafe_allow_html=True,
    )


# 3.3 Título e descrição
def cabecalho(titulo, descricao, contexto):
    with st.container(key='gama_titulo_pagina'):
        st.markdown(
            f'<div class="gama-eyebrow">{escape(contexto)}</div>',
            unsafe_allow_html=True,
        )
        st.title(titulo)
        st.caption(descricao)


# =========== 4. INDICADORES E ESTADOS DE CONTEÚDO ===========
# 4.1 Cards responsivos com dados reais
def indicadores(itens):
    cards = []
    for ordem, (rotulo, valor, descricao, simbolo) in enumerate(itens):
        numero = f'{valor:,}'.replace(',', '.') if isinstance(valor, int) else str(valor)
        cards.append(
            f'<article class="gama-kpi" style="--order:{ordem}">'
            f'<span class="gama-kpi-icon" data-icon="{escape(simbolo, quote=True)}">{icone(simbolo)}</span>'
            '<div class="gama-kpi-body">'
            f'<div class="gama-kpi-number">{escape(numero)}</div>'
            f'<div class="gama-kpi-label">{escape(rotulo)}</div>'
            f'<div class="gama-kpi-note">{escape(descricao)}</div></div></article>'
        )
    st.markdown(
        '<div class="gama-kpis">' + ''.join(cards) + '</div>',
        unsafe_allow_html=True,
    )


# 4.2 Coleções vazias
def vazio(titulo, descricao, simbolo='lista'):
    st.markdown(
        f'<div class="gama-empty"><span class="gama-empty-icon">{icone(simbolo)}</span>'
        f'<h4>{escape(titulo)}</h4><p>{escape(descricao)}</p></div>',
        unsafe_allow_html=True,
    )


# 4.3 Prévia com a mesma formatação usada no email
def texto_template(texto):
    from formatting import body_html

    st.markdown(
        '<div class="gama-template-text" style="white-space:normal">'
        + body_html(str(texto)) + '</div>',
        unsafe_allow_html=True,
    )


# 4.4 Resumos de listas
def item_lista(nome, quantidade):
    st.markdown(
        f'<div class="gama-list-row"><strong>{escape(nome)}</strong>'
        f'<span class="gama-list-count">{quantidade} contatos</span></div>',
        unsafe_allow_html=True,
    )


# 4.5 Distribuição de resultados registrados
def resumo_envios(itens):
    total = sum(max(0, int(valor)) for _, valor, _ in itens)
    linhas = []
    for rotulo, valor, estado in itens:
        valor = max(0, int(valor))
        percentual = valor * 100 / total if total else 0
        linhas.append(
            '<div class="gama-distribution-row">'
            f'<span class="gama-distribution-label">{escape(rotulo)}</span>'
            f'<div class="gama-distribution-track" aria-hidden="true"><span data-state="{escape(estado, quote=True)}" '
            f'style="width:{percentual:.2f}%"></span></div>'
            f'<strong>{valor:,}</strong></div>'
        )
    st.markdown(
        '<div class="gama-distribution">' + ''.join(linhas) + '</div>',
        unsafe_allow_html=True,
    )
    if not total:
        st.caption('Os resultados aparecerão após o primeiro envio registrado.')


# 4.6 Atividades recentes
def atividade_recente(itens):
    if not itens:
        vazio(
            'Sua atividade aparecerá aqui.',
            'Os próximos envios registrados serão mostrados nesta área.',
            'email',
        )
        return
    linhas = []
    for item in itens:
        data = str(item.get('data') or '')
        exibicao = data[:16].replace('T', ' ') if data else 'Sem data'
        estado = str(item.get('estado') or '')
        linhas.append(
            '<div class="gama-activity-row">'
            f'<span class="gama-activity-icon" data-state="{escape(estado, quote=True)}">{icone("email")}</span>'
            '<div class="gama-activity-body">'
            f'<strong>{escape(str(item.get("titulo") or "Envio registrado"))}</strong>'
            f'<span>{escape(str(item.get("descricao") or ""))}</span>'
            f'<time title="{escape(data, quote=True)}">{escape(exibicao)} UTC</time></div>'
            f'<span class="gama-status" data-state="{escape(estado, quote=True)}">{escape(estado)}</span></div>'
        )
    st.markdown(
        '<div class="gama-activity">' + ''.join(linhas) + '</div>',
        unsafe_allow_html=True,
    )


# =========== 5. TABELAS DE CONSULTA ===========
# 5.1 Tabelas somente para leitura
def tabela(dados):
    if not dados:
        vazio(
            'Nenhum registro por aqui',
            'Os resultados aparecerão assim que houver dados para esta seleção.',
        )
        return
    colunas = list(dict.fromkeys(chave for linha in dados for chave in linha))
    cabecalho_html = ''.join(
        f'<th scope="col">{escape(str(coluna))}</th>' for coluna in colunas
    )
    linhas = []
    for linha in dados:
        celulas = []
        for coluna in colunas:
            valor = linha.get(coluna, '')
            valor = '' if valor is None else str(valor)
            conteudo = escape(valor)
            if str(coluna).casefold() == 'status':
                conteudo = (
                    f'<span class="gama-status" data-state="{escape(valor, quote=True)}">'
                    f'{conteudo}</span>'
                )
            celulas.append(f'<td>{conteudo}</td>')
        linhas.append('<tr>' + ''.join(celulas) + '</tr>')
    st.markdown(
        f'<div class="gama-table-wrap"><table class="gama-table"><thead><tr>{cabecalho_html}</tr>'
        '</thead><tbody>' + ''.join(linhas) + '</tbody></table></div>',
        unsafe_allow_html=True,
    )