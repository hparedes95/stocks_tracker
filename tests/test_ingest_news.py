from __future__ import annotations

from datetime import UTC, date, datetime

import pandas as pd
import pytest

from stocks_tracker.core import db
from stocks_tracker.ingest import ingest_news


@pytest.fixture
def warehouse(tmp_path, monkeypatch):
    class Stub:
        warehouse_path = tmp_path / "news.duckdb"
        logs_dir = tmp_path / "logs"

    monkeypatch.setattr(db, "get_settings", lambda: Stub())
    db.migrate()
    return Stub.warehouse_path


def test_targets_solo_incluye_posiciones_abiertas(warehouse):
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO positions (id, ticker, qty, closed_at) VALUES "
            "('1', 'OPEN', 2, NULL), ('2', 'CLOSED', 2, ?)", [date.today()]
        )
        conn.execute("INSERT INTO watchlist (ticker, list_name) VALUES ('WATCH', 'default')")
    assert ingest_news.targets() == ["OPEN", "WATCH"]


def test_ingest_es_idempotente(warehouse, monkeypatch):
    class Provider:
        name = "fake"
        configured = True

        def fetch(self, ticker, start, end):
            return pd.DataFrame([{
                "source": "fake", "external_id": "1", "ticker": ticker,
                "published_at": datetime.now(UTC), "headline": "News",
                "summary": "", "url": "", "sentiment": 0.0,
                "sentiment_method": "test", "ingested_at": datetime.now(UTC),
            }])

    with db.connect() as conn:
        conn.execute("INSERT INTO watchlist (ticker, list_name) VALUES ('AAA', 'default')")
    assert ingest_news.ingest(provider=Provider()) == 1
    assert ingest_news.ingest(provider=Provider()) == 1
    with db.connect(read_only=True) as conn:
        assert conn.execute("SELECT COUNT(*) FROM news_items").fetchone()[0] == 1
