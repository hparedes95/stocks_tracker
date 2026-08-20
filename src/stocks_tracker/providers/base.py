"""Contratos de los proveedores de datos.

yfinance es una API NO OFICIAL de Yahoo: se ha roto antes y se volvera a
romper. Todo el codigo que la toca vive detras de estos Protocols, de modo que
sustituirla sea escribir un adaptador nuevo y nada mas.

Regla verificada por test: NINGUN modulo fuera de `providers/` puede importar
`yfinance`.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Protocol, runtime_checkable

import numpy as np
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

    ES LA PUERTA DE ENTRADA. Todo precio que use el programa pasa por aqui, asi
    que lo que se cuele aqui ya no lo para nadie: los indicadores lo calculan
    sin rechistar y el resultado tiene aspecto de numero.
    """
    if df is None or df.empty:
        return empty_ohlcv()

    out = df.copy()
    for col in OHLCV_COLUMNS:
        if col not in out.columns:
            out[col] = pd.NA

    out = out[OHLCV_COLUMNS]
    # Se conserva la version datetime para poder comparar fechas: sobre
    # `datetime.date` sueltos, un NaT revienta la comparacion con un TypeError
    # en lugar de quedarse fuera sin ruido.
    fechas = pd.to_datetime(out["date"], errors="coerce")
    out["date"] = fechas.dt.date
    for col in ("open", "high", "low", "close", "adj_close"):
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out["volume"] = pd.to_numeric(out["volume"], errors="coerce").fillna(0).astype("int64")

    # `isfinite` y no solo `> 0`: un infinito es NOTNA y es MAYOR QUE CERO, asi
    # que pasaba las dos condiciones y entraba en el almacen. Basta con que un
    # proveedor divida por cero en su lado para que llegue, y a partir de ahi
    # cualquier media movil que lo toque sale infinita durante toda su ventana.
    precio = pd.to_numeric(out["adj_close"], errors="coerce")
    util = np.isfinite(precio.to_numpy(dtype="float64")) & (precio > 0)

    # Un precio de manana no existe. Llega por husos horarios del proveedor y
    # por barras provisionales mal fechadas, y hace mas dano de lo que parece:
    # la vista `current_session` toma la fecha mas reciente del almacen, asi que
    # UNA fila fechada en el futuro convierte todo el dashboard en el retrato de
    # un dia que no ha ocurrido.
    #
    # Un dia de margen porque hay mercados por delante de UTC: en Tokio, la
    # sesion de "manana" ya esta abierta mientras aqui es hoy.
    manana = pd.Timestamp(date.today() + timedelta(days=1))

    out = out[fechas.notna() & util & (fechas <= manana)]
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
