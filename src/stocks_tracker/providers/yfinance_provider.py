"""Proveedor de yfinance: precios y fundamentales de Yahoo Finance.

Yahoo bloquea IPs con uso intensivo. Toda la estrategia defensiva vive aqui:
descarga por lotes, sin hilos, pausas con jitter, presupuesto de peticiones y
degradacion elegante (si un lote falla, el resto continua).

`import yfinance` solo puede aparecer en este fichero. Hay un test que lo
comprueba recorriendo el AST de todo `src/`.
"""

from __future__ import annotations

import random
import time
from datetime import date

import pandas as pd

from ..core.config import get_settings
from .base import (
    FUNDAMENTALS_COLUMNS,
    ProviderError,
    RateLimitError,
    empty_fundamentals,
    normalize_ohlcv,
)


def _import_yfinance():
    try:
        import yfinance as yf  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - depende del entorno
        raise ProviderError(
            "yfinance no esta instalado. Ejecuta `make setup` (extra 'data') "
            "o usa el proveedor sintetico con `make ingest-demo`."
        ) from exc
    return yf


def _is_rate_limit(exc: Exception) -> bool:
    text = f"{type(exc).__name__} {exc}".lower()
    return "ratelimit" in text or "429" in text or "too many requests" in text


class YFinanceProvider:
    """Implementa PriceProvider y FundamentalsProvider."""

    name = "yfinance"

    def __init__(self) -> None:
        cfg = get_settings().ingest
        self.batch_size = int(cfg.get("batch_size", 45))
        self.use_threads = bool(cfg.get("threads", False))
        sleep_range = cfg.get("sleep_between_batches", [1.5, 3.5])
        self.sleep_min, self.sleep_max = float(sleep_range[0]), float(sleep_range[1])
        self.max_requests = int(cfg.get("max_requests_per_run", 400))
        self.requests_used = 0

    def supports(self, ticker: str) -> bool:  # noqa: ARG002
        return True

    def _budget_left(self) -> bool:
        return self.requests_used < self.max_requests

    def _pause(self) -> None:
        time.sleep(random.uniform(self.sleep_min, self.sleep_max))

    # ------------------------------------------------------------------
    # Precios
    # ------------------------------------------------------------------
    def fetch_ohlcv(
        self, tickers: list[str], start: date, end: date, interval: str = "1d"
    ) -> pd.DataFrame:
        """Descarga OHLCV por lotes.

        Un universo de 750 valores son ~16 peticiones, no 750. `threads=False`
        es deliberado: la concurrencia es justo lo que dispara el bloqueo, y en
        un proceso nocturno el tiempo total da igual.
        """
        yf = _import_yfinance()
        frames: list[pd.DataFrame] = []
        failed: list[str] = []

        for i in range(0, len(tickers), self.batch_size):
            if not self._budget_left():
                failed.extend(tickers[i:])
                break

            chunk = tickers[i : i + self.batch_size]
            try:
                raw = yf.download(
                    tickers=chunk,
                    start=start,
                    end=end,
                    interval=interval,
                    group_by="ticker",
                    auto_adjust=False,
                    actions=False,
                    threads=self.use_threads,
                    progress=False,
                )
                self.requests_used += 1
                frames.append(self._reshape(raw, chunk))
            except Exception as exc:  # noqa: BLE001
                if _is_rate_limit(exc):
                    # Se marca el resto como fallido y se sale: insistir cuando
                    # Yahoo ya esta limitando solo empeora el bloqueo.
                    failed.extend(tickers[i:])
                    break
                failed.extend(chunk)

            if i + self.batch_size < len(tickers):
                self._pause()

        result = (
            normalize_ohlcv(pd.concat(frames, ignore_index=True), self.name)
            if frames
            else normalize_ohlcv(pd.DataFrame(), self.name)
        )
        result.attrs["failed_tickers"] = failed
        result.attrs["requests_used"] = self.requests_used
        return result

    @staticmethod
    def _reshape(raw: pd.DataFrame, chunk: list[str]) -> pd.DataFrame:
        """Pasa el MultiIndex de yfinance a formato largo."""
        if raw is None or raw.empty:
            return pd.DataFrame()

        out: list[pd.DataFrame] = []
        multi = isinstance(raw.columns, pd.MultiIndex)

        for ticker in chunk:
            try:
                sub = raw[ticker] if multi else raw
            except KeyError:
                continue
            if sub is None or sub.empty:
                continue

            frame = sub.reset_index()
            frame.columns = [str(c).lower().replace(" ", "_") for c in frame.columns]
            if "date" not in frame.columns and "datetime" in frame.columns:
                frame = frame.rename(columns={"datetime": "date"})
            # Sin dividendos ni splits, Yahoo no devuelve adj_close: se cae al
            # cierre normal, que para indices y divisas es lo correcto.
            if "adj_close" not in frame.columns:
                frame["adj_close"] = frame.get("close")
            frame["ticker"] = ticker
            out.append(frame)

        return pd.concat(out, ignore_index=True) if out else pd.DataFrame()

    # ------------------------------------------------------------------
    # Fundamentales
    # ------------------------------------------------------------------
    def fetch_snapshot(self, tickers: list[str]) -> pd.DataFrame:
        """Foto actual de ratios, un ticker por peticion.

        Aviso importante: esto es un SNAPSHOT, no una serie point-in-time. Los
        ratios son los de hoy, no los que habia hace tres anos. Por eso el
        backtest de factores fundamentales se marca como no valido hasta
        acumular historico propio (ver docs/00 seccion 6).
        """
        yf = _import_yfinance()
        rows: list[dict] = []
        today = date.today()

        for ticker in tickers:
            if not self._budget_left():
                break
            try:
                info = yf.Ticker(ticker).info or {}
                self.requests_used += 1
            except Exception as exc:  # noqa: BLE001
                if _is_rate_limit(exc):
                    raise RateLimitError(str(exc)) from exc
                continue

            market_cap = info.get("marketCap")
            fcf = info.get("freeCashflow")
            rows.append(
                {
                    "ticker": ticker,
                    "as_of": today,
                    "trailing_pe": info.get("trailingPE"),
                    "forward_pe": info.get("forwardPE"),
                    "peg_ratio": info.get("trailingPegRatio") or info.get("pegRatio"),
                    "price_to_book": info.get("priceToBook"),
                    "price_to_sales": info.get("priceToSalesTrailing12Months"),
                    "ev_to_ebitda": info.get("enterpriseToEbitda"),
                    "ev_to_revenue": info.get("enterpriseToRevenue"),
                    "fcf_yield": (fcf / market_cap) if (fcf and market_cap) else None,
                    "earnings_yield": (
                        1.0 / info["trailingPE"] if info.get("trailingPE") else None
                    ),
                    "gross_margin": info.get("grossMargins"),
                    "operating_margin": info.get("operatingMargins"),
                    "profit_margin": info.get("profitMargins"),
                    "roe": info.get("returnOnEquity"),
                    "roa": info.get("returnOnAssets"),
                    "revenue_growth_yoy": info.get("revenueGrowth"),
                    "earnings_growth_yoy": info.get("earningsGrowth"),
                    "debt_to_equity": info.get("debtToEquity"),
                    "net_debt_to_ebitda": self._net_debt_to_ebitda(info),
                    "current_ratio": info.get("currentRatio"),
                    "dividend_yield": info.get("dividendYield"),
                    "payout_ratio": info.get("payoutRatio"),
                    "shares_outstanding": info.get("sharesOutstanding"),
                    "beta": info.get("beta"),
                    "market_cap": market_cap,
                    "currency": info.get("currency"),
                }
            )

        if not rows:
            return empty_fundamentals()
        return pd.DataFrame(rows, columns=FUNDAMENTALS_COLUMNS)

    @staticmethod
    def _net_debt_to_ebitda(info: dict) -> float | None:
        debt, cash, ebitda = (
            info.get("totalDebt"),
            info.get("totalCash"),
            info.get("ebitda"),
        )
        if debt is None or ebitda in (None, 0):
            return None
        return (debt - (cash or 0)) / ebitda

    def fetch_metadata(self, tickers: list[str]) -> pd.DataFrame:
        yf = _import_yfinance()
        rows: list[dict] = []
        for ticker in tickers:
            if not self._budget_left():
                break
            try:
                info = yf.Ticker(ticker).info or {}
                self.requests_used += 1
            except Exception:  # noqa: BLE001
                continue
            rows.append(
                {
                    "ticker": ticker,
                    "name": info.get("longName") or info.get("shortName") or ticker,
                    "asset_class": self._asset_class(ticker, info),
                    "exchange": info.get("exchange"),
                    "currency": info.get("currency"),
                    "country": info.get("country"),
                    "gics_sector": info.get("sector"),
                    "gics_industry": info.get("industry"),
                    "market_cap": info.get("marketCap"),
                }
            )
        return pd.DataFrame(rows)

    @staticmethod
    def _asset_class(ticker: str, info: dict) -> str:
        quote_type = (info.get("quoteType") or "").upper()
        mapping = {
            "ETF": "etf", "INDEX": "index", "CRYPTOCURRENCY": "crypto",
            "CURRENCY": "fx", "FUTURE": "commodity", "EQUITY": "equity",
        }
        if quote_type in mapping:
            return mapping[quote_type]
        if ticker.startswith("^"):
            return "index"
        if ticker.endswith("-USD"):
            return "crypto"
        if ticker.endswith("=F"):
            return "commodity"
        if ticker.endswith("=X"):
            return "fx"
        return "equity"
