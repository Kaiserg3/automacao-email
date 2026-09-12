import streamlit as st
import openpyxl
import smtplib
import os
import time
from email.mime.text import MIMEText
from dotenv import load_dotenv
from email_validator import validate_email, EmailNotValidError

st.set_page_config(page_title="Sistema de Envio de Emails", layout="wide")
st.title("📧 Sistema de Envio de Emails")

# "session_state" guarda dados que precisam sobreviver entre uma ação e outra
# (sem isso, cada clique faria o Streamlit "esquecer" os contatos já carregados)
if "contatos" not in st.session_state:
    st.session_state.contatos = []
if "colunas" not in st.session_state:
    st.session_state.colunas = ["nome", "email", "empresa", "cargo"]

aba1, aba2, aba3 = st.tabs(["1️⃣ Contatos", "2️⃣ Template", "3️⃣ Enviar"])

# --- ABA 1: Contatos ---
with aba1:
    # Controla se o formulário de contato manual está visível ou não
    if "mostrar_form_contato" not in st.session_state:
        st.session_state.mostrar_form_contato = False

    # Controla os campos que o usuário está montando na hora
    if "campos_novo_contato" not in st.session_state:
        st.session_state.campos_novo_contato = [{"campo": "email", "valor": ""}]

    col_botao, col_resto = st.columns([1, 6])
    with col_botao:
        if st.button("➕", help="Adicionar contato manualmente"):
            st.session_state.mostrar_form_contato = not st.session_state.mostrar_form_contato

    if st.session_state.mostrar_form_contato:
        st.subheader("Novo contato")

        for i, linha in enumerate(st.session_state.campos_novo_contato):
            col_nome, col_valor = st.columns(2)
            with col_nome:
                if i == 0:
                    # Primeira linha travada como "email" (não pode editar o nome do campo)
                    st.text_input("Nome do campo", value="email", disabled=True, key=f"campo_nome_{i}")
                    linha["campo"] = "email"
                else:
                    linha["campo"] = st.text_input("Nome do campo", value=linha["campo"], key=f"campo_nome_{i}")
            with col_valor:
                linha["valor"] = st.text_input("Valor", value=linha["valor"], key=f"campo_valor_{i}")

        col_add, col_salvar = st.columns(2)
        with col_add:
            if st.button("+ Adicionar campo"):
                st.session_state.campos_novo_contato.append({"campo": "", "valor": ""})
                st.rerun()
        with col_salvar:
            if st.button("💾 Salvar contato", type="primary"):
                contato = {}
                for linha in st.session_state.campos_novo_contato:
                    if linha["campo"]:
                        contato[linha["campo"].strip().lower()] = linha["valor"]

                if not contato.get("email"):
                    st.error("O campo 'email' precisa ter um valor.")
                else:
                    st.session_state.contatos.append(contato)
                    # Atualiza a lista de colunas conhecidas, incluindo campos novos
                    for chave in contato:
                        if chave not in st.session_state.colunas:
                            st.session_state.colunas.append(chave)

                    st.success(f"Contato adicionado!")
                    st.session_state.campos_novo_contato = [{"campo": "email", "valor": ""}]
                    st.session_state.mostrar_form_contato = False
                    st.rerun()

        st.divider()

    st.subheader("Enviar planilha")
    arquivo_planilha = st.file_uploader("Envie sua planilha de contatos (.xlsx)", type=["xlsx"])

    if arquivo_planilha is not None:
        planilha = openpyxl.load_workbook(arquivo_planilha)
        aba = planilha.active

        cabecalho = [str(c.value).strip().lower() if c.value else "" for c in aba[1]]
        st.session_state.colunas = list(set(st.session_state.colunas) | set(c for c in cabecalho if c))

        novos_contatos = []
        for linha in aba.iter_rows(min_row=2, values_only=True):
            contato = dict(zip(cabecalho, linha))
            if not contato.get("email"):
                continue
            novos_contatos.append(contato)

        st.session_state.contatos.extend(novos_contatos)
        st.success(f"{len(novos_contatos)} contato(s) carregado(s) da planilha!")

    st.divider()
    st.subheader(f"Contatos atuais ({len(st.session_state.contatos)})")
    if st.session_state.contatos:
        st.dataframe(st.session_state.contatos, use_container_width=True, hide_index=True)

