"""Horizontes en sesiones de bolsa, independientes de los huecos del proveedor."""
from functools import lru_cache

import pandas as pd

COUNTRIES = {'US': 'XNYS', 'United States': 'XNYS', 'ES': 'XMAD', 'Spain': 'XMAD',
             'DE': 'XETR', 'Germany': 'XETR', 'FR': 'XPAR', 'France': 'XPAR',
             'GB': 'XLON', 'United Kingdom': 'XLON', 'CA': 'XTSE', 'JP': 'XTKS'}
EXCHANGES = {'NASDAQ': 'XNYS', 'NYSE': 'XNYS', 'NMS': 'XNYS', 'NYQ': 'XNYS',
             'NCM': 'XNYS', 'NGM': 'XNYS', 'PCX': 'XNYS', 'BME': 'XMAD',
             'MAD': 'XMAD', 'LSE': 'XLON', 'GER': 'XETR', 'PAR': 'XPAR'}


def calendar_name(country=None, exchange=None):
    return EXCHANGES.get(exchange) or COUNTRIES.get(country)


@lru_cache(maxsize=64)
def _calendar(name, first_year, last_year):
    import exchange_calendars as xcals
    # El 1 de enero puede quedar antes de la primera sesión del calendario
    # construido. Un margen permite consultar también festivos en los bordes.
    return xcals.get_calendar(name, start=f'{first_year - 1}-01-01', end=f'{last_year + 1}-12-31')


def sessions(name, start, end):
    if not name:
        raise ValueError('No se conoce el calendario del mercado; no se inventan sesiones.')
    start, end = pd.Timestamp(start).normalize(), pd.Timestamp(end).normalize()
    result = _calendar(name, start.year, end.year).sessions_in_range(start, end)
    return result.tz_localize(None) if result.tz is not None else result


def dates_for_horizon(name, signal, horizon):
    if horizon not in (21, 63, 126):
        raise ValueError('Horizonte no admitido')
    start = pd.Timestamp(signal).normalize() + pd.Timedelta(days=1)
    future = sessions(name, start, start + pd.Timedelta(days=365))
    if len(future) <= horizon:
        raise ValueError('Calendario insuficiente')
    return future[0], future[horizon]


def check_history(frame, name):
    if frame.empty:
        return {'sessions': 0, 'missing': [], 'reason': 'Sin histórico.'}
    dates = pd.DatetimeIndex(pd.to_datetime(frame.date)).normalize()
    expected = sessions(name, dates.min(), dates.max())
    missing = expected.difference(dates)
    extra = dates.difference(expected)
    reason = ''
    if len(missing) or len(extra):
        reason = f'Calendario {name}: faltan {len(missing)} sesiones y hay {len(extra)} fechas fuera de sesión.'
    return {'sessions': len(expected), 'missing': [str(d.date()) for d in missing], 'reason': reason}
