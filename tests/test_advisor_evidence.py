from datetime import date, timedelta

import duckdb
import pandas as pd
import pytest

from stocks_tracker.core import advisor_evidence as gate
from stocks_tracker.core.advice import Conviccion, Recomendacion, Veredicto
from stocks_tracker.core.db import schema_path
from stocks_tracker.core.investor_profile import DEFAULTS, save
from stocks_tracker.core.predictive_validation import Validation
from stocks_tracker.core.trading_calendar import sessions


@pytest.fixture
def connection():
    c = duckdb.connect(':memory:')
    c.execute(schema_path().read_text(encoding='utf-8'))
    c.execute("INSERT INTO instruments(ticker,country,currency) VALUES ('AAA','Spain','EUR'), ('EWP','US','USD')")
    days = sessions('XMAD', date.today() - timedelta(days=450), date.today())[-260:]
    prices = pd.DataFrame({'ticker': 'AAA', 'date': days, 'adj_close': range(100, 100 + len(days)),
                           'close': range(100, 100 + len(days)), 'source': 'fixture'})
    from stocks_tracker.core.db import upsert_df
    upsert_df(c, 'prices_daily', prices, keys=['ticker', 'date'])
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


def test_partial_portfolio_blocks_new_buys_even_with_finite_placeholder_value(connection, monkeypatch):
    save(connection, {**DEFAULTS, 'confirmed': True})
    monkeypatch.setattr(gate, 'validate_models', lambda *a, **k: Validation('test',30,True,.1))
    positions = pd.DataFrame([{'ticker':'unresolved', 'broker_symbol':'CIBR', 'valor_eur':100.,
                              'supported':False, 'analysis_supported':False, 'reason':'Producto CFD sin modelo de margen.'}])
    result = run(connection, positions=positions)[0]
    assert result.veredicto == Veredicto.SIN_OPINION and result.importe_eur is None
    assert any('CIBR' in reason and 'parcial' in reason for reason in result.motivos)
    assert 'parcial' in result.assessment['encaje']
    warning = Recomendacion('AAA',Veredicto.REDUCIR,Conviccion.ALTA,motivos=['Aviso independiente de riesgo.'])
    assert run(connection, [warning], positions=positions)[0] == warning


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


@pytest.mark.parametrize('bound', [float('nan'), float('inf'), float('-inf')])
def test_nonfinite_evidence_never_authorizes_purchase(connection, monkeypatch, bound):
    save(connection, {**DEFAULTS, 'confirmed': True, 'recommendation_mode': 'validated'})
    monkeypatch.setattr(gate, 'validate_models', lambda *a, **k: Validation('test', 30, True, bound))
    assert run(connection)[0].veredicto == Veredicto.SIN_OPINION


def test_one_unpriced_lot_cannot_disappear_from_position_total(connection, monkeypatch):
    save(connection, {**DEFAULTS, 'confirmed': True})
    monkeypatch.setattr(gate, 'validate_models', lambda *a, **k: Validation('test', 30, True, .1))
    positions = pd.DataFrame({'ticker': ['AAA', 'AAA'], 'valor_eur': [100., float('nan')]})
    result = run(connection, positions=positions)[0]
    assert result.veredicto == Veredicto.SIN_OPINION
    assert any('valorar íntegramente' in reason for reason in result.motivos)


@pytest.mark.parametrize('field,value', [('importe_eur', float('nan')), ('importe_eur', -100.),
    ('importe_eur', 0.), ('titulos', float('inf')), ('titulos', -1.), ('stop', float('nan'))])
def test_invalid_purchase_numbers_are_rejected(connection, monkeypatch, field, value):
    from dataclasses import replace
    save(connection, {**DEFAULTS, 'confirmed': True})
    monkeypatch.setattr(gate, 'validate_models', lambda *a, **k: Validation('test', 30, True, .1))
    result = run(connection, [replace(proposal(), **{field: value})])[0]
    assert result.veredicto == Veredicto.SIN_OPINION
    assert result.importe_eur is None and result.titulos is None


@pytest.mark.parametrize('cash', [None, 'invalid', float('nan'), -1])
def test_invalid_cash_blocks_instead_of_crashing(connection, cash):
    assert run(connection, cash=cash)[0].veredicto == Veredicto.SIN_OPINION


def test_rules_can_recommend_without_waiting_years_but_do_not_claim_validation(connection):
    save(connection, {**DEFAULTS, 'confirmed': True})
    rec = run(connection)[0]
    assert rec.veredicto == Veredicto.COMPRAR
    assert rec.conviccion == Conviccion.BAJA
    assert rec.assessment['decision_basis'] == 'rules'
    assert 'sin ventaja predictiva demostrada' in rec.assessment['evidencia']
    assert connection.execute('SELECT COUNT(*) FROM orders').fetchone()[0] == 0
    save(connection, {**DEFAULTS, 'confirmed': True, 'recommendation_mode': 'validated'})
    assert run(connection)[0].veredicto == Veredicto.SIN_OPINION


@pytest.mark.parametrize('mutation', [
    "DELETE FROM prices_daily",
    "UPDATE prices_daily SET source='synthetic'",
    "UPDATE prices_daily SET adj_close=-1",
    "INSERT INTO prices_quarantine(ticker,date) SELECT ticker,MAX(date) FROM prices_daily GROUP BY ticker",
])
def test_rules_never_bypass_price_quality(connection, mutation):
    save(connection, {**DEFAULTS, 'confirmed': True})
    connection.execute(mutation)
    assert run(connection)[0].veredicto == Veredicto.SIN_OPINION
