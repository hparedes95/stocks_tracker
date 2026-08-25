"""Punto de entrada del dashboard.

Se arranca con `make run`, que fija `--server.address 127.0.0.1` de forma
deliberada: Streamlit no tiene autenticación, así que exponerlo en 0.0.0.0
dejaría la aplicación abierta a cualquiera en la red. Para acceso remoto, tunel
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
    render_integrity_badge,
    render_pending_alerts_badge,
)

st.set_page_config(
    page_title="Stocks Tracker",
    page_icon=":material/monitoring:",
    layout="wide",
    initial_sidebar_state="expanded",
)


@st.cache_resource
def _actualizar_esquema() -> str:
    """Pone al dia las tablas al arrancar el dashboard.

    Hasta ahora el esquema solo se actualizaba al ingerir o al calcular. Un
    usuario que actualiza el programa y abre el dashboard ANTES de descargar
    nada se encontraba un "Catalog Error: Table with name X does not exist" en
    la cara: la version nueva del codigo consulta una tabla que su almacen,
    creado con la version anterior, todavia no tiene.

    Se vio en el propio almacen de desarrollo con `price_consensus`, y es un
    fallo que llega a cualquier instalacion ya existente en cuanto se anade una
    tabla. Migrar aqui lo resuelve para todas de una vez.

    Es seguro: el esquema entero son `CREATE TABLE IF NOT EXISTS` y
    `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`, asi que ejecutarlo mil veces
    deja lo mismo que ejecutarlo una.

    Si falla no se para el dashboard. El motivo tipico es que la ingesta este
    corriendo en ese momento —DuckDB admite un solo escritor— y en ese caso el
    esquema ya lo esta actualizando ella.

    `cache_resource` y no `cache_data`: se ejecuta una vez por proceso, no una
    vez por sesion de navegador.
    """
    from stocks_tracker.core.db import migrate

    try:
        migrate()
    except Exception as exc:  # noqa: BLE001
        return f"{type(exc).__name__}: {exc}"
    return ""


_fallo_esquema = _actualizar_esquema()

# Cinta de cotizaciones: pulso permanente, fuera de la navegacion para que
# aparezca en todas las paginas.
tv_widgets.ticker_tape(compact=True)

# Se guarda aparte porque el aviso de la barra lateral enlaza con ella, y
# `st.page_link` necesita el objeto de pagina, no su ruta.
alerts_page = st.Page("pages/9_alertas.py", title="Alertas",
                      icon=":material/notifications:", url_path="alertas")
estado_page = st.Page("pages/8_estado.py", title="Estado de los datos",
                      icon=":material/database:", url_path="estado")

pages = {
    # El asesor va PRIMERO y en su propio grupo. Es la pantalla que responde a
    # la pregunta con la que se abre el programa —qué hago hoy— y el resto
    # existe para sostenerla: los datos, la calidad, los factores y el deterioro
    # acaban aquí, en una decisión por valor.
    "Qué hacer": [
        st.Page("pages/12_asesor.py", title="Qué haría hoy",
                icon=":material/lightbulb:", url_path="asesor"),
    ],
    "Mercado": [
        st.Page("pages/1_que_se_mueve_hoy.py", title="Qué se mueve hoy",
                icon=":material/trending_up:", url_path="hoy", default=True),
        st.Page("pages/2_sectores.py", title="Sectores y rotación",
                icon=":material/donut_large:", url_path="sectores"),
        st.Page("pages/6_macro.py", title="Macro y riesgo",
                icon=":material/public:", url_path="macro"),
    ],
    "Selección": [
        st.Page("pages/3_oportunidades.py", title="Oportunidades",
                icon=":material/filter_alt:", url_path="oportunidades"),
        st.Page("pages/4_ficha_valor.py", title="Ficha de valor",
                icon=":material/search:", url_path="valor"),
    ],
    "Análisis": [
        st.Page("pages/7_validacion.py", title="Validación de señales",
                icon=":material/science:", url_path="validacion"),
    ],
    "Mi cartera": [
        st.Page("pages/5_watchlist.py", title="Cartera y watchlist",
                icon=":material/bookmark:", url_path="watchlist"),
        st.Page("pages/11_diario.py", title="Diario de decisiones",
                icon=":material/history_edu:", url_path="diario"),
        alerts_page,
    ],
    "El bot": [
        st.Page("pages/10_bot.py", title="Qué hace el bot",
                icon=":material/smart_toy:", url_path="bot"),
    ],
    "Sistema": [
        estado_page,
    ],
}

navigation = st.navigation(pages)

# Antes que nada: si los precios no son reales, hay que decirlo antes de que
# el usuario lea una sola cifra.
render_data_origin_banner()

if _fallo_esquema:
    st.warning(
        "No se ha podido poner al dia el esquema de la base de datos "
        f"(`{_fallo_esquema}`). Si la descarga esta corriendo ahora mismo es "
        "normal y se arregla solo; si no, alguna pantalla puede fallar al "
        "buscar una tabla que todavia no existe.",
        icon=":material/database_off:",
    )

with st.sidebar:
    st.markdown("### Stocks Tracker")
    render_freshness_badge()
    # El semaforo va ANTES que los avisos: si los datos no son fiables, lo
    # demas que diga la pantalla importa menos.
    render_integrity_badge(estado_page)
    render_pending_alerts_badge(alerts_page)

navigation.run()
