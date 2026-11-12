"""Métricas e gerenciamento seletivo do histórico de envios do GAMA."""

# =========== 1. IMPORTAÇÕES E ESTADO DA INTERFACE ===========
# 1.1 Recursos utilizados pela página
import hashlib
from contextlib import closing

import streamlit as st

from storage import clear_delivery_history, connect
from tracking import summary
from visual import cabecalho, indicadores


# 1.2 Vincular cada seleção aos registros exibidos
def _chave(contexto):
    return hashlib.sha256(
        repr(contexto).encode("utf-8")
    ).hexdigest()[:20]


def _selecionados(registros, linhas, campo="id"):
    return [
        registros[indice][campo]
        for indice in dict.fromkeys(linhas)
        if type(indice) is int
        and 0 <= indice < len(registros)
    ]


# 1.3 Atualizar métricas e invalidar seleções anteriores
def _atualizar(mensagem):
    st.session_state["gama_historico_revisao"] = (
        st.session_state.get(
            "gama_historico_revisao",
            0,
        ) + 1
    )

    st.session_state["gama_historico_aviso"] = mensagem
    st.rerun()


# =========== 2. HISTÓRICO ATUAL E EXCLUSÃO SELETIVA ===========
# 2.1 Consultar registros com filtros e paginação
def _historico_atual(db, campanha_id):
    st.subheader("Histórico de envios")

    status = st.selectbox(
        "Filtrar status",
        [
            "Todos",
            "Enviado",
            "Falhou",
            "Incerto",
        ],
        key="gama_historico_status",
    )

    condicoes = []
    parametros = []

    if campanha_id is not None:
        condicoes.append("d.campaign_id=?")
        parametros.append(campanha_id)

    if status != "Todos":
        condicoes.append("d.status=?")
        parametros.append(status)

    where = (
        " WHERE " + " AND ".join(condicoes)
        if condicoes
        else ""
    )

    total = db.execute(
        "SELECT COUNT(*) FROM deliveries d" + where,
        parametros,
    ).fetchone()[0]

    contexto = (
        campanha_id,
        status,
        st.session_state.get(
            "gama_historico_revisao",
            0,
        ),
    )

    paginas = max(
        1,
        (total + 49) // 50,
    )

    pagina = st.number_input(
        "Página do histórico",
        min_value=1,
        max_value=paginas,
        value=1,
        key="gama_historico_pagina_" + _chave(contexto),
    )

    registros = [
        dict(registro)
        for registro in db.execute(
            """
            SELECT
                d.*,
                c.name AS campanha,
                t.first_opened
            FROM deliveries d
            LEFT JOIN campaigns c
                ON c.id=d.campaign_id
            LEFT JOIN tracking_messages t
                ON t.token=d.tracking_token
            """
            + where
            + """
            ORDER BY d.created DESC,d.id DESC
            LIMIT 50 OFFSET ?
            """,
            [
                *parametros,
                (pagina - 1) * 50,
            ],
        )
    ]

    st.caption(
        f"{total} registros · Página {pagina} de {paginas}. "
        "Selecione as linhas que deseja gerenciar."
    )

    if not registros:
        st.info(
            "Nenhum registro encontrado para esta seleção."
        )
        return

    chave = _chave((
        contexto,
        pagina,
        tuple(
            registro["id"]
            for registro in registros
        ),
    ))

    # 2.2 Tabela com seleção de múltiplas linhas
    linhas = [
        {
            "Campanha": (
                registro["campanha"]
                or "Campanha indisponível"
            ),
            "Email": registro["email"],
            "Status": registro["status"],
            "Abertura": (
                (
                    "Detectada"
                    if registro["first_opened"]
                    else "Não detectada"
                )
                if registro["tracking_token"]
                else "Não rastreado"
            ),
            "Detalhe": registro["detail"],
            "Data (UTC)": registro["created"],
        }
        for registro in registros
    ]

    evento = st.dataframe(
        linhas,
        hide_index=True,
        width="stretch",
        row_height=48,
        selection_mode="multi-row",
        on_select="rerun",
        key="gama_historico_tabela_" + chave,
    )

    ids = _selecionados(
        registros,
        evento.selection.rows,
    )

    st.caption(
        f"{len(ids)} registros selecionados nesta página."
    )

    # 2.3 Apagar somente os registros selecionados e confirmados
    with st.expander(
        "Apagar registros e liberar reenvio",
        expanded=bool(ids),
    ):
        st.write(
            "Apagar remove os registros selecionados "
            "e suas aberturas detectadas. Isso permite "
            "enviar novamente para esses contatos "
            "nesta campanha."
        )

        st.caption(
            "Emails já enviados continuam na caixa "
            "do destinatário. Descadastros e outros "
            "registros de envio continuam sendo respeitados."
        )

        confirmacao = st.checkbox(
            "Confirmo a exclusão dos registros selecionados "
            "e das aberturas associadas.",
            key=(
                "gama_historico_confirmar_"
                + _chave((chave, ids))
            ),
            disabled=not ids,
        )

        if st.button(
            "Apagar registros selecionados",
            type="primary",
            disabled=not ids or not confirmacao,
            key="gama_historico_apagar_" + chave,
        ):
            try:
                resultado = clear_delivery_history(
                    db,
                    delivery_ids=ids,
                )

            except ValueError as erro:
                st.error(str(erro))

            else:
                _atualizar(
                    f"{resultado['deliveries']} registros "
                    "apagados. A seleção de envio será "
                    "atualizada em Campanhas."
                )

    # 2.4 Conferir manualmente resultados incertos selecionados
    incertos = [
        registro
        for registro in registros
        if registro["id"] in ids
        and registro["status"] == "Incerto"
    ]

    if incertos:
        with st.expander(
            "Conferir resultados incertos"
        ):
            st.caption(
                "A ação será aplicada somente aos "
                "resultados incertos selecionados "
                "nesta página."
            )

            resolucao = st.radio(
                "Após conferir a conta",
                [
                    "Marcar como enviado",
                    "Liberar nova tentativa",
                ],
                key="gama_conferencia_" + chave,
            )

            confirme = st.checkbox(
                "Conferi este envio na conta do remetente",
                key=(
                    "gama_conferi_"
                    + _chave((chave, ids))
                ),
            )

            if st.button(
                "Salvar conferência",
                disabled=not confirme,
                key="gama_conferir_" + chave,
            ):
                try:
                    with db:
                        for entrega in incertos:
                            estado = db.execute(
                                """
                                SELECT status
                                FROM campaigns
                                WHERE id=?
                                """,
                                (entrega["campaign_id"],),
                            ).fetchone()

                            if (
                                estado
                                and estado["status"] == "Em envio"
                            ):
                                raise ValueError(
                                    "Aguarde o envio terminar "
                                    "antes de conferir "
                                    "estes registros."
                                )

                            novo_status = (
                                "Enviado"
                                if resolucao == "Marcar como enviado"
                                else "Falhou"
                            )

                            db.execute(
                                """
                                UPDATE deliveries
                                SET status=?,detail=?
                                WHERE id=?
                                """,
                                (
                                    novo_status,
                                    "Resultado confirmado "
                                    "manualmente pelo usuário",
                                    entrega["id"],
                                ),
                            )

                            if entrega["tracking_token"]:
                                db.execute(
                                    """
                                    UPDATE tracking_messages
                                    SET accepted=?
                                    WHERE token=?
                                    """,
                                    (
                                        int(
                                            novo_status == "Enviado"
                                        ),
                                        entrega["tracking_token"],
                                    ),
                                )

                except ValueError as erro:
                    st.error(str(erro))

                else:
                    _atualizar(
                        "Conferência salva."
                    )


