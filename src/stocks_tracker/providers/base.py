"""Contratos de los proveedores de datos.

yfinance es una API NO OFICIAL de Yahoo: se ha roto antes y se volvera a
romper. Todo el codigo que la toca vive detras de estos Protocols, de modo que
sustituirla sea escribir un adaptador nuevo y nada mas.

Regla verificada por test: NINGUN modulo fuera de `providers/` puede importar
`yfinance`.
"""

from __future__ import annotations

from datetime import date
from typing import Protocol, runtime_checkable

import pandas as pd

from ..core.timeutils import utcnow

OHLCV_COLUMNS = [
    "ticker", "date", "open", "high", "low", "close", "adj_close", "volume",
]

QUOTE_COLUMNS = [
    "ticker", "as_of", "price", "previous_close", "change_pct",
    "day_high", "day_low", "volume", "currency",
]

FUNDAMENTALS_COLUMNS = [
    "ticker", "as_of", "trailing_pe", "forward_pe", "peg_ratio", "price_to_book",
    "price_to_sales", "ev_to_ebitda", "ev_to_revenue", "fcf_yield", "earnings_yield",
    "gross_margin", "operating_margin", "profit_margin", "roe", "roa",
    "revenue_growth_yoy", "earnings_growth_yoy", "debt_to_equity",
    "net_debt_to_ebitda", "current_ratio", "dividend_yield", "payout_ratio",
    "shares_outstanding", "beta", "market_cap", "currency",
]


class ProviderError(RuntimeError):
    """Fallo generico de un proveedor."""


class RateLimitError(ProviderError):
    """El proveedor esta limitando las peticiones (HTTP 429 o equivalente)."""


class NotSupportedError(ProviderError):
    """El proveedor no ofrece este dato. El registro pasa al siguiente."""


@runtime_checkable
class PriceProvider(Protocol):
    name: str

    def fetch_ohlcv(
        self, tickers: list[str], start: date, end: date, interval: str = "1d"
    ) -> pd.DataFrame:
        """OHLCV diario. Devuelve el esquema OHLCV_COLUMNS, formato largo."""
        ...

    def supports(self, ticker: str) -> bool: ...


@runtime_checkable
class FundamentalsProvider(Protocol):
    name: str

    def fetch_snapshot(self, tickers: list[str]) -> pd.DataFrame:
        """Foto actual de ratios. Devuelve FUNDAMENTALS_COLUMNS."""
        ...

    def fetch_metadata(self, tickers: list[str]) -> pd.DataFrame:
        """Nombre, sector GICS, bolsa, divisa, capitalizacion."""
        ...


@runtime_checkable
class QuoteProvider(Protocol):
    name: str

    def fetch_quotes(self, tickers: list[str]) -> pd.DataFrame:
        """Cotizacion actual. Devuelve QUOTE_COLUMNS, una fila por ticker."""
        ...


def empty_quotes() -> pd.DataFrame:
    return pd.DataFrame(columns=QUOTE_COLUMNS)


def normalize_quotes(df: pd.DataFrame, source: str) -> pd.DataFrame:
    """Esquema canonico de cotizaciones y descarte de filas inservibles."""
    if df is None or df.empty:
        return empty_quotes()

    out = df.copy()
    for col in QUOTE_COLUMNS:
        if col not in out.columns:
            out[col] = pd.NA

    out = out[QUOTE_COLUMNS]
    for col in ("price", "previous_close", "day_high", "day_low", "volume"):
        out[col] = pd.to_numeric(out[col], errors="coerce")

    out = out[out["price"].notna() & (out["price"] > 0)]
    if out.empty:
        return empty_quotes()

    prev = out["previous_close"].where(out["previous_close"] > 0)
    out["change_pct"] = out["price"] / prev - 1.0

    out["source"] = source
    return out.reset_index(drop=True)


def empty_ohlcv() -> pd.DataFrame:
    return pd.DataFrame(columns=OHLCV_COLUMNS)


def empty_fundamentals() -> pd.DataFrame:
    return pd.DataFrame(columns=FUNDAMENTALS_COLUMNS)


def normalize_ohlcv(df: pd.DataFrame, source: str) -> pd.DataFrame:
    """Deja el DataFrame en el esquema canonico y descarta filas inservibles.

    Las fechas corruptas de un proveedor no deben tumbar todo el lote: se
    convierten en NaT, se descartan junto con las filas sin precio util y el
    resto del universo continua la ingesta.
    """
    if df is None or df.empty:
        return empty_ohlcv()

    out = df.copy()
    for col in OHLCV_COLUMNS:
        if col not in out.columns:
            out[col] = pd.NA

    out = out[OHLCV_COLUMNS]
    out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.date
    for col in ("open", "high", "low", "close", "adj_close"):
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out["volume"] = pd.to_numeric(out["volume"], errors="coerce").fillna(0).astype("int64")

    out = out[out["date"].notna() & out["adj_close"].notna() & (out["adj_close"] > 0)]
    if out.empty:
        return empty_ohlcv()

    out["source"] = source
    out["ingested_at"] = utcnow()
    return out.drop_duplicates(subset=["ticker", "date"]).reset_index(drop=True)


def completeness(row: pd.Series, fields: list[str]) -> float:
    """Fraccion de campos con dato."""
    if not fields:
        return 0.0
    present = sum(1 for f in fields if f in row.index and pd.notna(row[f]))
    return present / len(fields)
