"""eToro long ETF CFDs: gross exposure and broker NAV are separate quantities.

This allocation risk view never replaces spot holdings or reports a contract's
liquidation value. Units express exposure; leverage is not multiplied again.
No collateral, financing or liquidation level is invented.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field

import pandas as pd

from . import fx
from . import snaptrade_portfolio as snap
from .broker_symbols import resolve, text, venue


@dataclass
class RiskBook:
    positions: pd.DataFrame = field(default_factory=pd.DataFrame)
    equity: float | None = None
    reason: str = ''
    note: str = ''

    @property
    def ready(self):
        return self.equity is not None and not self.reason


def _positive(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def build(conn, positions, *, as_of):
    """All-or-nothing risk view for the connected account, with no data entry."""
    current = snap.state(conn)
    if not current.get('active'):
        return RiskBook()
    snapshot = json.loads(current.get('snapshot') or '{}')
    contracts = [p for p in snapshot.get('positions', []) if p.get('kind') == 'cfd' and p.get('qty') != 0]
    if not contracts:
        return RiskBook()
    def missing(reason):
        return RiskBook(reason='Importes pendientes: ' + reason)
    if issue := snap.readiness(conn):
        return missing(issue)
    # Cached account total and positions must belong to the same sync cycle.
    # These are provider sync dates, not real-time price guarantees.
    try:
        stamps = [snap.timestamp(snapshot.get(k)) for k in
                  ('as_of', 'cash_as_of', 'account_positions_as_of')]
    except snap.SnapTradeError:
        return missing('SnapTrade no informa las fechas de sincronización conjunta de cuenta, saldos y posiciones.')
    if max(stamps) - min(stamps) > pd.Timedelta(minutes=5):
        return missing('El patrimonio, los saldos y las posiciones pertenecen a sincronizaciones distintas. Se reintentará al sincronizar.')
    value = snapshot.get('net_value') or {}
    if not _positive(value.get('amount')) or not value.get('currency'):
        return missing('SnapTrade no ha comunicado un patrimonio neto positivo de la cuenta (net_value).')
    rates = fx.tipos(conn, as_of)
    def euro(amount, currency):
        return float(fx.a_base(pd.Series([amount]), pd.Series([currency]), rates).iloc[0])
    equity = euro(value['amount'], value['currency'])
    cash = snap.cash_eur(conn, as_of)
    if not _positive(equity) or not math.isfinite(cash) or cash < 0 or cash > equity:
        return missing('El patrimonio neto y el efectivo no se pueden conciliar en EUR; revisa la próxima sincronización automática.')
    metadata = conn.execute('SELECT ticker,currency,exchange,mic,gics_sector FROM instruments').fetchdf()
    book = positions.copy(deep=True)
    if book.empty or book.id.duplicated().any():
        return missing('La lista de posiciones no permite identificar todos los contratos.')
    expected = {p['id'] for p in snapshot['positions'] if p['qty'] != 0 and not p['cash_equivalent']}
    if set(book.id) != expected:
        return missing('La cuenta ha cambiado durante el cálculo; hay que volver a sincronizar.')
    for pos in contracts:
        symbol = text(pos.get('symbol'))
        underlying = pos.get('underlying') or {}
        if (symbol not in ('CIBR', 'ITA') or not _positive(pos.get('qty'))
                or pos.get('cash_equivalent') or underlying.get('kind') != 'etf'
                or text(underlying.get('symbol')) != symbol
                or underlying.get('currency') != 'USD' or pos.get('currency') != 'USD'
                or pos.get('listing_currency') not in (None, 'USD')):
            return missing(f'{symbol}: falta identidad ETF/USD del subyacente o no es un CFD largo admitido.')
        target = resolve({**pos, 'kind': 'etf', 'listing_currency': underlying['currency'],
                          'exchange': underlying.get('exchange')}, metadata)
        if target.ticker != symbol or symbol not in set(metadata.ticker):
            return missing(f'{symbol}: el mercado del subyacente no coincide con la ficha local.')
        if pos.get('exchange') and venue(pos['exchange']) not in ('OTC', venue(underlying.get('exchange'))):
            return missing(f'{symbol}: mercados contradictorios en la respuesta del bróker.')
        if not _positive(pos.get('price')):
            return missing(f'{symbol}: SnapTrade no informa un precio válido del contrato.')
        history = conn.execute('SELECT p.date,p.adj_close,p.close,p.source,q.date IS NOT NULL AS quarantined '
            'FROM prices_daily p LEFT JOIN prices_quarantine q ON p.ticker=q.ticker AND p.date=q.date '
            'WHERE p.ticker=? AND p.date<=? ORDER BY p.date DESC LIMIT 1', [symbol, as_of]).fetchdf()
        # Valuing current exposure needs a current reference quote, not 200
        # closes for a trend model. Correlation is checked separately per buy.
        if history.empty:
            return missing(f'{symbol}: falta un cierre de referencia descargado para contrastar el precio.')
        reference = history.iloc[0]
        if reference.quarantined:
            return missing(f'{symbol}: el cierre de referencia está en cuarentena.')
        source = str(reference.source or '').strip().lower()
        if not source or 'synthetic' in source or (pd.Timestamp(as_of) - pd.Timestamp(reference.date)).days > 7:
            return missing(f'{symbol}: el cierre de referencia está desactualizado o carece de una fuente válida.')
        market_price = float(history.close.iloc[-1])
        if not _positive(market_price) or abs(pos['price'] / market_price - 1) > .05:
            return missing(f'{symbol}: precio del contrato y referencia ETF difieren más del 5 %; no se presume que sean equivalentes.')
        exposure = euro(pos['qty'] * max(pos['price'], market_price), 'USD')
        if not _positive(exposure):
            return missing(f'{symbol}: falta conversión USD/EUR vigente.')
        selection = book.id == pos['id']
        book.loc[selection, ['ticker', 'valor_eur', 'supported', 'analysis_supported']] = [symbol, exposure, True, True]
        book.loc[selection, 'asset_class'] = 'cfd'
        local = metadata.loc[metadata.ticker == symbol].iloc[0]
        book.loc[selection, 'gics_sector'] = local.gics_sector if pd.notna(local.gics_sector) and local.gics_sector else '__unknown__'
        book.loc[selection, 'reason'] = 'Exposición bruta CFD; no es patrimonio ni valor de liquidación.'
    if snap.limitations(book):
        return missing('Hay otras posiciones sin exposición identificada: ' + ' · '.join(snap.limitations(book)))
    if any(not _positive(v) for v in book.valor_eur):
        return missing('No se puede calcular la exposición de todas las posiciones.')
    if equity > cash + float(book.valor_eur.sum()) + max(1., equity * .01):
        return missing('El patrimonio comunicado supera efectivo más exposición; la cuenta no está conciliada.')
    book['peso_pct'] = book.valor_eur / equity * 100
    note = (f'Patrimonio neto comunicado por SnapTrade/eToro: {equity:.2f} EUR; '
            f'exposición bruta total: {book.valor_eur.sum():.2f} EUR; última sincronización de saldos: '
            + str(snapshot['cash_as_of']) + '. CIBR/ITA cuentan por su exposición bruta, '
            'sin volver a multiplicar el apalancamiento. El ETF es una referencia de riesgo; '
            'no reproduce financiación, margen ni liquidación del CFD.')
    return RiskBook(book, equity, note=note)