# --- ABA 2: Template ---
with aba2:
    st.write(f"Campos disponíveis para usar no template: {', '.join('{' + c + '}' for c in st.session_state.colunas)}")

    template_padrao = """Olá, {nome},

Meu nome é [seu nome], da [nome da sua empresa]. Trabalhamos com palestras e treinamentos corporativos voltados para o desenvolvimento de equipes, e imaginei que isso poderia ser relevante para a {empresa}.

Nossos treinamentos abordam temas como [liderança, produtividade, comunicação, etc.], sempre adaptados à realidade de cada empresa.

Se fizer sentido, ficaria feliz em agendar uma conversa rápida de 15 minutos para entender melhor as necessidades da {empresa} e ver se há um encaixe.

Fico à disposição!

Atenciosamente,
[seu nome]
[seu cargo / empresa]"""

    template = st.text_area(
        "Edite o texto do email:",
        value=template_padrao,
        height=300
    )

    if st.session_state.contatos:
        with st.expander("👁️ Pré-visualização (usando o primeiro contato)"):
            try:
                preview = template.format(**st.session_state.contatos[0])
                st.text(preview)
            except KeyError as campo_faltando:
                st.warning(f"O template usa o campo {campo_faltando}, que não existe nesse contato.")

# --- ABA 3: Enviar ---
with aba3:
    contatos = st.session_state.contatos

    if not contatos:
        st.warning("Adicione contatos na aba 'Contatos' primeiro.")
    else:
        st.write(f"**{len(contatos)}** contato(s) prontos para processar.")

        if st.button("📤 Enviar emails", type="primary"):
            load_dotenv()
            email_remetente = os.getenv("EMAIL_REMETENTE")
            senha_app = os.getenv("SENHA_APP")

            try:
                with open("enviados.txt", "r", encoding="utf-8") as arq:
                    ja_enviados = set(linha.strip() for linha in arq)
            except FileNotFoundError:
                ja_enviados = set()

            servidor = smtplib.SMTP("smtp.gmail.com", 587)
            servidor.starttls()
            servidor.login(email_remetente, senha_app)

            total_enviados = 0
            total_falhas = 0
            total_pulados = 0

            barra_progresso = st.progress(0)
            log_container = st.container(height=300, border=True)
            resumo = st.empty()

            for i, contato in enumerate(contatos):
                email = contato.get("email")
                nome = contato.get("nome", email)
                empresa = contato.get("empresa", "")
                status_icone = ""
                status_texto = ""

                if email in ja_enviados:
                    status_icone = "⏭️"
                    status_texto = "já recebeu antes"
                    total_pulados += 1
                else:
                    try:
                        validate_email(email)
                        corpo = template.format(**contato)

                        mensagem = MIMEText(corpo, "plain", "utf-8")
                        mensagem["Subject"] = f"Treinamentos que fortalecem a equipe da {empresa}" if empresa else "Treinamentos para sua equipe"
                        mensagem["From"] = email_remetente
                        mensagem["To"] = email

                        servidor.sendmail(email_remetente, email, mensagem.as_string())
                        total_enviados += 1
                        status_icone = "✅"
                        status_texto = "enviado"

                        with open("enviados.txt", "a", encoding="utf-8") as arq:
                            arq.write(email + "\n")

                    except EmailNotValidError:
                        total_pulados += 1
                        status_icone = "❌"
                        status_texto = "formato de email inválido"
                    except KeyError as campo_faltando:
                        total_falhas += 1
                        status_icone = "⚠️"
                        status_texto = f"faltou o campo {campo_faltando} nesse contato"
                    except Exception as erro:
                        total_falhas += 1
                        status_icone = "⚠️"
                        status_texto = f"falha no envio ({erro})"

                log_container.write(f"{status_icone} **{nome}** ({email}) — {status_texto}")
                barra_progresso.progress((i + 1) / len(contatos))
                resumo.info(f"✅ Enviados: {total_enviados} | ⚠️ Falhas: {total_falhas} | ⏭️ Pulados: {total_pulados}")

                time.sleep(5)

            servidor.quit()
            st.success("Campanha finalizada!")