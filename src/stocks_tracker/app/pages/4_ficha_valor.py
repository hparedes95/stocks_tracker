"""Pagina 4 — Ficha de valor.

Dos graficos en pestanas y a proposito: el de TradingView (la herramienta
familiar) y el nuestro con nuestras senales dibujadas encima. La redundancia
sirve para comprobar de un vistazo que nuestros calculos coinciden con la
referencia del mercado.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from stocks_tracker.app import data_access as da
from stocks_tracker.app.components import charts, tv_widgets
from stocks_tracker.app.components.common import (
    render_disclaimer,
    render_flags,
    render_reasons,
)
from stocks_tracker.app.components.theme import (
    format_market_cap,
    format_num,
    format_pct,
)
from stocks_tracker.core.config import get_explanations
from stocks_tracker.core.explain import build_reasons
from stocks_tracker.core.flags import red_flags

st.title("Ficha de valor")

tickers = da.all_tickers()
if not tickers:
    st.warning("No hay instrumentos cargados.")
    st.stop()

default = st.session_state.get("selected_ticker", tickers[0])
if default not in tickers:
    default = tickers[0]

ticker = st.selectbox(
    "Valor", options=tickers, index=tickers.index(default),
    key="selected_ticker",
)

instrument = da.get_instrument(ticker)
prices = da.get_price_history(ticker, days=500)
indicators = da.get_indicator_history(ticker, days=500)
signals = da.get_signal_history(ticker, days=500)
fundamentals = da.get_fundamentals(ticker)
tv_symbol = da.get_tv_symbol(ticker)
labels = get_explanations().get("signal_labels", {})

if prices.empty:
    st.warning(f"Sin precios para {ticker}.")
    st.stop()

latest_ind = indicators.iloc[-1] if not indicators.empty else pd.Series(dtype=float)
latest_price = prices.iloc[-1]

# ---------------------------------------------------------------------------
# Cabecera
# ---------------------------------------------------------------------------
name = instrument["name"] if instrument is not None else ticker
sector = (instrument["gics_sector"] if instrument is not None else None) or "Sin sector"
currency = (instrument["currency"] if instrument is not None else "USD") or "USD"

st.subheader(f"{ticker} · {name}")
st.caption(f"{sector} · {currency}" + (f" · {tv_symbol}" if tv_symbol else " · sin equivalencia en TradingView"))

kpi = st.columns(6)
kpi[0].metric("Precio", format_num(latest_price["close"]),
              format_pct(latest_ind.get("ret_1d")))
kpi[1].metric("1 mes", format_pct(latest_ind.get("roc_1m"), with_sign=False),
              format_pct(latest_ind.get("roc_1m")))
kpi[2].metric("12 meses", format_pct(latest_ind.get("roc_12m"), with_sign=False),
              format_pct(latest_ind.get("roc_12m")))
kpi[3].metric("RSI (14)", format_num(latest_ind.get("rsi14"), 0))
kpi[4].metric("Vol. anual", format_pct(latest_ind.get("realized_vol_252"), 0, False))
kpi[5].metric("Desde maximos", format_pct(latest_ind.get("dist_52w_high"), 1, False))

# ---------------------------------------------------------------------------
# Graficos
# ---------------------------------------------------------------------------
# Nuestro grafico va primero a proposito: se dibuja siempre con datos locales.
# El widget de TradingView depende de que el navegador alcance su dominio, y si
# no lo alcanza (sin conexion, bloqueador, red restringida) la pestana sale en
# blanco. Abrir en una pestana vacia haria parecer que la aplicacion no funciona.
tab_own, tab_tv, tab_fund, tab_news = st.tabs(
    ["Nuestras senales", "Grafico TradingView", "Fundamentales", "Noticias"]
)

with tab_own:
    st.caption(
        "Nuestros datos con nuestras senales marcadas. Los widgets de "
        "TradingView no permiten dibujar nada encima: son sus datos, no los nuestros."
    )
    st.plotly_chart(
        charts.price_with_signals(prices, indicators, signals, height=440),
        width="stretch",
        config={"displayModeBar": False},
    )
    osc_left, osc_right = st.columns(2)
    with osc_left:
        st.plotly_chart(
            charts.oscillator_panel(indicators, "rsi14", "RSI (14)", bands=(30, 70)),
            width="stretch", config={"displayModeBar": False},
        )
    with osc_right:
        st.plotly_chart(
            charts.oscillator_panel(indicators, "macd_hist", "Histograma MACD", bands=(0,)),
            width="stretch", config={"displayModeBar": False},
        )

with tab_tv:
    st.caption(
        "Grafico completo de TradingView. Si aparece en blanco, el navegador no "
        "puede alcanzar tradingview.com (sin conexion, bloqueador o red restringida): "
        "usa la pestana **Nuestras senales**, que funciona con datos locales."
    )
    tv_widgets.advanced_chart(
        tv_symbol,
        height=560,
        fallback=lambda: st.plotly_chart(
            charts.price_with_signals(prices, indicators, signals, height=460),
            width="stretch",
            config={"displayModeBar": False},
        ),
    )

with tab_fund:
    if fundamentals is None:
        st.caption("Sin datos fundamentales para este valor.")
    else:
        medians = da.get_sector_medians(sector)
        rows = [
            ("PER", "trailing_pe", "{:.1f}"),
            ("Precio / valor contable", "price_to_book", "{:.2f}"),
            ("EV / EBITDA", "ev_to_ebitda", "{:.1f}"),
            ("Precio / ventas", "price_to_sales", "{:.2f}"),
            ("Margen neto", "profit_margin", "{:.1%}"),
            ("ROE", "roe", "{:.1%}"),
            ("Crecimiento de ingresos", "revenue_growth_yoy", "{:+.1%}"),
            ("Deuda neta / EBITDA", "net_debt_to_ebitda", "{:.1f}"),
            ("Rentabilidad por dividendo", "dividend_yield", "{:.2%}"),
            ("Payout", "payout_ratio", "{:.0%}"),
        ]
        table = []
        for label, field, fmt in rows:
            value = fundamentals.get(field)
            median = medians.get(field) if medians is not None else None
            table.append(
                {
                    "Metrica": label,
                    "Valor": fmt.format(value) if pd.notna(value) else "—",
                    f"Mediana de {sector}": (
                        fmt.format(median) if median is not None and pd.notna(median) else "—"
                    ),
                }
            )
        st.dataframe(pd.DataFrame(table), hide_index=True, height=390)
        st.caption(
            "Comparar contra la mediana del propio sector es lo unico que da "
            "sentido a estos numeros: un PER de 12 es caro en un sector y barato en otro."
        )
        cov = fundamentals.get("completeness")
        if pd.notna(cov):
            st.caption(f"Cobertura de datos: {float(cov):.0%}")

        if tv_widgets.enabled() and tv_symbol:
            with st.expander("Estados financieros completos (TradingView)"):
                tv_widgets.fundamental_data(tv_symbol, height=440)

with tab_news:
    if tv_widgets.enabled() and tv_symbol:
        tv_widgets.top_stories(tv_symbol, height=480)
    else:
        st.caption("Noticias no disponibles sin TradingView o sin simbolo equivalente.")

# ---------------------------------------------------------------------------
# Perfil factorial y explicacion
# ---------------------------------------------------------------------------
st.divider()
candidates = da.get_candidates("TODOS", (), limit=1000)
row = candidates[candidates["ticker"] == ticker]

profile_col, reasons_col, extra_col = st.columns([1, 1.2, 1])

with profile_col:
    st.subheader("Perfil factorial")
    if row.empty:
        st.caption("Este valor no esta puntuado (indices y macro no se puntuan).")
    else:
        r = row.iloc[0]
        scores = {
            k: float(r[k]) for k in
            ["value_z", "growth_z", "quality_z", "momentum_z",
             "lowvol_z", "dividend_z", "technical_z"]
            if k in r.index and pd.notna(r[k])
        }
        st.plotly_chart(
            charts.factor_radar(scores), width="stretch",
            config={"displayModeBar": False},
        )

with reasons_col:
    st.subheader("Por que destaca")
    if row.empty:
        st.caption("Sin puntuacion.")
    else:
        r = row.iloc[0]
        contributions = da.get_contributions(ticker)
        active = da.get_active_signals(ticker)
        reasons = build_reasons(
            r, contributions=contributions, active_signals=active,
            sector_medians=da.get_sector_medians(sector),
        )
        if reasons.is_empty:
            st.caption("Sin motivos suficientes para justificarlo con datos.")
        else:
            render_reasons(reasons)
        flags = red_flags(r)
        if flags:
            st.markdown("**Banderas rojas**")
            render_flags(flags)

with extra_col:
    st.subheader("Riesgo")
    atr = latest_ind.get("atr14")
    close = latest_price["close"]
    st.metric("ATR (14)", format_num(atr))
    if pd.notna(atr) and pd.notna(close):
        st.metric("Referencia tecnica de stop", format_num(close - 2 * float(atr)))
        st.caption(
            "Precio menos dos veces el ATR. Es una **referencia tecnica**, no una "
            "recomendacion: un hueco de apertura puede saltarlo sin ejecutarse ahi."
        )
    st.metric("Caida maxima 1 ano", format_pct(latest_ind.get("max_dd_1y"), 0, False))

    if tv_widgets.enabled() and tv_symbol:
        tv_widgets.technical_analysis(tv_symbol, height=360)

# ---------------------------------------------------------------------------
# Historial de senales
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Senales detectadas")
if signals.empty:
    st.caption("Ninguna senal en el periodo cargado.")
else:
    recent = signals.tail(25).iloc[::-1]
    view = pd.DataFrame(
        {
            "Fecha": pd.to_datetime(recent["date"]).dt.strftime("%d/%m/%Y"),
            "Senal": recent["signal_id"].map(lambda s: labels.get(s, s)),
            "Direccion": recent["direction"].map(
                {"bullish": "Alcista", "bearish": "Bajista", "neutral": "Neutral"}
            ),
            "Fuerza": recent["strength"],
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
    st.caption(
        "Sin la validacion historica de la fase 3, estas senales son "
        "observaciones sin evidencia de que aporten valor."
    )

if st.button("Anadir a la watchlist"):
    da.add_to_watchlist(ticker, price=float(latest_price["close"]))
    st.toast(f"{ticker} anadido a la watchlist")

del format_market_cap
st.divider()
render_disclaimer()
