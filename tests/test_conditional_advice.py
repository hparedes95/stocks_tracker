"""A CFD must not suppress other assets or create fictitious allocation."""
from dataclasses import replace
from datetime import date

import pandas as pd
import pytest

from stocks_tracker.core import advisor_evidence as gate
from stocks_tracker.core.advice import CONDICIONALES, Veredicto
from stocks_tracker.core.investor_profile import DEFAULTS, save
from tests.test_advisor_evidence import connection, proposal, run  # noqa: F401


def partial():
    return pd.DataFrame([{'ticker': 'SnapTrade:' + t, 'broker_symbol': t,
        'valor_eur': float('nan'), 'supported': False, 'analysis_supported': False,
        'reason': 'CFD sin valoración del contrato.'} for t in ('CIBR', 'ITA')])


@pytest.mark.parametrize('verdict,expected', [(Veredicto.COMPRAR, Veredicto.COMPRA_CONDICIONADA),
    (Veredicto.AMPLIAR, Veredicto.AMPLIACION_CONDICIONADA)])
def test_partial_gate_keeps_other_assets_and_never_reserves_cash(connection, verdict, expected):  # noqa: F811
    save(connection, {**DEFAULTS, 'confirmed': True})
    check = gate.build_gate(connection, as_of=date.today(), positions=partial(), cash=10, tickers=['AAA'])
    rec = replace(proposal(), veredicto=verdict, importe_eur=None, titulos=None, stop=None,
                  assessment={'sizing': 'pending'})
    first, second = check(rec), check(rec)
    assert first == second and first.veredicto == expected
    assert not first.accionable
    assert first.importe_eur is first.titulos is first.stop is first.riesgo_eur is None
    assert 'CIBR' in first.assessment['encaje'] and 'ITA' in first.assessment['encaje']


@pytest.mark.parametrize('mutation', [
    "DELETE FROM prices_daily",
    "UPDATE prices_daily SET source='synthetic'",
    "INSERT INTO prices_quarantine(ticker,date) SELECT ticker,MAX(date) FROM prices_daily GROUP BY ticker",
    "INSERT INTO earnings_events(ticker,report_date) VALUES ('AAA',current_date)",
])
def test_partial_portfolio_does_not_bypass_candidate_checks(connection, mutation):  # noqa: F811
    save(connection, {**DEFAULTS, 'confirmed': True})
    connection.execute(mutation)
    assert run(connection, positions=partial())[0].veredicto == Veredicto.SIN_OPINION


@pytest.mark.parametrize('cash', [0, -1, None, float('nan')])
def test_partial_portfolio_still_requires_verified_positive_cash(connection, cash):  # noqa: F811
    save(connection, {**DEFAULTS, 'confirmed': True})
    assert run(connection, positions=partial(), cash=cash)[0].veredicto == Veredicto.SIN_OPINION


def test_unsupported_candidate_itself_never_becomes_conditional(connection):  # noqa: F811
    save(connection, {**DEFAULTS, 'confirmed': True})
    book = partial()
    book.loc[0, 'broker_symbol'] = 'AAA'
    assert run(connection, positions=book)[0].veredicto == Veredicto.SIN_OPINION
    save(connection, {**DEFAULTS, 'confirmed': True, 'recommendation_mode': 'validated'})
    assert run(connection, positions=partial())[0].veredicto == Veredicto.SIN_OPINION


def test_resolving_partial_book_restores_normal_sized_purchase(connection):  # noqa: F811
    save(connection, {**DEFAULTS, 'confirmed': True})
    assert run(connection, positions=partial())[0].veredicto in CONDICIONALES
    rec = run(connection)[0]
    assert rec.veredicto == Veredicto.COMPRAR and rec.importe_eur == 1000
