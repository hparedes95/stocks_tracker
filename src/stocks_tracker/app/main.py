"""Punto de entrada del dashboard.

Se arranca con `make run`, que fija `--server.address 127.0.0.1` de forma
deliberada: Streamlit no tiene autenticacion, asi que exponerlo en 0.0.0.0
dejaria la aplicacion abierta a cualquiera en la red. Para acceso remoto, tunel
SSH o Tailscale.
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

# Permite ejecutar `streamlit run src/stocks_tracker/app/main.py` sin instalar.
_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from stocks_tracker.app.components import tv_widgets  # noqa: E402
from stocks_tracker.app.components.common import (  # noqa: E402
    render_data_origin_banner,
    render_freshness_badge,
    render_pending_alerts_badge,
)

st.set_page_config(
    page_title="Stocks Tracker",
    page_icon=":material/monitoring:",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Cinta de cotizaciones: pulso permanente, fuera de la navegacion para que
# aparezca en todas las paginas.
tv_widgets.ticker_tape(compact=True)

# Se guarda aparte porque el aviso de la barra lateral enlaza con ella, y
# `st.page_link` necesita el objeto de pagina, no su ruta.
alerts_page = st.Page("pages/9_alertas.py", title="Alertas",
                      icon=":material/notifications:", url_path="alertas")

pages = {
    "Mercado": [
        st.Page("pages/1_que_se_mueve_hoy.py", title="Que se mueve hoy",
                icon=":material/trending_up:", url_path="hoy", default=True),
        st.Page("pages/2_sectores.py", title="Sectores y rotacion",
                icon=":material/donut_large:", url_path="sectores"),
        st.Page("pages/6_macro.py", title="Macro y riesgo",
                icon=":material/public:", url_path="macro"),
    ],
    "Seleccion": [
        st.Page("pages/3_oportunidades.py", title="Oportunidades",
                icon=":material/filter_alt:", url_path="oportunidades"),
        st.Page("pages/4_ficha_valor.py", title="Ficha de valor",
                icon=":material/search:", url_path="valor"),
    ],
    "Analisis": [
        st.Page("pages/7_validacion.py", title="Validacion de senales",
                icon=":material/science:", url_path="validacion"),
    ],
    "Mi cartera": [
        st.Page("pages/5_watchlist.py", title="Cartera y watchlist",
                icon=":material/bookmark:", url_path="watchlist"),
        alerts_page,
    ],
    "El bot": [
        st.Page("pages/10_bot.py", title="Que hace el bot",
                icon=":material/smart_toy:", url_path="bot"),
    ],
    "Sistema": [
        st.Page("pages/8_estado.py", title="Estado de los datos",
                icon=":material/database:", url_path="estado"),
    ],
}

navigation = st.navigation(pages)

# Antes que nada: si los precios no son reales, hay que decirlo antes de que
# el usuario lea una sola cifra.
render_data_origin_banner()

with st.sidebar:
    st.markdown("### Stocks Tracker")
    render_freshness_badge()
    render_pending_alerts_badge(alerts_page)

navigation.run()
