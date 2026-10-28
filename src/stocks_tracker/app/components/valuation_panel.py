"""Shared read-only diagnosis of the actual portfolio, not a sample book."""
import streamlit as st

from stocks_tracker.core.portfolio_valuation import issues


def render_valuation_issues(positions, rates):
    report = issues(positions, rates)
    if report.empty:
        return
    st.warning('Posiciones que impiden valorar toda la cartera: ' + ', '.join(report['Símbolo']) +
               '. El resto de valores puede recibir recomendaciones condicionadas. '
               'Los importes de compra quedan pendientes de comprobar el riesgo conjunto.')
    st.dataframe(report, hide_index=True, width='stretch')
    st.caption('Estas posiciones no se eliminan ni se valoran a cero. El análisis de sus subyacentes '
               'identificados sigue disponible, pero no sustituye la valoración del contrato.')
