"""Noticias opcionales de Finnhub, aisladas del cálculo financiero."""

from __future__ import annotations

from datetime import date

import pandas as pd
import requests

from ..core import secrets
from ..core.timeutils import utcnow
from .base import ProviderError, RateLimitError

_URL = "https://finnhub.io/api/v1/company-news"
_TIMEOUT = 20
VARIABLE_DE_ENTORNO = "FINNHUB_API_KEY"

_POSITIVE = frozenset({
    "beat", "beats", "growth", "profit", "profits", "upgrade", "upgraded",
    "record", "surge", "gain", "gains", "strong", "bullish", "outperform",
})
_NEGATIVE = frozenset({
    "miss", "misses", "loss", "losses", "downgrade", "downgraded", "fraud",
    "probe", "lawsuit", "cuts", "weak", "bearish", "underperform", "bankruptcy",
})


def lexical_sentiment(text: str) -> float:
    """Score transparente -1..1; útil para ordenar, nunca para operar."""
    words = {part.strip(".,:;!?()[]{}\"'").lower() for part in text.split()}
    positive = len(words & _POSITIVE)
    negative = len(words & _NEGATIVE)
    total = positive + negative
    return 0.0 if not total else (positive - negative) / total


def interpret(payload, ticker: str) -> pd.DataFrame:
    if not isinstance(payload, list):
        raise ProviderError("Finnhub no devolvio una lista de noticias")
    rows = []
    for item in payload:
        if not isinstance(item, dict) or not item.get("headline") or not item.get("id"):
            continue
        timestamp = pd.to_datetime(item.get("datetime"), unit="s", utc=True, errors="coerce")
        if pd.isna(timestamp):
            continue
        headline = str(item["headline"]).strip()
        summary = str(item.get("summary") or "").strip()
        url = str(item.get("url") or "").strip()
        if url and not url.startswith(("https://", "http://")):
            url = ""
        rows.append({
            "source": "finnhub",
            "external_id": str(item["id"]),
            "ticker": ticker,
            "published_at": timestamp.to_pydatetime(),
            "headline": headline,
            "summary": summary,
            "url": url,
            "sentiment": lexical_sentiment(f"{headline} {summary}"),
            "sentiment_method": "lexical-v1",
            "ingested_at": utcnow(),
        })
    return pd.DataFrame(rows)


class FinnhubNewsProvider:
    name = "finnhub"

    def __init__(self, key: str | None = None) -> None:
        self._key = key or secrets.get(VARIABLE_DE_ENTORNO, required=False)
        self._session = requests.Session()

    @property
    def configured(self) -> bool:
        return bool(self._key)

    def fetch(self, ticker: str, start: date, end: date) -> pd.DataFrame:
        if not self.configured:
            return pd.DataFrame()
        try:
            response = self._session.get(
                _URL,
                params={"symbol": ticker, "from": start.isoformat(),
                        "to": end.isoformat(), "token": self._key},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise ProviderError(f"Finnhub no responde para {ticker}") from exc
        if response.status_code == 429:
            raise RateLimitError("Finnhub esta limitando las peticiones")
        if response.status_code != 200:
            raise ProviderError(f"Finnhub devolvio {response.status_code} para {ticker}")
        try:
            return interpret(response.json(), ticker)
        except ValueError as exc:
            raise ProviderError("Finnhub no devolvio JSON") from exc
