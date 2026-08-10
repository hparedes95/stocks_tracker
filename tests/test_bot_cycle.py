"""Ciclo completo del bot contra el broker simulado. Sin red y sin almacen real.

Prueba lo que ninguna pieza suelta puede probar: que estrategia, riesgo,
ejecucion y registro encajan. Y sobre todo, la invariante de auditoria — que
todo candidato deja rastro, tambien los descartados—, porque sin ella "por que
no compraste X el martes" no tiene respuesta.
"""

from __future__ import annotations

from datetime import date

import duckdb
import pandas as pd
import pytest

from stocks_tracker.core import db
from stocks_tracker.trading import journal
from stocks_tracker.trading.brokers.simulated import SimulatedBroker
from stocks_tracker.trading.context import StrategyContext
from stocks_tracker.trading.risk import RiskManager
from stocks_tracker.trading.run_bot import run_cycle
from stocks_tracker.trading.strategies.momentum_multifactor import MomentumMultifactor
from tests.test_risk_manager import cfg

MONDAY = date(2024, 6, 3)


@pytest.fixture
def warehouse(tmp_path, monkeypatch):
    """Almacen temporal. Ningun test toca la base real del usuario."""
    path = tmp_path / "test.duckdb"

    class Stub:
        warehouse_path = path

    monkeypatch.setattr(db, "get_settings", lambda: Stub())
    db.migrate()
    return path


def prices() -> pd.DataFrame:
    rows = []
    for i, day in enumerate(pd.bdate_range("2024-06-03", periods=6)):
        for ticker, base in (("AAA", 50.0), ("BBB", 40.0), ("CCC", 30.0)):
            price = base + i
            rows.append((day.date(), ticker, price, price + 1, price - 1, price))
    return pd.DataFrame(
        rows, columns=["date", "ticker", "open", "high", "low", "close"]
    ).assign(volume=1_000_000)


