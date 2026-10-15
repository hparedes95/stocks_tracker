"""Private broker snapshots, separate from manual positions and market bundles.

Snapshots are not trades: disappearing holdings never create fictitious sales,
purchase dates, dividends, fees or realised returns. Only one selected account
replaces the manual portfolio as input; it is never added on top of CSV data.
"""
from __future__ import annotations

import json
import math
from datetime import UTC, datetime

import duckdb
import pandas as pd

from ..providers.snaptrade import Client, SnapTradeError
from . import fx


def state(conn):
    try:
        result = conn.execute('SELECT * FROM snaptrade_state WHERE singleton=1').fetchdf()
    except duckdb.CatalogException:
        return {}
    return result.iloc[0].to_dict() if not result.empty else {}


def active(conn):
    return bool(state(conn).get('active'))


def timestamp(value):
    try:
        result = pd.Timestamp(value)
        if pd.isna(result):
            raise ValueError
        return result.tz_localize('UTC') if result.tzinfo is None else result.tz_convert('UTC')
    except (TypeError, ValueError):
        raise SnapTradeError('Falta una fecha válida de sincronización del proveedor.') from None


def number(value, *, optional=False):
    if value is None and optional:
        return None
    try:
        result = float(value)
        if isinstance(value, bool) or not math.isfinite(result):
            raise ValueError
        return result
    except (TypeError, ValueError):
        raise SnapTradeError('SnapTrade ha devuelto un importe o cantidad inválidos.') from None


def normalise(payload):
    """Validate the complete response before any write. Keep unknown products visible."""
    try:
        positions, balances = payload['positions'], payload['balances']
        rows = positions['results']
        if not isinstance(rows, list) or not isinstance(balances, list) or not balances:
            raise ValueError
        as_of = timestamp(positions['data_freshness']['as_of']).isoformat()
        cash_as_of = payload['account'].get('sync_status', {}).get('balances')
        cash_as_of = timestamp(cash_as_of).isoformat() if cash_as_of else None
        clean, seen, currencies, cash = [], set(), set(), []
        for row in rows:
            inst = row['instrument']
            identity = inst['id']
            if not isinstance(identity, str) or not identity or identity in seen:
                raise ValueError
            seen.add(identity)
            qty = number(row['units'])
            price = number(row.get('price'), optional=True)
            cost = number(row.get('cost_basis'), optional=True)
            if price is not None and price <= 0 or cost is not None and cost < 0:
                raise ValueError
            currency = row.get('currency')
            if not isinstance(currency, str) or len(currency) != 3 or not currency.isupper():
                raise ValueError
            symbol = inst.get('symbol')
            if not isinstance(symbol, str) or not symbol.strip():
                raise ValueError
            equivalent = row.get('cash_equivalent', False)
            if not isinstance(equivalent, bool):
                raise ValueError
            clean.append({'id': identity, 'symbol': symbol, 'kind': inst.get('kind'),
                          'listing_currency': inst.get('currency'), 'exchange': inst.get('exchange'),
                          'qty': qty, 'avg_cost': cost, 'currency': currency, 'price': price,
                          'cash_equivalent': equivalent})
        for row in balances:
            currency = row['currency']['code']
            if not isinstance(currency, str) or len(currency) != 3 or not currency.isupper() or currency in currencies:
                raise ValueError
            currencies.add(currency)
            cash.append({'currency': currency, 'cash': number(row['cash'])})
        return {'account_id': payload['account']['id'], 'as_of': as_of, 'cash_as_of': cash_as_of,
                'positions': clean, 'balances': cash}
    except (KeyError, TypeError, ValueError, AttributeError):
        raise SnapTradeError('Respuesta incompleta o incompatible; se conserva la cartera anterior.') from None


def store_snapshot(conn, snapshot, *, now=None):
    now = now or datetime.now(UTC).replace(tzinfo=None)
    account = snapshot['account_id']
    previous = state(conn)
    if previous.get('active') and previous.get('account_id') != account:
        raise SnapTradeError('Desactiva la cuenta actual antes de elegir otra.')
    encoded = json.dumps(snapshot, sort_keys=True, allow_nan=False)
    conn.execute('BEGIN TRANSACTION')
    try:
        conn.execute('INSERT INTO snaptrade_state(singleton,active,auto_sync) VALUES (1,FALSE,FALSE) ON CONFLICT DO NOTHING')
        conn.execute('UPDATE snaptrade_state SET account_id=?, previous_snapshot=snapshot, snapshot=?, '
                     'last_attempt=?,last_success=?,error=NULL WHERE singleton=1', [account, encoded, now, now])
        if previous.get('active'):
            conn.execute('DELETE FROM advisor_runs')
            conn.execute('DELETE FROM advisor_diagnostics')
        conn.execute('COMMIT')
    except Exception:  # noqa: BLE001 — rollback then propagate every failure
        conn.execute('ROLLBACK')
        raise


def enable(conn, enabled, *, automatic=True):
    current = state(conn)
    if enabled and not current.get('snapshot'):
        raise SnapTradeError('Primero consulta y revisa la cartera de SnapTrade.')
    conn.execute('BEGIN TRANSACTION')
    try:
        conn.execute('UPDATE snaptrade_state SET active=?,auto_sync=? WHERE singleton=1', [enabled, enabled and automatic])
        conn.execute('DELETE FROM advisor_runs')
        conn.execute('DELETE FROM advisor_diagnostics')
        conn.execute('COMMIT')
    except Exception:  # noqa: BLE001 — rollback then propagate every failure
        conn.execute('ROLLBACK')
        raise


