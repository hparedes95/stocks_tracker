from contextlib import contextmanager

import duckdb
import pytest

from stocks_tracker.core.db import schema_path
from stocks_tracker.ingest import history_extension as history


@pytest.fixture
def setup(monkeypatch):
    c = duckdb.connect(':memory:')
    c.execute(schema_path().read_text(encoding='utf-8'))
    c.execute("INSERT INTO instruments(ticker,country,currency) VALUES ('A','US','USD')")
    @contextmanager
    def connect(**kwargs):
        yield c
    monkeypatch.setattr(history, 'connect', connect)
    monkeypatch.setattr(history, 'frozen_market', lambda: False)
    calls = []
    def ingest(**kwargs):
        calls.append(kwargs)
        return 0
    monkeypatch.setattr(history, 'ingest_prices', ingest)
    yield c, calls
    c.close()


def test_only_three_targets_and_no_retry_loop_for_missing_history(setup):
    c, calls = setup
    history.extend('A')
    assert {call['tickers_override'][0] for call in calls} == {'A','SPY','EURUSD=X'}
    assert all(call['years'] == 30 and call['full'] for call in calls)
    history.extend('A')
    assert len(calls) == 3
    assert c.execute("SELECT COUNT(*) FROM ingest_log WHERE status='UNAVAILABLE'").fetchone()[0] == 3


def test_frozen_market_never_downloads(setup, monkeypatch):
    _, calls = setup
    monkeypatch.setattr(history, 'frozen_market', lambda: True)
    with pytest.raises(ValueError, match='congelada'):
        history.extend('A')
    assert calls == []
