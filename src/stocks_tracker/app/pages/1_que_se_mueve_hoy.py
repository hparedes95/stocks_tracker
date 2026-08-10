"""Pagina 1 — Que se mueve hoy.

Responde a la pregunta que el usuario se hace al abrir el dashboard: que esta
pasando y que merece que lo mire. No a "que va a pasar".
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from stocks_tracker.app import data_access as da
from stocks_tracker.app.components import charts, tv_widgets
from stocks_tracker.app.components.common import (
    metric_row,
    movers_table,
    render_disclaimer,
    sidebar_filters,
)
from stocks_tracker.app.components.theme import STATUS, format_pct
from stocks_tracker.core.config import get_explanations
from stocks_tracker.core.narrative import MarketContext, render_market_summary

st.title("Que se mueve hoy")

filters = sidebar_filters("mueve")
universe = filters["universe"]

last_date = da.last_price_date()
if last_date is None:
    st.stop()

labels = get_explanations().get("signal_labels", {})

# ---------------------------------------------------------------------------
# Datos
# ---------------------------------------------------------------------------
gainers = da.get_movers(universe, n=10, ascending=False)
losers = da.get_movers(universe, n=10, ascending=True)
breakouts_high = da.get_breakouts_52w(universe, high=True)
breakouts_low = da.get_breakouts_52w(universe, high=False)
volume_spikes = da.get_volume_spikes(universe, threshold=2.0)
trend_changes = da.get_trend_changes(universe)
sectors = da.get_sector_performance()
breadth = da.get_breadth("SP100")
regime = da.get_regime()
kpis = da.get_market_kpis()

# ---------------------------------------------------------------------------
# Bloque 0 — Resumen en lenguaje natural
# ---------------------------------------------------------------------------
ctx = MarketContext(date=last_date, universe=universe)

if not sectors.empty:
    ordered = sectors.dropna(subset=["ret_1d"]).sort_values("ret_1d", ascending=False)
    if not ordered.empty:
        ctx.sector_leaders = [(r.sector, float(r.ret_1d)) for r in ordered.head(3).itertuples()]
        ctx.sector_laggards = [(r.sector, float(r.ret_1d)) for r in ordered.tail(3).itertuples()][::-1]

ctx.n_breakouts_high = len(breakouts_high)
ctx.n_breakouts_low = len(breakouts_low)
ctx.n_volume_spikes = len(volume_spikes)

if not breadth.empty:
    latest = breadth.iloc[-1]
    ctx.pct_above_sma200 = float(latest["pct_above_sma200"])
    ctx.advances = int(latest["advances"])
    ctx.declines = int(latest["declines"])
    if len(breadth) > 5:
        ctx.pct_above_sma200_prev_week = float(breadth.iloc[-6]["pct_above_sma200"])

if not regime.empty:
    latest_regime = regime.iloc[-1]
    ctx.regime = str(latest_regime["regime"])
    ctx.risk_score = float(latest_regime["risk_score"])
    ctx.vix = float(latest_regime["vix"]) if pd.notna(latest_regime["vix"]) else None
    ctx.vix_pctile = (
        float(latest_regime["vix_percentile_1y"])
        if pd.notna(latest_regime["vix_percentile_1y"]) else None
    )
    if len(regime) > 1:
        ctx.risk_score_prev = float(regime.iloc[-2]["risk_score"])

if not kpis.empty:
    index_row = kpis[kpis["ticker"] == "^GSPC"]
    if not index_row.empty and pd.notna(index_row.iloc[0]["ret_1d"]):
        ctx.index_ret_1d = float(index_row.iloc[0]["ret_1d"])

if not trend_changes.empty:
    ctx.top_signal_counts = trend_changes["signal_id"].value_counts().to_dict()

summary = render_market_summary(ctx, labels)
st.info("  \n".join(f"· {line}" for line in summary))

# ---------------------------------------------------------------------------
# Indicadores de cabecera
# ---------------------------------------------------------------------------
# Dos filas a proposito, y la distincion importa: la de TradingView se mueve
# ahora mismo pero no sabe nada de nuestros calculos; la nuestra es del cierre
# y es la que usan el ranking, las senales y las alertas. Mezclarlas en una
# sola daria un numero que no se sabe de cuando es.
if tv_widgets.enabled():
    live_tab, close_tab = st.tabs(["En vivo", "Al cierre"])
else:
    live_tab, close_tab = None, st.container()

if live_tab is not None:
    with live_tab:
        # El texto va ANTES del widget a proposito. El widget es un iframe de
        # TradingView: si la red lo bloquea o tarda, deja un hueco en blanco de
        # 380 px sin decir nada, y esta es la primera pestana que ve cualquiera
        # al abrir el programa. Un aviso que siempre se pinta convierte ese
        # vacio en algo comprensible.
        # El aviso NO dice "tu red lo bloquea". Lo dijo en su primera version y
        # mando a diagnosticar la red cuando el hueco en blanco lo causaba una
        # clave mal puesta en nuestra configuracion. Un widget vacio tiene dos
        # causas posibles y desde fuera son identicas, asi que el texto propone
        # la comprobacion que las separa en lugar de dar por buena una.
        st.caption(
            ":grey[Precios **en directo** de TradingView. No alimentan el "
            "analisis: el ranking, las senales y las alertas se calculan sobre "
            "el cierre del dia, que es la pestana de al lado. El S&P 500, el "
            "Nasdaq y el VIX se muestran a traves de contratos que los "
            "replican, porque los indices oficiales no estan disponibles en la "
            "version gratuita; fuera del horario de Wall Street pueden marcar "
            "unas decimas distintas del valor oficial. Si sale un hueco vacio, "
            "mira el grafico de velas de la ficha de cualquier valor: si ese si "
            "se ve, el fallo es de esta pantalla; si tampoco, tu red esta "
            "bloqueando TradingView.]"
        )
        tv_widgets.market_overview(height=380)

with close_tab:
    if not kpis.empty:
        wanted = ["^GSPC", "^NDX", "^IBEX", "^STOXX50E", "^VIX", "BTC-USD",
                  "GC=F", "CL=F"]
        names = {
            "^GSPC": "S&P 500", "^NDX": "Nasdaq 100", "^IBEX": "IBEX 35",
            "^STOXX50E": "Euro Stoxx 50", "^VIX": "VIX", "BTC-USD": "Bitcoin",
            "GC=F": "Oro", "CL=F": "Petroleo",
        }
        items = []
        for ticker in wanted:
            row = kpis[kpis["ticker"] == ticker]
            if row.empty:
                continue
            r = row.iloc[0]
            items.append(
                {
                    "label": names.get(ticker, ticker),
                    "value": f"{float(r['close']):,.2f}".replace(",", " "),
                    "delta": format_pct(r["ret_1d"]),
                }
            )
        if items:
            metric_row(items, columns=min(4, len(items)))
            st.caption(
                f":grey[Cierre del {last_date:%d/%m/%Y}. Estos son los numeros "
                "con los que se calculan el ranking, las senales y las alertas.]"
            )

st.divider()

# ---------------------------------------------------------------------------
# Pulso del mercado
# ---------------------------------------------------------------------------
pulse_left, pulse_right = st.columns([1, 2])

with pulse_left:
    st.subheader("Pulso del mercado")
    if not regime.empty:
        latest_regime = regime.iloc[-1]
        st.plotly_chart(
            charts.risk_gauge(
                float(latest_regime["risk_score"]), str(latest_regime["regime"])
            ),
            width="stretch",
            config={"displayModeBar": False},
        )
        st.caption(
            "Combina VIX, amplitud, cobre/oro y el comportamiento relativo de "
            "activos ciclicos frente a defensivos. Es una lectura del clima "
            "actual, no una prevision."
        )
    else:
        st.caption("Sin datos de regimen todavia.")

with pulse_right:
    st.subheader("Amplitud del mercado")
    if not breadth.empty:
        st.plotly_chart(
            charts.breadth_lines(breadth),
            width="stretch",
            config={"displayModeBar": False},
        )
        latest = breadth.iloc[-1]
        cols = st.columns(4)
        cols[0].metric("Sobre la MM200", f"{latest['pct_above_sma200']:.0f}%")
        cols[1].metric("Suben / bajan", f"{int(latest['advances'])} / {int(latest['declines'])}")
        cols[2].metric("Nuevos maximos 52s", int(latest["new_highs_52w"]))
        cols[3].metric("Nuevos minimos 52s", int(latest["new_lows_52w"]))
    else:
        st.caption("Sin datos de amplitud todavia.")

st.divider()

# ---------------------------------------------------------------------------
# Mayores movimientos
# ---------------------------------------------------------------------------
st.subheader("Mayores movimientos del dia")
left, right = st.columns(2)

with left:
    st.markdown("**Suben** :green[▲]")
    movers_table(gainers)

with right:
    st.markdown("**Bajan** :red[▼]")
    movers_table(losers)

# ---------------------------------------------------------------------------
# Rupturas, volumen y cambios de tendencia
# ---------------------------------------------------------------------------
st.divider()
tab_break, tab_vol, tab_trend = st.tabs(
    [
        f"Rupturas anuales ({len(breakouts_high) + len(breakouts_low)})",
        f"Volumen inusual ({len(volume_spikes)})",
        f"Cambios de tendencia ({len(trend_changes)})",
    ]
)

with tab_break:
    st.caption(
        "Valores que hoy rompen su maximo o minimo de 52 semanas. Se excluyen "
        "los que ya llevaban dias en esa zona: lo informativo es el cruce, no el estado."
    )
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Rompen maximos**")
        movers_table(breakouts_high, height=260)
    with c2:
        st.markdown("**Perforan minimos**")
        movers_table(breakouts_low, height=260)

with tab_vol:
    st.caption(
        "Mas del doble de su volumen habitual. El volumen es un evento, no una "
        "direccion: mira la columna del dia para distinguir acumulacion de capitulacion."
    )
    movers_table(volume_spikes, height=380)

with tab_trend:
    if trend_changes.empty:
        st.caption("Ninguna senal de cambio de tendencia hoy.")
    else:
        st.caption(
            "Senales disparadas hoy. Sin validacion historica (fase 3) son "
            "observaciones, no recomendaciones."
        )
        bull = trend_changes[trend_changes["direction"] == "bullish"]
        bear = trend_changes[trend_changes["direction"] == "bearish"]
        c1, c2 = st.columns(2)
        for col, data, title, color in (
            (c1, bull, "Alcistas", STATUS["good"]),
            (c2, bear, "Bajistas", STATUS["critical"]),
        ):
            with col:
                st.markdown(f"**{title}** ({len(data)})")
                if data.empty:
                    st.caption("Ninguna.")
                    continue
                view = pd.DataFrame(
                    {
                        "Ticker": data["ticker"],
                        "Nombre": data["name"].fillna(""),
                        "Senal": data["signal_id"].map(lambda s: labels.get(s, s)),
                        "Fuerza": data["strength"],
                    }
                )
                st.dataframe(
                    view, hide_index=True, height=300,
                    column_config={
                        "Fuerza": st.column_config.ProgressColumn(
                            min_value=0.0, max_value=1.0, format="%.2f"
                        )
                    },
                )
                del color

# ---------------------------------------------------------------------------
# Sectores
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Sectores lideres y rezagados")

if sectors.empty:
    st.caption("Sin datos sectoriales todavia.")
else:
    horizon = st.radio(
        "Horizonte",
        options=["ret_1d", "ret_5d", "ret_1m", "ret_3m"],
        format_func=lambda c: {"ret_1d": "Dia", "ret_5d": "Semana",
                               "ret_1m": "Mes", "ret_3m": "Trimestre"}[c],
        horizontal=True,
        label_visibility="collapsed",
    )
    st.plotly_chart(
        charts.sector_bars(sectors, horizon),
        width="stretch",
        config={"displayModeBar": False},
    )
    st.caption(
        "Mediana del sector, no media: asi un solo valor disparado por una "
        "operacion corporativa no arrastra a todo el grupo."
    )

    if tv_widgets.enabled():
        with st.expander("Mapa de calor de TradingView"):
            tv_widgets.stock_heatmap("SPX500")

st.divider()
render_disclaimer()
