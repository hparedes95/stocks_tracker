"""Regression for the real live portfolio: no purchase dates, two ETF CFDs,
no saved actionable orders. Every identifiable holding must have its research.
"""
from datetime import UTC, datetime

import duckdb
import numpy as np
import pandas as pd
import pytest

from stocks_tracker.core import db
from stocks_tracker.core import snaptrade_portfolio as snap
from stocks_tracker.core.holding_analysis import analyse, fundamental_metrics, holdings
from stocks_tracker.core.trading_calendar import sessions

DAY = pd.Timestamp('2026-10-15')


def test_fundamental_display_never_confuses_fractions_percentages_and_multiples():
    assert fundamental_metrics({'trailing_pe':25., 'profit_margin':.25,
                                'revenue_growth_yoy':-.1, 'net_debt_to_ebitda':2.5}) == {
        'PER (veces)':25., 'Margen neto (%)':25., 'Crecimiento de ingresos (%)':-10.,
        'Deuda neta / EBITDA (veces)':2.5}


def seed(conn):
    now = datetime.now(UTC).isoformat()
    positions = []
    for symbol, kind, exchange in [('AAPL', 'stock', 'XNAS'), ('CIBR', 'cfd', None), ('ITA', 'cfd', 'OTC')]:
        conn.execute('INSERT INTO instruments(ticker,currency,exchange,asset_class) VALUES (?,?,?,?)',
                     [symbol, 'USD', 'NMS' if symbol != 'ITA' else 'BATS', 'equity' if kind == 'stock' else 'etf'])
        positions.append({'id':symbol, 'symbol':symbol, 'kind':kind, 'listing_currency':'USD',
                          'exchange':exchange, 'qty':2., 'avg_cost':None, 'currency':'USD',
                          'price':100., 'cash_equivalent':False})
        days = sessions('XNYS', DAY - pd.Timedelta(days=700), DAY)[-400:]
        values = np.linspace(200, 100, len(days)) if symbol == 'ITA' else np.linspace(100, 200, len(days))
        db.upsert_df(conn, 'prices_daily', pd.DataFrame({'ticker':symbol, 'date':days,
                     'adj_close':values, 'close':values, 'source':'yfinance'}), keys=['ticker', 'date'])
    snap.store_snapshot(conn, {'account_id':'example', 'as_of':now, 'cash_as_of':now,
                              'balances':[{'currency':'EUR', 'cash':390.12}], 'positions':positions})
    snap.enable(conn, True)


@pytest.fixture
def conn():
    c = duckdb.connect(':memory:')
    c.execute(db.schema_path().read_text(encoding='utf-8'))
    seed(c)
    yield c
    c.close()


def test_actual_live_holdings_get_analysis_without_dates_or_saved_orders(conn):
    reports = {r['symbol']:r for r in analyse(conn, as_of=DAY)}
    assert set(reports) == {'AAPL', 'CIBR', 'ITA'}
    assert reports['AAPL']['stance'] == 'Mantener en vigilancia'
    assert reports['CIBR']['stance'] == 'Subyacente favorable; vigilar el CFD'
    assert reports['ITA']['stance'] == 'Subyacente débil; revisar el CFD'
    assert all(r['metrics'] and len(r['reasons']) >= 3 and r['review_if'] for r in reports.values())
    assert reports['CIBR']['ticker'] == 'CIBR' and reports['ITA']['ticker'] == 'ITA'
    assert 'CFD' in ' '.join(reports['ITA']['limitations'])
    assert reports['CIBR']['price_date'] == '2026-10-15'
    # Reading research neither invents an actionable record nor values a CFD.
    assert conn.execute('SELECT count(*) FROM recommendations').fetchone()[0] == 0
    book = snap.portfolio(conn, DAY.date())
    assert book.loc[book.broker_symbol.isin(['CIBR', 'ITA']), 'close'].isna().all()
    assert not book.loc[book.broker_symbol.isin(['CIBR', 'ITA']), 'supported'].any()


@pytest.mark.parametrize('mutation, expected', [
    ("DELETE FROM prices_daily WHERE ticker='CIBR'", 'No hay histórico'),
    ("UPDATE prices_daily SET source='synthetic' WHERE ticker='CIBR'", 'sintética'),
    ("UPDATE prices_daily SET source=NULL WHERE ticker='CIBR'", 'Procedencia'),
    ("UPDATE prices_daily SET adj_close=-1 WHERE ticker='CIBR' AND date='2026-10-15'", 'inválidos'),
    ("DELETE FROM prices_daily WHERE ticker='CIBR' AND date>'2026-09-01'", 'desactualizados'),
    ("DELETE FROM prices_daily WHERE ticker='CIBR' AND date<'2026-09-01'", '200'),
    ("INSERT INTO prices_quarantine(ticker,date) VALUES ('CIBR','2026-10-15')", 'cuarentena'),
    ("DELETE FROM prices_daily WHERE ticker='CIBR' AND date BETWEEN '2026-08-01' AND '2026-08-20'", 'huecos'),
    ("DELETE FROM prices_daily WHERE ticker='CIBR' AND date='2026-10-14'", 'faltan 1 sesiones'),
])
def test_bad_position_does_not_hide_other_analyses(conn, mutation, expected):
    conn.execute(mutation)
    reports = {r['symbol']:r for r in analyse(conn, as_of=DAY)}
    assert not reports['CIBR']['metrics']
    assert expected in ' '.join(reports['CIBR']['limitations'])
    assert reports['AAPL']['metrics'] and reports['ITA']['metrics']


