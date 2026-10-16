"""Presentation of existing holding reports. Not a second recommendation engine."""
from hashlib import blake2s
from html import escape

import pandas as pd
import streamlit as st

from ...core.holding_analysis import fundamental_metrics
from .charts import sparkline


def research_summary(reports, *, horizon):
    ready = sum(bool(r['metrics']) for r in reports)
    a, b, c = st.columns(3)
    a.metric('Posiciones en análisis', str(len(reports)))
    b.metric('Con lectura de mercado', f'{ready} / {len(reports)}',
             help='Cobertura de datos para la lectura actual. No es un porcentaje de acierto.')
    c.metric('Horizonte elegido', f'{horizon} meses')


def render_holding_card(holding, *, prices=None):
    key = blake2s(str(holding['id']).encode(), digest_size=8).hexdigest()
    with st.container(key=f'holding-card-{key}'):
        scope = 'CFD · referencia ETF' if holding['kind'] == 'cfd' else 'Posición al contado'
        with st.container(key=f'holding-header-{key}'):
            st.html(f'<span class="stx-position-kind">{escape(scope)}</span>')
            st.subheader(holding['symbol'], anchor=False)
            # Full verdict stays visible, independently of its color or badge.
            st.write(holding['stance'])
            st.caption(f"Último cierre: {holding['price_date'] or 'no disponible'} · "
                       f"Fuente: {holding['source'] or 'no disponible'}")
        metrics = holding['metrics']
        if metrics:
            if prices is not None and not prices.empty:
                # Same cut as the report. Never draw future or unvalidated data
                # when its recent technical window was rejected by the engine.
                window = prices.loc[pd.to_datetime(prices.date) <= pd.Timestamp(holding['price_date'])].tail(63)
                if not window.empty:
                    st.plotly_chart(sparkline(window.adj_close, dates=window.date, height=90),
                                    width='stretch', height=100, key=f'holding-chart-{key}',
                                    config={'displayModeBar':False})
                    start = pd.Timestamp(window.date.iloc[0]).strftime('%d/%m/%Y')
                    end = pd.Timestamp(window.date.iloc[-1]).strftime('%d/%m/%Y')
                    st.html(f'<div class="stx-chart-range"><span>{start}</span><span>{end}</span></div>')
                    st.caption(f'Evolución ajustada · {len(window)} cierres · no es tu rentabilidad')
            cols = st.columns(3)
            cols[0].metric('Retorno ajustado', f"{metrics['momentum_3m_pct']:+.2f} %",
                           help='Variación del precio ajustado en 63 sesiones, no tu beneficio personal.')
            cols[1].metric('Caída desde máximo', f"{metrics['drawdown_pct']:.2f} %",
                           help='Distancia al máximo de la ventana de hasta 253 cierres ajustados.')
            cols[2].metric('Volatilidad diaria', f"{metrics['daily_vol_pct']:.2f} %",
                           help='Desviación de los retornos diarios de las últimas 60 sesiones.')
            st.caption('Medias y retornos usan precios ajustados; no representan tu beneficio ni la rentabilidad neta del contrato.')
        if holding['reasons']:
            st.caption('Observación de la lectura actual')
            st.write(holding['reasons'][-1])
        # Provenance remains accessible even when data coverage is insufficient.
        with st.expander(f"Motivos y datos de {holding['symbol']}"):
            st.caption(f"{scope} · Serie: {holding['ticker'] or 'sin identificar'} · "
                       f"Corte del análisis: {holding['cutoff']}")
            if holding['reasons']:
                for reason in holding['reasons'][:-1]:
                    st.write(reason)
            fund = holding['fundamentals']
            if fund:
                st.write('Fundamentales actuales:', fundamental_metrics(fund))
                st.caption(f"Fundamentales: {fund['date']} · {fund['source']}. No se reconstruyen los de la compra.")
            scenario = holding['scenario']
            if scenario:
                st.write(f"Escenarios históricos a {scenario['horizon']} sesiones ({scenario['episodes']} episodios): "
                         f"percentil 10 {scenario['low_pct']:+.1f} %, mediana {scenario['median_pct']:+.1f} %, "
                         f"percentil 90 {scenario['high_pct']:+.1f} %.")
                st.caption('Distribución histórica del subyacente, no objetivo de precio, intervalo garantizado ni retorno neto del CFD.')
        if holding['review_if']:
            with st.expander(f"Qué revisar en {holding['symbol']}"):
                for condition in holding['review_if']:
                    st.write(condition)
        if holding['limitations']:
            with st.expander(f"Cobertura y límites de {holding['symbol']}", expanded=not bool(metrics)):
                for limitation in holding['limitations']:
                    st.write(limitation)