# =========== 3. HISTÓRICO ANTIGO ===========
# 3.1 Selecionar e remover registros importados de enviados.txt
def _historico_antigo(db):
    total = db.execute(
        "SELECT COUNT(*) FROM legacy_deliveries"
    ).fetchone()[0]

    if not total:
        return

    with st.expander(
        f"Histórico antigo: {total} destinatários"
    ):
        st.caption(
            "Importado de enviados.txt, sem data ou campanha. "
            "Registros apagados aqui não serão restaurados "
            "automaticamente pelo arquivo original."
        )

        revisao = st.session_state.get(
            "gama_historico_revisao",
            0,
        )

        paginas = max(
            1,
            (total + 49) // 50,
        )

        pagina = st.number_input(
            "Página do histórico antigo",
            min_value=1,
            max_value=paginas,
            value=1,
            key=f"gama_legado_pagina_{revisao}",
        )

        registros = [
            dict(registro)
            for registro in db.execute(
                """
                SELECT email
                FROM legacy_deliveries
                ORDER BY email
                LIMIT 50 OFFSET ?
                """,
                ((pagina - 1) * 50,),
            )
        ]

        chave = _chave((
            revisao,
            pagina,
            tuple(
                registro["email"]
                for registro in registros
            ),
        ))

        evento = st.dataframe(
            [
                {
                    "Email": registro["email"],
                }
                for registro in registros
            ],
            hide_index=True,
            width="stretch",
            row_height=48,
            selection_mode="multi-row",
            on_select="rerun",
            key="gama_legado_tabela_" + chave,
        )

        emails = _selecionados(
            registros,
            evento.selection.rows,
            "email",
        )

        confirmado = st.checkbox(
            "Confirmo a exclusão dos destinatários "
            "selecionados do histórico antigo.",
            disabled=not emails,
            key=(
                "gama_legado_confirmar_"
                + _chave((chave, emails))
            ),
        )

        if st.button(
            "Apagar histórico antigo selecionado",
            disabled=not emails or not confirmado,
            key="gama_legado_apagar_" + chave,
        ):
            try:
                resultado = clear_delivery_history(
                    db,
                    legacy_emails=emails,
                )

            except ValueError as erro:
                st.error(str(erro))

            else:
                _atualizar(
                    f"{resultado['legacy']} destinatários "
                    "removidos do histórico antigo."
                )