def test_no_lookahead_or_future_fundamentals(conn):
    before = analyse(conn, as_of=DAY)
    conn.execute("INSERT INTO prices_daily(ticker,date,adj_close,source) VALUES ('AAPL','2026-10-16',1,'yfinance')")
    conn.execute("INSERT INTO fundamentals_snapshot(ticker,as_of,net_debt_to_ebitda,source) "
                 "VALUES ('AAPL','2026-10-16',100,'yfinance')")
    assert analyse(conn, as_of=DAY) == before


def test_fundamental_risks_override_bullish_technical_reading(conn):
    conn.execute("INSERT INTO fundamentals_snapshot(ticker,as_of,net_debt_to_ebitda,source) "
                 "VALUES ('AAPL','2026-10-15',5,'yfinance')")
    aapl = analyse(conn, as_of=DAY)[0]
    assert aapl['stance'] == 'Revisar riesgos fundamentales; no ampliar'
    assert 'Deuda elevada' in ' '.join(aapl['reasons'])
    assert aapl['fundamentals']['date'] == '2026-10-15'


@pytest.mark.parametrize('as_of,source', [('2025-01-01','yfinance'), ('2026-10-15','synthetic'), ('2026-10-15',None)])
def test_old_unattributed_and_synthetic_fundamentals_are_not_current(conn, as_of, source):
    conn.execute('INSERT INTO fundamentals_snapshot(ticker,as_of,net_debt_to_ebitda,source) VALUES (?,?,?,?)',
                 ['AAPL', as_of, 5, source])
    aapl = analyse(conn, as_of=DAY)[0]
    assert not aapl['fundamentals']
    assert aapl['stance'] == 'Mantener en vigilancia'


def test_conflicting_etf_identity_cannot_be_disguised(conn):
    conn.execute("UPDATE instruments SET currency='EUR' WHERE ticker='CIBR'")
    cibr = next(r for r in analyse(conn, as_of=DAY) if r['symbol'] == 'CIBR')
    assert not cibr['ticker'] and not cibr['metrics']
    assert 'contradice' in ' '.join(cibr['limitations'])


def test_account_error_leaves_market_reading_but_no_personal_stance(conn):
    conn.execute("UPDATE snaptrade_state SET error='failed'")
    reports = analyse(conn, as_of=DAY)
    assert all(r['stance'] == 'Lectura de mercado; cartera sin sincronizar' for r in reports)
    assert all(r['metrics'] for r in reports)


def test_manual_positions_without_purchase_date_also_get_current_analysis(conn):
    snap.enable(conn, False)
    conn.execute("INSERT INTO positions(id,ticker,qty,currency) VALUES ('manual','AAPL',1,'USD')")
    assert holdings(conn)[0]['ticker'] == 'AAPL'
    assert analyse(conn, as_of=DAY)[0]['metrics']


def test_refresh_prioritises_only_identified_holdings(conn, monkeypatch):
    from contextlib import contextmanager

    from stocks_tracker.ingest import holding_refresh

    @contextmanager
    def connect(**kwargs):
        yield conn

    calls = []
    monkeypatch.setattr(holding_refresh, 'connect', connect)
    monkeypatch.setattr(holding_refresh, 'migrate', lambda: None)
    monkeypatch.setattr(holding_refresh, 'frozen_market', lambda: False)
    monkeypatch.setattr(holding_refresh, 'ingest_prices', lambda **kw: calls.append(kw['tickers_override']))
    holding_refresh.refresh()
    assert calls == [['AAPL'], ['CIBR'], ['ITA']]
    monkeypatch.setattr(holding_refresh, 'frozen_market', lambda: True)
    with pytest.raises(ValueError, match='congelada'):
        holding_refresh.refresh()
    assert len(calls) == 3


def test_held_stock_fundamentals_do_not_wait_for_universe_weekly_shard(conn, monkeypatch):
    from contextlib import contextmanager
    from datetime import date
    from types import SimpleNamespace

    from stocks_tracker.ingest import run_ingest

    @contextmanager
    def connect(**kwargs):
        yield conn

    calls = []

    def fetch(tickers):
        calls.append(tickers)
        return pd.DataFrame()

    monkeypatch.setattr(run_ingest, 'connect', connect)
    monkeypatch.setattr(run_ingest, 'migrate', lambda: None)
    monkeypatch.setattr(run_ingest, 'get_settings', lambda: SimpleNamespace(ingest={'fundamentals_shard_count':7}))
    monkeypatch.setattr(run_ingest, '_tickers_to_download', lambda: ['ZZZ'])
    monkeypatch.setattr(run_ingest, '_stable_shard', lambda *a: (date.today().toordinal() + 1) % 7)
    monkeypatch.setattr(run_ingest, 'get_price_provider', lambda *a: SimpleNamespace(fetch_snapshot=fetch))
    run_ingest.ingest_fundamentals()
    assert calls == [['AAPL']]


def test_one_unavailable_download_does_not_block_other_holdings(conn, monkeypatch):
    from contextlib import contextmanager

    from stocks_tracker.ingest import holding_refresh
    from stocks_tracker.providers.base import ProviderError

    @contextmanager
    def connect(**kwargs):
        yield conn

    calls = []

    def ingest(**kwargs):
        ticker = kwargs['tickers_override'][0]
        calls.append(ticker)
        if ticker == 'CIBR':
            raise ProviderError('Untrusted response body')

    monkeypatch.setattr(holding_refresh, 'connect', connect)
    monkeypatch.setattr(holding_refresh, 'migrate', lambda: None)
    monkeypatch.setattr(holding_refresh, 'frozen_market', lambda: False)
    monkeypatch.setattr(holding_refresh, 'ingest_prices', ingest)
    holding_refresh.refresh()
    assert calls == ['AAPL', 'CIBR', 'ITA']
    assert conn.execute("SELECT target,status,error FROM ingest_log WHERE task='holding_prices'").fetchall() == [
        ('CIBR', 'FAILED', 'ProviderError')]
