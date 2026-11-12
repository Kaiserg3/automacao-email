"""Armazenamento local, importação e personalização de mensagens."""

# =========== 1. IMPORTAÇÕES E CONEXÃO COM O BANCO ===========
# 1.1 Bibliotecas padrão; dependências de arquivos são carregadas quando usadas
import base64
import binascii
import csv
import io
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile


# 1.2 Estrutura local e atualização aditiva do banco existente
def connect():
    path = Path(os.getenv('AUTOMACAO_DATA_DIR', str(Path(__file__).parent / 'data')))
    path.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path / 'email.sqlite3', timeout=15)
    db.row_factory = sqlite3.Row
    legacy_imports_existed = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='legacy_delivery_imports'"
    ).fetchone() is not None
    db.executescript('''
    CREATE TABLE IF NOT EXISTS contacts(id INTEGER PRIMARY KEY, email TEXT UNIQUE COLLATE NOCASE, name TEXT, company TEXT, phone TEXT, list_name TEXT, extra TEXT, unsubscribed INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS templates(id INTEGER PRIMARY KEY, name TEXT UNIQUE, subject TEXT, body TEXT, attachment_name TEXT, attachment BLOB);
    CREATE TABLE IF NOT EXISTS campaigns(id INTEGER PRIMARY KEY, name TEXT, template_id INTEGER, list_name TEXT, interval_seconds INTEGER, batch_size INTEGER, batch_pause INTEGER, ordering TEXT, status TEXT DEFAULT 'Rascunho', created TEXT);
    CREATE TABLE IF NOT EXISTS deliveries(id INTEGER PRIMARY KEY, campaign_id INTEGER, contact_id INTEGER, email TEXT, status TEXT, detail TEXT, created TEXT, UNIQUE(campaign_id, contact_id));
    CREATE TABLE IF NOT EXISTS legacy_deliveries(email TEXT UNIQUE COLLATE NOCASE);
    CREATE TABLE IF NOT EXISTS legacy_delivery_imports(email TEXT PRIMARY KEY COLLATE NOCASE);
    CREATE TABLE IF NOT EXISTS legacy_template_imports(name TEXT PRIMARY KEY);
    CREATE TABLE IF NOT EXISTS campaign_contacts(
        campaign_id INTEGER, contact_id INTEGER,
        PRIMARY KEY(campaign_id, contact_id)
    );
    CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
    CREATE TABLE IF NOT EXISTS tracking_messages(
        token TEXT PRIMARY KEY,
        campaign_id INTEGER,
        contact_id INTEGER,
        email TEXT,
        created TEXT,
        accepted INTEGER DEFAULT 0,
        first_opened TEXT,
        last_opened TEXT,
        open_count INTEGER DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS tracking_campaign_idx ON tracking_messages(campaign_id);
    ''')

    # A migração preserva dados e permite iniciar a interface e o pixel juntos.
    for table, column, definition in (
        ('campaigns', 'members_custom', 'INTEGER DEFAULT 0'),
        ('deliveries', 'tracking_token', 'TEXT'),
    ):
        columns = {row['name'] for row in db.execute(f'PRAGMA table_info({table})')}
        if column not in columns:
            try:
                db.execute(f'ALTER TABLE {table} ADD COLUMN {column} {definition}')
                db.commit()
            except sqlite3.OperationalError:
                columns = {row['name'] for row in db.execute(f'PRAGMA table_info({table})')}
                if column not in columns:
                    raise

    # Registrar a origem impede que um histórico apagado seja importado novamente.
    if not legacy_imports_existed:
        with db:
            db.execute('''INSERT OR IGNORE INTO legacy_delivery_imports(email)
                SELECT email FROM legacy_deliveries''')
    return db


# 1.3 Datas em UTC para o histórico persistido
def now():
    return datetime.now(timezone.utc).isoformat()