def context(**overrides) -> StrategyContext:
    indicators = pd.DataFrame(
        {"ticker": ["AAA", "BBB", "CCC"],
         "close": [50.0, 40.0, 30.0],
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
        as_of=MONDAY, mode="simulated", equity=100.0, cash=100.0,
        indicators=indicators, scores=scores,
        sectors={"AAA": "Tecnologia", "BBB": "Banca", "CCC": "Salud"},
        dollar_volume_20d={"AAA": 5e8, "BBB": 5e8, "CCC": 5e8},
        last_price_date=MONDAY, peak_equity=100.0, day_start_equity=100.0,
    )
    base.update(overrides)
    return StrategyContext(**base)


# ---------------------------------------------------------------------------
def test_a_full_cycle_proposes_approves_and_submits(warehouse):
    broker = SimulatedBroker(prices=prices(), initial_cash=100.0, slippage_bps=0.0)
    log = journal.Journal(run_id="R1", mode="simulated", strategy_id="s")

    result = run_cycle(context(), MomentumMultifactor(), RiskManager(cfg=cfg()),
                       log, broker=broker)

    assert result.n_intents > 0, "la estrategia no ha propuesto nada"
    assert result.n_approved > 0, "el riesgo no ha aprobado nada"
    assert result.n_submitted == result.n_approved

    broker.advance()
    assert broker.get_positions(), "no se ha ejecutado ninguna compra"


def test_every_candidate_leaves_a_trace(warehouse):
    """La invariante de auditoria, incluidos los descartados."""
    broker = SimulatedBroker(prices=prices(), initial_cash=100.0, slippage_bps=0.0)
    log = journal.Journal(run_id="R2", mode="simulated", strategy_id="s")

    # Un sector saturado hace que una de las tres se vete: las tres tienen que
    # aparecer igualmente en el registro.
    ctx = context(positions={"ZZZ": {"qty": 1.0, "market_value": 35.0}},
                  cash=65.0,
                  sectors={"AAA": "Tecnologia", "BBB": "Banca", "CCC": "Salud",
                           "ZZZ": "Tecnologia"})
    run_cycle(ctx, MomentumMultifactor(), RiskManager(cfg=cfg()), log, broker=broker)
    counts = log.flush()

    assert counts["decision_log"] >= 3
    rows = db.query("SELECT ticker, decision, reason_code, reason_text "
                    "FROM decision_log ORDER BY ticker")
    assert set(rows["ticker"]) >= {"AAA", "BBB", "CCC"}
    assert (rows["reason_text"].str.len() > 0).all(), "hay decisiones sin explicar"
    assert "VETOED" in set(rows["decision"])


def test_the_canonical_audit_query_works(warehouse):
    """La consulta que la adenda promete que siempre funciona."""
    broker = SimulatedBroker(prices=prices(), initial_cash=100.0, slippage_bps=0.0)
    log = journal.Journal(run_id="R3", mode="simulated", strategy_id="s")
    run_cycle(context(), MomentumMultifactor(), RiskManager(cfg=cfg()), log,
              broker=broker)
    log.flush()

    rows = db.query(
        "SELECT logged_at, decision, reason_code, reason_text FROM decision_log "
        "WHERE ticker = ? AND logged_at::DATE = ? AND mode = ? ORDER BY logged_at",
        ["AAA", date.today(), "simulated"],
    )
    assert not rows.empty


def test_intents_are_persisted_with_their_verdict(warehouse):
    broker = SimulatedBroker(prices=prices(), initial_cash=100.0, slippage_bps=0.0)
    log = journal.Journal(run_id="R4", mode="simulated", strategy_id="s")
    run_cycle(context(), MomentumMultifactor(), RiskManager(cfg=cfg()), log,
              broker=broker)
    log.flush()

    rows = db.query("SELECT ticker, side, risk_verdict, notional_approved, "
                    "stop_price FROM intents")
    assert not rows.empty
    approved = rows[rows["risk_verdict"] != "VETO"]
    assert (approved["stop_price"] > 0).all(), "hay compras aprobadas sin stop"


def test_nothing_is_written_when_the_flush_fails_midway(warehouse):
    """El registro se vuelca en una transaccion: o entero o nada.

    Media decision guardada es peor que ninguna, porque parece completa: al
    leerla despues no hay forma de saber que falta el resto.
    """
    log = journal.Journal(run_id="R5", mode="simulated", strategy_id="s")
    log.decision("AAA", "PROPOSED", "OK", "una")
    log.decision("BBB", "PROPOSED", "OK", "dos")

    # Las decisiones son validas; la fila de intencion no. El fallo ocurre
    # DESPUES de haber insertado las decisiones, que es el escenario que
    # importa: si no hubiera transaccion, quedarian escritas.
    log._intents.append(tuple([None] * 26))

    with pytest.raises(duckdb.Error):
        log.flush()

    assert db.query("SELECT COUNT(*) AS n FROM decision_log")["n"][0] == 0, (
        "las decisiones se han quedado escritas pese a fallar el volcado"
    )


def test_the_bot_does_nothing_on_a_non_rebalance_day_with_a_full_book(warehouse):
    tuesday = date(2024, 6, 4)
    positions = {f"P{i}": {"qty": 1.0, "market_value": 10.0} for i in range(7)}
    ctx = context(as_of=tuesday, positions=positions, cash=30.0,
                  last_price_date=tuesday,
                  sectors={f"P{i}": "Otros" for i in range(7)})
    log = journal.Journal(run_id="R6", mode="simulated", strategy_id="s")
    result = run_cycle(ctx, MomentumMultifactor(), RiskManager(cfg=cfg()), log)

    assert result.n_intents == 0
    log.flush()
    rows = db.query("SELECT reason_code FROM decision_log")
    assert "NOT_A_REBALANCE_DAY" in set(rows["reason_code"])


def test_a_stop_exit_is_proposed_when_the_price_breaks_the_stop(warehouse):
    """Entrada a 50 con ATR 1 y multiplo 2,5: el stop esta en 47,5."""
    indicators = pd.DataFrame(
        {"ticker": ["AAA"], "close": [45.0], "atr14": [1.0], "rsi14": [50.0],
         "above_sma200": [True]}
    ).set_index("ticker")
    scores = pd.DataFrame(
        {"ticker": ["AAA"], "composite_pctile": [0.95], "coverage": [0.9]}
    ).set_index("ticker")
    ctx = context(
        indicators=indicators, scores=scores,
        positions={"AAA": {"qty": 1.0, "avg_entry_price": 50.0,
                           "market_value": 45.0, "current_price": 45.0}},
        bot_positions={"AAA": {"opened_at": date(2024, 1, 2),
                               "highest_close_since_entry": 50.0}},
    )
    intents = MomentumMultifactor().propose(ctx)
    stops = [i for i in intents if i.is_protective]
    assert stops, "el stop no se ha disparado con el precio por debajo"
    assert stops[0].ticker == "AAA"


def test_the_strategy_never_touches_the_database(warehouse):
    """Una estrategia que lee de la base podria ver datos de otra fecha que el
    resto del ciclo, y reconstruir la decision seria imposible."""
    import ast

    from stocks_tracker.core.config import project_root

    path = (project_root()
            / "src/stocks_tracker/trading/strategies/momentum_multifactor.py")
    for node in ast.walk(ast.parse(path.read_text("utf-8"))):
        if isinstance(node, ast.ImportFrom):
            assert "db" not in (node.module or ""), "la estrategia importa la BD"
