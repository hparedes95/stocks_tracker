"""Tests del resumen narrativo, la explicacion y las banderas rojas."""

from __future__ import annotations

import pandas as pd
import pytest

from stocks_tracker.core.config import get_explanations
from stocks_tracker.core.explain import MAX_CONS, MAX_PROS, build_reasons
from stocks_tracker.core.flags import red_flags
from stocks_tracker.core.narrative import (
    MAX_SENTENCES,
    MarketContext,
    contains_forbidden,
    render_market_summary,
)
from stocks_tracker.core.safe_eval import UnsafeExpressionError, compile_condition, evaluate


# ---------------------------------------------------------------------------
# Narrativa
# ---------------------------------------------------------------------------
def test_empty_context_returns_quiet_sentence():
    """Cuando no pasa nada, decirlo es mas util que callar."""
    lines = render_market_summary(MarketContext())
    assert len(lines) == 1
    assert "sin movimientos destacables" in lines[0].lower()


def test_lead_sentence_names_leader_and_laggard():
    ctx = MarketContext(
        sector_leaders=[("Energia", 0.018)],
        sector_laggards=[("Tecnologia", -0.012)],
    )
    lines = render_market_summary(ctx)
    assert "Energia" in lines[0]
    assert "Tecnologia" in lines[0]
    assert "+1.8%" in lines[0]


def test_divergence_rule_fires_when_index_rises_but_breadth_falls():
    """El caso clasico: el indice sube sostenido por cuatro valores."""
    ctx = MarketContext(
        sector_leaders=[("Energia", 0.01)],
        sector_laggards=[("Salud", -0.01)],
        index_ret_1d=0.008,
        advances=120,
        declines=340,
    )
    text = " ".join(render_market_summary(ctx))
    assert "concentrada en pocos nombres" in text


def test_divergence_rule_silent_when_breadth_agrees():
    ctx = MarketContext(
        sector_leaders=[("Energia", 0.01)],
        sector_laggards=[("Salud", -0.01)],
        index_ret_1d=0.008,
        advances=340,
        declines=120,
    )
    text = " ".join(render_market_summary(ctx))
    assert "concentrada en pocos nombres" not in text


def test_breadth_extreme_takes_priority_over_trend():
    ctx = MarketContext(pct_above_sma200=22.0, pct_above_sma200_prev_week=40.0)
    text = " ".join(render_market_summary(ctx))
    assert "extrema baja" in text


def test_breadth_trend_fires_only_on_meaningful_change():
    small = MarketContext(pct_above_sma200=55.0, pct_above_sma200_prev_week=54.0)
    assert "amplitud" not in " ".join(render_market_summary(small)).lower()

    big = MarketContext(pct_above_sma200=55.0, pct_above_sma200_prev_week=45.0)
    assert "amplitud" in " ".join(render_market_summary(big)).lower()


def test_summary_never_exceeds_max_sentences():
    ctx = MarketContext(
        sector_leaders=[("Energia", 0.02)], sector_laggards=[("Salud", -0.02)],
        index_ret_1d=0.01, advances=100, declines=300,
        pct_above_sma200=15.0, pct_above_sma200_prev_week=45.0,
        regime="risk_off", risk_score=-55.0, risk_score_prev=10.0,
        n_breakouts_high=12, n_breakouts_low=1,
        vix=34.0, vix_pctile=0.95,
        n_volume_spikes=22,
        top_signal_counts={"GOLDEN_CROSS": 9},
    )
    assert len(render_market_summary(ctx)) <= MAX_SENTENCES


def test_summary_never_uses_forbidden_language():
    """Guardarrail: la herramienta describe lo que ha pasado, no lo que pasara.

    Es facil colar un 'va a' al anadir una plantilla nueva; este test lo caza.
    """
    contexts = [
        MarketContext(),
        MarketContext(sector_leaders=[("Banca", 0.03)], sector_laggards=[("Ocio", -0.02)]),
        MarketContext(pct_above_sma200=88.0, n_breakouts_high=20),
        MarketContext(regime="risk_off", risk_score=-70, risk_score_prev=5),
        MarketContext(vix=40.0, vix_pctile=0.97, n_volume_spikes=30),
    ]
    for ctx in contexts:
        for line in render_market_summary(ctx):
            assert not contains_forbidden(line), f"Lenguaje prohibido en: {line}"


