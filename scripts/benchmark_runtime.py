"""Offline, deterministic timings; never opens the user's warehouse."""
from __future__ import annotations

import json
import statistics
import tempfile
import time
from pathlib import Path

import numpy as np
import pandas as pd

from stocks_tracker.core import db, holding_analysis, indicators, integrity
from stocks_tracker.core.config import get_settings, project_root
from stocks_tracker.core.trading_calendar import sessions


def measure(call, repeats=5):
    samples = []
    for _ in range(repeats):
        start = time.perf_counter()
        call()
        samples.append(time.perf_counter() - start)
    return round(statistics.median(samples), 6)


def main():
    rng = np.random.default_rng(42)
    days = sessions('XNYS', '2010-01-01', '2026-10-23')
    prices = pd.Series(100 * np.exp(np.cumsum(rng.normal(.0002, .01, len(days)))), index=days)
    bars = pd.DataFrame({'adj_close': prices, 'close': prices, 'high': prices * 1.01,
                         'low': prices * .99, 'open': prices, 'volume': 1_000_000})
    results = {
        'bars_per_symbol': len(bars),
        'max_drawdown_seconds': measure(lambda: indicators.max_drawdown(prices)),
        'indicators_20_symbols_seconds': measure(
            lambda: [indicators.compute_all(bars) for _ in range(20)], repeats=3),
    }
    root = project_root() / 'reports'
    root.mkdir(exist_ok=True)
    settings = get_settings()
    old_path = settings.raw['paths']['warehouse']
    # Only owns this fresh, isolated directory, never existing user data.
    with tempfile.TemporaryDirectory(prefix='runtime-benchmark-', dir=root) as directory:
        settings.raw['paths']['warehouse'] = str(Path(directory) / 'test.duckdb')
        try:
            db.migrate()
            with db.connect() as conn:
                frame = bars.reset_index(names='date').assign(ticker='BENCH', source='benchmark')
                db.upsert_df(conn, 'prices_daily', frame, ['ticker', 'date'])
                conn.execute("INSERT INTO instruments(ticker,asset_class,currency,exchange) "
                             "VALUES ('BENCH','equity','USD','NYQ')")
                conn.execute("INSERT INTO positions(id,ticker,qty,currency,avg_cost) "
                             "SELECT 'lot-' || i, 'BENCH', 1, 'USD', 100 FROM range(20) t(i)")
            results['migrate_existing_seconds'] = measure(db.migrate)
            with db.connect(read_only=True) as conn:
                results['holdings_20_lots_seconds'] = measure(
                    lambda: holding_analysis.analyse(conn, as_of=days[-1]))
                results['integrity_seconds'] = measure(lambda: integrity.revisar(conn))
        finally:
            settings.raw['paths']['warehouse'] = old_path
    print(json.dumps(results, indent=2))


if __name__ == '__main__':
    main()
