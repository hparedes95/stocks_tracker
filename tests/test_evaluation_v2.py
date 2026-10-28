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


def test_conditional_advice_is_not_scored_and_does_not_hide_later_purchase(data):
    import json

    data.execute("UPDATE recommendations SET model_version='test', veredicto='comprar_condicionado'")
    row = data.execute('SELECT * FROM recommendations').fetchdf().iloc[0].to_dict()
    data.execute("INSERT INTO recommendation_ledger VALUES ('conditional','2025-01-01',?)",
                 [json.dumps(row, default=str)])
    assert evaluate(data, hasta='2025-12-31').empty
    row['veredicto'] = 'comprar'
    data.execute("INSERT INTO recommendation_ledger VALUES ('purchase','2025-01-01 01:00:00',?)",
                 [json.dumps(row, default=str)])
    results = evaluate(data, hasta='2025-12-31')
    assert len(results) == 1 and results.iloc[0]['veredicto'] == 'comprar'
    assert results.iloc[0]['retorno'] == pytest.approx(.20)


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


def test_first_closed_result_survives_provider_correction(data):
    from stocks_tracker.core.db import upsert_df
    from stocks_tracker.core.evaluation import freeze_evaluations

    data.execute("UPDATE recommendations SET emitida_at='2025-01-01 12:00:00'")
    freeze_evaluations(data, hasta='2025-07-01')
    corrected = data.execute("SELECT * FROM prices_daily WHERE ticker='AAA' AND date='2025-07-01'").fetchdf()
    corrected['adj_close'] = 90.
    upsert_df(data, 'prices_daily', corrected, ['ticker', 'date'])
    freeze_evaluations(data, hasta='2025-12-31')
    result = evaluate(data, hasta='2025-12-31')
    assert len(result) == 1
    assert result.iloc[0]['retorno'] == pytest.approx(.2)
    assert data.execute('SELECT COUNT(*) FROM evaluation_ledger').fetchone()[0] == 1
    assert data.execute('SELECT COUNT(*) FROM price_revisions').fetchone()[0] == 1
    upsert_df(data, 'prices_daily', corrected, ['ticker', 'date'])
    assert data.execute('SELECT COUNT(*) FROM price_revisions').fetchone()[0] == 1


def test_fx_reference_excludes_future_and_stale_prices(data):
    from stocks_tracker.core.fx import tipos

    data.execute("INSERT INTO prices_daily(ticker,date,close,source) VALUES "
                 "('EURUSD=X','2025-01-01',1.1,'ecb'), ('EURUSD=X','2025-02-01',1.2,'ecb')")
    assert tipos(data, '2025-01-02')['USD'] == 1.1
    assert tipos(data, '2025-01-20') == {}
