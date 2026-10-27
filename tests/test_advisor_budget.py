"""Las recomendaciones comparten presupuesto y plazas, no lo reutilizan."""
import pandas as pd
import pytest

from stocks_tracker.core.advice import Veredicto
from stocks_tracker.core.advice_build import de_los_candidatos


def ranking(n=25, sector="Tech"):
    return pd.DataFrame([{
        "ticker": f"A{i}", "composite_pctile": .98, "coverage": .9,
        "close": 100., "atr_pct": 2., "currency": "EUR",
        "gics_sector": sector if sector else str(i),
    } for i in range(n)])


def purchases(recommendations):
    return [r for r in recommendations if r.veredicto in (
        Veredicto.COMPRAR, Veredicto.AMPLIAR)]


def test_twenty_five_candidates_do_not_spend_the_same_cash():
    proposals = purchases(de_los_candidatos(
        ranking(), equity=10000., caja=2000.))
    assert proposals
    assert sum(r.importe_eur for r in proposals) <= 1000. + 1e-8


def test_new_positions_do_not_exceed_remaining_slots():
    proposals = purchases(de_los_candidatos(
        ranking(sector=None), equity=10000., caja=9000., n_posiciones=6))
    assert len(proposals) == 1


def test_sector_room_caps_the_size_and_input_is_not_mutated():
    sectors = {"Tech": 34.}
    positions = {"A0": 2.}
    proposals = purchases(de_los_candidatos(
        ranking(), equity=10000., caja=9000.,
        pesos_sector=sectors, pesos_actuales=positions))
    assert sum(r.importe_eur for r in proposals) == pytest.approx(100.)
    assert sectors == {"Tech": 34.}
    assert positions == {"A0": 2.}


def test_duplicate_ticker_does_not_create_duplicate_proposals():
    data = ranking(1)
    proposals = purchases(de_los_candidatos(
        pd.concat([data, data]), equity=10000., caja=9000.))
    assert len(proposals) == 1


def test_rejected_candidate_does_not_consume_budget_or_slot():
    from dataclasses import replace
    def review(rec):
        if rec.ticker == 'A0':
            return replace(rec, veredicto=Veredicto.SIN_OPINION, importe_eur=None, titulos=None)
        return rec
    proposals = purchases(de_los_candidatos(ranking(2, sector=None), equity=10000.,
                          caja=9000., n_posiciones=6, review=review, entry_cost=.005))
    assert [r.ticker for r in proposals] == ['A1']


def test_owned_position_beyond_top_25_is_still_considered_first():
    result = de_los_candidatos(ranking(40, sector=None), equity=10000., caja=9000.,
                              pesos_actuales={'A39': 1.}, n_posiciones=1)
    assert result[0].ticker == 'A39'
    assert result[0].veredicto == Veredicto.AMPLIAR
