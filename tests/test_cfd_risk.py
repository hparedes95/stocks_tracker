"""Automatic CFD exposure from documented SnapTrade fields; no user inputs."""
import json
from datetime import UTC, datetime

import pandas as pd
import pytest

from stocks_tracker.compute.run_advice import _cartera, calculate_policy
from stocks_tracker.core import cfd_risk
from stocks_tracker.core import snaptrade_portfolio as snap
from stocks_tracker.core.advice import Veredicto
from stocks_tracker.core.investor_profile import DEFAULTS, save
from tests.test_holding_analysis import DAY, conn  # noqa: F401


def prepare(c):
    now = datetime.now(UTC).isoformat()
    c.execute("UPDATE instruments SET country='US'")
    c.execute("INSERT INTO instruments(ticker,country,currency) VALUES ('SPY','US','USD'),('EURUSD=X',NULL,'USD')")
    c.execute("INSERT INTO prices_daily(ticker,date,close,adj_close,source) "
              "SELECT 'EURUSD=X',date,1,1,'ecb' FROM prices_daily WHERE ticker='AAPL'")
    c.execute("INSERT INTO prices_daily(ticker,date,close,adj_close,source) "
              "SELECT 'SPY',date,close,adj_close,source FROM prices_daily WHERE ticker='AAPL'")
    for t in ('AAPL', 'CIBR', 'ITA'):
        c.execute("INSERT INTO indicators_daily(ticker,date,close,atr_pct) SELECT ticker,date,close,2 "
                  "FROM prices_daily WHERE ticker=? AND date=?", [t, DAY.date()])
        c.execute("INSERT INTO factor_scores(ticker,date,weights_hash,composite,composite_pctile,coverage) "
                  "VALUES (?,?,'test',2,.99,.9)", [t, DAY.date()])
    rows = []
    for symbol, qty, price, market in [('AAPL', .2, 200, 'XNAS'), ('CIBR', .25, 200, 'XNAS'), ('ITA', .5, 100, 'BATS')]:
        kind = 'stock' if symbol == 'AAPL' else 'cfd'
        inst = {'id': symbol, 'symbol': symbol, 'kind': kind, 'currency': 'USD', 'exchange': market}
        if kind == 'cfd':
            inst['underlying_instrument'] = {'kind': 'etf', 'symbol': symbol, 'currency': 'USD', 'exchange': market}
        rows.append({'instrument': inst, 'units': str(qty), 'price': str(price), 'cost_basis': None, 'currency': 'USD'})
    payload = {'account': {'id': 'example', 'net_value': {'amount': 1040, 'currency': 'EUR'},
                'sync_status': {'positions': now, 'balances': now}},
               'positions': {'results': rows, 'data_freshness': {'as_of': now}},
               'balances': [{'currency': {'code': 'EUR'}, 'cash': 900, 'buying_power': 9000}]}
    snap.store_snapshot(c, snap.normalise(payload))
    snap.enable(c, True)
    save(c, {**DEFAULTS, 'confirmed': True})
    return payload


def calculate(c):
    return {r.ticker: r for r in calculate_policy(c, 'test', 0, decision_date=DAY.date(), use_live_portfolio=True)[0]}


def book(c):
    return cfd_risk.build(c, _cartera(c, DAY.date())[0], as_of=DAY.date())


def test_complete_snapshot_enables_sized_add_without_converting_cfds_to_stocks(conn):  # noqa: F811
    prepare(conn)
    original = _cartera(conn, DAY.date())[0]
    risk = book(conn)
    assert risk.ready, risk.reason
    assert risk.equity == 1040
    assert risk.positions.set_index('ticker').valor_eur.to_dict() == {'AAPL': 40, 'CIBR': 50, 'ITA': 50}
    assert original.loc[original.broker_symbol.isin(['CIBR', 'ITA']), 'valor_eur'].isna().all()
    recs = calculate(conn)
    assert recs['AAPL'].veredicto == Veredicto.AMPLIAR
    assert 0 < recs['AAPL'].importe_eur < 900
    assert 'Patrimonio neto comunicado' in recs['AAPL'].assessment['encaje']
    assert recs['CIBR'].veredicto == recs['ITA'].veredicto == Veredicto.SIN_OPINION
    assert conn.execute('SELECT count(*) FROM orders').fetchone()[0] == 0


