"""Tabela e editor que acompanham a aparência do GAMA."""

# =========== 1. IMPORTAÇÕES E ESTADO DOS COMPONENTES ===========
# 1.1 Componentes oficiais V2, sem bibliotecas ou recursos externos
import streamlit as st


def _estado(chave):
    estado = st.session_state.get(chave, {})
    return estado if isinstance(estado, dict) else {}


def _validar_selecao(indices, quantidade, modo):
    if not isinstance(indices, (list, tuple)):
        return []
    validos = list(dict.fromkeys(
        indice for indice in indices
        if type(indice) is int and 0 <= indice < quantidade
    ))
    return validos[:1] if modo == 'single' else validos


# =========== 2. TABELA COM SELEÇÃO ===========
# 2.1 A tabela usa elementos HTML e variáveis herdadas do tema
_TABELA_HTML = '<div class="gama-table-scroll"><table class="gama-selectable" role="grid" aria-label="Registros selecionáveis"></table></div>'

_TABELA_CSS = """
:host { display:block; min-width:0; font-family:Inter,Aptos,'Segoe UI',sans-serif; color:var(--gama-text,#edf0f7); }
* { box-sizing:border-box; }
.gama-table-scroll { overflow:auto; max-height:440px; border:1px solid var(--gama-line,#30384b); border-radius:10px; background:var(--gama-surface,#181d2b); }
.gama-selectable { width:100%; min-width:680px; border-collapse:separate; border-spacing:0; font-size:var(--gama-font-support,20px); line-height:1.5; }
th { position:sticky; top:0; z-index:1; background:var(--gama-inset,#131826); color:var(--gama-muted,#adb5c8); font-size:var(--gama-font-small,18px); font-weight:600; text-align:left; white-space:nowrap; }
th,td { padding:12px 14px; border-bottom:1px solid var(--gama-line,#30384b); vertical-align:middle; overflow-wrap:anywhere; max-width:440px; }
tbody tr:last-child td { border-bottom:0; }
tbody tr { cursor:pointer; background:var(--gama-surface,#181d2b); transition:background .15s; }
tbody tr:hover { background:var(--gama-hover,#222a3c); }
tbody tr[aria-selected="true"] { background:var(--gama-accent-soft,#8b72df14); }
tbody tr:focus-visible { outline:2px solid var(--gama-accent,#c1afff); outline-offset:-2px; }
.selection-cell { width:48px; min-width:48px; text-align:center; }
input { width:18px; height:18px; margin:0; cursor:pointer; accent-color:var(--gama-button,#7052cf); }
input:focus-visible { outline:2px solid var(--gama-accent,#c1afff); outline-offset:3px; }
.empty { text-align:center; color:var(--gama-muted,#adb5c8); padding:25px; }
@media(prefers-reduced-motion:reduce) { * { transition:none !important; } }
"""

