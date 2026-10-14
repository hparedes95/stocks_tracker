"""Ampliación explícita y acotada: un valor, su referencia y su divisa."""
from datetime import timedelta

from ..core import fx
from ..core.db import connect
from ..core.evaluation import BENCHMARKS
from ..core.market_bundle import frozen_market
from ..core.timeutils import utcnow
from .run_ingest import ingest_prices


def extend(ticker):
    if frozen_market():
        raise ValueError('La copia está congelada: confirma su desactivación antes de descargar.')
    with connect(read_only=True) as c:
        meta = c.execute('SELECT country,currency FROM instruments WHERE ticker=?', [ticker]).fetchone()
        if not meta:
            raise ValueError('Instrumento no conocido')
        targets = {ticker, BENCHMARKS.get(meta[0], 'VT')}
        if meta[1] in fx.PARES:
            targets.add(fx.PARES[meta[1]])
        done = {r[0] for r in c.execute("SELECT target FROM ingest_log WHERE task='history_extend' "
                "AND finished_at >= ?", [utcnow() - timedelta(days=7)]).fetchall()}
    results = []
    for target in sorted(targets):
        if target in done:
            results.append({'ticker': target, 'status': 'pausa_de_siete_dias'})
            continue
        start = utcnow()
        status, count = 'FAILED', 0
        try:
            count = ingest_prices(full=True, years=30, tickers_override=[target])
            status = 'OK' if count else 'UNAVAILABLE'
        finally:
            with connect() as c:
                c.execute('INSERT INTO ingest_log(run_id,task,target,status,started_at,finished_at,rows_written) '
                           "VALUES (?, 'history_extend', ?, ?, ?, ?, ?)",
                           [f'extend-{target}-{start.isoformat()}', target, status, start, utcnow(), count])
        results.append({'ticker': target, 'status': status, 'rows': count})
    return results


def main():
    import argparse
    import json

    from ..core.locking import single_writer
    parser = argparse.ArgumentParser()
    parser.add_argument('ticker')
    args = parser.parse_args()
    with single_writer('ingesta'):
        print(json.dumps(extend(args.ticker)))


if __name__ == '__main__':
    main()
