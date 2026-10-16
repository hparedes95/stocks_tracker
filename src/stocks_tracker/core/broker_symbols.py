"""Broker identity resolution. A ticker alias never changes units or listing currency.

Known listings verified against the issuers; references live in docs/07-snaptrade-personal.md.
Missing/conflicting market identity can be valued from a broker quote, but is never
silently associated with the history of a different listing.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

VENUES = {'NASDAQ': 'XNAS', 'NAS': 'XNAS', 'NMS': 'XNAS', 'NGM': 'XNAS', 'NCM': 'XNAS',
          'NASDAQGS': 'XNAS', 'NASDAQGM': 'XNAS', 'NASDAQCM': 'XNAS',
          'NYSE': 'XNYS', 'NYQ': 'XNYS', 'NYSE ARCA': 'ARCX', 'PCX': 'ARCX',
          'BTS': 'BATS', 'BATS': 'BATS', 'CBOE BZX': 'BATS',
          'AMS': 'XAMS', 'EURONEXT AMSTERDAM': 'XAMS',
          'BME': 'XMAD', 'MAD': 'XMAD', 'GER': 'XETR', 'PAR': 'XPAR', 'LSE': 'XLON'}

# (broker symbol, listing currency, allowed MICs, local history ticker, asset class)
LISTINGS = (
    ('TSM', 'USD', frozenset({'XNYS'}), 'TSM', 'equity'),
    ('ASML', 'USD', frozenset({'XNAS'}), 'ASML', 'equity'),
    ('ASML', 'EUR', frozenset({'XAMS'}), 'ASML.AS', 'equity'),
    ('ASML.AS', 'EUR', frozenset({'XAMS'}), 'ASML.AS', 'equity'),
    ('CIBR', 'USD', frozenset({'XNAS'}), 'CIBR', 'etf'),
    # ITA's primary listing moved; older provider metadata can still say Arca.
    ('ITA', 'USD', frozenset({'BATS', 'ARCX'}), 'ITA', 'etf'),
)
CRYPTO = {'BTC': 'BTC-USD', 'BTC-USD': 'BTC-USD', 'ETH': 'ETH-USD', 'ETH-USD': 'ETH-USD'}


def text(value):
    return value.strip().upper() if isinstance(value, str) else ''


def venue(value):
    value = text(value)
    return VENUES.get(value, value)


@dataclass(frozen=True)
class Resolution:
    ticker: str | None
    currency: str | None
    asset_class: str | None
    reason: str = ''


def resolve(pos, metadata: pd.DataFrame) -> Resolution:
    symbol, kind = text(pos['symbol']), pos['kind']
    if pos['qty'] <= 0:
        return Resolution(None, None, None, 'Posición corta: falta un modelo de margen y garantías.')
    if kind not in ('stock', 'adr', 'etf', 'crypto'):
        return Resolution(None, None, None, f'Producto {kind or "desconocido"}: no se interpreta como una posición al contado.')
    if kind == 'crypto':
        ticker = CRYPTO.get(symbol)
        return (Resolution(ticker, 'USD', 'crypto') if ticker else
                Resolution(None, None, None, 'Criptomoneda sin correspondencia verificada.'))
    listing_currency, market = text(pos.get('listing_currency')), venue(pos.get('exchange'))
    for alias, currency, markets, ticker, asset_class in LISTINGS:
        if symbol == alias and market in markets and (not listing_currency or listing_currency == currency):
            local = metadata[metadata.ticker == ticker]
            if len(local) == 1 and text(local.iloc[0].currency) not in ('', currency):
                return Resolution(None, None, None, 'La divisa de la base local contradice la cotización identificada.')
            if len(local) == 1:
                local_market = venue(local.iloc[0].mic) or venue(local.iloc[0].exchange)
                if local_market and local_market not in markets:
                    return Resolution(None, None, None, 'El mercado de la base local contradice la cotización identificada.')
            return Resolution(ticker, currency, asset_class)
    candidates = metadata[(metadata.ticker == symbol) & (metadata.currency == listing_currency)]
    candidates = candidates[candidates.apply(lambda row: bool(market) and
                 (venue(row.mic) == market or venue(row.exchange) == market), axis=1)]
    if len(candidates) == 1:
        return Resolution(symbol, listing_currency, 'etf' if kind == 'etf' else 'equity')
    if not market:
        reason = 'El bróker no identifica el mercado; se conserva su valoración sin asociar un histórico.'
    elif not listing_currency:
        reason = 'Falta la divisa de cotización y no hay una correspondencia verificada.'
    else:
        reason = 'Símbolo, mercado o divisa sin correspondencia verificada; no se usa otro mercado como sustituto.'
    return Resolution(None, None, None, reason)


def can_value_unmapped(pos):
    """Only known requested spot instruments; quote value never implies model coverage."""
    return (pos['qty'] > 0 and pos['kind'] in ('stock', 'adr', 'etf') and
            text(pos['symbol']) in {entry[0] for entry in LISTINGS})


def research_target(pos, metadata: pd.DataFrame) -> Resolution:
    """Research identity, NEVER an identity for valuing a derivative contract.

    For the two confirmed ETF CFDs, study an explicitly labelled USD ETF
    reference even when the broker reports the contract's OTC venue. This
    establishes neither the contract multiplier nor its financing/margin.
    Other ambiguous products still need an exact listing identity.
    """
    if pos.get('kind') != 'cfd':
        return resolve(pos, metadata)
    symbol = text(pos.get('symbol'))
    if symbol not in ('CIBR', 'ITA'):
        return Resolution(None, None, None, 'Subyacente del CFD sin correspondencia verificada.')
    currency = text(pos.get('listing_currency')) or text(pos.get('currency'))
    if currency != 'USD':
        return Resolution(None, None, None, 'La divisa del CFD no permite identificar la referencia USD.')
    local = metadata[metadata.ticker == symbol]
    markets = next(entry[2] for entry in LISTINGS if entry[0] == symbol)
    if len(local) == 1:
        row = local.iloc[0]
        market = venue(row.mic) or venue(row.exchange)
        if text(row.currency) not in ('', 'USD') or market and market not in markets:
            return Resolution(None, None, None, 'La ficha local contradice la referencia ETF verificada.')
    return Resolution(symbol, 'USD', 'etf', 'Referencia ETF del CFD; no es la valoración del contrato.')