# =========== 2. CADASTRO E PERSONALIZAÇÃO DE CONTATOS ===========
# 2.1 Validação sintática sem consulta DNS e gravação de campos adicionais
def save_contact(db, email, name='', company='', phone='', list_name='Principal', extra=None, unsubscribed=False, contact_id=None):
    from email_validator import validate_email

    email = validate_email(email.strip(), check_deliverability=False).normalized
    values = (
        email, name, company, phone, list_name.strip() or 'Principal',
        json.dumps(extra or {}, ensure_ascii=False), int(unsubscribed),
    )
    if contact_id:
        db.execute(
            'UPDATE contacts SET email=?,name=?,company=?,phone=?,list_name=?,extra=?,unsubscribed=? WHERE id=?',
            (*values, contact_id),
        )
    else:
        # 2.1.1 O histórico reserva IDs de contatos que já foram excluídos.
        db.execute('''INSERT INTO contacts(id,email,name,company,phone,list_name,extra,unsubscribed)
            SELECT COALESCE(MAX(value),0)+1,?,?,?,?,?,?,? FROM (
                SELECT id AS value FROM contacts
                UNION ALL SELECT contact_id AS value FROM deliveries
                UNION ALL SELECT contact_id AS value FROM tracking_messages
                UNION ALL SELECT contact_id AS value FROM campaign_contacts
            )''', values)
    db.commit()


# 2.2 Compatibilidade com {nome}, {Nome}, {NOME} e campos da planilha
def fields(contact):
    extra = json.loads(contact['extra'] or '{}')
    values = {str(key).strip().lower(): value for key, value in extra.items()}
    values.update({
        'nome': contact['name'],
        'email': contact['email'],
        'empresa': contact['company'],
        'telefone': contact['phone'],
    })
    aliases = {}
    for key, value in values.items():
        value = '' if value is None else value
        for alias in (key.lower(), key.capitalize(), key.upper()):
            aliases[alias] = value
    return aliases


# 2.3 A mesma personalização é usada na prévia e na preparação do envio
def render(template, contact):
    values = fields(contact)
    return template['subject'].format(**values), template['body'].format(**values)


# =========== 3. COMPATIBILIDADE COM ARQUIVOS ANTERIORES ===========
# 3.1 Importação repetível sem sobrescrever modelos nem modificar os originais
def import_legacy(db, legacy_dir):
    directory = Path(legacy_dir)
    warnings = []
    templates = {}
    sent = []

    # 3.2 Leitura dos modelos antigos
    try:
        templates = json.loads((directory / 'templates.json').read_text(encoding='utf-8-sig'))
        if not isinstance(templates, dict):
            templates = {}
            warnings.append('templates.json precisa conter um objeto de modelos. O original foi preservado.')
        elif any(not isinstance(name, str) or not isinstance(body, str) for name, body in templates.items()):
            templates = {
                name: body for name, body in templates.items()
                if isinstance(name, str) and isinstance(body, str)
            }
            warnings.append('Alguns modelos antigos têm formato incompatível e não foram importados.')
    except FileNotFoundError:
        pass
    except (json.JSONDecodeError, UnicodeDecodeError):
        warnings.append('templates.json não pôde ser interpretado. O arquivo original foi preservado.')
    except OSError:
        warnings.append('Não foi possível ler templates.json. O arquivo original foi preservado.')

    # 3.3 Histórico antigo
    try:
        sent = [
            line.strip()
            for line in (directory / 'enviados.txt').read_text(encoding='utf-8-sig').splitlines()
            if line.strip()
        ]
    except FileNotFoundError:
        pass
    except (OSError, UnicodeDecodeError):
        warnings.append('Não foi possível ler enviados.txt. O arquivo original foi preservado.')

    # 3.4 Uma transação e restrições UNIQUE tornam a repetição segura
    with db:
        for name, body in templates.items():
            if db.execute('SELECT 1 FROM legacy_template_imports WHERE name=?', (name,)).fetchone():
                continue
            db.execute(
                'INSERT OR IGNORE INTO templates(name,subject,body) VALUES(?,?,?)',
                (name, 'Mensagem para sua equipe', body),
            )
            db.execute('INSERT INTO legacy_template_imports(name) VALUES(?)', (name,))
        db.executemany('''INSERT OR IGNORE INTO legacy_deliveries(email)
            SELECT ? WHERE NOT EXISTS(
                SELECT 1 FROM legacy_delivery_imports WHERE email=?
            )''', [(email, email) for email in sent])
        db.executemany(
            'INSERT OR IGNORE INTO legacy_delivery_imports(email) VALUES(?)',
            [(email,) for email in sent],
        )
    return warnings