# ---------------------------------------------------------------------------
# Evaluador seguro
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "expression",
    [
        "__import__('os').system('ls')",
        "open('/etc/passwd').read()",
        "(lambda: 1)()",
        "[].__class__",
        "print(1)",
    ],
)
def test_unsafe_expressions_are_rejected(expression):
    with pytest.raises((UnsafeExpressionError, SyntaxError)):
        compile_condition(expression)
    # Y por la puerta publica, simplemente devuelve False.
    assert evaluate(expression, {}) is False


def test_safe_expressions_evaluate():
    assert evaluate("x > 10 and z <= -0.5", {"x": 20, "z": -1.0}) is True
    assert evaluate("x > 10", {"x": 5}) is False


def test_missing_variable_returns_false_instead_of_raising():
    """Una frase que no se puede evaluar no se muestra; no rompe la pagina."""
    assert evaluate("no_existe > 3", {}) is False


# ---------------------------------------------------------------------------
# Explicacion
# ---------------------------------------------------------------------------
def _row(**overrides) -> pd.Series:
    base = {
        "trailing_pe": 11.2, "price_to_book": 1.1, "roe": 0.19,
        "revenue_growth_yoy": 0.14, "dividend_yield": 0.052,
        "payout_ratio": 0.68, "rsi14": 34.0, "above_sma200": True,
        "mom_12_1": 0.22, "rel_volume_20": 2.3, "dist_52w_high": -0.08,
        "drawdown": -0.10, "net_debt_to_ebitda": 4.1,
        "realized_vol_252": 0.28, "coverage": 0.82, "days_above_sma200": 84,
    }
    base.update(overrides)
    return pd.Series(base)


def test_build_reasons_respects_limits():
    reasons = build_reasons(_row())
    assert len(reasons.pros) <= MAX_PROS
    assert len(reasons.cons) <= MAX_CONS
    assert reasons.pros, "Deberia encontrar motivos a favor con estos datos"


def test_reasons_are_human_readable():
    reasons = build_reasons(_row())
    joined = " ".join(reasons.pros)
    # Frases, no numeros sueltos.
    assert any(len(p.split()) >= 4 for p in reasons.pros)
    assert "%" in joined or "PER" in joined


def test_metric_is_never_both_pro_and_con():
    reasons = build_reasons(_row())
    assert not (set(reasons.pros) & set(reasons.cons))


def test_high_debt_appears_as_concern():
    reasons = build_reasons(_row(net_debt_to_ebitda=5.2))
    assert any("Deuda" in c for c in reasons.cons)


def test_signals_are_translated_to_labels():
    reasons = build_reasons(_row(), active_signals=["PULLBACK_IN_UPTREND"])
    assert reasons.signals == ["Correccion dentro de tendencia alcista"]


def test_all_templates_format_without_error():
    """Ninguna plantilla del YAML puede petar con una fila completa."""
    cfg = get_explanations()
    row = _row()
    reasons = build_reasons(row)
    for phrase in reasons.pros + reasons.cons:
        assert "{" not in phrase, f"Plantilla sin rellenar: {phrase}"
    assert cfg.get("submetrics")


# ---------------------------------------------------------------------------
# Banderas rojas
# ---------------------------------------------------------------------------
def test_red_flags_detect_uncovered_dividend():
    flags = red_flags(_row(payout_ratio=1.4))
    assert any("Dividendo no cubierto" in f for f in flags)


def test_red_flags_detect_downtrend():
    flags = red_flags(_row(above_sma200=False))
    assert any("MM200" in f for f in flags)


def test_red_flags_shown_even_with_good_metrics():
    """Un score alto no puede esconder que el dividendo no esta cubierto."""
    flags = red_flags(_row(payout_ratio=1.2, roe=0.45, revenue_growth_yoy=0.5))
    assert flags


def test_clean_row_has_no_flags():
    flags = red_flags(_row(payout_ratio=0.4, net_debt_to_ebitda=1.0, drawdown=-0.05))
    assert flags == []
