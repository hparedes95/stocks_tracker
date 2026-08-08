"""Pagina 6 — Macro y riesgo.

El contexto en el que se mueve todo lo demas. Nada de esto sirve para acertar
un movimiento concreto: sirve para saber si el viento sopla a favor o en contra.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from stocks_tracker.app import data_access as da
from stocks_tracker.app.components import charts, tv_widgets
from stocks_tracker.app.components.common import render_disclaimer
from stocks_tracker.app.components.theme import format_num, format_pct
from stocks_tracker.core.config import get_fred_series, get_macro_config

st.title("Macro y riesgo")

regime = da.get_regime()
has_fred = da.macro_available()
series_config = get_fred_series()

# ---------------------------------------------------------------------------
# Semaforo
# ---------------------------------------------------------------------------
if regime.empty:
    st.warning("Sin datos de regimen. Ejecuta `make compute`.")
    st.stop()

latest = regime.iloc[-1]
gauge_col, detail_col = st.columns([1, 2])

with gauge_col:
    st.plotly_chart(
        charts.risk_gauge(float(latest["risk_score"]), str(latest["regime"])),
        width="stretch", config={"displayModeBar": False},
    )

with detail_col:
    st.subheader("Que empuja el semaforo")
    components = get_macro_config().get("regime_components", {})
    st.caption(
        "El semaforo es la media de varios componentes normalizados. Se muestra "
        "el desglose para que se vea **que** lo mueve, no solo el numero."
    )
    rows = []
    for key, spec in components.items():
        rows.append({"Componente": spec.get("label", key), "Peso": spec.get("weight", 1.0)})
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True, height=210)

    metrics = st.columns(3)
    metrics[0].metric("VIX", format_num(latest.get("vix"), 1))
    pctile = latest.get("vix_percentile_1y")
    metrics[1].metric(
        "Percentil del VIX",
        f"{float(pctile):.0%}" if pd.notna(pctile) else "—",
        help="Posicion del VIX dentro del ultimo ano.",
    )
    metrics[2].metric(
        "Valores sobre la MM200",
        f"{float(latest['pct_above_sma200']):.0f}%"
        if pd.notna(latest.get("pct_above_sma200")) else "—",
    )

st.divider()

# ---------------------------------------------------------------------------
# Historico del semaforo
# ---------------------------------------------------------------------------
st.subheader("Historico del semaforo")
history = regime.rename(columns={"risk_score": "value"})[["date", "value"]]
st.plotly_chart(
    charts.macro_series(history, "Score de riesgo (-100 aversion / +100 apetito)",
                        zero_line=True, height=280),
    width="stretch", config={"displayModeBar": False},
)

# ---------------------------------------------------------------------------
# Series de FRED
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Tipos, credito y actividad")

if not has_fred:
    st.info(
        "Las series de tipos, credito y actividad vienen de FRED y necesitan una "
        "clave gratuita.\n\n"
        "1. Consiguela en https://fred.stlouisfed.org/docs/api/api_key.html\n"
        "2. Ponla en `.env` como `FRED_API_KEY=...`\n"
        "3. Ejecuta `make ingest`\n\n"
        "El resto del dashboard funciona sin ella: ningun calculo del nucleo "
        "depende de esta clave.",
        icon=":material/key:",
    )
else:
    groups: dict[str, list[str]] = {}
    for series_id, spec in series_config.items():
        groups.setdefault(spec.get("group", "otros"), []).append(series_id)

    group_labels = {
        "tipos": "Tipos y curva", "riesgo": "Credito y estres",
        "actividad": "Actividad y precios", "divisa": "Divisa",
    }
    tabs = st.tabs([group_labels.get(g, g.title()) for g in groups])

    for tab, ids in zip(tabs, groups.values(), strict=False):
        with tab:
            data = da.get_macro_series(tuple(ids))
            if data.empty:
                st.caption("Sin datos descargados para este grupo.")
                continue
            for series_id in ids:
                subset = data[data["series_id"] == series_id]
                if subset.empty:
                    continue
                spec = series_config.get(series_id, {})
                st.plotly_chart(
                    charts.macro_series(
                        subset, spec.get("name", series_id),
                        zero_line=(series_id == "T10Y2Y"),
                        mark_negative=(series_id == "T10Y2Y"), height=230,
                    ),
                    width="stretch", config={"displayModeBar": False},
                    key=f"macro_{series_id}",
                )
                note = spec.get("note")
                if note:
                    st.caption(note)

# ---------------------------------------------------------------------------
# Termometros de precio (no dependen de FRED)
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Termometros de mercado")
st.caption(
    "Estos salen de los precios, asi que estan disponibles siempre, con o sin "
    "clave de FRED."
)

macro_tickers = ("GC=F", "HG=F", "CL=F", "DX-Y.NYB", "BTC-USD", "^VIX")
macro_prices = da.get_macro_prices(macro_tickers)

if macro_prices.empty:
    st.caption("Sin series macro descargadas.")
else:
    names = {
        "GC=F": "Oro", "HG=F": "Cobre", "CL=F": "Petroleo",
        "DX-Y.NYB": "Dolar", "BTC-USD": "Bitcoin", "^VIX": "VIX",
    }
    kpis = []
    for ticker in macro_tickers:
        subset = macro_prices[macro_prices["ticker"] == ticker].sort_values("date")
        if len(subset) < 65:
            continue
        last = float(subset.iloc[-1]["adj_close"])
        prev = float(subset.iloc[-2]["adj_close"])
        quarter = float(subset.iloc[-64]["adj_close"])
        kpis.append(
            {
                "label": names.get(ticker, ticker),
                "value": format_num(last, 2),
                "delta": format_pct((last / prev) - 1),
                "help": f"3 meses: {format_pct((last / quarter) - 1)}",
            }
        )
    if kpis:
        cols = st.columns(min(6, len(kpis)))
        for i, item in enumerate(kpis):
            with cols[i % len(cols)]:
                st.metric(item["label"], item["value"], item["delta"], help=item["help"])

    # Cobre/oro: proxy de crecimiento frente a miedo. El cobre se usa en obra y
    # fabricacion; el oro es refugio. Su cociente resume esa tension mejor que
    # cualquiera de los dos por separado.
    copper = macro_prices[macro_prices["ticker"] == "HG=F"].set_index("date")["adj_close"]
    gold = macro_prices[macro_prices["ticker"] == "GC=F"].set_index("date")["adj_close"]
    if not copper.empty and not gold.empty:
        ratio = (copper / gold.reindex(copper.index).ffill()).dropna()
        if not ratio.empty:
            frame = pd.DataFrame({"date": ratio.index, "value": ratio.to_numpy()})
            st.plotly_chart(
                charts.macro_series(frame, "Cobre / oro (crecimiento frente a miedo)",
                                    height=250),
                width="stretch", config={"displayModeBar": False},
            )

# ---------------------------------------------------------------------------
# Correlacion media
# ---------------------------------------------------------------------------
breadth = da.get_breadth()
if not breadth.empty and breadth["avg_pairwise_corr"].notna().any():
    st.divider()
    st.subheader("Correlacion media entre valores")
    st.plotly_chart(
        charts.correlation_line(breadth, height=260),
        width="stretch", config={"displayModeBar": False},
    )
    st.caption(
        "Cuando la correlacion sube, el mercado se mueve en bloque por razones "
        "macro y elegir valores concretos aporta poco: casi todo sube o baja "
        "junto. Cuando baja, las diferencias entre empresas pesan mas."
    )

if tv_widgets.enabled():
    st.divider()
    with st.expander("Calendario economico (TradingView)"):
        tv_widgets.economic_calendar(height=520)

st.divider()
render_disclaimer()
