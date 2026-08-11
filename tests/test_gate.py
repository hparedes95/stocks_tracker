"""Tests de la puerta 1.

Lo que se prueba aqui con mas cuidado no es que apruebe cuando toca, sino que
NO apruebe cuando no toca. Un aprobado falso en esta puerta es el camino por el
que dinero real acaba en una estrategia que no funciona, y a diferencia de un
error de programacion no da ninguna senal: sale un informe con buena pinta.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from stocks_tracker.core import db
from stocks_tracker.trading import gate


@pytest.fixture
def warehouse(tmp_path, monkeypatch):
    class Stub:
        warehouse_path = tmp_path / "test.duckdb"

    monkeypatch.setattr(db, "get_settings", lambda: Stub())
    db.migrate()
    return Stub.warehouse_path


def curve(days: int = 1400, daily: float = 0.0006, start_equity: float = 55.0):
    """Curva de resultados creciente y sin sobresaltos."""
    fechas = pd.bdate_range("2019-01-01", periods=days)
    equity = [start_equity]
    for _ in range(days - 1):
        equity.append(equity[-1] * (1 + daily))
    return list(zip([d.date() for d in fechas], equity, strict=False))


def summary(**overrides) -> dict:
    base = {"sessions": 1400, "operaciones": 150, "curva": curve(),
            "equity_inicial": 55.0, "equity_final": 120.0, "retorno_pct": 118.0}
    base.update(overrides)
    return base


def seed_real_prices(n_days: int = 400) -> None:
    """Precios marcados como reales y ranking suficiente, para que no salten
    los bloqueos y se puedan probar los umbrales."""
    from stocks_tracker.core.config import get_factor_config
    from stocks_tracker.core.scoring import weights_hash

    whash = weights_hash(get_factor_config().weights("bot_core"))
    fechas = [date(2024, 1, 1) + timedelta(days=i) for i in range(n_days)]
    with db.connect() as conn:
        conn.executemany(
            "INSERT INTO prices_daily (ticker, date, close, source) VALUES (?,?,?,?)",
            [("AAA", d, 100.0, "yfinance") for d in fechas],
        )
        conn.executemany(
            "INSERT INTO factor_scores (ticker, date, weights_hash, composite_pctile) "
            "VALUES (?,?,?,?)",
            [("AAA", d, whash, 0.9) for d in fechas],
        )


# ---------------------------------------------------------------------------
# Las dos negativas
# ---------------------------------------------------------------------------
def test_synthetic_prices_block_certification(warehouse):
    """Un backtest sobre series que hemos generado nosotros mide lo bien que el
    generador imita a la estrategia, y sale espectacular."""
    with db.connect() as conn:
        conn.execute("INSERT INTO prices_daily (ticker, date, close, source) "
                     "VALUES ('AAA', DATE '2024-01-02', 100.0, 'synthetic')")

    blockers = gate.find_blockers()
    assert any("SINTETICOS" in b for b in blockers)

    report = gate.evaluate(summary())
    assert not report.passed, "ha certificado sobre precios inventados"
    assert "NO SE PUEDE CERTIFICAR" in gate.render(report)


def test_a_preset_with_fundamentals_blocks_certification(warehouse):
    """No hay serie historica de fundamentales: puntuar 2019 con los balances de
    hoy hace que el backtest salga bien por construccion."""
    seed_real_prices()
    blockers = gate.find_blockers(preset="balanced")
    assert any("fundamentales" in b for b in blockers)


def test_the_bot_preset_is_free_of_fundamentals(warehouse):
    """Guardarrail sobre la configuracion, no sobre el codigo: si alguien anade
    calidad al preset del bot, su backtest deja de ser valido."""
    from stocks_tracker.compute.run_compute import PRICE_ONLY_FACTORS
    from stocks_tracker.core.config import get_factor_config

    usados = {f for f, w in get_factor_config().weights("bot_core").items() if w}
    assert usados <= PRICE_ONLY_FACTORS, (
        f"el preset del bot usa {sorted(usados - PRICE_ONLY_FACTORS)}, que no "
        "tienen historico y convierten el backtest en una prediccion del pasado"
    )


def test_too_little_scoring_history_blocks_certification(warehouse):
    with db.connect() as conn:
        conn.execute("INSERT INTO prices_daily (ticker, date, close, source) "
                     "VALUES ('AAA', DATE '2024-01-02', 100.0, 'yfinance')")
    assert any("Calcula el historico" in b for b in gate.find_blockers())


def test_blockers_beat_a_perfect_backtest(warehouse):
    """Aunque los nueve umbrales salgan perfectos, un bloqueo manda."""
    with db.connect() as conn:
        conn.execute("INSERT INTO prices_daily (ticker, date, close, source) "
                     "VALUES ('AAA', DATE '2024-01-02', 100.0, 'synthetic')")
    report = gate.evaluate(summary(), robustness={"a": 2.0})
    assert not report.passed


# ---------------------------------------------------------------------------
# Los umbrales
# ---------------------------------------------------------------------------
def checks_of(report) -> dict[str, bool]:
    return {c.name: c.passed for c in report.checks}


def test_a_short_backtest_fails_the_period_check(warehouse):
    seed_real_prices()
    report = gate.evaluate(summary(curva=curve(days=300)))
    assert checks_of(report)["Periodo cubierto"] is False


def test_too_few_trades_fail(warehouse):
    seed_real_prices()
    report = gate.evaluate(summary(operaciones=40))
    assert checks_of(report)["Operaciones"] is False


def test_a_losing_strategy_fails_the_expectancy_check(warehouse):
    seed_real_prices()
    report = gate.evaluate(summary(curva=curve(daily=-0.0004)))
    assert checks_of(report)["Expectativa por operacion"] is False


def test_a_deep_drawdown_fails(warehouse):
    seed_real_prices()
    caida = curve(days=1400)
    # Un desplome del 40 % a mitad de camino.
    caida = [(d, e * (0.6 if 600 < i < 900 else 1.0))
             for i, (d, e) in enumerate(caida)]
    report = gate.evaluate(summary(curva=caida))
    assert checks_of(report)["Caida maxima"] is False


def test_robustness_is_not_assumed_when_it_was_not_measured(warehouse):
    """No medir algo no es aprobarlo."""
    seed_real_prices()
    report = gate.evaluate(summary(), robustness=None)
    assert checks_of(report)["Robustez a parametros"] is False
    assert any("sin medir" in c.observed for c in report.checks)


def test_fragile_parameters_fail(warehouse):
    seed_real_prices()
    report = gate.evaluate(summary(), robustness={"stop 1,9x": 0.10,
                                                  "stop 3,1x": 0.9})
    assert checks_of(report)["Robustez a parametros"] is False, (
        "una estrategia que solo funciona con un stop concreto es una "
        "casualidad del historico"
    )


# ---------------------------------------------------------------------------
# El informe
# ---------------------------------------------------------------------------
def test_the_report_never_promises_profit(warehouse):
    """El texto de un aprobado es tan importante como el aprobado."""
    seed_real_prices()
    report = gate.evaluate(summary(), robustness={"a": 1.0})
    texto = gate.render(report)
    assert "NO dice que la estrategia vaya a ganar dinero" in texto or not report.passed


def test_a_failed_gate_says_the_strategy_is_not_activated(warehouse):
    seed_real_prices()
    report = gate.evaluate(summary(operaciones=1))
    texto = gate.render(report)
    assert "NO SUPERADA" in texto
    assert "no se opera" in texto


def test_a_blocker_overrides_every_passing_check():
    """Aislado del backtest: aunque las nueve comprobaciones esten en verde, un
    bloqueo manda. Es la linea que separa "no he encontrado fallos" de "he
    comprobado esto de verdad"."""
    todo_bien = [gate.Check(f"c{i}", True, "ok", "ok") for i in range(9)]

    assert gate.GateReport(checks=todo_bien).passed is True
    assert gate.GateReport(checks=todo_bien, blockers=["datos inventados"]).passed is False


def test_an_empty_report_does_not_pass():
    """No haber comprobado nada no es aprobar."""
    assert gate.GateReport().passed is False
