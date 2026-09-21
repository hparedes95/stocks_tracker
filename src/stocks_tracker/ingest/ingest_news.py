"""Ingesta acotada de noticias para cartera, watchlist y señales."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from ..core.db import connect, migrate, upsert_df
from ..core.observability import emit
from ..providers.base import ProviderError, RateLimitError
from ..providers.finnhub_news_provider import FinnhubNewsProvider

MAX_TICKERS = 25


def targets() -> list[str]:
    with connect(read_only=True) as conn:
        rows = conn.execute(
            """
            SELECT ticker FROM positions WHERE closed_at IS NULL AND qty > 0
            UNION SELECT ticker FROM watchlist
            UNION SELECT ticker FROM signals
                  WHERE date = (SELECT MAX(date) FROM signals)
            ORDER BY ticker LIMIT ?
            """, [MAX_TICKERS],
        ).fetchall()
    return [row[0] for row in rows]


def ingest(
    days: int = 3,
    provider: FinnhubNewsProvider | None = None,
    run_id: str | None = None,
) -> int:
    migrate()
    provider = provider or FinnhubNewsProvider()
    if not provider.configured:
        emit("news.skipped", reason="missing_credentials", run_id=run_id)
        return 0
    frames: list[pd.DataFrame] = []
    end = date.today()
    start = end - timedelta(days=max(1, days))
    for ticker in targets():
        try:
            frame = provider.fetch(ticker, start, end)
        except RateLimitError:
            emit("news.rate_limited", level="warning", provider=provider.name, run_id=run_id)
            break
        except ProviderError as exc:
            emit("news.failed", level="warning", ticker=ticker,
                 error_type=type(exc).__name__, run_id=run_id)
            continue
        if not frame.empty:
            frames.append(frame)
    if not frames:
        return 0
    payload = pd.concat(frames, ignore_index=True)
    with connect() as conn:
        written = upsert_df(conn, "news_items", payload, ["source", "external_id"])
        conn.execute("DELETE FROM news_items WHERE published_at < CURRENT_TIMESTAMP - INTERVAL 90 DAY")
    emit(
        "news.completed", provider=provider.name, rows=written,
        tickers=len(frames), run_id=run_id,
    )
    return written


def main() -> None:
    print(f"Noticias guardadas: {ingest()}")


if __name__ == "__main__":
    main()
