"""Refresh held instruments first, before consuming a free provider's budget."""
from requests import RequestException

from ..core.db import connect, migrate
from ..core.holding_analysis import holdings
from ..core.ids import ulid
from ..core.locking import single_writer
from ..core.market_bundle import frozen_market
from ..providers.base import ProviderError
from .run_ingest import _log, ingest_prices


def refresh():
    if frozen_market():
        raise ValueError('La copia está congelada; no se cambian sus precios.')
    migrate()
    with connect(read_only=True) as conn:
        tickers = sorted({p['ticker'] for p in holdings(conn) if p['ticker']})
    # One bounded instrument per request. Already current histories are skipped
    # by ingest_prices; missing histories use the normal configured backfill.
    run_id = ulid()
    for ticker in tickers:
        try:
            ingest_prices(tickers_override=[ticker], run_id=run_id)
        except (ProviderError, OSError, RequestException) as exc:
            # A failed symbol must not prevent refreshing other holdings. Keep a
            # safe failure record; the report exposes stale/missing prices.
            with connect() as conn:
                _log(conn, run_id, 'holding_prices', ticker, 'FAILED', error=type(exc).__name__)


if __name__ == '__main__':
    with single_writer('ingesta'):
        refresh()
