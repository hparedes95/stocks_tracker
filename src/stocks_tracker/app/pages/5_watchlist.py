"""Pagina 5 — Watchlist."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from stocks_tracker.app import data_access as da
from stocks_tracker.app.components import tv_widgets
from stocks_tracker.app.components.common import render_disclaimer
from stocks_tracker.app.components.theme import format_pct

st.title("Watchlist")

watchlist = da.get_watchlist()

add_col, _ = st.columns([2, 3])
with add_col:
    options = [t for t in da.all_tickers() if t not in set(watchlist["ticker"])]
    if options:
        new_ticker = st.selectbox("Anadir un valor", options=options, index=None,
                                  placeholder="Busca un ticker")
        if new_ticker and st.button("Anadir", type="primary"):
            history = da.get_price_history(new_ticker, days=1)
            price = float(history.iloc[-1]["close"]) if not history.empty else None
            da.add_to_watchlist(new_ticker, price=price)
            st.rerun()

if watchlist.empty:
    st.info(
        "Watchlist vacia. Anade valores desde aqui o con el boton "
        "**+ Watchlist** de la pagina de oportunidades."
    )
    render_disclaimer()
    st.stop()

# Variacion desde que se anadio: es la unica metrica que dice si seguir un valor
# estaba justificado, y por eso se guarda el precio de entrada.
watchlist = watchlist.copy()
watchlist["desde_alta"] = (
    (watchlist["close"] - watchlist["added_price"]) / watchlist["added_price"]
).where(watchlist["added_price"].notna() & (watchlist["added_price"] > 0))

view = pd.DataFrame(
    {
        "Ticker": watchlist["ticker"],
        "Nombre": watchlist["name"].fillna(""),
        "Sector": watchlist["gics_sector"].fillna("—"),
        "Precio": watchlist["close"],
        "Dia": watchlist["ret_1d"] * 100,
        "Desde alta": watchlist["desde_alta"] * 100,
        "Percentil": watchlist["composite_pctile"],
        "Anadido": pd.to_datetime(watchlist["added_at"]).dt.strftime("%d/%m/%Y"),
    }
)
st.dataframe(
    view, hide_index=True, height=380,
    column_config={
        "Precio": st.column_config.NumberColumn(format="%.2f"),
        "Dia": st.column_config.NumberColumn(format="%+.2f%%"),
        "Desde alta": st.column_config.NumberColumn(format="%+.2f%%"),
        "Percentil": st.column_config.ProgressColumn(
            min_value=0.0, max_value=1.0, format="%.0f%%"
        ),
    },
)

summary = st.columns(3)
tracked = len(watchlist)
positive = int((watchlist["desde_alta"] > 0).sum())
avg = watchlist["desde_alta"].mean()
summary[0].metric("Valores seguidos", tracked)
summary[1].metric("En positivo desde el alta", f"{positive} de {tracked}")
summary[2].metric("Variacion media", format_pct(avg))

st.divider()
detail_col, remove_col = st.columns([3, 1])

with remove_col:
    st.subheader("Quitar")
    to_remove = st.selectbox("Valor", options=watchlist["ticker"].tolist(),
                             key="remove_ticker")
    if st.button("Quitar de la watchlist"):
        da.remove_from_watchlist(to_remove)
        st.rerun()

with detail_col:
    st.subheader("Vistazo rapido")
    chosen = st.selectbox("Valor", options=watchlist["ticker"].tolist(),
                          key="preview_ticker")
    tv_symbol = da.get_tv_symbol(chosen)
    if tv_widgets.enabled() and tv_symbol:
        tv_widgets.mini_symbol_overview(tv_symbol, height=220)
    else:
        st.caption("Sin equivalencia en TradingView para este valor.")

st.divider()
render_disclaimer()
