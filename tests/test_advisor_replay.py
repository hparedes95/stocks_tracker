import duckdb
import pandas as pd
import pytest

from stocks_tracker.backtest.advisor_replay import (
    Ledger,
    ReplaySpec,
    read_inputs,
    run_replay,
    snapshot,
)
from stocks_tracker.core.advice import Conviccion, Recomendacion, Veredicto
from stocks_tracker.core.db import schema_path


def test_cash_sales_costs_and_balance_are_exact():
    book = Ledger(1000.)
    book.trade('2025-01-02', 'A', 5, 100, .01)
    assert book.cash == 495
    assert book.nav({'A': 100}) == 995
    book.trade('2025-01-03', 'A', -2, 120, .01)
    assert book.cash == pytest.approx(732.6)
    assert book.holdings['A'] == 3
    assert book.nav({'A': 120}) == pytest.approx(1092.6)
    book.trade('2025-01-03', 'A', -99, 120, .01)
    assert not book.holdings
    assert book.cash == pytest.approx(1089.)


def test_dividend_does_not_fund_buys_and_raw_split_conserves_nav():
    book = Ledger(1000.)
    book.trade('2025-01-02', 'A', 10, 100, 0)
    book.dividend('2025-01-03', 'A', 2)
    assert book.cash == 0
    assert book.nav({'A': 98}) == 1000
    book.split('2025-01-06', 'A', 2)
    assert book.holdings['A'] == 20
    assert book.nav({'A': 49}) == 1000
    book.trade('2025-01-06', 'B', 1, 10, 0)
    assert 'B' not in book.holdings


@pytest.fixture
def warehouse():
    c = duckdb.connect(':memory:')
    c.execute(schema_path().read_text(encoding='utf-8'))
    c.execute("INSERT INTO instruments(ticker,currency,country,asset_class) VALUES "
              "('A','EUR','US','equity'),('VT','EUR','US','etf')")
    for day, asset in [('2025-01-02',100), ('2025-01-03',100), ('2025-01-06',110), ('2025-01-07',120)]:
        for symbol, value in [('A',asset),('VT',100)]:
            c.execute("INSERT INTO prices_daily(ticker,date,close,adj_close,source) VALUES (?,?,?,?,'fixture')",
                      [symbol, day, value, value])
            c.execute('INSERT INTO indicators_daily(ticker,date,close) VALUES (?,?,?)', [symbol,day,value])
    c.execute("INSERT INTO fundamentals_snapshot(ticker,as_of,trailing_pe) VALUES ('A','2025-01-07',99)")
    c.execute("INSERT INTO positions(id,ticker,qty) VALUES ('private','DO_NOT_TOUCH',42)")
    yield c
    c.close()


def test_snapshot_cannot_read_future_or_private_positions(warehouse):
    frames = read_inputs(warehouse)
    with snapshot(frames, pd.Timestamp('2025-01-02'), Ledger(1000)) as c:
        assert c.execute('SELECT COUNT(*) FROM positions').fetchone()[0] == 0
        assert c.execute('SELECT COUNT(*) FROM fundamentals_snapshot').fetchone()[0] == 0
        assert str(c.execute('SELECT MAX(date) FROM prices_daily').fetchone()[0]) == '2025-01-02'


def test_replay_executes_shared_interface_next_session_and_is_reproducible(warehouse):
    seen = []
    def policy(c, weights, cash, *, decision_date):
        seen.append(str(c.execute('SELECT MAX(date) FROM prices_daily').fetchone()[0]))
        rec = Recomendacion('A', Veredicto.COMPRAR, Conviccion.BAJA, importe_eur=500)
        return [rec], None, None, decision_date, None
    spec = ReplaySpec('2025-01-02','2025-01-07','test',initial_cash=1000,roundtrip_cost=.02,slippage=0)
    result = run_replay(warehouse, spec, policy=policy)
    assert seen == ['2025-01-02']
    assert result['movements'].iloc[0]['date'] == '2025-01-03'
    assert result['nav'].iloc[-1].nav_eur == 1095
    assert result['summary']['qualified'] is False
    assert warehouse.execute('SELECT qty FROM positions').fetchone()[0] == 42
    repeated = run_replay(warehouse, spec, policy=policy)
    pd.testing.assert_frame_equal(result['nav'], repeated['nav'])
    assert result['run_id'] == repeated['run_id']


def test_missing_price_does_not_roll_execution_forward(warehouse):
    warehouse.execute("DELETE FROM prices_daily WHERE ticker='A' AND date='2025-01-03'")
    def policy(c, weights, cash, *, decision_date):
        return [Recomendacion('A',Veredicto.COMPRAR,Conviccion.BAJA,importe_eur=500)],None,None,decision_date,None
    result = run_replay(warehouse, ReplaySpec('2025-01-02','2025-01-07','test'),policy=policy)
    assert result['movements'].empty
    assert 'sin_ejecucion' in result['decisions'].verdict.tolist()


def test_conditional_recommendation_is_logged_but_never_traded(warehouse):
    def policy(c, weights, cash, *, decision_date):
        rec = Recomendacion('A', Veredicto.COMPRA_CONDICIONADA, Conviccion.BAJA)
        return [rec], None, None, decision_date, None
    result = run_replay(warehouse, ReplaySpec('2025-01-02', '2025-01-07', 'test'), policy=policy)
    assert result['movements'].empty
    assert result['decisions'].verdict.tolist() == ['comprar_condicionado']
    assert result['summary']['pending_orders'] == 0


def test_real_advisor_policy_runs_without_mutating_account(warehouse):
    result = run_replay(warehouse, ReplaySpec('2025-01-02','2025-01-07','test'))
    assert result['movements'].empty
    assert not result['summary']['qualified']
    assert warehouse.execute('SELECT COUNT(*) FROM orders').fetchone()[0] == 0


def test_failed_research_attempt_is_recorded(warehouse):
    from stocks_tracker.backtest.advisor_replay import run_recorded

    with pytest.raises(ValueError, match='referencia'):
        run_recorded(warehouse, ReplaySpec('2025-01-02','2025-01-07','test', benchmark='MISSING'))
    assert warehouse.execute('SELECT status FROM advisor_replay_runs').fetchone()[0] == 'failed'
    assert warehouse.execute("SELECT estado FROM experiments WHERE signal_id='advisor-full-policy'").fetchone()[0] == 'sin_datos'
