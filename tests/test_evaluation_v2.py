from datetime import date

import duckdb
import pandas as pd
import pytest

from stocks_tracker.core.db import schema_path
from stocks_tracker.core.evaluation import evaluate, independent_episodes


@pytest.fixture
def data():
    c = duckdb.connect(':memory:')
    c.execute(schema_path().read_text(encoding='utf-8'))
    c.execute("INSERT INTO instruments(ticker,currency,country) VALUES ('AAA','EUR','ES'), ('EWP','EUR','US')")
    c.execute("INSERT INTO recommendations(fecha,ticker,weights_hash,veredicto,precio,horizonte_meses,roundtrip_cost) "
              "VALUES ('2025-01-01','AAA','one','comprar',100,6,0.01)")
    for symbol, values in [('AAA', [100,100,120,80]), ('EWP', [100,100,105,110])]:
        for day, value in zip(['2025-01-01','2025-01-02','2025-07-01','2025-12-31'], values, strict=True):
            c.execute("INSERT INTO prices_daily(ticker,date,close,adj_close,source) VALUES (?,?,?,?,'test-real')",
                      [symbol,day,value,value])
    yield c
    c.close()


def test_score_is_fixed_to_calendar_maturity(data):
    first = evaluate(data, hasta=date(2025,7,1)).iloc[0]
    later = evaluate(data, hasta=date(2025,12,31)).iloc[0]
    assert first['retorno'] == later['retorno'] == pytest.approx(.20)
    assert later['salida'] == pd.Timestamp('2025-07-01')
    assert later['entrada'] == pd.Timestamp('2025-01-02')
    assert later['ventaja_neta'] == pytest.approx(.14)


def test_adjusted_returns_not_raw_price_changes(data):
    data.execute("UPDATE prices_daily SET close=60 WHERE ticker='AAA' AND date='2025-07-01'")
    assert evaluate(data, hasta='2025-12-31').iloc[0]['retorno'] == pytest.approx(.20)


@pytest.mark.parametrize('problem', ['currency','maturity','source','future_issue','benchmark','cost_unknown'])
def test_missing_evidence_is_not_invented(data, problem):
    if problem == 'currency':
        data.execute("UPDATE instruments SET currency=NULL WHERE ticker='AAA'")
    elif problem == 'maturity':
        data.execute("DELETE FROM prices_daily WHERE date='2025-07-01'")
    elif problem == 'source':
        data.execute("UPDATE prices_daily SET source='synthetic'")
    elif problem == 'future_issue':
        data.execute("UPDATE recommendations SET emitida_at='2025-12-31'")
    elif problem == 'benchmark':
        data.execute("DELETE FROM prices_daily WHERE ticker='EWP'")
    else:
        data.execute("UPDATE recommendations SET roundtrip_cost=NULL")
        assert pd.isna(evaluate(data,hasta='2025-12-31').iloc[0]['ventaja_neta'])
        return
    assert evaluate(data,hasta='2025-12-31').empty


def test_repeated_daily_advice_is_not_independent():
    frame = pd.DataFrame({'ticker':['A']*3, 'model_version':['v1','v1','v2'],
                          'entrada':pd.to_datetime(['2025-01-02','2025-01-03','2025-01-03']),
                          'salida':pd.to_datetime(['2025-07-01']*3)})
    assert len(independent_episodes(frame)) == 2


@pytest.mark.parametrize('source,value', [('synthetic', 1.1), (' ', 1.1), (None, 1.1), ('ecb', 0), ('ecb', float('inf'))])
def test_unreliable_fx_is_not_used_for_performance_or_risk(data, source, value):
    from stocks_tracker.core.portfolio_risk import returns_in_eur

    data.execute("UPDATE instruments SET currency='USD' WHERE ticker='AAA'")
    for day in ['2025-01-01', '2025-01-02', '2025-07-01', '2025-12-31']:
        data.execute("INSERT INTO prices_daily(ticker,date,close,adj_close,source) VALUES ('EURUSD=X',?,?,?,?)",
                     [day, value, value, source])
    assert evaluate(data, hasta='2025-12-31').empty
    returns = returns_in_eur(data, ['AAA'], as_of='2025-12-31')
    assert returns.empty or returns['AAA'].isna().all()


def test_distinct_horizons_are_not_deduplicated_together():
    frame = pd.DataFrame({'ticker': ['A', 'A'], 'model_version': ['v1', 'v1'],
                          'horizonte_meses': [1, 6],
                          'entrada': pd.to_datetime(['2025-01-02', '2025-01-02']),
                          'salida': pd.to_datetime(['2025-02-01', '2025-07-01'])})
    assert len(independent_episodes(frame)) == 2
