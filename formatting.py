"""Negrito e itálico compartilhados pela prévia e pelos emails do GAMA."""

# =========== 1. IMPORTAÇÕES E SINTAXE ACEITA ===========
# 1.1 O HTML é produzido pelo aplicativo; tags digitadas permanecem texto
from dataclasses import dataclass, field
from email.message import EmailMessage
from html import escape
from urllib.parse import urlsplit


# 1.2 Marcadores: **negrito**, *itálico*, _itálico_ e ***ambos***
_TAGS = {
    '**': ('<strong>', '</strong>'),
    '*': ('<em>', '</em>'),
    '_': ('<em>', '</em>'),
    '***': ('<strong><em>', '</em></strong>'),
}
_ESCAPABLE = {'\\', '*', '_'}
_MAX_DEPTH = 32


@dataclass
class _Frame:
    marker: str = ''
    nodes: list = field(default_factory=list)


# =========== 2. INTERPRETAÇÃO DOS MARCADORES ===========
# 2.1 Espaços nas pontas e sublinhados dentro de palavras não iniciam estilos
def _can_open(text, position, marker):
    end = position + len(marker)
    if end >= len(text) or text[end].isspace():
        return False
    if marker == '_' and position and (
        text[position - 1].isalnum() or text[position - 1] == '_'
    ):
        return False
    return True


def _can_close(text, position, marker):
    if not position or text[position - 1].isspace():
        return False
    end = position + len(marker)
    if marker == '_' and end < len(text) and (
        text[end].isalnum() or text[end] == '_'
    ):
        return False
    return True


# 2.2 Uma pilha limita o trabalho a uma passagem e preserva marcadores incompletos
def _parse(text):
    text = '' if text is None else str(text)
    frames = [_Frame()]
    position = 0

    while position < len(text):
        char = text[position]

        if (
            char == '\\'
            and position + 1 < len(text)
            and text[position + 1] in _ESCAPABLE
        ):
            frames[-1].nodes.append(('text', text[position + 1]))
            position += 2
            continue

        if char not in {'*', '_'}:
            end = position + 1
            while end < len(text) and text[end] not in {'\\', '*', '_'}:
                end += 1
            frames[-1].nodes.append(('text', text[position:end]))
            position = end
            continue

        end = position + 1
        while end < len(text) and text[end] == char:
            end += 1

        run = end - position
        if (char == '_' and run > 1) or run > 3:
            frames[-1].nodes.append(('text', text[position:end]))
            position = end
            continue

        top = frames[-1].marker
        closes = (
            bool(top)
            and top[0] == char
            and run >= len(top)
            and not (top == '*' and run == 2)
            and _can_close(text, position, top)
        )

        if closes:
            completed = frames.pop()
            frames[-1].nodes.append((completed.marker, completed.nodes))
            position += len(completed.marker)
            continue

        marker = char * run
        if _can_open(text, position, marker) and len(frames) < _MAX_DEPTH:
            frames.append(_Frame(marker=marker))
        else:
            frames[-1].nodes.append(('text', marker))
        position = end

    # Um trecho não fechado continua literal, incluindo seu marcador inicial.
    while len(frames) > 1:
        incomplete = frames.pop()
        frames[-1].nodes.append(('text', incomplete.marker))
        frames[-1].nodes.extend(incomplete.nodes)

    return frames[0].nodes


# 2.3 Gerar HTML ou texto simples a partir dos mesmos trechos reconhecidos
def _render(nodes, html=False):
    parts = []

    for marker, content in nodes:
        if marker == 'text':
            parts.append(escape(content) if html else content)
        else:
            inner = _render(content, html=html)
            if html:
                opening, closing = _TAGS[marker]
                parts.append(opening + inner + closing)
            else:
                parts.append(inner)

    return ''.join(parts)


# =========== 3. FUNÇÕES COMPARTILHADAS PELA INTERFACE E PELO ENVIO ===========
# 3.1 Verificar somente estilos completos
def has_formatting(text):
    """Retorna True quando existe um trecho formatado reconhecido."""
    return any(marker != 'text' for marker, _ in _parse(text))


def body_plain(text):
    """Remove os marcadores reconhecidos e mantém conteúdo e quebras."""
    return _render(_parse(text))


def body_html(text):
    """Retorna HTML escapado, com negrito, itálico e quebras de linha."""
    result = _render(_parse(text), html=True)
    return (
        result.replace('\r\n', '\n')
        .replace('\r', '\n')
        .replace('\n', '<br>\n')
    )


# 3.2 Montar a mensagem HTML com um pixel opcional
def build_html(text, pixel_url=None):
    pixel = ''

    if pixel_url is not None:
        if not isinstance(pixel_url, str) or any(
            char.isspace() or ord(char) < 32 for char in pixel_url
        ):
            raise ValueError(
                'O endereço do pixel precisa ser uma URL HTTPS válida.'
            )

        try:
            parsed = urlsplit(pixel_url)
            valid = (
                parsed.scheme.lower() == 'https'
                and parsed.hostname
                and not parsed.username
                and not parsed.password
            )
        except ValueError:
            valid = False

        if not valid:
            raise ValueError(
                'O endereço do pixel precisa ser uma URL HTTPS válida.'
            )

        pixel = (
            f'<img src="{escape(pixel_url, quote=True)}" '
            'width="1" height="1" alt="" aria-hidden="true" '
            'style="display:block;width:1px;height:1px;border:0">'
        )

    return (
        '<!doctype html><html><head><meta charset="utf-8"></head><body>'
        '<div style="font-family:Arial,sans-serif;line-height:1.6">'
        + body_html(text)
        + '</div>'
        + pixel
        + '</body></html>'
    )


# =========== 4. CORPO MIME DO EMAIL ===========
# 4.1 Substituir o corpo preservando cabeçalhos e anexos
def set_message_body(message: EmailMessage, text, pixel_url=None):
    """Define as alternativas de texto simples e HTML, mantendo anexos."""
    html = build_html(text, pixel_url=pixel_url)
    plain = body_plain(text)
    target = message

    while target.get_content_type() in {
        'multipart/mixed',
        'multipart/related',
    }:
        parts = list(target.iter_parts())
        body = next(
            (
                part
                for part in parts
                if part.get_content_disposition() != 'attachment'
            ),
            None,
        )

        if body is None:
            body = EmailMessage(policy=message.policy)
            target.set_payload([body, *parts])

        target = body

    target.clear_content()
    target.set_content(plain, charset='utf-8')
    target.add_alternative(html, subtype='html', charset='utf-8')