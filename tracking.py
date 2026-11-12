"""Pixel de abertura do GAMA e serviço HTTP independente da interface."""

# =========== 1. IMPORTAÇÕES E CONSTANTES ===========
# 1.1 Bibliotecas do serviço
import argparse
import ipaddress
import os
import re
import secrets
import sqlite3
from contextlib import closing
from email.message import EmailMessage
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, urlunsplit

import storage


# 1.2 GIF transparente e tokens sem dados pessoais na URL
PIXEL = (
    b'GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff'
    b'!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01'
    b'\x00\x00\x02\x02D\x01\x00;'
)
TOKEN_PATTERN = re.compile(r'[A-Za-z0-9_-]{43}')
PIXEL_PATH = re.compile(r'/open/([A-Za-z0-9_-]{43})\.gif')


# =========== 2. ENDEREÇO PÚBLICO DO RASTREAMENTO ===========
# 2.1 Validar HTTPS e endereço público
def validate_public_url(value):
    value = str(value or '').strip()
    if not value or any(ord(char) < 32 or char.isspace() for char in value):
        raise ValueError('Informe um endereço HTTPS público válido para o rastreamento.')
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        raise ValueError('O endereço público de rastreamento é inválido.') from None
    if (
        parsed.scheme.lower() != 'https' or not host
        or parsed.username is not None or parsed.password is not None
        or '?' in value or '#' in value or '\\' in value
    ):
        raise ValueError('Use uma URL HTTPS sem credenciais, parâmetros ou fragmentos.')

    host = host.rstrip('.').lower()
    if host == 'localhost' or host.endswith((
        '.localhost', '.local', '.internal', '.invalid', '.test',
    )):
        raise ValueError('O rastreamento exige um endereço público, acessível fora da rede local.')

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        try:
            host = host.encode('idna').decode('ascii')
        except UnicodeError:
            raise ValueError('O domínio de rastreamento é inválido.') from None
        labels = host.split('.')
        if (
            len(labels) < 2 or len(host) > 253
            or any(
                not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label)
                for label in labels
            )
            or all(
                re.fullmatch(r'(?:[0-9]+|0x[0-9a-f]+)', label)
                for label in labels
            )
        ):
            raise ValueError('Use um domínio público completo para o rastreamento.')
        authority = host
    else:
        if not address.is_global:
            raise ValueError('Endereços privados e locais não podem receber o pixel público.')
        authority = (
            f'[{address.compressed}]'
            if address.version == 6 else str(address)
        )

    if port is not None and port != 443:
        authority += f':{port}'
    path = parsed.path.rstrip('/')
    if any(part in {'.', '..'} for part in path.split('/')):
        raise ValueError('O caminho do endereço de rastreamento é inválido.')
    return urlunsplit(('https', authority, path, '', ''))


# 2.2 A variável de ambiente tem prioridade
def base_url(db):
    value = (
        os.getenv('GAMA_TRACKING_URL', '').strip()
        or storage.get_setting(db, 'tracking_url')
    )
    return validate_public_url(value) if value else ''


# =========== 3. PREPARAÇÃO DAS MENSAGENS ===========
# 3.1 Gerar um token por tentativa de envio
def create_token(db, campaign_id, contact_id, email):
    token = secrets.token_urlsafe(32)
    db.execute(
        '''INSERT INTO tracking_messages(token,campaign_id,contact_id,email,created)
           VALUES(?,?,?,?,?)''',
        (token, campaign_id, contact_id, email, storage.now()),
    )
    db.commit()
    return token


# 3.2 Preservar a formatação e acrescentar o pixel
def attach_pixel(message: EmailMessage, body: str, url: str, token: str):
    public_url = validate_public_url(url)
    if not TOKEN_PATTERN.fullmatch(token):
        raise ValueError('Token de rastreamento inválido.')
    from formatting import set_message_body

    set_message_body(
        message, body,
        pixel_url=f'{public_url}/open/{token}.gif',
    )


# 3.3 Somente mensagens aceitas entram nas taxas
def mark_accepted(db, token, accepted=True):
    if not token:
        return False
    result = db.execute(
        'UPDATE tracking_messages SET accepted=? WHERE token=?',
        (int(accepted), token),
    )
    db.commit()
    return result.rowcount == 1


