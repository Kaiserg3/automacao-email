import streamlit as st
import openpyxl
import smtplib
import os
import time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from dotenv import load_dotenv
from email_validator import validate_email, EmailNotValidError

st.set_page_config(page_title="Sistema de Envio de Emails", layout="wide")
st.title("📧 Sistema de Envio de Emails")

if "contatos" not in st.session_state:
    st.session_state.contatos = []
if "colunas" not in st.session_state:
    st.session_state.colunas = ["nome", "email", "empresa", "cargo"]
if "mostrar_form_contato" not in st.session_state:
    st.session_state.mostrar_form_contato = False
if "campos_novo_contato" not in st.session_state:
    st.session_state.campos_novo_contato = [{"campo": "email", "valor": ""}]
if "anexo_dados" not in st.session_state:
    st.session_state.anexo_dados = None
if "anexo_nome" not in st.session_state:
    st.session_state.anexo_nome = None

aba1, aba2, aba3 = st.tabs(["1️⃣ Contatos", "2️⃣ Template", "3️⃣ Enviar"])

# --- ABA 1: Contatos ---
with aba1:
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
                    for chave in contato:
                        if chave not in st.session_state.colunas:
                            st.session_state.colunas.append(chave)

                    st.success("Contato adicionado!")
                    st.session_state.campos_novo_contato = [{"campo": "email", "valor": ""}]
                    st.session_state.mostrar_form_contato = False
                    st.rerun()

        st.divider()

    st.subheader("Enviar planilha")
    arquivo_planilha = st.file_uploader("Envie sua planilha de contatos (.xlsx)", type=["xlsx"])

    if "ultimo_arquivo_id" not in st.session_state:
        st.session_state.ultimo_arquivo_id = None

    if arquivo_planilha is not None and arquivo_planilha.file_id != st.session_state.ultimo_arquivo_id:
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
        st.session_state.ultimo_arquivo_id = arquivo_planilha.file_id
        st.success(f"{len(novos_contatos)} contato(s) carregado(s) da planilha!")

    st.divider()
    st.subheader(f"Contatos atuais ({len(st.session_state.contatos)})")

    if st.session_state.contatos:
        contatos_editados = st.data_editor(
            st.session_state.contatos,
            use_container_width=True,
            hide_index=True,
            num_rows="dynamic",
            key="editor_contatos"
        )

        # Remove linhas em branco (ex: linha nova criada mas ainda sem email)
        st.session_state.contatos = [
            c for c in contatos_editados if c.get("email")
        ]

# --- ABA 2: Template ---
with aba2:
    campos_existentes = set()
    for contato in st.session_state.contatos:
        campos_existentes.update(contato.keys())

    if campos_existentes:
        st.caption(f"Campos disponíveis: {', '.join('{' + c + '}' for c in sorted(campos_existentes))}")
    else:
        st.caption("Adicione contatos na aba anterior para ver quais campos estão disponíveis.")

    template = st.text_area(
        "Texto do email:",
        value="",
        placeholder="Escreva aqui o texto do seu email. Use chaves para inserir dados do contato, como {nome} ou {empresa}.",
        height=300
    )

    if st.session_state.contatos and template:
        with st.expander("👁️ Pré-visualização (usando o primeiro contato)"):
            try:
                preview = template.format(**st.session_state.contatos[0])
                st.text(preview)
            except KeyError as campo_faltando:
                st.warning(f"O template usa o campo {campo_faltando}, que não existe nesse contato.")

    st.divider()
    st.subheader("Anexo (opcional)")
    anexo = st.file_uploader("Anexar arquivo a todos os emails", type=None, key="anexo_uploader")

    if anexo is not None:
        st.session_state.anexo_dados = anexo.read()
        st.session_state.anexo_nome = anexo.name
        tamanho_kb = len(st.session_state.anexo_dados) / 1024
        st.caption(f"📎 {anexo.name} ({tamanho_kb:.1f} KB)")

        extensao = anexo.name.lower().split(".")[-1]

        if extensao in ["png", "jpg", "jpeg", "gif", "webp"]:
            st.image(st.session_state.anexo_dados, caption=anexo.name, width=400)
        elif extensao == "pdf":
            import base64
            base64_pdf = base64.b64encode(st.session_state.anexo_dados).decode("utf-8")
            st.markdown(
                f'<iframe src="data:application/pdf;base64,{base64_pdf}" width="100%" height="500" type="application/pdf"></iframe>',
                unsafe_allow_html=True
            )
        else:
            st.info(f"Pré-visualização não disponível para arquivos .{extensao} — o anexo será enviado normalmente.")
    else:
        st.session_state.anexo_dados = None
        st.session_state.anexo_nome = None

# --- ABA 3: Enviar ---
with aba3:
    contatos_completos = st.session_state.contatos

    if not contatos_completos:
        st.warning("Adicione contatos na aba 'Contatos' primeiro.")
    else:
        st.subheader("Selecione os contatos para esta campanha")

        contatos_para_tabela = []
        for contato in contatos_completos:
            linha = dict(contato)
            linha["Selecionado"] = True
            contatos_para_tabela.append(linha)

        contatos_editados = st.data_editor(
            contatos_para_tabela,
            use_container_width=False,
            hide_index=True,
            disabled=[c for c in st.session_state.colunas],
            column_order=st.session_state.colunas + ["Selecionado"],
            column_config={
                col: st.column_config.Column(width=220) for col in st.session_state.colunas
            } | {
                "Selecionado": st.column_config.CheckboxColumn(width=70)
            },
            key="tabela_selecao"
        )

        contatos = [
            {k: v for k, v in linha.items() if k != "Selecionado"}
            for linha in contatos_editados
            if linha["Selecionado"]
        ]

        st.write(f"**{len(contatos)}** de **{len(contatos_completos)}** contato(s) selecionado(s) para esta campanha.")

        if st.session_state.anexo_nome:
            st.caption(f"📎 Anexo configurado: {st.session_state.anexo_nome}")

        if not contatos:
            st.info("Selecione ao menos um contato para habilitar o envio.")
        elif st.button("📤 Enviar emails", type="primary"):
            dados_anexo = st.session_state.get("anexo_dados")
            nome_anexo = st.session_state.get("anexo_nome")

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

                        mensagem = MIMEMultipart()
                        mensagem["Subject"] = f"Treinamentos que fortalecem a equipe da {empresa}" if empresa else "Treinamentos para sua equipe"
                        mensagem["From"] = email_remetente
                        mensagem["To"] = email

                        mensagem.attach(MIMEText(corpo, "plain", "utf-8"))

                        if dados_anexo is not None:
                            parte_anexo = MIMEBase("application", "octet-stream")
                            parte_anexo.set_payload(dados_anexo)
                            encoders.encode_base64(parte_anexo)
                            parte_anexo.add_header(
                                "Content-Disposition",
                                f"attachment; filename={nome_anexo}"
                            )
                            mensagem.attach(parte_anexo)

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