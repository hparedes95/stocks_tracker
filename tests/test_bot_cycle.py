"""Ciclo completo del bot contra el broker simulado. Sin red y sin almacen real.

Prueba lo que ninguna pieza suelta puede probar: que estrategia, riesgo,
ejecucion y registro encajan. Y sobre todo, la invariante de auditoria — que
todo candidato deja rastro, tambien los descartados—, porque sin ella "por que
no compraste X el martes" no tiene respuesta.

Estos tests usaban la estrategia de acciones, que se ha retirado. Lo que
comprueban no es de ninguna estrategia en concreto —el rastro de auditoria, la
atomicidad del registro, que la estrategia no toque la base— asi que se han
re-apuntado a la de cripto en lugar de borrarlos: la cobertura era buena y el
motivo de la retirada era otro.
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
from stocks_tracker.trading.strategies.crypto_momentum import CryptoMomentum
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


PARES = ("BTC/EUR", "ETH/EUR", "SOL/EUR")


def prices() -> pd.DataFrame:
    rows = []
    for i, day in enumerate(pd.bdate_range("2024-06-03", periods=6)):
        for ticker, base in zip(PARES, (50.0, 40.0, 30.0), strict=True):
            price = base + i
            rows.append((day.date(), ticker, price, price + 1, price - 1, price))
    return pd.DataFrame(
        rows, columns=["date", "ticker", "open", "high", "low", "close"]
    ).assign(volume=1_000_000)


def context(**overrides) -> StrategyContext:
    indicators = pd.DataFrame(
        {"ticker": list(PARES),
         "close": [50.0, 40.0, 30.0],
         "atr14": [1.0, 1.0, 1.0],
         "rsi14": [55.0, 55.0, 55.0],
         "roc_3m": [0.50, 0.40, 0.30],
         "roc_6m": [0.60, 0.50, 0.40],
         "above_sma50": [True, True, True],
         "above_sma200": [True, True, True]}
    ).set_index("ticker")
    base = dict(
        as_of=MONDAY, mode="simulated", equity=100.0, cash=100.0,
        indicators=indicators,
        sectors=dict.fromkeys(PARES, "Crypto"),
        dollar_volume_20d=dict.fromkeys(PARES, 5e8),
        last_price_date=MONDAY, peak_equity=100.0, day_start_equity=100.0,
    )
    base.update(overrides)
    return StrategyContext(**base)


# ---------------------------------------------------------------------------
def test_a_full_cycle_proposes_approves_and_submits(warehouse):
    broker = SimulatedBroker(prices=prices(), initial_cash=100.0, slippage_bps=0.0)
    log = journal.Journal(run_id="R1", mode="simulated", strategy_id="s")

    result = run_cycle(context(), CryptoMomentum(), RiskManager(cfg=cfg()),
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
                  sectors={"BTC/EUR": "Tecnologia", "ETH/EUR": "Banca", "SOL/EUR": "Salud",
                           "ZZZ": "Tecnologia"})
    run_cycle(ctx, CryptoMomentum(), RiskManager(cfg=cfg()), log, broker=broker)
    counts = log.flush()

    assert counts["decision_log"] >= 3
    rows = db.query("SELECT ticker, decision, reason_code, reason_text "
                    "FROM decision_log ORDER BY ticker")
    assert set(rows["ticker"]) >= {"BTC/EUR", "ETH/EUR", "SOL/EUR"}
    assert (rows["reason_text"].str.len() > 0).all(), "hay decisiones sin explicar"
    assert "VETOED" in set(rows["decision"])


def test_the_canonical_audit_query_works(warehouse):
    """La consulta que la adenda promete que siempre funciona."""
    broker = SimulatedBroker(prices=prices(), initial_cash=100.0, slippage_bps=0.0)
    log = journal.Journal(run_id="R3", mode="simulated", strategy_id="s")
    run_cycle(context(), CryptoMomentum(), RiskManager(cfg=cfg()), log,
              broker=broker)
    log.flush()

    rows = db.query(
        "SELECT logged_at, decision, reason_code, reason_text FROM decision_log "
        "WHERE ticker = ? AND logged_at::DATE = ? AND mode = ? ORDER BY logged_at",
        ["BTC/EUR", date.today(), "simulated"],
    )
    assert not rows.empty


def test_intents_are_persisted_with_their_verdict(warehouse):
    broker = SimulatedBroker(prices=prices(), initial_cash=100.0, slippage_bps=0.0)
    log = journal.Journal(run_id="R4", mode="simulated", strategy_id="s")
    run_cycle(context(), CryptoMomentum(), RiskManager(cfg=cfg()), log,
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
    log.decision("BTC/EUR", "PROPOSED", "OK", "una")
    log.decision("ETH/EUR", "PROPOSED", "OK", "dos")

    # Las decisiones son validas; la fila de intencion no. El fallo ocurre
    # DESPUES de haber insertado las decisiones, que es el escenario que
    # importa: si no hubiera transaccion, quedarian escritas.
    log._intents.append(tuple([None] * 26))

    with pytest.raises(duckdb.Error):
        log.flush()

    assert db.query("SELECT COUNT(*) AS n FROM decision_log")["n"][0] == 0, (
        "las decisiones se han quedado escritas pese a fallar el volcado"
    )


def test_a_quiet_day_leaves_a_reason_and_not_just_silence(warehouse):
    """Este test comprobaba el dia de rebalanceo semanal de la estrategia de
    acciones, que ya no existe: cripto no cierra y el ciclo corre todos los
    dias. Lo que limita la actividad es el mandato —dos ordenes al dia,
    veintiun dias de permanencia— y no un calendario.

    Lo que si sigue importando es que un dia sin nada que hacer deje escrito
    POR QUE. Sin esa linea, "el bot no hizo nada el martes" es
    indistinguible de "el bot no se ejecuto el martes".
    """
    tuesday = date(2024, 6, 4)
    # Cartera llena y sin nada que cambiar: nadie cae del ranking ni toca stop.
    positions = {t: {"qty": 1.0, "market_value": 10.0, "avg_entry_price": 1.0}
                 for t in PARES}
    ctx = context(as_of=tuesday, positions=positions, cash=0.0,
                  last_price_date=tuesday)
    log = journal.Journal(run_id="R6", mode="simulated", strategy_id="s")
    result = run_cycle(ctx, CryptoMomentum(), RiskManager(cfg=cfg()), log)

    assert result.n_intents == 0
    log.flush()
    rows = db.query("SELECT reason_code FROM decision_log")
    assert "NO_CANDIDATES" in set(rows["reason_code"]), (
        "un dia tranquilo no ha dejado constancia de que se ejecuto"
    )


def test_a_stop_exit_is_proposed_when_the_price_breaks_the_stop(warehouse):
    """Entrada a 50 con ATR 1 y multiplo 2,5: el stop esta en 47,5."""
    indicators = pd.DataFrame(
        {"ticker": ["BTC/EUR"], "close": [45.0], "atr14": [1.0], "rsi14": [50.0],
         "above_sma200": [True]}
    ).set_index("ticker")
    scores = pd.DataFrame(
        {"ticker": ["BTC/EUR"], "composite_pctile": [0.95], "coverage": [0.9]}
    ).set_index("ticker")
    ctx = context(
        indicators=indicators, scores=scores,
        positions={"BTC/EUR": {"qty": 1.0, "avg_entry_price": 50.0,
                           "market_value": 45.0, "current_price": 45.0}},
        bot_positions={"BTC/EUR": {"opened_at": date(2024, 1, 2),
                               "highest_close_since_entry": 50.0}},
    )
    intents = CryptoMomentum().propose(ctx)
    stops = [i for i in intents if i.is_protective]
    assert stops, "el stop no se ha disparado con el precio por debajo"
    assert stops[0].ticker == "BTC/EUR"


def test_the_strategy_never_touches_the_database(warehouse):
    """Una estrategia que lee de la base podria ver datos de otra fecha que el
    resto del ciclo, y reconstruir la decision seria imposible."""
    import ast

    from stocks_tracker.core.config import project_root

    path = (project_root()
            / "src/stocks_tracker/trading/strategies/crypto_momentum.py")
    for node in ast.walk(ast.parse(path.read_text("utf-8"))):
        if isinstance(node, ast.ImportFrom):
            assert "db" not in (node.module or ""), "la estrategia importa la BD"
