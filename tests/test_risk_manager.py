"""Una prueba por regla del gestor de riesgo.

`risk.py` es el modulo con mas consecuencias del proyecto: es lo unico que
separa una idea de una orden. Cada regla se prueba con una cartera sintetica,
sin broker y sin base de datos.

El test mas importante del fichero es `test_an_internal_failure_vetoes`: si el
modulo que decide si algo es seguro se rompe, la respuesta tiene que ser "no".
Un sistema de riesgo que falla abierto es peor que no tenerlo, porque da
confianza sin darla.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from stocks_tracker.core.config import TradingConfig
from stocks_tracker.trading.context import StrategyContext
from stocks_tracker.trading.intents import Decision, Intent, IntentType, Side
from stocks_tracker.trading.risk import RiskManager

TODAY = date(2024, 6, 3)

RAW = {
    "mode": "simulated", "autonomy": "semi", "capital_cap": 100.0,
    "initial_equity": 100.0,
    "universe": {"allowed": [], "min_price": 5.0,
                 "min_dollar_volume_20d": 20_000_000},
    "risk": {
        "risk_per_trade_pct": 1.5, "atr_stop_mult": 2.5, "min_notional": 1.0,
        "max_position_pct": 22.0, "target_position_pct": 15.0,
        "max_positions": 7, "max_sector_pct": 35.0,
        "max_gross_exposure_pct": 90.0, "min_cash_pct": 10.0,
        "max_daily_loss_pct": 3.0, "max_drawdown_pct": 15.0,
        "max_orders_per_day": 6, "max_orders_per_ticker_per_day": 1,
        "max_new_positions_per_day": 3, "max_day_trades_5d": 2,
        "min_holding_days": 2, "block_days_before_earnings": 3,
        "block_days_after_earnings": 1,
        "block_if_evidence_not_validated": True,
        "min_data_coverage": 0.5, "max_data_staleness_hours": 30,
    },
    "execution": {}, "approval": {}, "kill_switch": {},
    "strategies": {},
}


def cfg(**risk_overrides) -> TradingConfig:
    raw = {**RAW, "risk": {**RAW["risk"], **risk_overrides}}
    return TradingConfig(raw=raw)


def make_ctx(**overrides) -> StrategyContext:
    indicators = pd.DataFrame(
        {"ticker": ["AAA", "BBB", "CCC"],
         "close": [50.0, 50.0, 50.0],
         "atr14": [1.0, 1.0, 1.0],
         "rsi14": [55.0, 55.0, 55.0],
         "above_sma200": [True, True, True]}
    ).set_index("ticker")
    scores = pd.DataFrame(
        {"ticker": ["AAA", "BBB", "CCC"],
         "composite_pctile": [0.95, 0.90, 0.85],
         "coverage": [0.9, 0.9, 0.9]}
    ).set_index("ticker")

    base = dict(
        as_of=TODAY, mode="simulated", equity=100.0, cash=100.0,
        indicators=indicators, scores=scores,
        sectors={"AAA": "Tecnologia", "BBB": "Tecnologia", "CCC": "Banca"},
        dollar_volume_20d={"AAA": 5e8, "BBB": 5e8, "CCC": 5e8},
        last_price_date=TODAY, peak_equity=100.0, day_start_equity=100.0,
    )
    base.update(overrides)
    return StrategyContext(**base)


def buy(ticker="AAA", price=50.0, **kw) -> Intent:
    return Intent(ticker=ticker, side=Side.BUY, intent_type=IntentType.OPEN,
                  ref_price=price, strategy_id="t", notional_requested=15.0,
                  score_pctile=0.9, **kw)


def sell(ticker="AAA", qty=1.0, kind=IntentType.CLOSE) -> Intent:
    return Intent(ticker=ticker, side=Side.SELL, intent_type=kind,
                  ref_price=50.0, strategy_id="t", qty_requested=qty)


def only(manager, intents, ctx):
    return manager.evaluate(intents, ctx)[0]


# ---------------------------------------------------------------------------
# El comportamiento que sostiene todo lo demas
# ---------------------------------------------------------------------------
def test_an_internal_failure_vetoes(monkeypatch):
    """Falla cerrado. Es la propiedad mas importante del modulo."""
    manager = RiskManager(cfg=cfg(), mode="simulated")

    def explode(*args, **kwargs):
        raise RuntimeError("algo se ha roto por dentro")

    monkeypatch.setattr(manager, "_evaluate", explode)
    verdict = only(manager, [buy()], make_ctx())
    assert verdict.decision is Decision.VETO
    assert verdict.reason_code == "RISK_INTERNAL_ERROR"


def test_every_intent_gets_a_verdict_in_the_original_order():
    """La invariante de auditoria: nada se evalua en silencio."""
    manager = RiskManager(cfg=cfg(), mode="simulated")
    intents = [buy("CCC"), buy("AAA"), buy("BBB")]
    verdicts = manager.evaluate(intents, make_ctx())
    assert [v.intent.ticker for v in verdicts] == ["CCC", "AAA", "BBB"]
    assert all(v.reason_text for v in verdicts), "hay veredictos sin explicacion"


def test_a_normal_buy_is_approved():
    verdict = only(RiskManager(cfg=cfg()), [buy()], make_ctx())
    assert verdict.approved
    assert verdict.notional_approved > 0
    assert verdict.stop_price < 50.0


# ---------------------------------------------------------------------------
# 1. kill_switch_active
# ---------------------------------------------------------------------------
def test_halt_new_blocks_openings():
    verdict = only(RiskManager(cfg=cfg()), [buy()], make_ctx(state="HALT_NEW"))
    assert verdict.decision is Decision.VETO
    assert verdict.reason_code == "KILL_SWITCH_HALT_NEW"


def test_halt_new_still_lets_protective_exits_through():
    """Dejar de protegerse cuando algo va mal seria empeorarlo."""
    ctx = make_ctx(state="HALT_NEW",
                   positions={"AAA": {"qty": 1.0, "market_value": 50.0,
                                      "current_price": 50.0}})
    verdict = only(RiskManager(cfg=cfg()),
                   [sell(kind=IntentType.STOP_EXIT)], ctx)
    assert verdict.approved
    assert verdict.reason_code == "PROTECTIVE_EXIT"


def test_halted_blocks_even_protective_exits():
    """Tras liquidar ya no queda nada que proteger, y el bot no vuelve a mandar
    ordenes hasta que una persona lo rearme."""
    ctx = make_ctx(state="HALTED",
                   positions={"AAA": {"qty": 1.0, "market_value": 50.0}})
    verdict = only(RiskManager(cfg=cfg()),
                   [sell(kind=IntentType.STOP_EXIT)], ctx)
    assert verdict.decision is Decision.VETO
    assert verdict.reason_code == "KILL_SWITCH_HALTED"


# ---------------------------------------------------------------------------
# 2. stale_data
# ---------------------------------------------------------------------------
def test_stale_prices_stop_everything():
    ctx = make_ctx(last_price_date=TODAY - timedelta(days=10))
    manager = RiskManager(cfg=cfg())
    verdict = only(manager, [buy()], ctx)
    assert verdict.reason_code == "STALE_DATA"
    assert any(v["rule_id"] == "stale_data" for v in manager.violations)


def test_fresh_prices_do_not_trigger_it():
    ctx = make_ctx(last_price_date=TODAY - timedelta(days=1))
    assert only(RiskManager(cfg=cfg()), [buy()], ctx).approved


# ---------------------------------------------------------------------------
# 3. max_drawdown  /  4. daily_loss
# ---------------------------------------------------------------------------
def test_max_drawdown_triggers_the_kill_switch():
    ctx = make_ctx(equity=80.0, peak_equity=100.0)
    manager = RiskManager(cfg=cfg())
    verdict = only(manager, [buy()], ctx)
    assert verdict.reason_code == "MAX_DRAWDOWN"
    violation = next(v for v in manager.violations if v["rule_id"] == "max_drawdown")
    assert violation["severity"] == "kill"
    assert violation["action_taken"] == "flatten"


def test_daily_loss_only_halts_new_business():
    ctx = make_ctx(equity=95.0, day_start_equity=100.0, peak_equity=100.0)
    manager = RiskManager(cfg=cfg())
    verdict = only(manager, [buy()], ctx)
    assert verdict.reason_code == "DAILY_LOSS"
    violation = next(v for v in manager.violations if v["rule_id"] == "daily_loss")
    assert violation["action_taken"] == "halt_new"


def test_a_drawdown_below_the_limit_does_not_trigger():
    ctx = make_ctx(equity=90.0, peak_equity=100.0, day_start_equity=90.0)
    assert only(RiskManager(cfg=cfg()), [buy()], ctx).approved


# ---------------------------------------------------------------------------
# 6. pdt_limit  /  16. min_holding_days
# ---------------------------------------------------------------------------
def test_min_holding_days_blocks_an_early_close():
    ctx = make_ctx(
        positions={"AAA": {"qty": 1.0, "market_value": 50.0}},
        bot_positions={"AAA": {"opened_at": TODAY}},
    )
    verdict = only(RiskManager(cfg=cfg()), [sell()], ctx)
    assert verdict.reason_code == "MIN_HOLDING_DAYS"


def test_a_stop_exit_ignores_the_holding_period():
    """Protegerse tiene prioridad sobre evitar un day trade."""
    ctx = make_ctx(
        positions={"AAA": {"qty": 1.0, "market_value": 50.0}},
        bot_positions={"AAA": {"opened_at": TODAY}},
    )
    verdict = only(RiskManager(cfg=cfg()),
                   [sell(kind=IntentType.STOP_EXIT)], ctx)
    assert verdict.approved


def test_closing_a_position_you_do_not_have_is_vetoed():
    verdict = only(RiskManager(cfg=cfg()), [sell("ZZZ")], make_ctx())
    assert verdict.reason_code == "NO_POSITION"


# ---------------------------------------------------------------------------
# 7. max_orders_per_day  /  10. max_positions
# ---------------------------------------------------------------------------
def test_the_daily_order_budget_is_enforced():
    ctx = make_ctx(orders_today=6)
    verdict = only(RiskManager(cfg=cfg()), [buy()], ctx)
    assert verdict.reason_code == "MAX_ORDERS_PER_DAY"


def test_the_position_count_is_capped():
    positions = {f"P{i}": {"qty": 1.0, "market_value": 1.0} for i in range(7)}
    ctx = make_ctx(positions=positions,
                   sectors={**{f"P{i}": "Otros" for i in range(7)},
                            "AAA": "Tecnologia"})
    verdict = only(RiskManager(cfg=cfg()), [buy()], ctx)
    assert verdict.reason_code == "MAX_POSITIONS"


def test_new_positions_per_day_are_capped():
    manager = RiskManager(cfg=cfg(max_new_positions_per_day=2))
    verdicts = manager.evaluate([buy("AAA"), buy("BBB"), buy("CCC")], make_ctx())
    codes = [v.reason_code for v in verdicts]
    assert codes.count("MAX_NEW_POSITIONS") == 1


def test_the_scarce_budget_goes_to_the_best_candidates():
    """Si solo cabe una orden, que sea la mejor y no la primera de la lista."""
    manager = RiskManager(cfg=cfg(max_new_positions_per_day=1))
    weak = buy("CCC")
    strong = buy("AAA")
    object.__setattr__(weak, "score_pctile", 0.50)
    object.__setattr__(strong, "score_pctile", 0.99)
    verdicts = {v.intent.ticker: v for v in manager.evaluate([weak, strong],
                                                             make_ctx())}
    assert verdicts["AAA"].approved
    assert not verdicts["CCC"].approved


# ---------------------------------------------------------------------------
# 8. min_cash  /  9. max_gross_exposure  /  11. max_sector_pct  /  12. max_position_pct
# ---------------------------------------------------------------------------
def test_the_cash_reserve_survives():
    ctx = make_ctx(cash=10.0, equity=100.0)
    verdict = only(RiskManager(cfg=cfg()), [buy()], ctx)
    assert verdict.reason_code == "MIN_CASH"


def test_the_sector_cap_resizes_before_vetoing():
    ctx = make_ctx(
        positions={"BBB": {"qty": 1.0, "market_value": 30.0}},
        cash=70.0,
    )
    verdict = only(RiskManager(cfg=cfg()), [buy("AAA")], ctx)
    # Tecnologia esta en 30 de un tope de 35: solo caben 5 mas.
    assert verdict.approved
    assert verdict.notional_approved == pytest.approx(5.0)
    assert verdict.decision is Decision.RESIZE


def test_a_full_sector_is_vetoed():
    ctx = make_ctx(positions={"BBB": {"qty": 1.0, "market_value": 35.0}},
                   cash=65.0)
    verdict = only(RiskManager(cfg=cfg()), [buy("AAA")], ctx)
    assert verdict.reason_code == "MAX_SECTOR_PCT"


def test_the_per_asset_cap_counts_what_is_already_held():
    ctx = make_ctx(positions={"AAA": {"qty": 1.0, "market_value": 22.0}},
                   cash=78.0)
    verdict = only(RiskManager(cfg=cfg()), [buy("AAA")], ctx)
    assert verdict.reason_code == "MAX_POSITION_PCT"


def test_gross_exposure_is_capped():
    positions = {"AAA": {"qty": 1.0, "market_value": 45.0},
                 "CCC": {"qty": 1.0, "market_value": 45.0}}
    ctx = make_ctx(positions=positions, cash=10.0, equity=100.0,
                   sectors={"AAA": "Tecnologia", "CCC": "Banca",
                            "BBB": "Salud"})
    verdict = only(RiskManager(cfg=cfg(min_cash_pct=0.0)), [buy("BBB")], ctx)
    assert verdict.reason_code == "MAX_GROSS_EXPOSURE"


def test_several_intents_cannot_break_a_limit_together():
    """Evaluadas contra la cartera actual, cinco compras podrian aprobarse
    todas y romper juntas un tope que ninguna rompia por separado."""
    manager = RiskManager(cfg=cfg(max_sector_pct=20.0))
    verdicts = manager.evaluate([buy("AAA"), buy("BBB")], make_ctx())
    total = sum(v.notional_approved or 0.0 for v in verdicts)
    assert total <= 20.0 + 1e-9, f"entre las dos suman {total} sobre un tope de 20"


# ---------------------------------------------------------------------------
# 14. min_notional  /  13. position_sizing_atr
# ---------------------------------------------------------------------------
def test_an_order_below_the_broker_minimum_is_vetoed():
    """Quedan 0,50 libres y el broker no acepta menos de 5. El motivo que se
    registra tiene que ser la caja, no la volatilidad: son investigaciones
    distintas."""
    ctx = make_ctx(equity=100.0, cash=10.5)
    verdict = only(RiskManager(cfg=cfg(min_notional=5.0)), [buy()], ctx)
    assert verdict.reason_code == "MIN_NOTIONAL_ABOVE_CASH"
    assert "efectivo" in verdict.reason_text


def test_a_position_too_small_for_the_cap_reports_the_cap():
    """El otro motivo, con su propio codigo: aqui si sobra efectivo, lo que no
    cabe es el minimo del broker dentro del tope por activo."""
    # Cartera de 10: el tope por activo son 2,20 y el minimo del broker 5.
    ctx = make_ctx(equity=10.0, cash=10.0, peak_equity=10.0,
                   day_start_equity=10.0)
    verdict = only(RiskManager(cfg=cfg(min_notional=5.0)), [buy()], ctx)
    assert verdict.reason_code == "POSITION_TOO_SMALL_FOR_RISK"


def test_no_atr_means_no_entry():
    indicators = pd.DataFrame(
        {"ticker": ["AAA"], "close": [50.0], "atr14": [None],
         "rsi14": [55.0], "above_sma200": [True]}
    ).set_index("ticker")
    verdict = only(RiskManager(cfg=cfg()), [buy()], make_ctx(indicators=indicators))
    assert verdict.reason_code == "NO_ATR"


# ---------------------------------------------------------------------------
# 15. earnings_blackout
# ---------------------------------------------------------------------------
def test_earnings_inside_the_window_block_the_entry():
    ctx = make_ctx(earnings={"AAA": [TODAY + timedelta(days=2)]})
    verdict = only(RiskManager(cfg=cfg()), [buy()], ctx)
    assert verdict.reason_code == "EARNINGS_BLACKOUT"


def test_earnings_far_away_do_not_block():
    ctx = make_ctx(earnings={"AAA": [TODAY + timedelta(days=30)]})
    assert only(RiskManager(cfg=cfg()), [buy()], ctx).approved


# ---------------------------------------------------------------------------
# 17. evidence_gate
# ---------------------------------------------------------------------------
def test_an_unvalidated_signal_cannot_be_traded():
    """El limite que da sentido a todo lo anterior: sin el, el bot operaria
    senales que nadie ha comprobado."""
    intent = buy()
    object.__setattr__(intent, "rationale", {"signals": ["PULLBACK_IN_UPTREND"]})
    ctx = make_ctx(evidence={"PULLBACK_IN_UPTREND": "no_validada"})
    verdict = only(RiskManager(cfg=cfg()), [intent], ctx)
    assert verdict.reason_code == "EVIDENCE_NOT_VALIDATED"


def test_a_validated_signal_passes():
    intent = buy()
    object.__setattr__(intent, "rationale", {"signals": ["PULLBACK_IN_UPTREND"]})
    ctx = make_ctx(evidence={"PULLBACK_IN_UPTREND": "validada"})
    assert only(RiskManager(cfg=cfg()), [intent], ctx).approved


def test_a_signal_with_no_evidence_row_is_treated_as_unvalidated():
    intent = buy()
    object.__setattr__(intent, "rationale", {"signals": ["DESCONOCIDA"]})
    verdict = only(RiskManager(cfg=cfg()), [intent], make_ctx())
    assert verdict.reason_code == "EVIDENCE_NOT_VALIDATED"


# ---------------------------------------------------------------------------
# 18. universe_gate
# ---------------------------------------------------------------------------
def test_a_ticker_outside_the_universe_is_vetoed():
    ctx = make_ctx(universe_allowed={"BBB"})
    verdict = only(RiskManager(cfg=cfg()), [buy("AAA")], ctx)
    assert verdict.reason_code == "OUT_OF_UNIVERSE"


def test_a_cheap_stock_is_vetoed():
    verdict = only(RiskManager(cfg=cfg()), [buy("AAA", price=2.0)], make_ctx())
    assert verdict.reason_code == "PRICE_TOO_LOW"


def test_an_illiquid_stock_is_vetoed():
    ctx = make_ctx(dollar_volume_20d={"AAA": 1_000_000.0})
    verdict = only(RiskManager(cfg=cfg()), [buy()], ctx)
    assert verdict.reason_code == "ILLIQUID"


def test_poor_factor_coverage_is_vetoed():
    scores = pd.DataFrame(
        {"ticker": ["AAA"], "composite_pctile": [0.95], "coverage": [0.2]}
    ).set_index("ticker")
    verdict = only(RiskManager(cfg=cfg()), [buy()], make_ctx(scores=scores))
    assert verdict.reason_code == "LOW_COVERAGE"


# ---------------------------------------------------------------------------
# 20. duplicate_intent
# ---------------------------------------------------------------------------
def test_a_second_order_for_the_same_ticker_today_is_vetoed():
    ctx = make_ctx(tickers_ordered_today={"AAA"})
    verdict = only(RiskManager(cfg=cfg()), [buy()], ctx)
    assert verdict.reason_code == "DUPLICATE_INTENT"


def test_two_intents_for_the_same_ticker_in_one_cycle():
    manager = RiskManager(cfg=cfg())
    verdicts = manager.evaluate([buy("AAA"), buy("AAA")], make_ctx())
    assert sum(1 for v in verdicts if v.approved) == 1


# ---------------------------------------------------------------------------
# Acunacion
# ---------------------------------------------------------------------------
def test_only_approved_verdicts_become_orders():
    manager = RiskManager(cfg=cfg())
    ctx = make_ctx(universe_allowed={"AAA"})
    verdicts = manager.evaluate([buy("AAA"), buy("BBB")], ctx)
    orders = manager.approve(verdicts)
    assert [o.ticker for o in orders] == ["AAA"]


def test_the_order_carries_a_deterministic_id():
    """Es lo que hace idempotente el reenvio tras una caida."""
    manager = RiskManager(cfg=cfg())
    verdicts = manager.evaluate([buy()], make_ctx())
    order = manager.approve(verdicts)[0]
    assert order.client_order_id == f"st-{order.intent_id}"


def test_a_buy_order_uses_notional_and_a_sell_uses_quantity():
    """Alpaca compra por importe y vende por cantidad; mezclarlo lo rechaza."""
    manager = RiskManager(cfg=cfg())
    ctx = make_ctx(positions={"BBB": {"qty": 2.0, "market_value": 100.0}},
                   bot_positions={"BBB": {"opened_at": TODAY - timedelta(days=30)}})
    orders = manager.approve(manager.evaluate([buy("AAA"), sell("BBB", qty=2.0)], ctx))
    by_ticker = {o.ticker: o for o in orders}
    assert by_ticker["AAA"].notional is not None
    assert by_ticker["BBB"].notional is None
    assert by_ticker["BBB"].qty == pytest.approx(2.0)
