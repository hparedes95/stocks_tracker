"""Pagina 2 — Sectores y rotacion."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from stocks_tracker.app import data_access as da
from stocks_tracker.app.components import charts, tv_widgets
from stocks_tracker.app.components.common import render_disclaimer
from stocks_tracker.core.config import get_sector_etfs

st.title("Sectores y rotacion")

sectors = da.get_sector_performance()
if sectors.empty:
    st.warning("Sin datos sectoriales. Ejecuta `make compute`.")
    st.stop()

st.caption(
    "Todas las cifras son la **mediana** del sector. La media se dispara con un "
    "solo valor en operacion corporativa y deja de describir al grupo."
)

# ---------------------------------------------------------------------------
# Mapa sector x horizonte
# ---------------------------------------------------------------------------
st.subheader("Rendimiento por sector y horizonte")
st.plotly_chart(
    charts.heatmap_sector_horizon(sectors, height=420),
    width="stretch",
    config={"displayModeBar": False},
)

# ---------------------------------------------------------------------------
# Tabla
# ---------------------------------------------------------------------------
st.subheader("Detalle por sector")
table = pd.DataFrame(
    {
        "Sector": sectors["sector"],
        "Valores": sectors["n_valores"],
        "Dia": sectors["ret_1d"] * 100,
        "Semana": sectors["ret_5d"] * 100,
        "Mes": sectors["ret_1m"] * 100,
        "Trimestre": sectors["ret_3m"] * 100,
        "12 meses": sectors["ret_12m"] * 100,
        "Sobre MM200": sectors["pct_sobre_mm200"],
    }
)
st.dataframe(
    table, hide_index=True, height=440,
    column_config={
        **{
            col: st.column_config.NumberColumn(format="%+.2f%%")
            for col in ["Dia", "Semana", "Mes", "Trimestre", "12 meses"]
        },
        "Sobre MM200": st.column_config.ProgressColumn(
            min_value=0.0, max_value=100.0, format="%.0f%%"
        ),
    },
)
st.caption(
    "La columna **Sobre MM200** es la amplitud interna del sector: un sector "
    "que sube con solo el 30% de sus valores en tendencia alcista se apoya en pocos nombres."
)

# ---------------------------------------------------------------------------
# Amplitud por sector
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Amplitud interna de un sector")
sector_names = sectors["sector"].tolist()
chosen = st.selectbox("Sector", options=sector_names)
breadth = da.get_breadth(f"GICS:{chosen}")
if breadth.empty:
    st.caption("Sin serie de amplitud para este sector todavia.")
else:
    st.plotly_chart(
        charts.breadth_lines(breadth, height=280),
        width="stretch",
        config={"displayModeBar": False},
    )

# ---------------------------------------------------------------------------
# Complementos de TradingView
# ---------------------------------------------------------------------------
if tv_widgets.enabled():
    st.divider()
    tab_map, tab_crypto = st.tabs(["Mapa de mercado", "Cripto"])
    with tab_map:
        source = st.selectbox(
            "Universo", options=["SPX500", "NASDAQ100", "IBC", "SX5E"],
            format_func=lambda s: {
                "SPX500": "S&P 500", "NASDAQ100": "Nasdaq 100",
                "IBC": "IBEX 35", "SX5E": "Euro Stoxx 50",
            }[s],
        )
        tv_widgets.stock_heatmap(source, height=560)
    with tab_crypto:
        st.caption(
            "La cripto funciona como termometro de apetito por riesgo: cuando "
            "sube con fuerza, suele haber tolerancia al riesgo en el resto del mercado."
        )
        tv_widgets.crypto_heatmap(height=520)

# ETFs sectoriales configurados, como referencia de seguimiento.
etfs = get_sector_etfs()
if etfs:
    with st.expander("ETFs sectoriales de referencia"):
        st.dataframe(
            pd.DataFrame(
                {"ETF": list(etfs), "Sector GICS": [etfs[k] for k in etfs]}
            ),
            hide_index=True,
        )

st.divider()
render_disclaimer()
