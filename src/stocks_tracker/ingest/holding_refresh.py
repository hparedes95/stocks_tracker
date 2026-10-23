"""Refresh held instruments first, before consuming a free provider's budget."""
from requests import RequestException

from ..core import fx
from ..core.db import arrancar, connect, migrate
from ..core.holding_analysis import holdings
from ..core.ids import ulid
from ..core.locking import AlreadyRunning, single_writer
from ..core.market_bundle import frozen_market
from ..providers.base import ProviderError
from .run_ingest import _log, ingest_prices


def refresh():
    if frozen_market():
        raise ValueError('La copia está congelada; no se cambian sus precios.')
    migrate()
    with connect(read_only=True) as conn:
        positions = holdings(conn)
        tickers = sorted({p['ticker'] for p in positions if p['ticker']})
        rates = fx.tipos(conn)
        currency_tickers = sorted({fx.PARES[p['currency']] for p in positions
                                   if p.get('currency') in fx.PARES and p['currency'] not in rates})
    # One bounded instrument per request. Already current histories are skipped
    # by ingest_prices; missing histories use the normal configured backfill.
    run_id = ulid()
    for ticker in dict.fromkeys([*currency_tickers, *tickers]):
        try:
            ingest_prices(tickers_override=[ticker], run_id=run_id)
        except (ProviderError, OSError, RequestException) as exc:
            # A failed symbol must not prevent refreshing other holdings. Keep a
            # safe failure record; the report exposes stale/missing prices.
            with connect() as conn:
                _log(conn, run_id, 'holding_prices', ticker, 'FAILED', error=type(exc).__name__)


def main():
    try:
        with single_writer('ingesta'):
            refresh()
    except AlreadyRunning:
        print('Ya hay otro proceso actualizando los datos. Espera a que termine.')
        return 75
    return 0


if __name__ == '__main__':
    arrancar(main)
