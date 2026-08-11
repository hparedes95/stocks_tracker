"""La fecha sobre la que se calcula el ranking.

Averia real: el ranking se quedaba vacio y el mensaje era "Sin instrumentos que
puntuar", que sonaba a problema de descarga. No lo era. El calculo tomaba la
ultima fecha del almacen a secas, y esa fecha puede no tener ni una sola
accion: el bitcoin cotiza los domingos y los indices tienen barra antes de que
cierre Wall Street.

O sea que el ranking desaparecia todos los fines de semana, y por las tardes,
por una consulta de una linea.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from stocks_tracker.compute import run_compute
from stocks_tracker.core import db


@pytest.fixture
def warehouse(tmp_path, monkeypatch):
    class Stub:
        warehouse_path = tmp_path / "test.duckdb"

    monkeypatch.setattr(db, "get_settings", lambda: Stub())
    db.migrate()
    return Stub.warehouse_path


def seed(instruments: list[tuple[str, str]], rows: list[tuple[str, date]]) -> None:
    with db.connect() as conn:
        conn.executemany(
            "INSERT INTO instruments (ticker, asset_class, gics_sector, is_active) "
            "VALUES (?, ?, 'Tecnologia', TRUE)", instruments,
        )
        frame = pd.DataFrame(rows, columns=["ticker", "date"])
        for column, value in (("close", 100.0), ("atr14", 1.0), ("rsi14", 50.0),
                              ("realized_vol_252", 0.2), ("mom_12_1", 0.1),
                              ("above_sma200", True)):
            frame[column] = value
        db.upsert_df(conn, "indicators_daily", frame, keys=["ticker", "date"])


def test_the_scoring_date_is_the_last_one_with_equities(warehouse):
    """El domingo el bitcoin cotiza y las acciones no. El ranking tiene que
    seguir siendo el del viernes, no desaparecer."""
    friday, sunday = date(2026, 8, 7), date(2026, 8, 9)
    seed(
        [("AAA", "equity"), ("BBB", "equity"), ("BTC-USD", "crypto")],
        [("AAA", friday), ("BBB", friday), ("BTC-USD", friday),
         ("BTC-USD", sunday)],
    )

    with db.connect(read_only=True) as conn:
        naive = conn.execute("SELECT MAX(date) FROM indicators_daily").fetchone()[0]
        correct = conn.execute(
            "SELECT MAX(i.date) FROM indicators_daily i "
            "JOIN instruments inst USING (ticker) "
            "WHERE inst.asset_class IN ('equity', 'etf')"
        ).fetchone()[0]

    assert pd.Timestamp(naive).date() == sunday, "el escenario no reproduce el problema"
    assert pd.Timestamp(correct).date() == friday
    assert naive != correct, (
        "sin esta diferencia el test no prueba nada"
    )


def test_scoring_does_not_give_up_when_only_crypto_is_fresher(warehouse):
    """La prueba de verdad: que `compute_factor_scores` puntue algo."""
    friday, sunday = date(2026, 8, 7), date(2026, 8, 9)
    seed(
        [(f"T{i}", "equity") for i in range(12)] + [("BTC-USD", "crypto")],
        [(f"T{i}", friday) for i in range(12)] + [("BTC-USD", sunday)],
    )

    # No debe lanzar SystemExit(EXIT_NOTHING_TO_SCORE).
    run_compute.compute_factor_scores(preset="balanced")

    scores = db.query("SELECT DISTINCT date FROM factor_scores")
    assert not scores.empty, "no se ha puntuado nada pese a haber acciones"
    assert pd.Timestamp(scores["date"][0]).date() == friday


def test_the_query_used_by_the_code_filters_by_asset_class():
    """Guardarrail: volver al MAX(date) pelado reintroduce la averia, y su
    sintoma —un ranking vacio— no apunta a esta linea."""
    from stocks_tracker.core.config import project_root

    src = (project_root() / "src/stocks_tracker/compute/run_compute.py").read_text("utf-8")
    block = src[src.index("def compute_factor_scores"):]
    block = block[:block.index("snapshot = conn.execute")]
    # Quitar comentarios: la explicacion menciona MAX(date) a proposito.
    code = "\n".join(line for line in block.splitlines()
                     if not line.strip().startswith("#"))

    assert 'MAX(i.date)' in code, "la fecha del ranking no filtra por clase"
    assert "asset_class IN ('equity', 'etf')" in code


def test_a_stock_without_enough_history_does_not_kill_the_ranking():
    """`bool(pd.NA)` no es False: lanza "boolean value of NA is ambiguous" y
    tumba el calculo entero. Le pasa a cualquier valor recien salido a bolsa,
    que aun no tiene 200 sesiones, y el error no dice ni que ticker era."""
    from stocks_tracker.core.signals import technical_score

    recien_listada = pd.Series({"above_sma200": pd.NA, "above_sma50": pd.NA,
                                "rsi14": pd.NA, "macd_hist": pd.NA})
    assert isinstance(technical_score(recien_listada, []), float)

    con_nan = pd.Series({"above_sma200": float("nan"), "above_sma50": None})
    assert isinstance(technical_score(con_nan, []), float)

    # Y sigue puntuando bien cuando el dato si esta.
    alcista = pd.Series({"above_sma200": True, "above_sma50": True})
    bajista = pd.Series({"above_sma200": False, "above_sma50": False})
    assert technical_score(alcista, []) > technical_score(bajista, [])
