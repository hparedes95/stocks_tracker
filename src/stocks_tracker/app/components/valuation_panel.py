"""Shared read-only diagnosis of the actual portfolio, not a sample book."""
import streamlit as st

from stocks_tracker.app.components.theme import format_money
from stocks_tracker.core.portfolio_valuation import issues


def render_cfd_risk(book):
    """Separate a contract's gross exposure from net account equity."""
    if not book.ready:
        if book.reason:
            st.warning(book.reason)
        return False
    st.info('CIBR e ITA incluidos en el cálculo de exposición. El asesor puede calcular importes '
            'para el resto si quedan efectivo y margen dentro de tus límites.')
    st.metric('Patrimonio neto comunicado por el bróker', format_money(book.equity, 'EUR'))
    contracts = book.positions[book.positions.asset_class.eq('cfd')]
    st.dataframe(contracts[['broker_symbol', 'valor_eur', 'peso_pct']].rename(columns={
        'broker_symbol': 'CFD', 'valor_eur': 'Exposición bruta (EUR)', 'peso_pct': 'Exposición / patrimonio (%)'}),
        hide_index=True)
    st.caption(book.note)
    return True


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
