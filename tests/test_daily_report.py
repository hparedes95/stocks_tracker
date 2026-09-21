from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from stocks_tracker.core import daily_report, db


@pytest.fixture
def warehouse(tmp_path, monkeypatch):
    class Stub:
        warehouse_path = tmp_path / "report.duckdb"
        logs_dir = tmp_path / "logs"

    monkeypatch.setattr(db, "get_settings", lambda: Stub())
    db.migrate()
    return Stub.warehouse_path


def test_informe_vacio_es_util_y_se_puede_guardar(warehouse, tmp_path):
    text = daily_report.render()
    assert "Todavía no hay un ranking" in text
    assert "Sin precios almacenados" in text

    target = daily_report.write_report(tmp_path / "hoy.md")
    assert target.exists()
    assert target.read_text("utf-8").startswith("# Stocks Tracker")


def test_informe_incluye_origen_y_ranking(warehouse):
    today = date(2026, 8, 20)
    with db.connect() as conn:
        db.upsert_df(conn, "instruments", pd.DataFrame([{
            "ticker": "AAA", "name": "Empresa A"
        }]), ["ticker"])
        db.upsert_df(conn, "prices_daily", pd.DataFrame([{
            "ticker": "AAA", "date": today, "open": 9.0, "high": 11.0,
            "low": 8.0, "close": 10.0, "adj_close": 10.0, "volume": 100,
            "source": "prueba",
        }]), ["ticker", "date"])
        db.upsert_df(conn, "factor_scores", pd.DataFrame([{
            "ticker": "AAA", "date": today, "weights_hash": "x",
            "composite": 1.25, "composite_pctile": 0.9, "coverage": 0.8,
        }]), ["ticker", "date", "weights_hash"])

    text = daily_report.render()
    assert "Empresa A" in text
    assert "prueba: 1 filas" in text


def test_informe_tolera_un_score_parcial(warehouse):
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO factor_scores (ticker, date, weights_hash) VALUES "
            "('VACIO', '2026-08-20', 'x')"
        )
    assert "| VACIO | VACIO | — | — | — |" in daily_report.render()
