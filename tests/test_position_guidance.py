from datetime import timedelta

import pandas as pd
import pytest

from stocks_tracker.compute.run_advice import calculate_policy
from stocks_tracker.core.advice import Conviccion, Recomendacion, Veredicto
from stocks_tracker.core.position_guidance import complete
from tests.test_holding_analysis import DAY, conn  # noqa: F401


def policy(connection, day=DAY):
    return calculate_policy(connection, 'test', 1000, decision_date=day.date(),
                            use_live_portfolio=True)[0]


def prepare_session(c, weak=True):
    c.execute("INSERT INTO indicators_daily(ticker,date,close) VALUES ('AAPL',?,100)", [DAY.date()])
    if weak:
        c.execute("UPDATE prices_daily SET adj_close=300-adj_close, close=300-close WHERE ticker='AAPL'")


def test_snaptrade_reduction_works_without_purchase_date_or_complete_portfolio(conn):  # noqa: F811
    prepare_session(conn)
    recs = {r.ticker:r for r in policy(conn)}
    assert recs['AAPL'].veredicto == Veredicto.REDUCIR
    assert recs['AAPL'].titulos_a_soltar is None
    assert recs['AAPL'].assessment['decision_basis'] == 'position_rules'
    assert recs['CIBR'].veredicto == recs['ITA'].veredicto == Veredicto.SIN_OPINION
    assert conn.execute('SELECT count(*) FROM orders').fetchone()[0] == 0


def test_healthy_live_position_gets_explicit_hold_not_no_opinion(conn):  # noqa: F811
    prepare_session(conn, weak=False)
    recs = {r.ticker:r for r in policy(conn)}
    assert recs['AAPL'].veredicto == Veredicto.MANTENER
    assert not any(r.veredicto == Veredicto.COMPRAR for r in recs.values())


@pytest.mark.parametrize('mutation', [
    "DELETE FROM prices_daily WHERE ticker='AAPL'",
    "UPDATE prices_daily SET source='synthetic' WHERE ticker='AAPL'",
    "INSERT INTO prices_quarantine(ticker,date) VALUES ('AAPL','2026-10-15')",
])
def test_bad_prices_do_not_create_exit_recommendation(conn, mutation):  # noqa: F811
    prepare_session(conn)
    conn.execute(mutation)
    assert {r.ticker:r for r in policy(conn)}['AAPL'].veredicto == Veredicto.SIN_OPINION


def test_stale_session_does_not_emit_new_technical_reduction(conn):  # noqa: F811
    prepare_session(conn)
    assert {r.ticker:r for r in policy(conn, DAY + timedelta(days=8))}['AAPL'].veredicto == Veredicto.SIN_OPINION


def test_existing_exit_rule_keeps_precedence(conn):  # noqa: F811
    rec = Recomendacion('AAPL', Veredicto.VENDER, Conviccion.ALTA, motivos=['Stop verificado'])
    assert complete(conn, [rec], pd.DataFrame(), as_of=DAY.date(),
                    horizon_months=6, decision_date=DAY.date()) == [rec]
