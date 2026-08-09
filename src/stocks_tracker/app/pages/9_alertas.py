"""Pagina 9 — Alertas.

Historico de avisos, estado de los canales y las reglas configuradas.
"""

from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from stocks_tracker.alerts import evaluate as ev
from stocks_tracker.alerts import notify as nt
from stocks_tracker.alerts.rules import get_rules, reload
from stocks_tracker.app import data_access as da
from stocks_tracker.app.components.common import render_disclaimer
from stocks_tracker.app.components.theme import STATUS, palette

st.title("Alertas")

SEVERITY_COLOR = {
    "critica": STATUS["critical"], "alta": STATUS["warning"],
    "media": STATUS["good"], "baja": palette()["muted"],
}
SEVERITY_ORDER = {"critica": 0, "alta": 1, "media": 2, "baja": 3}


def _severity_of(payload) -> str:
    """La gravedad viaja dentro del JSON, para no anadir una columna al esquema."""
    if not payload:
        return "media"
    try:
        return str(json.loads(payload).get("severity", "media"))
    except (json.JSONDecodeError, TypeError):
        return "media"


tab_history, tab_channels, tab_rules = st.tabs(
    ["Historico", "Canales", "Reglas configuradas"]
)

# ---------------------------------------------------------------------------
# Historico
# ---------------------------------------------------------------------------
with tab_history:
    filter_left, filter_right = st.columns([1, 1])
    with filter_left:
        days = st.selectbox(
            "Periodo", options=[7, 30, 90, 365], index=1,
            format_func=lambda d: f"Ultimos {d} dias",
        )
    with filter_right:
        only_pending = st.toggle("Solo sin revisar", value=False)

    alerts = da.get_alerts(days=days, only_pending=only_pending)

    if alerts.empty:
        st.info(
            "Sin alertas en este periodo.\n\n"
            "Las alertas se generan con `make alerts`, o automaticamente si has "
            "programado `scripts/daily_update.sh` en cron.",
            icon=":material/notifications_off:",
        )
    else:
        alerts = alerts.copy()
        alerts["severidad"] = alerts["payload"].map(_severity_of)
        alerts["orden"] = alerts["severidad"].map(lambda s: SEVERITY_ORDER.get(s, 9))
        alerts = alerts.sort_values(["orden", "triggered_at"], ascending=[True, False])

        counts = alerts["severidad"].value_counts()
        summary = st.columns(4)
        for i, key in enumerate(["critica", "alta", "media", "baja"]):
            summary[i].metric(key.capitalize(), int(counts.get(key, 0)))

        pending = int((~alerts["acknowledged"].astype(bool)).sum())
        if pending:
            st.caption(f"{pending} sin revisar de {len(alerts)}.")

        view = pd.DataFrame(
            {
                "Cuando": pd.to_datetime(alerts["triggered_at"]).dt.strftime("%d/%m %H:%M"),
                "Gravedad": alerts["severidad"].str.capitalize(),
                "Aviso": alerts["message"],
                "Regla": alerts["rule_id"],
                "Revisada": alerts["acknowledged"].astype(bool).map(
                    {True: "Si", False: ""}
                ),
            }
        )
        st.dataframe(
            view, hide_index=True, height=min(420, 42 + 35 * len(view)),
            column_config={"Aviso": st.column_config.TextColumn(width="large")},
        )

        action_left, action_right = st.columns([1, 3])
        with action_left:
            if pending and st.button("Marcar todas como revisadas", width="stretch"):
                unread = alerts[~alerts["acknowledged"].astype(bool)]["id"].tolist()
                ev.acknowledge(unread)
                da.get_alerts.clear()
                da.count_pending_alerts.clear()
                st.rerun()
        with action_right:
            st.download_button(
                "Descargar CSV",
                data=view.to_csv(index=False).encode("utf-8"),
                file_name="alertas.csv", mime="text/csv",
            )

    st.caption(
        "Cada regla tiene un **periodo de espera**: una vez avisada, no vuelve a "
        "dispararse para el mismo valor durante varios dias. Sin eso, la misma "
        "alerta se repetiria cada jornada mientras la condicion siga siendo "
        "cierta, y en una semana dejarias de leerlas."
    )

# ---------------------------------------------------------------------------
# Canales
# ---------------------------------------------------------------------------
with tab_channels:
    st.subheader("Estado de los canales")
    status = nt.channel_status()
    st.dataframe(
        pd.DataFrame(status).rename(
            columns={"canal": "Canal", "activo": "Activo", "listo": "Listo",
                     "faltan": "Falta configurar"}
        ),
        hide_index=True,
    )
    st.caption(
        "El canal de **fichero** no depende de nada externo y por eso viene "
        "activado por defecto: escribe cada aviso en `data/alerts.jsonl`. "
        "Telegram y correo necesitan credenciales en `.env`."
    )

    st.subheader("Probar un canal")
    test_col, _ = st.columns([1, 2])
    with test_col:
        channel = st.selectbox("Canal", options=[s["canal"] for s in status])
        if st.button("Enviar mensaje de prueba", type="primary"):
            result = nt.test_channel(channel)
            if result.ok:
                st.success(f"Enviado por {result.channel}. {result.detail}")
            else:
                st.error(f"No se pudo enviar: {result.detail}")

    with st.expander("Como configurar Telegram"):
        st.markdown(
            """
1. Habla con **@BotFather** en Telegram y crea un bot con `/newbot`.
2. Copia el token que te da.
3. Escribe un mensaje a tu bot y visita
   `https://api.telegram.org/bot<TOKEN>/getUpdates` para ver tu `chat_id`.
4. Ponlos en `.env`:

   ```
   TELEGRAM_BOT_TOKEN=...
   TELEGRAM_CHAT_ID=...
   ```
5. Pon `enabled: true` en el canal `telegram` de `config/alerts.yaml`.

El token da control total sobre el bot: no lo compartas ni lo subas al
repositorio. `.env` esta en `.gitignore` por ese motivo.
            """
        )

# ---------------------------------------------------------------------------
# Reglas
# ---------------------------------------------------------------------------
with tab_rules:
    st.subheader("Reglas configuradas")
    reload()
    rules = get_rules()

    if not rules:
        st.warning("No hay reglas en `config/alerts.yaml`.")
    else:
        for rule in sorted(rules, key=lambda r: SEVERITY_ORDER.get(r.severity, 9)):
            color = SEVERITY_COLOR.get(rule.severity, palette()["muted"])
            with st.container(border=True):
                head, meta = st.columns([3, 1])
                with head:
                    st.markdown(
                        f"**{rule.name}** "
                        f"<span style='color:{color};font-size:0.85em'>"
                        f"({rule.severity})</span>",
                        unsafe_allow_html=True,
                    )
                    st.code(rule.when, language="python")
                with meta:
                    st.caption(f"**Ambito**  \n{rule.scope}")
                    st.caption(f"**Espera**  \n{rule.cooldown_days} dias")
                if rule.note:
                    st.caption(rule.note)

    st.caption(
        "Las reglas se editan en `config/alerts.yaml`. Las condiciones se "
        "evaluan con un interprete restringido que solo admite comparaciones y "
        "aritmetica: nunca se ejecuta codigo arbitrario."
    )

st.divider()
st.caption(
    "Una alerta es un aviso para que **mires** algo, nunca una orden de compra "
    "o de venta."
)
render_disclaimer()
