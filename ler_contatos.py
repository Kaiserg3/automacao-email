import openpyxl
import smtplib
import os
import time
from email.mime.text import MIMEText
from dotenv import load_dotenv
from email_validator import validate_email, EmailNotValidError

# Carrega as variáveis do arquivo .env (EMAIL_REMETENTE e SENHA_APP)
load_dotenv()
email_remetente = os.getenv("EMAIL_REMETENTE")
senha_app = os.getenv("SENHA_APP")

ASSUNTO = "Treinamentos que fortalecem a equipe da {empresa}"

# Abre a planilha e o template
planilha = openpyxl.load_workbook("contatos.xlsx")
aba = planilha.active

with open("template_email.txt", "r", encoding="utf-8") as arquivo:
    template = arquivo.read()

# Lê a lista de emails já enviados (começa vazia se o arquivo ainda não existir)
try:
    with open("enviados.txt", "r", encoding="utf-8") as arquivo:
        ja_enviados = set(linha.strip() for linha in arquivo)
except FileNotFoundError:
    ja_enviados = set()


# Verifica quais contatos são válidos, sem enviar nada ainda
contatos_para_enviar = []
for linha in aba.iter_rows(min_row=2, max_col=4, values_only=True):
    nome, email_destino, empresa, cargo = linha
    if nome is None or email_destino is None:
        continue
    if email_destino in ja_enviados:
        continue
    try:
        validate_email(email_destino)
        contatos_para_enviar.append((nome, email_destino))
    except EmailNotValidError:
        continue

print(f"📋 {len(contatos_para_enviar)} contato(s) receberão o email:")
for nome, email_destino in contatos_para_enviar:
    print(f"   • {nome} ({email_destino})")

confirmacao = input("Deseja continuar com o envio? (s/n): ")

if confirmacao.lower() != "s":
    print("Envio cancelado.")
    exit()
    
# Conecta no servidor do Gmail/Google Workspace
servidor = smtplib.SMTP("smtp.gmail.com", 587)
servidor.starttls()
servidor.login(email_remetente, senha_app)

# Contadores do relatório final
total_enviados = 0
total_falhas = 0
total_pulados = 0

for linha in aba.iter_rows(min_row=2, max_col=4, values_only=True):
    nome, email_destino, empresa, cargo = linha

    if nome is None or email_destino is None:
        continue

    if email_destino in ja_enviados:
        print(f"⏭️  Pulando {nome} ({email_destino}) — já recebeu antes")
        total_pulados += 1
        continue

    try:
        validate_email(email_destino)
    except EmailNotValidError as erro:
        print(f"❌ Email inválido para {nome}: {email_destino} — {erro}")
        total_pulados += 1
        continue

    corpo = template.format(nome=nome, empresa=empresa)
    assunto_personalizado = ASSUNTO.format(empresa=empresa)

    mensagem = MIMEText(corpo, "plain", "utf-8")
    mensagem["Subject"] = assunto_personalizado
    mensagem["From"] = email_remetente
    mensagem["To"] = email_destino

    try:
        servidor.sendmail(email_remetente, email_destino, mensagem.as_string())
        print(f"✅ Enviado para {nome} ({email_destino})")
        total_enviados += 1

        with open("enviados.txt", "a", encoding="utf-8") as arquivo:
            arquivo.write(email_destino + "\n")

    except Exception as erro:
        print(f"❌ Falhou para {nome} ({email_destino}): {erro}")
        total_falhas += 1

    time.sleep(5)

servidor.quit()

print("=" * 50)
print("📊 RELATÓRIO FINAL")
print(f"✅ Enviados com sucesso: {total_enviados}")
print(f"❌ Falharam: {total_falhas}")
print(f"⏭️  Pulados (duplicados/inválidos): {total_pulados}")
print("=" * 50)
