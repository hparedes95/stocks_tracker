"""Alpha Vantage como contraste gratuito y limitado de acciones de EE.UU.

La cuenta gratuita permite muy pocas peticiones diarias. Por eso este modulo
no entra en la descarga del universo: se activa automaticamente en la auditoria
cruzada cuando existe ``ALPHA_VANTAGE_API_KEY`` y se limita a una seleccion
pequena de cartera, senales y muestra rotatoria.
"""

from __future__ import annotations

import os
import time
from datetime import date

import pandas as pd
import requests

from .base import NotSupportedError, ProviderError, RateLimitError, normalize_ohlcv

_BASE_URL = "https://www.alphavantage.co/query"
_TIMEOUT = 30
VARIABLE_DE_ENTORNO = "ALPHA_VANTAGE_API_KEY"

# El plan gratuito documenta 25 solicitudes diarias. Dejamos cinco libres para
# comprobaciones manuales y para no agotar la cuenta por un reintento.
MAX_PETICIONES = 20
MAX_POR_EJECUCION = 20
PAUSA_SEGUNDOS = 12.5


def api_key() -> str | None:
    value = os.environ.get(VARIABLE_DE_ENTORNO, "").strip()
    return value or None


class AlphaVantageProvider:
    name = "alpha_vantage"
    max_por_ejecucion = MAX_POR_EJECUCION

    def __init__(self, clave: str | None = None) -> None:
        self._clave = clave or api_key()
        self.requests_used = 0
        self.ha_respondido = False
        self._session = requests.Session()

    @property
    def configurado(self) -> bool:
        return bool(self._clave)

    def supports(self, ticker: str) -> bool:
        # En el plan gratuito se usa solo el simbolo simple estadounidense.
        # Los sufijos de Yahoo (.MC, .DE...) no son equivalentes a los de AV.
        return bool(
            self.configurado
            and ticker
            and ticker.replace(".", "").isalnum()
            and not any(char in ticker for char in (".", "-", "=", "^"))
        )

    def fetch_ohlcv(
        self, tickers: list[str], start: date, end: date, interval: str = "1d"
    ) -> pd.DataFrame:
        if interval != "1d":
            raise NotSupportedError("Alpha Vantage solo se usa en diario.")
        if not self.configurado:
            raise ProviderError(f"Falta {VARIABLE_DE_ENTORNO} en el entorno o .env.")

        frames: list[pd.DataFrame] = []
        failed: list[str] = []
        candidates = [t for t in tickers if self.supports(t)][:MAX_POR_EJECUCION]
        for index, ticker in enumerate(candidates):
            if self.requests_used >= MAX_PETICIONES:
                failed.extend(candidates[index:])
                break
            try:
                frame = self._one(ticker, start, end)
            except RateLimitError:
                failed.extend(candidates[index:])
                break
            except ProviderError:
                failed.append(ticker)
                continue

            if frame.empty:
                failed.append(ticker)
            else:
                frame["ticker"] = ticker
                frames.append(frame)
            if index + 1 < len(candidates):
                time.sleep(PAUSA_SEGUNDOS)

        out = normalize_ohlcv(
            pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(),
            self.name,
        )
        if not out.empty:
            self.ha_respondido = True
        out.attrs["failed_tickers"] = failed
        out.attrs["requests_used"] = self.requests_used
        return out

    def _one(self, ticker: str, start: date, end: date) -> pd.DataFrame:
        try:
            response = self._session.get(
                _BASE_URL,
                params={
                    "function": "TIME_SERIES_DAILY",
                    "symbol": ticker,
                    "outputsize": "compact",
                    "apikey": self._clave,
                },
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise ProviderError(f"Alpha Vantage no responde para {ticker}: {exc}") from exc
        finally:
            self.requests_used += 1

        if response.status_code == 429:
            raise RateLimitError("Alpha Vantage limita las peticiones.")
        if response.status_code != 200:
            raise ProviderError(f"Alpha Vantage devolvio HTTP {response.status_code}.")
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderError("Alpha Vantage no devolvio JSON.") from exc
        return interpretar(payload, ticker, start, end)


def interpretar(payload: dict, ticker: str, start: date, end: date) -> pd.DataFrame:
    if not isinstance(payload, dict):
        raise ProviderError(f"Alpha Vantage devolvio un formato invalido para {ticker}.")
    if payload.get("Error Message"):
        raise ProviderError(f"Alpha Vantage para {ticker}: {payload['Error Message']}")
    # Tanto Note como Information se usan para comunicar limites de cuota.
    limit_message = payload.get("Note") or payload.get("Information")
    if limit_message:
        raise RateLimitError(f"Alpha Vantage: {limit_message}")

    series = payload.get("Time Series (Daily)")
    if not isinstance(series, dict) or not series:
        return pd.DataFrame()

    rows = []
    for day, values in series.items():
        try:
            parsed = pd.Timestamp(day).date()
        except (TypeError, ValueError):
            continue
        if parsed < start or parsed > end or not isinstance(values, dict):
            continue
        rows.append({
            "date": parsed,
            "open": values.get("1. open"),
            "high": values.get("2. high"),
            "low": values.get("3. low"),
            "close": values.get("4. close"),
            "adj_close": values.get("4. close"),
            "volume": values.get("5. volume", 0),
        })
    return pd.DataFrame(rows)
