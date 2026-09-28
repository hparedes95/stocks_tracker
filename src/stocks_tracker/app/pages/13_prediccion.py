"""Investigación predictiva con datos, supuestos y límites visibles."""

import streamlit as st

from stocks_tracker.core.db import connect
from stocks_tracker.core.forecast import scenarios

st.title("Predicción y escenarios")
st.caption("Explora qué ocurrió después de tendencias similares. "
           "Estimación histórica experimental, todavía sin ventaja predictiva validada.")

with connect(read_only=True) as conn:
    instruments = conn.execute(
        "SELECT ticker, name FROM instruments WHERE asset_class IN ('equity', 'etf', 'index') "
        "ORDER BY ticker"
    ).fetchdf()
if instruments.empty:
    st.info("Descarga datos desde el asesor para empezar a analizar acciones y mercados.")
    st.stop()

ticker = st.selectbox("Acción o índice", instruments["ticker"].tolist())
horizon = st.selectbox("Horizonte", [21, 63, 126], index=1,
                       format_func=lambda n: f"{n} sesiones (aprox. {n // 21} meses)")
with connect(read_only=True) as conn:
    prices = conn.execute(
        "SELECT date, adj_close, source FROM prices_daily WHERE ticker = ? ORDER BY date",
        [ticker],
    ).fetchdf()
result = scenarios(prices, horizon=horizon)
if not prices.empty:
    st.caption("Fuente del histórico: " +
               ", ".join(sorted(prices["source"].dropna().astype(str).unique())) +
               ". No implica contraste independiente de todos los precios.")
if result.as_of is not None:
    st.caption(f"Datos hasta {result.as_of:%d/%m/%Y} · {result.trend} · "
               f"{result.observations} episodios sin solapamiento")
if result.reason:
    st.warning(result.reason)
else:
    a, b, c = st.columns(3)
    a.metric("Percentil histórico 10", f"{result.low:+.1%}")
    b.metric("Mediana histórica", f"{result.median:+.1%}")
    c.metric("Percentil histórico 90", f"{result.high:+.1%}")
    st.info("Estos números describen retornos pasados de este activo; no son objetivos "
            "de precio ni probabilidades calibradas. Son brutos, en la divisa del activo, "
            "sin comisiones ni cambio a euros. El futuro puede quedar fuera del rango.")
    if result.historical_up_frequency is not None:
        st.caption(f"Hubo subidas en el {result.historical_up_frequency:.0%} de la muestra. "
                   "Frecuencia histórica, no probabilidad de la próxima subida.")
    with st.expander("Episodios utilizados y método"):
        st.write("Misma posición respecto a la media de 200 sesiones. Entrada al cierre "
                 "siguiente y salida al horizonte seleccionado. No usa datos posteriores "
                 "a la fecha de corte y no selecciona episodios por su rentabilidad.")
        st.dataframe(result.episodes, hide_index=True)

st.caption("Las propuestas de compra o venta se consultan en el asesor, junto con la "
           "cartera y la calidad de los datos. Estos escenarios no ejecutan operaciones.")