@pytest.mark.parametrize('change,fragment', [
    ('no_nav', 'net_value'), ('no_date', 'fechas'), ('different_cycle', 'distintas'),
    ('no_underlying', 'identidad'), ('wrong_venue', 'mercado'), ('wrong_currency', 'identidad'),
    ('wrong_symbol', 'identidad'), ('short', 'CFD largo'), ('price_mismatch', '5 %'),
    ('no_price', 'precio válido'), ('inflated_nav', 'conciliada'), ('no_fx', 'EUR'),
    ('quarantine', 'cuarentena'),
])
def test_missing_or_inconsistent_fields_keep_conditional_advice(conn, change, fragment):  # noqa: F811
    payload = prepare(conn)
    row = payload['positions']['results'][1]
    if change == 'no_nav':
        payload['account'].pop('net_value')
    elif change == 'no_date':
        payload['account']['sync_status'].pop('positions')
    elif change == 'different_cycle':
        payload['account']['sync_status']['positions'] = (pd.Timestamp.now(tz='UTC') - pd.Timedelta(hours=1)).isoformat()
    elif change == 'no_underlying':
        row['instrument'].pop('underlying_instrument')
    elif change == 'wrong_venue':
        row['instrument']['underlying_instrument']['exchange'] = 'XLON'
    elif change == 'wrong_currency':
        row['instrument']['underlying_instrument']['currency'] = 'EUR'
    elif change == 'wrong_symbol':
        row['instrument']['underlying_instrument']['symbol'] = 'ITA'
    elif change == 'short':
        row['units'] = '-1'
    elif change == 'price_mismatch':
        row['price'] = '100'
    elif change == 'no_price':
        row['price'] = None
    elif change == 'inflated_nav':
        payload['account']['net_value']['amount'] = 9000
    elif change == 'no_fx':
        conn.execute("DELETE FROM prices_daily WHERE ticker='EURUSD=X'")
    else:
        conn.execute("INSERT INTO prices_quarantine(ticker,date) VALUES ('CIBR',?)", [DAY.date()])
    snap.store_snapshot(conn, snap.normalise(payload))
    risk = book(conn)
    assert not risk.ready and fragment in risk.reason
    rec = calculate(conn)['AAPL']
    assert rec.veredicto != Veredicto.AMPLIAR and rec.importe_eur is None


def test_nav_not_notional_is_the_risk_denominator(conn):  # noqa: F811
    payload = prepare(conn)
    payload['account']['net_value']['amount'] = 150
    payload['balances'][0]['cash'] = 100
    snap.store_snapshot(conn, snap.normalise(payload))
    risk = book(conn)
    assert risk.ready and risk.equity == 150
    assert sum(risk.positions.valor_eur) == 140
    from stocks_tracker.core.portfolio_risk import analyse
    result = analyse(pd.DataFrame(), {'AAPL':40, 'CIBR':50, 'ITA':50}, cash=100, equity_override=risk.equity)
    assert result['equity'] == 150 and result['shock_equity_30_eur'] == -42
    assert calculate(conn)['AAPL'].veredicto != Veredicto.AMPLIAR


def test_old_snapshot_does_not_gain_invented_fields(conn):  # noqa: F811
    old = json.loads(snap.state(conn)['snapshot'])
    assert 'net_value' not in old
    assert not book(conn).ready


def test_new_data_stays_in_private_snapshot_not_market_tables(conn):  # noqa: F811
    prepare(conn)
    from stocks_tracker.core.market_bundle import TABLES
    assert 'snaptrade_state' not in TABLES
    assert json.loads(snap.state(conn)['snapshot'])['positions'][1]['underlying']['symbol'] == 'CIBR'


def test_current_cash_is_used_not_leveraged_buying_power(conn):  # noqa: F811
    payload = prepare(conn)
    first = calculate(conn)['AAPL']
    payload['balances'][0]['buying_power'] *= 100
    snap.store_snapshot(conn, snap.normalise(payload))
    second = calculate(conn)['AAPL']
    assert first.importe_eur == second.importe_eur


def test_uncategorised_exposure_is_reserved_against_candidate_sectors(conn):  # noqa: F811
    prepare(conn)
    conn.execute("UPDATE instruments SET gics_sector=NULL")
    risk = book(conn)
    assert risk.ready
    assert risk.positions.loc[risk.positions.asset_class.eq('cfd'), 'gics_sector'].eq('__unknown__').all()
    assert calculate(conn)['AAPL'].importe_eur is not None
