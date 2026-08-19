"""Página 4 — Ficha de valor.

Dos gráficos en pestañas y a propósito: el de TradingView (la herramienta
familiar) y el nuestro con nuestras señales dibujadas encima. La redundancia
sirve para comprobar de un vistazo que nuestros cálculos coinciden con la
referencia del mercado.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from stocks_tracker.app import data_access as da
from stocks_tracker.app.components import charts, lwc, tv_widgets
from stocks_tracker.app.components.common import (
    render_disclaimer,
    render_flags,
    render_reasons,
)
from stocks_tracker.app.components.theme import (
    STATUS,
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
kpi[5].metric("Desde máximos", format_pct(latest_ind.get("dist_52w_high"), 1, False))

# ---------------------------------------------------------------------------
# Graficos
# ---------------------------------------------------------------------------
# Nuestro grafico va primero a proposito: se dibuja siempre con datos locales.
# El widget de TradingView depende de que el navegador alcance su dominio, y si
# no lo alcanza (sin conexion, bloqueador, red restringida) la pestana sale en
# blanco. Abrir en una pestana vacia haria parecer que la aplicacion no funciona.
tab_own, tab_tv, tab_fund, tab_cost, tab_news = st.tabs(
    ["Nuestras señales", "Gráfico TradingView", "Fundamentales",
     "Lo que cuesta", "Noticias"]
)

with tab_own:
    st.caption(
        "Nuestros datos con nuestras señales marcadas, nuestras medias y los "
        "niveles que hemos calculado. Los widgets de TradingView no permiten "
        "dibujar nada encima: son sus datos, no los nuestros."
    )

    # Se dibuja con lightweight-charts, la libreria de TradingView. A diferencia
    # del widget, aqui los datos y los marcadores son nuestros.
    overlays = {
        label: indicators[col]
        for col, label in (("sma50", "MM50"), ("sma200", "MM200"))
        if col in indicators.columns and indicators[col].notna().any()
    }

    sessions = lwc.sessions_of(prices)
    markers = lwc.markers_from_signals(signals, sessions, labels)

    price_lines = []
    for col, title, color in (
        ("support_near", "soporte", STATUS["good"]),
        ("resistance_near", "resistencia", STATUS["critical"]),
    ):
        level = latest_ind.get(col)
        if level is not None and pd.notna(level):
            price_lines.append(lwc.PriceLine(price=float(level), title=title, color=color))

    panes = []
    if "rsi14" in indicators.columns:
        panes.append(
            lwc.Pane(
                name="RSI",
                series={"RSI (14)": lwc.series_to_points(indicators["date"],
                                                         indicators["rsi14"])},
                levels=[30.0, 70.0], height=110,
            )
        )
    if "macd_hist" in indicators.columns:
        panes.append(
            lwc.Pane(
                name="MACD",
                series={"Histograma MACD": lwc.series_to_points(indicators["date"],
                                                                indicators["macd_hist"])},
                kind="histogram", levels=[0.0], height=100,
            )
        )

    rendered = lwc.price_chart(
        prices, overlays=overlays, markers=markers,
        price_lines=price_lines, panes=panes, height=520,
    )

    if rendered:
        notes = [f"{len(markers)} señales marcadas"]
        if price_lines:
            notes.append(f"{len(price_lines)} niveles calculados")
        st.caption(
            " · ".join(notes)
            + ". Se muestran solo las señales de cambio de estado y las más "
            "recientes: pintarlas todas taparía las velas."
        )
    else:
        # Respaldo si falta la libreria vendorizada.
        st.plotly_chart(
            charts.price_with_signals(prices, indicators, signals, height=440),
            width="stretch", config={"displayModeBar": False},
        )

with tab_tv:
    st.caption(
        "Gráfico completo de TradingView. Si aparece en blanco, el navegador no "
        "puede alcanzar tradingview.com (sin conexion, bloqueador o red restringida): "
        "usa la pestaña **Nuestras señales**, que funciona con datos locales."
    )
    tv_widgets.advanced_chart(
        tv_symbol,
        height=760,
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
        # Antes de la tabla y no despues: un PER de 3 se lee como una ganga en
        # cuanto aparece en pantalla, y el aviso llega tarde debajo.
        revision = da.review_fundamentals(ticker)
        if not revision.fiable:
            escribir = st.error if revision.rotos else st.warning
            escribir(
                "**Estos números no cuadran.** " + " ".join(
                    a.texto for a in revision.avisos
                ) + "\n\nVienen de un único proveedor gratuito y no hay una "
                "segunda fuente con la que compararlos: contrastalos con las "
                "cuentas de la empresa antes de decidir nada con ellos.",
                icon=":material/report:" if revision.rotos
                else ":material/help:",
            )

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
            "Comparar contra la mediana del propio sector es lo único que da "
            "sentido a estos números: un PER de 12 es caro en un sector y barato en otro."
        )
        cov = fundamentals.get("completeness")
        if pd.notna(cov):
            st.caption(f"Cobertura de datos: {float(cov):.0%}")

        if tv_widgets.enabled() and tv_symbol:
            with st.expander("Estados financieros completos (TradingView)"):
                tv_widgets.fundamental_data(tv_symbol, height=560)

with tab_cost:
    # Va en la ficha del valor y no en una calculadora aparte porque es aqui
    # donde se decide comprar. Una pantalla que hay que ir a buscar no se mira.
    from stocks_tracker.app.components.cost_panel import render_cost_panel

    render_cost_panel(
        ticker=ticker,
        currency=currency,
        dividend_yield=(
            float(fundamentals["dividend_yield"]) * 100.0
            if fundamentals is not None
            and pd.notna(fundamentals.get("dividend_yield"))
            else 0.0
        ),
        country=(instrument["country"] if instrument is not None else "") or "",
    )

with tab_news:
    if tv_widgets.enabled() and tv_symbol:
        tv_widgets.top_stories(tv_symbol, height=560)
    else:
        st.caption("Noticias no disponibles sin TradingView o sin símbolo equivalente.")

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
        st.caption("Este valor no esta puntuado (índices y macro no se puntuan).")
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
    st.subheader("Por qué destaca")
    if row.empty:
        st.caption("Sin puntuación.")
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
        st.metric("Referencia técnica de stop", format_num(close - 2 * float(atr)))
        st.caption(
            "Precio menos dos veces el ATR. Es una **referencia técnica**, no una "
            "recomendación: un hueco de apertura puede saltarlo sin ejecutarse ahí."
        )
    st.metric("Caída máxima 1 año", format_pct(latest_ind.get("max_dd_1y"), 0, False))

    support = latest_ind.get("support_near")
    resistance = latest_ind.get("resistance_near")
    if pd.notna(support) or pd.notna(resistance):
        st.caption(
            "**Niveles calculados** · soporte "
            + (format_num(support) if pd.notna(support) else "—")
            + " · resistencia "
            + (format_num(resistance) if pd.notna(resistance) else "—")
        )
        st.caption(
            "Agrupación de máximos y mínimos locales del último año. Un nivel "
            "tocado varias veces pesa más que uno tocado una sola vez."
        )

    if tv_widgets.enabled() and tv_symbol:
        tv_widgets.technical_analysis(tv_symbol, height=420)

# ---------------------------------------------------------------------------
# Historial de senales
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Señales detectadas")
if signals.empty:
    st.caption("Ninguna señal en el periodo cargado.")
else:
    recent = signals.tail(25).iloc[::-1]
    view = pd.DataFrame(
        {
            "Fecha": pd.to_datetime(recent["date"]).dt.strftime("%d/%m/%Y"),
            "Señal": recent["signal_id"].map(lambda s: labels.get(s, s)),
            "Dirección": recent["direction"].map(
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
        "Sin la validación histórica de la fase 3, estas señales son "
        "observaciones sin evidencia de que aporten valor."
    )

if st.button("Añadir a la watchlist"):
    da.add_to_watchlist(ticker, price=float(latest_price["close"]))
    st.toast(f"{ticker} añadido a la watchlist")

st.divider()
render_disclaimer()
