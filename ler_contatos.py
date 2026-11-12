# =========== 1. IMPORTAÇÕES E VARIÁVEIS DE AMBIENTE ===========
# 1.1 Importação de bibliotecas
import openpyxl
import smtplib
import os
import time
from email.mime.text import MIMEText
from dotenv import load_dotenv
from email_validator import validate_email, EmailNotValidError

# 1.2 Carregamento do .env
load_dotenv()
email_remetente = os.getenv("EMAIL_REMETENTE")
senha_app = os.getenv("SENHA_APP")

ASSUNTO = "Treinamentos que fortalecem a equipe da {empresa}"

# =========== 2. LEITURA DE DADOS ===========
# 2.1 Leitura da planilha de contatos
planilha = openpyxl.load_workbook("contatos.xlsx")
aba = planilha.active

# 2.2 Leitura do template de texto
with open("template_email.txt", "r", encoding="utf-8") as arquivo:
    template = arquivo.read()

# 2.3 Histórico de envios (enviados.txt)
try:
    with open("enviados.txt", "r", encoding="utf-8") as arquivo:
        ja_enviados = set(linha.strip() for linha in arquivo)
except FileNotFoundError:
    ja_enviados = set()

# =========== 3. VALIDAÇÃO PRÉVIA ===========
# 3.1 Filtragem de contatos válidos e não enviados
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

# 3.2 Confirmação do usuário no terminal
print(f"📋 {len(contatos_para_enviar)} contato(s) receberão o email:")
for nome, email_destino in contatos_para_enviar:
    print(f"   • {nome} ({email_destino})")

confirmacao = input("Deseja continuar com o envio? (s/n): ")

if confirmacao.lower() != "s":
    print("Envio cancelado.")
    exit()
    
# =========== 4. CONEXÃO E ENVIO ===========
# 4.1 Autenticação no servidor SMTP
servidor = smtplib.SMTP("smtp.gmail.com", 587)
servidor.starttls()
servidor.login(email_remetente, senha_app)

total_enviados = 0
total_falhas = 0
total_pulados = 0

# 4.2 Ciclo de envio e formatação
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

# =========== 5. FINALIZAÇÃO ===========
# 5.1 Relatório de envio
print("=" * 50)
print("📊 RELATÓRIO FINAL")
print(f"✅ Enviados com sucesso: {total_enviados}")
print(f"❌ Falharam: {total_falhas}")
print(f"⏭️  Pulados (duplicados/inválidos): {total_pulados}")
print("=" * 50)