_TABELA_JS = """
const anchors = new Map();
export default function(component) {
    const {data, parentElement, setStateValue} = component;
    const wrap = parentElement.querySelector('.gama-table-scroll');
    const table = parentElement.querySelector('table');
    const active = parentElement.activeElement;
    const focusedRow = active?.closest('tr')?.dataset.index;
    const focusCheckbox = active?.tagName === 'INPUT';
    const top = wrap.scrollTop;
    const left = wrap.scrollLeft;
    const rows = Array.isArray(data.rows) ? data.rows : [];
    const columns = Array.isArray(data.columns) ? data.columns : [];
    let selection = new Set((data.selection || []).filter(i => Number.isInteger(i) && i >= 0 && i < rows.length));
    const multi = data.mode === 'multi';
    const draftKey = String(data.draftKey);
    let anchor = anchors.get(draftKey);
    const head = document.createElement('thead');
    const body = document.createElement('tbody');
    const header = document.createElement('tr');
    const selectionHeader = document.createElement('th');
    selectionHeader.className = 'selection-cell';
    selectionHeader.scope = 'col';
    let all = null;
    if (multi && rows.length) {
        all = document.createElement('input');
        all.type = 'checkbox';
        all.setAttribute('aria-label','Selecionar todos os registros desta página');
        selectionHeader.appendChild(all);
    } else {
        selectionHeader.setAttribute('aria-label','Seleção');
    }
    header.appendChild(selectionHeader);
    columns.forEach(label => {
        const th = document.createElement('th');
        th.scope = 'col';
        th.textContent = String(label);
        header.appendChild(th);
    });
    head.appendChild(header);
    const rowNodes = [];
    const boxes = [];

    function repaint() {
        rowNodes.forEach((row,index) => row.setAttribute('aria-selected', selection.has(index) ? 'true' : 'false'));
        boxes.forEach((box,index) => { box.checked = selection.has(index); });
        if (all) {
            all.checked = rows.length > 0 && selection.size === rows.length;
            all.indeterminate = selection.size > 0 && selection.size < rows.length;
        }
    }

    function publish() {
        repaint();
        setStateValue('selection', Array.from(selection).sort((a,b) => a-b));
    }

    function toggle(index, extend=false) {
        if (multi && extend && Number.isInteger(anchor)) {
            for (let i=Math.min(anchor,index); i<=Math.max(anchor,index); i++) selection.add(i);
        }
        else if (selection.has(index)) selection.delete(index);
        else {
            if (!multi) selection.clear();
            selection.add(index);
        }
        if (!extend || !Number.isInteger(anchor)) anchor = index;
        anchors.delete(draftKey);
        anchors.set(draftKey,anchor);
        while (anchors.size > 32) anchors.delete(anchors.keys().next().value);
        publish();
    }

    rows.forEach((cells,index) => {
        const row = document.createElement('tr');
        row.tabIndex = 0;
        row.dataset.index = String(index);
        const selector = document.createElement('td');
        selector.className = 'selection-cell';
        const box = document.createElement('input');
        box.type = 'checkbox';
        const nameIndex = columns.indexOf('Nome');
        const label = (nameIndex >= 0 ? cells[nameIndex] : null) || cells.find(value => value !== '') || `Linha ${index+1}`;
        box.setAttribute('aria-label', `Selecionar ${String(label)}`);
        box.addEventListener('click', e => { e.stopPropagation(); box.dataset.extend = String(e.shiftKey); });
        box.addEventListener('change', () => toggle(index, box.dataset.extend === 'true'));
        selector.appendChild(box);
        row.appendChild(selector);
        cells.forEach(value => {
            const cell = document.createElement('td');
            cell.textContent = String(value ?? '');
            row.appendChild(cell);
        });
        row.addEventListener('click', event => toggle(index,event.shiftKey));
        row.addEventListener('keydown', event => {
            if (event.target === row && (event.key === 'Enter' || event.key === ' ')) {
                event.preventDefault();
                toggle(index,event.shiftKey);
            }
        });
        rowNodes.push(row);
        boxes.push(box);
        body.appendChild(row);
    });
    if (!rows.length) {
        const row = document.createElement('tr');
        const cell = document.createElement('td');
        cell.colSpan = columns.length + 1;
        cell.className = 'empty';
        cell.textContent = 'Nenhum registro nesta página.';
        row.appendChild(cell);
        body.appendChild(row);
    }
    if (all) all.addEventListener('change', () => {
        selection = all.checked ? new Set(rows.map((_,index) => index)) : new Set();
        publish();
    });
    table.replaceChildren(head,body);
    repaint();
    wrap.scrollTop = top;
    wrap.scrollLeft = left;
    if (focusedRow !== undefined && rowNodes[Number(focusedRow)]) {
        (focusCheckbox ? boxes[Number(focusedRow)] : rowNodes[Number(focusedRow)]).focus({preventScroll:true});
    }
}
"""

_tabela = st.components.v2.component(
    'gama_tabela_selecionavel', html=_TABELA_HTML, css=_TABELA_CSS,
    js=_TABELA_JS, isolate_styles=True,
)