# =========== 4. LEITURA DE PLANILHAS E LISTAS DE CONTATOS ===========
# 4.1 Cabeçalhos consistentes e dados normalizados antes da gravação
def read_contact_rows(filename, data):
    extension = Path(filename).suffix.lower()
    workbook = None
    try:
        # 4.2 CSV UTF-8, com BOM opcional e separadores comuns
        if extension == '.csv':
            try:
                text = data.decode('utf-8-sig')
            except UnicodeDecodeError:
                raise ValueError('Salve o CSV com codificação UTF-8 e tente novamente.') from None
            try:
                dialect = csv.Sniffer().sniff(text[:4096], delimiters=',;\t')
            except csv.Error:
                dialect = csv.excel
            source = iter(csv.reader(io.StringIO(text, newline=''), dialect=dialect))

        # 4.3 XLSX sem executar fórmulas
        elif extension == '.xlsx':
            import openpyxl

            try:
                workbook = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
                source = iter(workbook.active.values)
            except Exception:
                raise ValueError('Não foi possível ler o XLSX. Verifique se o arquivo é uma planilha válida.') from None
        else:
            raise ValueError('Use um arquivo .csv ou .xlsx.')

        # 4.4 Validação dos nomes de colunas
        try:
            first_row = next(source)
        except StopIteration:
            raise ValueError('O arquivo está vazio. Inclua os cabeçalhos e os contatos.') from None
        headers = [
            str(value).strip().lower() if value is not None else ''
            for value in first_row
        ]
        if any(not header for header in headers):
            raise ValueError('Todas as colunas precisam de um cabeçalho preenchido.')
        if len(set(headers)) != len(headers):
            raise ValueError('Os cabeçalhos devem ser únicos, mesmo após remover espaços e normalizar maiúsculas.')
        if 'email' not in headers:
            raise ValueError('Inclua a coluna obrigatória email no cabeçalho.')

        # 4.5 Linhas vazias são ignoradas
        rows = []
        for number, row in enumerate(source, start=2):
            values = [str(value).strip() if value is not None else '' for value in row]
            if not any(values):
                continue
            if len(values) > len(headers):
                raise ValueError(f'A linha {number} possui mais colunas do que o cabeçalho.')
            values.extend([''] * (len(headers) - len(values)))
            rows.append(dict(zip(headers, values)))
        return rows
    except (csv.Error, ParseError, BadZipFile, EOFError):
        raise ValueError('Não foi possível ler o arquivo completo. Verifique se o CSV ou XLSX está íntegro e tente novamente.') from None
    finally:
        if workbook is not None:
            workbook.close()


# =========== 5. PREPARAÇÃO DE ANEXOS ===========
# 5.1 O arquivo original é mantido, salvo conversão solicitada
def attachment_data(filename, data, convert_pdf=False):
    if Path(filename).suffix.lower() != '.pdf' or not convert_pdf:
        return filename, bytes(data)

    # 5.2 Conversão da primeira página do PDF para PNG
    import pymupdf as fitz

    try:
        with fitz.open(stream=data, filetype='pdf') as document:
            if document.needs_pass:
                raise ValueError('O PDF está protegido por senha. Escolha outro arquivo ou mantenha o original.')
            if not document.page_count:
                raise ValueError('O PDF não contém páginas para converter.')
            image = document.load_page(0).get_pixmap(dpi=150).tobytes('png')
    except ValueError:
        raise
    except Exception:
        raise ValueError('Não foi possível converter a primeira página do PDF. Mantenha o original ou escolha outro arquivo.') from None
    return Path(filename).stem + '.png', image


# =========== 6. ASSOCIAÇÃO DE CONTATOS A CAMPANHAS ===========
# 6.1 Uma campanha pode usar sua lista original ou uma seleção independente
def contacts_in_campaign(db, campaign_id):
    campaign = db.execute(
        'SELECT list_name,members_custom FROM campaigns WHERE id=?',
        (campaign_id,),
    ).fetchone()
    if campaign is None:
        raise ValueError('Campanha não encontrada.')
    if campaign['members_custom']:
        rows = db.execute(
            """SELECT c.* FROM contacts c
            INNER JOIN campaign_contacts cc ON cc.contact_id=c.id
            WHERE cc.campaign_id=? ORDER BY c.name,c.email""",
            (campaign_id,),
        )
    else:
        rows = db.execute(
            'SELECT * FROM contacts WHERE list_name=? ORDER BY name,email',
            (campaign['list_name'],),
        )
    return [dict(row) for row in rows]


# 6.2 Alias para consultar os destinatários de uma campanha
def campaign_contacts(db, campaign_id):
    return contacts_in_campaign(db, campaign_id)


