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

# Esquema fijo que TODO PriceProvider debe devolver, en este orden.
OHLCV_COLUMNS = [
    "ticker", "date", "open", "high", "low", "close", "adj_close", "volume",
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


def empty_ohlcv() -> pd.DataFrame:
    return pd.DataFrame(columns=OHLCV_COLUMNS)


def empty_fundamentals() -> pd.DataFrame:
    return pd.DataFrame(columns=FUNDAMENTALS_COLUMNS)


def normalize_ohlcv(df: pd.DataFrame, source: str) -> pd.DataFrame:
    """Deja el DataFrame en el esquema canonico y descarta filas inservibles."""
    if df is None or df.empty:
        return empty_ohlcv()

    out = df.copy()
    for col in OHLCV_COLUMNS:
        if col not in out.columns:
            out[col] = pd.NA

    out = out[OHLCV_COLUMNS]
    out["date"] = pd.to_datetime(out["date"]).dt.date
    for col in ("open", "high", "low", "close", "adj_close"):
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out["volume"] = pd.to_numeric(out["volume"], errors="coerce").fillna(0).astype("int64")

    # Sin precio de cierre ajustado la fila no sirve para nada aguas abajo.
    out = out[out["adj_close"].notna() & (out["adj_close"] > 0)]

    out["source"] = source
    out["ingested_at"] = utcnow()
    return out.drop_duplicates(subset=["ticker", "date"]).reset_index(drop=True)


def completeness(row: pd.Series, fields: list[str]) -> float:
    """Fraccion de campos con dato. Es el termometro de fiabilidad del scoring.

    Importa especialmente en Europa, donde Yahoo deja muchos campos vacios: sin
    esta metrica, un valor del IBEX con la mitad de los datos competiria de tu a
    tu con uno del S&P que los tiene todos.
    """
    if not fields:
        return 0.0
    present = sum(1 for f in fields if f in row.index and pd.notna(row[f]))
    return present / len(fields)
