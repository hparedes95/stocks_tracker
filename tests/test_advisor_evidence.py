from datetime import date, timedelta

import duckdb
import pandas as pd
import pytest

from stocks_tracker.core import advisor_evidence as gate
from stocks_tracker.core.advice import Conviccion, Recomendacion, Veredicto
from stocks_tracker.core.db import schema_path
from stocks_tracker.core.investor_profile import DEFAULTS, save
from stocks_tracker.core.predictive_validation import Validation


@pytest.fixture
def connection():
    c = duckdb.connect(':memory:')
    c.execute(schema_path().read_text(encoding='utf-8'))
    c.execute("INSERT INTO instruments(ticker,country,currency) VALUES ('AAA','Spain','EUR')")
    yield c
    c.close()


def proposal(ticker='AAA'):
    return Recomendacion(ticker, Veredicto.COMPRAR, Conviccion.ALTA, importe_eur=1000., titulos=10, stop=90)


def run(c, recs=None, **kwargs):
    return gate.apply_gate(c, recs or [proposal()], as_of=kwargs.get('as_of', date.today()),
                           positions=kwargs.get('positions', pd.DataFrame()), cash=kwargs.get('cash', 10000.))


def test_unconfirmed_profile_and_insufficient_history_block_buy(connection):
    result = run(connection)[0]
    assert result.veredicto == Veredicto.SIN_OPINION
    assert result.importe_eur is None
    assert any('Confirma' in x for x in result.motivos)


def test_qualified_evidence_can_pass_but_does_not_create_orders(connection, monkeypatch):
    save(connection, {**DEFAULTS, 'confirmed':True})
    monkeypatch.setattr(gate, 'validate_models', lambda *a, **k: Validation('evidencia de prueba',30,True,.1))
    result = run(connection)[0]
    assert result.veredicto == Veredicto.COMPRAR
    assert result.benchmark == 'EWP'
    assert result.roundtrip_cost == .01
    assert connection.execute('SELECT COUNT(*) FROM orders').fetchone()[0] == 0


def test_stale_data_never_become_current_buy(connection, monkeypatch):
    save(connection, {**DEFAULTS, 'confirmed':True})
    monkeypatch.setattr(gate, 'validate_models', lambda *a, **k: Validation('test',30,True,.1))
    assert run(connection, as_of=date.today()-timedelta(days=8))[0].veredicto == Veredicto.SIN_OPINION


def test_deterioration_warning_not_silenced_by_lack_of_prediction(connection):
    sell = Recomendacion('AAA',Veredicto.REDUCIR,Conviccion.ALTA,motivos=['concentración'])
    assert run(connection,[sell])[0] == sell


def test_event_and_cash_cost_guards(connection, monkeypatch):
    save(connection, {**DEFAULTS, 'confirmed':True})
    monkeypatch.setattr(gate, 'validate_models', lambda *a, **k: Validation('test',30,True,.1))
    connection.execute("INSERT INTO earnings_events(ticker,report_date) VALUES ('AAA',?)", [date.today()])
    result = run(connection, cash=1000)[0]
    assert result.veredicto == Veredicto.SIN_OPINION
    assert any('resultados' in s for s in result.motivos)
    assert any('coste de entrada' in s for s in result.motivos)


def test_unknown_currency_does_not_default_to_eur():
    from stocks_tracker.core.advice_build import de_los_candidatos
    row = pd.DataFrame([{'ticker':'A','composite_pctile':.99,'coverage':.99,'close':100.,'atr_pct':2.}])
    result = de_los_candidatos(row,equity=10000,caja=5000)[0]
    assert result.veredicto == Veredicto.SIN_OPINION


def test_profile_rejects_nonfinite_limits(connection):
    with pytest.raises(ValueError):
        save(connection, {**DEFAULTS, 'max_loss_pct':float('nan')})
