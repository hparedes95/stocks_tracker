from __future__ import annotations

from datetime import date

import pytest

from stocks_tracker.providers.base import RateLimitError
from stocks_tracker.providers.finnhub_news_provider import (
    FinnhubNewsProvider,
    interpret,
    lexical_sentiment,
)


def test_sentimiento_lexico_es_acotado_y_transparente():
    assert lexical_sentiment("Record profit beats estimates") > 0
    assert lexical_sentiment("Fraud probe and losses") < 0
    assert lexical_sentiment("Board meets on Tuesday") == 0


def test_interpreta_y_descarta_filas_inservibles():
    frame = interpret([
        {"id": 7, "datetime": 1_700_000_000, "headline": "Profit beats estimates",
         "summary": "Strong growth", "url": "https://example.test/n"},
        {"id": 8, "headline": "sin fecha"},
    ], "AAA")
    assert len(frame) == 1
    assert frame.iloc[0]["ticker"] == "AAA"
    assert frame.iloc[0]["sentiment_method"] == "lexical-v1"


def test_sin_clave_no_hace_red(monkeypatch):
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    provider = FinnhubNewsProvider()
    assert not provider.configured
    assert provider.fetch("AAA", date(2026, 1, 1), date(2026, 1, 2)).empty


def test_rate_limit_se_distingue(monkeypatch):
    class Response:
        status_code = 429

    provider = FinnhubNewsProvider("key")
    monkeypatch.setattr(provider._session, "get", lambda *a, **k: Response())
    with pytest.raises(RateLimitError):
        provider.fetch("AAA", date(2026, 1, 1), date(2026, 1, 2))