def sync(account_id):
    from .db import connect
    from .snaptrade_credentials import load
    from .timeutils import utcnow
    with connect() as conn:
        conn.execute('INSERT INTO snaptrade_state(singleton,active,auto_sync) VALUES (1,FALSE,FALSE) ON CONFLICT DO NOTHING')
        conn.execute('UPDATE snaptrade_state SET last_attempt=? WHERE singleton=1', [utcnow()])
    try:
        snapshot = normalise(Client(*load()).snapshot(account_id))
        with connect() as conn:
            store_snapshot(conn, snapshot)
    except (SnapTradeError, RuntimeError, OSError, duckdb.Error) as exc:
        # Only explicitly safe API/vault messages cross the UI boundary.
        message = str(exc) if isinstance(exc, SnapTradeError) else 'No se pudo acceder a las claves o guardar la copia local.'
        with connect() as conn:
            conn.execute('UPDATE snaptrade_state SET error=? WHERE singleton=1', [message])
            if active(conn):
                conn.execute('DELETE FROM advisor_runs')
        raise SnapTradeError(message) from None
    return snapshot


VENUES = {'NASDAQ': 'XNAS', 'NMS': 'XNAS', 'NGM': 'XNAS', 'NCM': 'XNAS',
          'NYSE': 'XNYS', 'NYQ': 'XNYS', 'PCX': 'ARCX', 'BME': 'XMAD', 'MAD': 'XMAD',
          'GER': 'XETR', 'PAR': 'XPAR', 'LSE': 'XLON'}


def portfolio(conn, as_of=None):
    """Return current quantities using local market valuations, without reapplying splits."""
    current = state(conn)
    if not current.get('active'):
        return None
    snapshot = json.loads(current['snapshot'])
    metadata = conn.execute('SELECT ticker,currency,exchange,mic,gics_sector FROM instruments').fetchdf()
    rows = []
    for pos in snapshot['positions']:
        if pos['cash_equivalent'] or pos['qty'] == 0:
            continue
        venue = VENUES.get(pos['exchange'], pos['exchange'])
        candidates = metadata[(metadata.ticker == pos['symbol']) & (metadata.currency == pos['listing_currency'])]
        candidates = candidates[candidates.apply(lambda x, venue=venue: bool(venue) and
                      (x.mic == venue or VENUES.get(x.exchange, x.exchange) == venue), axis=1)]
        supported = pos['kind'] in ('stock', 'etf', 'adr') and pos['qty'] > 0 and len(candidates) == 1
        sector = candidates.iloc[0].gics_sector if supported else None
        price = conn.execute('SELECT close FROM indicators_daily WHERE ticker=? AND date=?',
                             [pos['symbol'], as_of]).fetchone() if supported and as_of else None
        rows.append({'id': pos['id'], 'ticker': pos['symbol'], 'qty': pos['qty'],
                     'avg_cost': pos['avg_cost'] if pos['currency'] == pos['listing_currency'] else None,
                     'currency': pos['listing_currency'], 'gics_sector': sector,
                     'close': price[0] if price else float('nan'), 'opened_at': None,
                     'updated_at': timestamp(snapshot['as_of']).tz_localize(None),
                     'supported': supported})
    frame = pd.DataFrame(rows, columns=['id', 'ticker', 'qty', 'avg_cost', 'currency', 'gics_sector',
                                       'close', 'opened_at', 'updated_at', 'supported'])
    if frame.ticker.duplicated().any():
        # Multiple venues/currencies are not silently merged under a single ticker.
        frame.loc[frame.ticker.duplicated(keep=False), 'supported'] = False
        frame.loc[~frame.supported, 'close'] = float('nan')
    return frame


def cash_eur(conn, as_of=None):
    current = state(conn)
    if not current.get('active'):
        return None
    balances = pd.DataFrame(json.loads(current['snapshot'])['balances'])
    return fx.total(fx.a_base(balances.cash, balances.currency, fx.tipos(conn, as_of)))


def readiness(conn, *, now=None):
    current = state(conn)
    if not current.get('active'):
        return ''
    if current.get('error'):
        return 'La última sincronización falló. Revisa Conexión SnapTrade antes de recalcular el asesor.'
    snapshot = json.loads(current['snapshot'])
    clock = timestamp(now or datetime.now(UTC))
    for label, value in [('posiciones', snapshot['as_of']), ('efectivo', snapshot.get('cash_as_of'))]:
        if value is None:
            return f'SnapTrade no indica la antigüedad del {label}; no se activa el asesor.'
        age = clock - timestamp(value)
        if age.total_seconds() < -300 or age.total_seconds() > 36 * 3600:
            return f'La fecha de {label} es futura o tiene más de 36 horas. Sincroniza o reconecta SnapTrade.'
    return ''


def auto_sync():
    """Called by the UI; throttles persist across restarts. Never starts a service."""
    from .db import connect
    with connect(read_only=True) as conn:
        current = state(conn)
    if not current.get('active') or not current.get('auto_sync'):
        return False
    last = current.get('last_attempt')
    if pd.notna(last) and (timestamp(datetime.now(UTC)) - timestamp(last)).total_seconds() < 300:
        return False
    sync(current['account_id'])
    return True
