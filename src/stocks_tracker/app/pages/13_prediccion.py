"""Investigación predictiva con datos, supuestos y límites visibles."""

import streamlit as st

from stocks_tracker.core.db import connect
from stocks_tracker.core.forecast import scenarios
from stocks_tracker.core.market_bundle import state
from stocks_tracker.core.predictive_validation import validate_models
from stocks_tracker.core.trading_calendar import calendar_name, check_history

st.title("Predicción y escenarios")
st.caption("Explora qué ocurrió después de tendencias similares. "
           "Estimación histórica experimental, todavía sin ventaja predictiva validada.")

with connect(read_only=True) as conn:
    instruments = conn.execute(
        "SELECT ticker, name, country, exchange FROM instruments WHERE asset_class IN ('equity', 'etf', 'index') "
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
    shared = state(conn)
cutoff = shared.get('as_of') if shared.get('frozen') else None
instrument = instruments.set_index('ticker').loc[ticker]
prices.attrs['calendar'] = calendar_name(instrument['country'], instrument['exchange'])
result = scenarios(prices, horizon=horizon, as_of=cutoff)
if st.button("Solicitar más histórico gratuito de este valor", disabled=bool(shared.get('frozen'))):
    from stocks_tracker.app.research_actions import extend_history
    try:
        with st.spinner("Solicitando hasta treinta años para este valor, su referencia y su divisa…"):
            extend_history(ticker)
        st.cache_data.clear()
        st.info("Solicitud terminada. Vuelve a cargar la página para ver la cobertura. No todos los valores "
                "tienen treinta años disponibles. Se conserva una pausa de siete días entre solicitudes repetidas.")
    except Exception as exc:  # noqa: BLE001 — descarga solicitada por el usuario
        st.error(str(exc))
if cutoff:
    st.warning(f"Análisis histórico fijado a la copia del {cutoff}. No es una cotización actual.")
if not prices.empty:
    st.caption(f"Histórico disponible: {prices['date'].min():%d/%m/%Y} a "
               f"{prices['date'].max():%d/%m/%Y} · {len(prices):,} precios.")
    if prices.attrs['calendar']:
        coverage = check_history(prices, prices.attrs['calendar'])
        st.caption(f"Calendario {prices.attrs['calendar']} · "
                   f"{len(coverage['missing'])} sesiones sin precio dentro del histórico.")
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

with st.expander("¿Predice mejor que una referencia sencilla?", expanded=True):
    if st.button("Evaluar modelos sobre periodos posteriores al entrenamiento"):
        with st.spinner("Evaluación temporal sin utilizar el futuro para entrenar…"):
            validation = validate_models(prices, horizon=horizon, as_of=cutoff)
        st.info(validation.reason)
        if not validation.metrics.empty:
            st.dataframe(validation.metrics, hide_index=True)
            st.caption("Menor error absoluto y menor Brier son mejores. La cobertura mide cuántas "
                       "veces el retorno quedó entre los percentiles previstos. Estos resultados "
                       "no convierten las frecuencias en probabilidades futuras calibradas. "
                       "No se selecciona automáticamente el mejor modelo del pasado.")
            st.dataframe(validation.trials, hide_index=True)

with st.expander("Simular el asesor completo sin tocar mi cartera"):
    import dataclasses

    import pandas as pd

    from stocks_tracker.backtest.advisor_replay import ReplaySpec, run_recorded
    from stocks_tracker.core.config import get_settings
    from stocks_tracker.core.investor_profile import load as load_profile
    from stocks_tracker.core.scoring import preset_hash

    st.warning("Investigación parcial, no validación de rentabilidad. Reutiliza las reglas reales del asesor "
               "con una cartera simulada vacía. Necesita rankings guardados en su fecha; descargar precios "
               "antiguos no crea fundamentales ni decisiones que se conocieran entonces.")
    st.caption("Referencia fijada: VT en EUR y efectivo al 0 %. Capital: 10.000 EUR. Decisión cada 21 sesiones; "
               "ejecución al cierre siguiente. Costes de tu perfil y deslizamiento supuesto del 0,1 % por lado. "
               "Incluye tres ensayos predefinidos: base, costes dobles y una sesión de retraso. "
               "No hay selección automática del ganador ni órdenes a ningún bróker.")
    end_default = pd.Timestamp(cutoff or pd.Timestamp.today().date()).date()
    start_date = st.date_input("Inicio de la simulación", value=(pd.Timestamp(end_default) - pd.DateOffset(years=1)).date())
    end_date = st.date_input("Fin de la simulación", value=end_default)
    if st.button("Ejecutar y registrar los tres ensayos"):
        reports = []
        with st.spinner("Simulando la política con información anterior a cada decisión…"):
            with connect() as conn:
                profile = load_profile(conn)
                base = ReplaySpec(str(start_date), str(end_date),
                                  preset_hash(get_settings().compute.get('weights_preset', 'balanced')),
                                  roundtrip_cost=profile['roundtrip_pct'] / 100)
                specs = [('Base', base), ('Costes dobles', dataclasses.replace(base, roundtrip_cost=base.roundtrip_cost * 2)),
                         ('Retraso de una sesión', dataclasses.replace(base, delay_sessions=1))]
                for label, spec in specs:
                    try:
                        reports.append((label, run_recorded(conn, spec)))
                    except Exception as exc:  # noqa: BLE001 — informar errores registrados, sin inventar resultados
                        st.error(f"{label}: no se ha completado ({exc}).")
        st.session_state['advisor_replay_reports'] = reports
    for label, report in st.session_state.get('advisor_replay_reports', []):
        st.write(label)
        st.json(report['summary'])
        for limitation in report['limitations']:
            st.caption(limitation)
        if not report['nav'].empty:
            st.line_chart(report['nav'].set_index('date')[['nav_eur', 'benchmark_eur']])
        st.dataframe(report['movements'], hide_index=True)
        st.dataframe(report['decisions'], hide_index=True)
        st.caption(f"Huella reproducible: {report['run_id'][:20]}")
    with connect(read_only=True) as conn:
        attempts = conn.execute('SELECT started_at, status, spec FROM advisor_replay_runs ORDER BY started_at DESC LIMIT 50').fetchdf()
    if not attempts.empty:
        st.write("Últimos intentos (incluye fallos; el registro completo permanece en la base)")
        st.dataframe(attempts, hide_index=True)
        st.download_button("Descargar registro visible de ensayos", attempts.to_json(orient='records'),
                           file_name='advisor-research.json', mime='application/json')