# 2.2 Manter o mesmo contrato de índices utilizado pela paginação
def tabela_selecionavel(dados, key, modo='single', selecao_inicial=None):
    if modo not in {'single', 'multi'}:
        raise ValueError('Modo de seleção inválido.')
    registros = list(dados)
    if any(not isinstance(registro, dict) for registro in registros):
        raise ValueError('A tabela precisa receber registros em formato de dicionário.')
    colunas = list(dict.fromkeys(chave for registro in registros for chave in registro))
    selecao = _validar_selecao(
        _estado(key).get('selection', selecao_inicial or []), len(registros), modo,
    )
    resultado = _tabela(
        key=key, data={
            'columns': [str(coluna) for coluna in colunas],
            'rows': [['' if registro.get(coluna) is None else str(registro.get(coluna, ''))
                      for coluna in colunas] for registro in registros],
            'selection': selecao, 'mode': modo, 'draftKey': key,
        }, default={'selection': selecao}, on_selection_change=lambda: None,
        width='stretch', height='content',
    )
    return _validar_selecao(resultado.get('selection', selecao), len(registros), modo)


# =========== 3. EDITOR COM NEGRITO E ITÁLICO ===========
# 3.1 O texto permanece em Markdown para manter os modelos portáteis
_EDITOR_HTML = '''<label class="gama-editor-label" for="gama-editor-input">Texto do email</label>
<div class="gama-editor">
<div class="gama-toolbar" role="toolbar" aria-label="Formatação do texto">
  <button type="button" data-format="**" aria-label="Negrito" title="Negrito (Ctrl+B)"><strong>B</strong></button>
  <button type="button" data-format="*" aria-label="Itálico" title="Itálico (Ctrl+I)"><em>I</em></button>
  <span>Selecione um trecho e escolha a formatação.</span>
</div>
<textarea id="gama-editor-input" aria-label="Texto do email" spellcheck="true"></textarea>
<p class="gama-editor-help">Use **negrito** e *itálico*. A prévia mostra o resultado.</p>
</div>'''

_EDITOR_CSS = """
:host { display:block; min-width:0; color:var(--gama-text,#edf0f7); font-family:Inter,Aptos,'Segoe UI',sans-serif; }
* { box-sizing:border-box; }
.gama-editor-label { display:block; margin:0 0 8px; color:var(--gama-text,#edf0f7); font-size:var(--gama-font-base,22px); font-weight:500; line-height:1.5; }
.gama-editor { border:1px solid var(--gama-line,#30384b); border-radius:9px; overflow:hidden; background:var(--gama-inset,#131826); }
.gama-toolbar { display:flex; align-items:center; flex-wrap:wrap; gap:8px; padding:10px; border-bottom:1px solid var(--gama-line,#30384b); background:var(--gama-surface,#181d2b); }
button { width:40px; height:38px; border:1px solid var(--gama-line,#30384b); border-radius:6px; color:var(--gama-text,#edf0f7); background:var(--gama-inset,#131826); font:inherit; font-size:20px; cursor:pointer; }
button:hover { background:var(--gama-accent-soft,#8b72df14); border-color:var(--gama-accent,#c1afff); }
button:focus-visible { outline:2px solid var(--gama-accent,#c1afff); outline-offset:2px; }
.gama-toolbar span,.gama-editor-help { color:var(--gama-muted,#adb5c8); font-size:var(--gama-font-small,18px); line-height:1.5; }
textarea { display:block; width:100%; min-height:180px; max-height:1000px; padding:14px; border:0; outline:0; resize:vertical; color:var(--gama-text,#edf0f7); background:var(--gama-inset,#131826); font:inherit; font-size:var(--gama-font-base,22px); line-height:1.7; }
textarea:focus { box-shadow:inset 0 0 0 2px var(--gama-accent-line,#8b72df55); }
.gama-editor-help { margin:0; padding:8px 12px; border-top:1px solid var(--gama-line,#30384b); }
"""