# 6.3 Salvar uma seleção explícita, inclusive uma campanha sem contatos
def set_campaign_contacts(db, campaign_id, ids):
    contact_ids = _bulk_contact_ids(ids)
    if db.execute('SELECT 1 FROM campaigns WHERE id=?', (campaign_id,)).fetchone() is None:
        raise ValueError('Campanha não encontrada.')
    _require_contacts(db, contact_ids)
    with db:
        db.execute('DELETE FROM campaign_contacts WHERE campaign_id=?', (campaign_id,))
        db.executemany(
            'INSERT INTO campaign_contacts(campaign_id,contact_id) VALUES(?,?)',
            [(campaign_id, contact_id) for contact_id in contact_ids],
        )
        db.execute('UPDATE campaigns SET members_custom=1 WHERE id=?', (campaign_id,))


# 6.4 Excluir cadastro e vínculos preservando o histórico
def delete_contact(db, contact_id):
    delete_contacts(db, [contact_id])


# =========== 7. CONFIGURAÇÕES PERSISTIDAS ===========
# 7.1 Valores de configuração não secretos
def get_setting(db, key, default=''):
    row = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
    return row['value'] if row is not None else default


def set_setting(db, key, value):
    if not isinstance(key, str) or not key.strip() or not isinstance(value, str):
        raise ValueError('Configuração inválida.')
    with db:
        db.execute(
            """INSERT INTO settings(key,value) VALUES(?,?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
            (key, value),
        )


# =========== 8. TEMPLATES E ANEXOS DE CAMPANHA ===========
# 8.1 Validar o conteúdo de um template
def _template_values(name, subject, body, attachment_name, attachment):
    if not all(isinstance(value, str) and value.strip() for value in (name, subject, body)):
        raise ValueError('Preencha o nome, o assunto e o texto do template.')
    if '\r' in subject or '\n' in subject:
        raise ValueError('O assunto não pode conter quebras de linha.')
    if attachment is None:
        if attachment_name is not None:
            raise ValueError('O nome do anexo precisa acompanhar os dados do arquivo.')
    else:
        if not isinstance(attachment, (bytes, bytearray, memoryview)):
            raise ValueError('Os dados do anexo precisam ser binários.')
        if not isinstance(attachment_name, str) or not attachment_name.strip() or '\x00' in attachment_name:
            raise ValueError('Informe um nome válido para o anexo.')
        attachment = bytes(attachment)
    return name.strip(), subject, body, attachment_name, attachment


# 8.2 Criar ou editar sem sobrescrever outro template com o mesmo nome
def save_template(db, name, subject, body, attachment_name=None, attachment=None, template_id=None):
    values = _template_values(name, subject, body, attachment_name, attachment)
    try:
        with db:
            if template_id is None:
                result = db.execute(
                    'INSERT INTO templates(name,subject,body,attachment_name,attachment) VALUES(?,?,?,?,?)',
                    values,
                )
                return result.lastrowid
            result = db.execute(
                'UPDATE templates SET name=?,subject=?,body=?,attachment_name=?,attachment=? WHERE id=?',
                (*values, template_id),
            )
            if not result.rowcount:
                raise ValueError('Template não encontrado.')
            return template_id
    except sqlite3.IntegrityError:
        raise ValueError('Já existe um template com esse nome. Selecione-o para editar.') from None


# 8.3 Proteger templates associados a campanhas
def delete_template(db, template_id):
    with db:
        if db.execute('SELECT 1 FROM campaigns WHERE template_id=? LIMIT 1', (template_id,)).fetchone():
            raise ValueError('Este template está associado a uma campanha. Troque o template da campanha antes de excluir.')
        result = db.execute('DELETE FROM templates WHERE id=?', (template_id,))
        if not result.rowcount:
            raise ValueError('Template não encontrado.')


# 8.4 Exportar texto e anexo em JSON
def export_template(template):
    name, subject, body, filename, content = _template_values(
        template['name'], template['subject'], template['body'],
        template.get('attachment_name'), template.get('attachment'),
    )
    payload = {
        'version': 1,
        'name': name,
        'subject': subject,
        'body': body,
        'attachment_name': filename,
        'attachment_base64': base64.b64encode(content).decode('ascii') if content is not None else None,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2).encode('utf-8')


# 8.5 Importar o formato exportado pelo GAMA
def import_template(db, data):
    try:
        payload = json.loads(data.decode('utf-8-sig'))
    except (AttributeError, UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError('Não foi possível ler o template. Use um arquivo JSON exportado pelo GAMA.') from None
    if not isinstance(payload, dict) or type(payload.get('version')) is not int or payload['version'] != 1:
        raise ValueError('Formato ou versão do template não suportado.')
    try:
        name, subject, body = (payload[key] for key in ('name', 'subject', 'body'))
    except KeyError:
        raise ValueError('O arquivo precisa conter nome, assunto e texto do template.') from None
    encoded = payload.get('attachment_base64')
    if encoded is None:
        content = None
    elif isinstance(encoded, str):
        try:
            content = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            raise ValueError('O anexo do template está corrompido.') from None
    else:
        raise ValueError('O formato do anexo não é válido.')
    return save_template(db, name, subject, body, payload.get('attachment_name'), content)


# =========== 9. REMOÇÃO SELETIVA DO HISTÓRICO ===========
# 9.1 Validar a seleção inteira antes de modificar qualquer registro
def clear_delivery_history(db, delivery_ids=(), legacy_emails=()):
    if isinstance(delivery_ids, (str, bytes, dict)) or isinstance(legacy_emails, (str, bytes, dict)):
        raise ValueError('Selecione os registros do histórico que deseja apagar.')
    try:
        delivery_ids = tuple(delivery_ids)
        legacy_emails = tuple(legacy_emails)
    except TypeError:
        raise ValueError('Seleção de histórico inválida.') from None
    if any(type(value) is not int or value <= 0 for value in delivery_ids):
        raise ValueError('Os registros do histórico precisam ter identificadores válidos.')
    if any(not isinstance(email, str) or not email.strip() or '\x00' in email for email in legacy_emails):
        raise ValueError('Os emails do histórico antigo precisam ser válidos.')
    delivery_ids = tuple(dict.fromkeys(delivery_ids))
    legacy_emails = tuple({email.strip().casefold(): email.strip() for email in legacy_emails}.values())
    counts = {'deliveries': 0, 'tracking': 0, 'legacy': 0}
    if not delivery_ids and not legacy_emails:
        return counts

    # 9.2 Savepoint também funciona dentro de uma transação do chamador
    db.execute('SAVEPOINT gama_history_selection')
    try:
        deliveries = []
        for delivery_id in delivery_ids:
            row = db.execute('SELECT * FROM deliveries WHERE id=?', (delivery_id,)).fetchone()
            if row is None:
                raise ValueError('Um registro do histórico não está mais disponível. Atualize a seleção.')
            deliveries.append(row)

        legacy = []
        for email in legacy_emails:
            row = db.execute(
                'SELECT email FROM legacy_deliveries WHERE email=? COLLATE NOCASE',
                (email,),
            ).fetchone()
            if row is None:
                raise ValueError('Um email do histórico antigo não está mais disponível. Atualize a seleção.')
            legacy.append(row['email'])

        # 9.3 Proteger campanhas em processamento
        for delivery in deliveries:
            campaign = db.execute(
                'SELECT status FROM campaigns WHERE id=?',
                (delivery['campaign_id'],),
            ).fetchone()
            if campaign is not None and campaign['status'] == 'Em envio':
                raise ValueError('Aguarde o envio da campanha terminar antes de apagar seu histórico.')

        for email in legacy:
            active = db.execute('''SELECT 1 FROM campaigns c WHERE c.status='Em envio' AND (
                EXISTS(SELECT 1 FROM deliveries d
                    WHERE d.campaign_id=c.id AND d.email=? COLLATE NOCASE)
                OR EXISTS(SELECT 1 FROM contacts ct WHERE ct.email=? COLLATE NOCASE AND (
                    (COALESCE(c.members_custom,0)=0 AND ct.list_name=c.list_name)
                    OR (c.members_custom=1 AND EXISTS(SELECT 1 FROM campaign_contacts cc
                        WHERE cc.campaign_id=c.id AND cc.contact_id=ct.id))
                ))
            ) LIMIT 1''', (email, email)).fetchone()
            if active is not None:
                raise ValueError('Aguarde o envio para este contato terminar antes de apagar seu histórico.')

        # 9.4 Remover entregas e tentativas de pixel relacionadas
        for delivery in deliveries:
            where = 'campaign_id IS ? AND contact_id IS ?'
            parameters = [delivery['campaign_id'], delivery['contact_id']]
            if delivery['contact_id'] is None:
                where += ' AND email=? COLLATE NOCASE'
                parameters.append(delivery['email'])
            if delivery['tracking_token'] is not None:
                where = '(' + where + ') OR token=?'
                parameters.append(delivery['tracking_token'])
            result = db.execute('DELETE FROM tracking_messages WHERE ' + where, parameters)
            counts['tracking'] += result.rowcount
            result = db.execute('DELETE FROM deliveries WHERE id=?', (delivery['id'],))
            counts['deliveries'] += result.rowcount

        # 9.5 Impedir a restauração automática pelo arquivo antigo
        for email in legacy:
            db.execute(
                'INSERT OR IGNORE INTO legacy_delivery_imports(email) VALUES(?)',
                (email,),
            )
            result = db.execute(
                'DELETE FROM legacy_deliveries WHERE email=? COLLATE NOCASE',
                (email,),
            )
            counts['legacy'] += result.rowcount
    except Exception:
        db.execute('ROLLBACK TO SAVEPOINT gama_history_selection')
        db.execute('RELEASE SAVEPOINT gama_history_selection')
        raise
    db.execute('RELEASE SAVEPOINT gama_history_selection')
    return counts


# =========== 10. CONTATOS EM LOTE E EXCLUSÃO DE CAMPANHAS ===========
# 10.1 Validar identificadores antes de uma ação em lote
def _bulk_contact_ids(contact_ids):
    if isinstance(contact_ids, (str, bytes, dict)):
        raise ValueError('Selecione contatos com identificadores válidos.')
    try:
        ids = tuple(contact_ids)
    except TypeError:
        raise ValueError('Seleção de contatos inválida.') from None
    if any(type(value) is not int or value <= 0 for value in ids):
        raise ValueError('Os contatos selecionados precisam ter identificadores válidos.')
    return tuple(dict.fromkeys(ids))


def _bulk_campaign_id(campaign_id):
    if type(campaign_id) is not int or campaign_id <= 0:
        raise ValueError('Selecione uma campanha válida.')
    return campaign_id


# 10.2 Preservar transações iniciadas pelo chamador
@contextmanager
def _bulk_transaction(db):
    db.execute('SAVEPOINT gama_bulk_operation')
    try:
        yield
    except Exception:
        db.execute('ROLLBACK TO SAVEPOINT gama_bulk_operation')
        db.execute('RELEASE SAVEPOINT gama_bulk_operation')
        raise
    db.execute('RELEASE SAVEPOINT gama_bulk_operation')


def _contact_chunks(ids):
    for inicio in range(0, len(ids), 500):
        yield ids[inicio:inicio + 500]


def _require_contacts(db, ids):
    existentes = set()
    for lote in _contact_chunks(ids):
        marcadores = ','.join('?' for _ in lote)
        existentes.update(row[0] for row in db.execute(
            f'SELECT id FROM contacts WHERE id IN ({marcadores})',
            lote,
        ))
    if existentes != set(ids):
        raise ValueError('Um ou mais contatos não existem mais. Atualize a seleção.')


# 10.3 Impedir alterações nos destinatários de um envio em andamento
def _contacts_in_active_campaign(db, ids=None):
    consulta = '''SELECT 1 FROM contacts ct WHERE EXISTS(
        SELECT 1 FROM campaigns c WHERE c.status='Em envio' AND (
            (COALESCE(c.members_custom,0)=0 AND c.list_name=ct.list_name)
            OR (c.members_custom=1 AND EXISTS(
                SELECT 1 FROM campaign_contacts cc
                WHERE cc.campaign_id=c.id AND cc.contact_id=ct.id
            ))
        )
    )'''
    if ids is None:
        return db.execute(consulta + ' LIMIT 1').fetchone() is not None
    for lote in _contact_chunks(ids):
        marcadores = ','.join('?' for _ in lote)
        if db.execute(
            consulta + f' AND ct.id IN ({marcadores}) LIMIT 1',
            lote,
        ).fetchone():
            return True
    return False


# 10.4 Excluir selecionados ou toda a base preservando o histórico
def delete_contacts(db, contact_ids=(), all_contacts=False):
    ids = _bulk_contact_ids(contact_ids)
    if type(all_contacts) is not bool:
        raise ValueError('A opção de excluir todos os contatos precisa ser confirmada.')
    if all_contacts and ids:
        raise ValueError('Escolha excluir a seleção ou todos os contatos, não ambos.')
    if not all_contacts and not ids:
        return 0

    with _bulk_transaction(db):
        if not all_contacts:
            _require_contacts(db, ids)
        if _contacts_in_active_campaign(db, None if all_contacts else ids):
            raise ValueError('Aguarde o envio das campanhas terminar antes de excluir estes contatos.')

        if all_contacts:
            db.execute('DELETE FROM campaign_contacts')
            return db.execute('DELETE FROM contacts').rowcount

        removidos = 0
        for lote in _contact_chunks(ids):
            marcadores = ','.join('?' for _ in lote)
            db.execute(
                f'DELETE FROM campaign_contacts WHERE contact_id IN ({marcadores})',
                lote,
            )
            removidos += db.execute(
                f'DELETE FROM contacts WHERE id IN ({marcadores})',
                lote,
            ).rowcount
        return removidos


# 10.5 Adicionar, remover ou substituir participantes de uma campanha
def update_campaign_members(db, campaign_id, contact_ids, action='add'):
    campaign_id = _bulk_campaign_id(campaign_id)
    ids = _bulk_contact_ids(contact_ids)
    if not isinstance(action, str) or action not in {'add', 'remove', 'replace'}:
        raise ValueError('Escolha adicionar, remover ou substituir os participantes.')

    with _bulk_transaction(db):
        campanha = db.execute(
            'SELECT list_name,members_custom,status FROM campaigns WHERE id=?',
            (campaign_id,),
        ).fetchone()
        if campanha is None:
            raise ValueError('Campanha não encontrada.')
        if campanha['status'] == 'Em envio':
            raise ValueError('Aguarde o envio terminar antes de alterar os participantes.')

        _require_contacts(db, ids)
        if campanha['members_custom']:
            atuais = {
                row[0] for row in db.execute('''
                    SELECT c.id FROM contacts c
                    INNER JOIN campaign_contacts cc ON cc.contact_id=c.id
                    WHERE cc.campaign_id=?
                ''', (campaign_id,))
            }
        else:
            atuais = {
                row[0] for row in db.execute(
                    'SELECT id FROM contacts WHERE list_name=?',
                    (campanha['list_name'],),
                )
            }

        if not ids and action != 'replace':
            return len(atuais)

        escolhidos = set(ids)
        membros = (atuais | escolhidos) if action == 'add' else (
            atuais - escolhidos if action == 'remove' else escolhidos
        )
        db.execute('DELETE FROM campaign_contacts WHERE campaign_id=?', (campaign_id,))
        db.executemany(
            'INSERT INTO campaign_contacts(campaign_id,contact_id) VALUES(?,?)',
            [(campaign_id, contact_id) for contact_id in sorted(membros)],
        )
        db.execute('UPDATE campaigns SET members_custom=1 WHERE id=?', (campaign_id,))
        return len(membros)


# 10.6 Excluir campanha preservando contatos e modelos
def delete_campaign(db, campaign_id, remove_history=False):
    campaign_id = _bulk_campaign_id(campaign_id)
    if type(remove_history) is not bool:
        raise ValueError('Confirme se deseja apagar também o histórico da campanha.')

    with _bulk_transaction(db):
        campanha = db.execute(
            'SELECT status FROM campaigns WHERE id=?',
            (campaign_id,),
        ).fetchone()
        if campanha is None:
            raise ValueError('Campanha não encontrada.')
        if campanha['status'] == 'Em envio':
            raise ValueError('Aguarde o envio terminar antes de excluir esta campanha.')

        resultado = {'contacts_unlinked': 0, 'deliveries': 0, 'tracking': 0}
        if remove_history:
            entregas = [
                row[0] for row in db.execute(
                    'SELECT id FROM deliveries WHERE campaign_id=?',
                    (campaign_id,),
                )
            ]
            contagens = clear_delivery_history(db, delivery_ids=entregas)
            resultado['deliveries'] = contagens['deliveries']
            resultado['tracking'] = contagens['tracking']
            resultado['tracking'] += db.execute(
                'DELETE FROM tracking_messages WHERE campaign_id=?',
                (campaign_id,),
            ).rowcount

        resultado['contacts_unlinked'] = db.execute(
            'DELETE FROM campaign_contacts WHERE campaign_id=?',
            (campaign_id,),
        ).rowcount
        db.execute('DELETE FROM campaigns WHERE id=?', (campaign_id,))
        return resultado