# =========== 4. REGISTRO E AGREGAÇÃO DAS ABERTURAS ===========
# 4.1 Registrar primeira e última abertura
def record_open(db, token):
    instant = storage.now()
    result = db.execute('''UPDATE tracking_messages SET
        first_opened=COALESCE(first_opened,?), last_opened=?, open_count=open_count+1
        WHERE token=?''', (instant, instant, token))
    db.commit()
    return result.rowcount == 1


# 4.2 Calcular a taxa das mensagens rastreadas aceitas pelo SMTP
def summary(db, campaign_id=None):
    where = 'WHERE accepted=1'
    parameters = ()
    if campaign_id is not None:
        where += ' AND campaign_id=?'
        parameters = (campaign_id,)
    result = db.execute(f'''SELECT COUNT(*) AS tracked,
        COALESCE(SUM(CASE WHEN open_count>0 THEN 1 ELSE 0 END),0) AS opened,
        COALESCE(SUM(open_count),0) AS opens,
        COUNT(DISTINCT CASE WHEN open_count>0 THEN LOWER(email) END) AS contacts_opened
        FROM tracking_messages {where}''', parameters).fetchone()
    stats = dict(result)
    stats['never_opened'] = stats['tracked'] - stats['opened']
    stats['open_rate'] = (
        stats['opened'] / stats['tracked'] * 100
        if stats['tracked'] else None
    )
    return stats


# =========== 5. SERVIDOR HTTP DO PIXEL ===========
# 5.1 Publicar atrás de um proxy HTTPS
class TrackingHandler(BaseHTTPRequestHandler):
    server_version = 'GAMA'
    sys_version = ''

    def log_message(self, format, *args):
        pass

    def _send(self, status, content_type, body, head=False):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header(
            'Cache-Control',
            'no-store, no-cache, must-revalidate, max-age=0',
        )
        self.send_header('Pragma', 'no-cache')
        self.send_header('Expires', '0')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        if not head:
            self.wfile.write(body)

    # 5.2 HEAD verifica o recurso sem registrar abertura
    def do_HEAD(self):
        self._handle(head=True)

    def do_GET(self):
        self._handle(head=False)

    def _handle(self, head):
        try:
            path = urlsplit(self.path).path
        except ValueError:
            self._send(
                404, 'text/plain; charset=utf-8',
                b'Recurso indisponivel.', head,
            )
            return

        token_match = PIXEL_PATH.fullmatch(path)
        if path == '/healthz':
            try:
                with closing(storage.connect()) as db:
                    db.execute(
                        'SELECT 1 FROM tracking_messages LIMIT 1'
                    ).fetchone()
            except (sqlite3.Error, OSError):
                self._send(
                    503, 'application/json',
                    b'{"status":"unavailable"}', head,
                )
                return
            self._send(200, 'application/json', b'{"status":"ok"}', head)
        elif token_match:
            try:
                if not head:
                    with closing(storage.connect()) as db:
                        record_open(db, token_match.group(1))
            except (sqlite3.Error, OSError):
                self._send(503, 'image/gif', PIXEL, head)
                return
            self._send(200, 'image/gif', PIXEL, head)
        else:
            self._send(
                404, 'text/plain; charset=utf-8',
                b'Recurso indisponivel.', head,
            )


# 5.3 Evitar dados de visitantes nos logs de erros
class TrackingServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        pass


# 5.4 Construtor do servidor
def make_server(host='127.0.0.1', port=8787):
    return TrackingServer((host, port), TrackingHandler)


# =========== 6. EXECUÇÃO INDEPENDENTE DA INTERFACE ===========
# 6.1 Carregar a mesma configuração e o mesmo banco
def main():
    from pathlib import Path
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).with_name('.env'))
    parser = argparse.ArgumentParser(
        description='Servidor do pixel de abertura GAMA',
    )
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', default=8787, type=int)
    args = parser.parse_args()
    with make_server(args.host, args.port) as server:
        print('GAMA: serviço de rastreamento iniciado. Configure o proxy HTTPS para publicar /open/.')
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == '__main__':
    main()