_EDITOR_JS = """
const drafts = new Map();
export default function(component) {
    const {data,parentElement,setStateValue} = component;
    const input = parentElement.querySelector('textarea');
    const buttons = Array.from(parentElement.querySelectorAll('button[data-format]'));
    const key = String(data.draftKey);
    const received = typeof data.value === 'string' ? data.value : '';
    let draft = drafts.get(key);
    if (!draft) {
        draft = {value:received,lastServer:received,lastSent:received,start:0,end:0,focused:false};
    } else if (!draft.focused && draft.value === draft.lastServer) {
        draft.value = received;
    }
    draft.lastServer = received;
    drafts.delete(key);
    drafts.set(key,draft);
    while (drafts.size > 32) drafts.delete(drafts.keys().next().value);

    input.style.height = `${data.height}px`;
    if (input.value !== draft.value) input.value = draft.value;
    function remember() {
        draft.value = input.value;
        draft.start = input.selectionStart;
        draft.end = input.selectionEnd;
    }
    function publish() {
        remember();
        if (draft.lastSent !== draft.value) {
            draft.lastSent = draft.value;
            setStateValue('value',draft.value);
        }
    }
    function focus() { draft.focused = true; remember(); }
    function blur() { draft.focused = false; publish(); }
    function format(marker) {
        const start = input.selectionStart;
        const end = input.selectionEnd;
        const selected = input.value.slice(start,end);
        const content = selected || 'texto';
        input.setRangeText(marker + content + marker,start,end,'select');
        input.focus({preventScroll:true});
        input.setSelectionRange(start+marker.length,start+marker.length+content.length);
        draft.focused = true;
        publish();
    }
    function keydown(event) {
        if ((event.ctrlKey || event.metaKey) && !event.altKey) {
            const key = event.key.toLowerCase();
            if (key === 'b' || key === 'i') {
                event.preventDefault();
                format(key === 'b' ? '**' : '*');
            }
        }
    }
    function preserveSelection(event) { event.preventDefault(); }
    function toolbar(event) { format(event.currentTarget.dataset.format); }

    // Uma propriedade por evento impede o acúmulo de manipuladores.
    const handlers = {oninput:publish,onfocus:focus,onblur:blur,onkeydown:keydown,
        onkeyup:remember,onselect:remember,onmouseup:remember};
    Object.entries(handlers).forEach(([name,handler]) => { input[name] = handler; });
    buttons.forEach(button => {
        button.onmousedown = preserveSelection;
        button.onclick = toolbar;
    });
    if (draft.focused) requestAnimationFrame(() => {
        if (input.isConnected) {
            input.focus({preventScroll:true});
            input.setSelectionRange(draft.start,draft.end);
        }
    });
    return () => {
        Object.entries(handlers).forEach(([name,handler]) => {
            if (input[name] === handler) input[name] = null;
        });
        buttons.forEach(button => {
            if (button.onmousedown === preserveSelection) button.onmousedown = null;
            if (button.onclick === toolbar) button.onclick = null;
        });
    };
}
"""

_editor = st.components.v2.component(
    'gama_editor_formatado', html=_EDITOR_HTML, css=_EDITOR_CSS,
    js=_EDITOR_JS, isolate_styles=True,
)


# 3.2 Cada chave identifica um rascunho ou modelo independente
def editor_formatado(valor, key, altura=340):
    valor = valor if isinstance(valor, str) else ''
    anterior = _estado(key).get('value', valor)
    if not isinstance(anterior, str):
        anterior = valor
    altura = max(180, min(1000, int(altura)))
    resultado = _editor(
        key=key, data={'value': anterior, 'height': altura, 'draftKey': key},
        default={'value': anterior}, on_value_change=lambda: None,
        width='stretch', height='content',
    )
    texto = resultado.get('value', anterior)
    return texto if isinstance(texto, str) else anterior