# =========== 4. TELA DE DESEMPENHO ===========
# 4.1 Carregar a página e seus filtros
def mostrar_desempenho():
    with closing(connect()) as db:
        campanhas = [
            dict(registro)
            for registro in db.execute(
                """
                SELECT id,name
                FROM campaigns
                ORDER BY name,id
                """
            )
        ]

        cabecalho(
            "Desempenho",
            "Acompanhe os resultados e gerencie "
            "o histórico dos seus testes.",
            "Seus resultados",
        )

        aviso = st.session_state.pop(
            "gama_historico_aviso",
            None,
        )

        if aviso:
            st.success(aviso)

        st.button(
            "Atualizar métricas",
            key="gama_atualizar_metricas",
        )

        campanha_id = st.selectbox(
            "Campanha do desempenho",
            [None] + [
                campanha["id"]
                for campanha in campanhas
            ],
            format_func=lambda valor: (
                "Todas as campanhas"
                if valor is None
                else next(
                    campanha["name"]
                    for campanha in campanhas
                    if campanha["id"] == valor
                )
            ),
            key="gama_desempenho_campanha",
        )

        # 4.2 Consultar contagens e métricas do pixel
        where = (
            ""
            if campanha_id is None
            else " WHERE campaign_id=?"
        )

        parametros = (
            []
            if campanha_id is None
            else [campanha_id]
        )

        contagens = {
            registro["status"]: registro["total"]
            for registro in db.execute(
                "SELECT status,COUNT(*) AS total "
                "FROM deliveries"
                + where
                + " GROUP BY status",
                parametros,
            )
        }

        dados = summary(
            db,
            campanha_id,
        )

        enviados = contagens.get(
            "Enviado",
            0,
        )

        taxa = (
            "—"
            if dados["open_rate"] is None
            else f"{dados['open_rate']:.1f}%"
        )

        # 4.3 Indicadores principais
        indicadores([
            (
                "Emails enviados",
                enviados,
                "Aceitos pelo servidor SMTP",
                "email",
            ),
            (
                "Aberturas detectadas",
                dados["opened"],
                "Mensagens com pelo menos uma abertura",
                "modelo",
            ),
            (
                "Taxa de abertura",
                taxa,
                "Sobre mensagens enviadas com pixel",
                "campanha",
            ),
            (
                "Falhas registradas",
                contagens.get("Falhou", 0),
                "Mensagens recusadas pelo servidor",
                "contatos",
            ),
        ])

        indicadores([
            (
                "Mensagens rastreadas",
                dados["tracked"],
                "Envios aceitos com pixel",
                "email",
            ),
            (
                "Contatos que abriram",
                dados["contacts_opened"],
                "Destinatários distintos",
                "contatos",
            ),
            (
                "Sem abertura detectada",
                dados["never_opened"],
                "Pixel ainda não solicitado",
                "modelo",
            ),
            (
                "Resultados incertos",
                contagens.get("Incerto", 0),
                "Confira antes de tentar novamente",
                "campanha",
            ),
        ])

        nao_rastreados = max(
            0,
            enviados - dados["tracked"],
        )

        st.caption(
            f"{dados['opens']} carregamentos do pixel. "
            f"{nao_rastreados} envios sem rastreamento "
            "nesta seleção."
        )

        st.info(
            "O pixel detecta o carregamento da imagem. "
            "Bloqueio de imagens, proxies e mecanismos "
            "de privacidade afetam a detecção. "
            "Aceitação SMTP não garante entrega "
            "na caixa de entrada."
        )

        # 4.4 Histórico atual e histórico da versão antiga
        _historico_atual(
            db,
            campanha_id,
        )

        _historico_antigo(db)