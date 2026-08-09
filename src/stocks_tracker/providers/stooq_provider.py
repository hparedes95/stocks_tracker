"""Proveedor de respaldo: precios diarios desde Stooq.

Existe por una razon concreta: yfinance es una API NO OFICIAL de Yahoo que ya
se ha roto antes y se volvera a romper. Cuando eso pase, el sistema debe seguir
actualizandose aunque sea con menos cobertura, en lugar de quedarse congelado.

Stooq sirve CSV plano sin clave ni registro. A cambio:

- **Una peticion por ticker**, no por lote. Para 750 valores son 750
  peticiones: es el camino de emergencia, no el habitual.
- **No hay fundamentales.** `fetch_snapshot` y `fetch_metadata` lanzan
  `NotSupportedError` para que la cadena pase al siguiente proveedor.
- **El cierre NO esta ajustado por dividendos.** Esto importa mas de lo que
  parece y se explica en `adj_close` mas abajo.

La cobertura fuera de Estados Unidos es parcial y el mapeo de sufijos esta
hecho a partir de la convencion publica de Stooq, no verificado mercado por
mercado. Un ticker que Stooq no conozca devuelve vacio y queda registrado como
fallido: es exactamente el comportamiento que se quiere de un respaldo.
"""

from __future__ import annotations

import io
import random
import time
from datetime import date

import pandas as pd
import requests

from ..core.config import get_settings
from .base import (
    NotSupportedError,
    ProviderError,
    RateLimitError,
    normalize_ohlcv,
)

_BASE_URL = "https://stooq.com/q/d/l/"
_TIMEOUT = 30

# Sufijo de Yahoo -> sufijo de Stooq. Solo mercados con convencion conocida.
_MARKET_SUFFIX = {
    ".MC": ".es",   # Madrid
    ".DE": ".de",   # Xetra
    ".F": ".de",    # Frankfurt
    ".PA": ".fr",   # Paris
    ".AS": ".nl",   # Amsterdam
    ".BR": ".be",   # Bruselas
    ".MI": ".it",   # Milan
    ".LS": ".pt",   # Lisboa
    ".VI": ".at",   # Viena
    ".CO": ".dk",   # Copenhague
    ".ST": ".se",   # Estocolmo
    ".OL": ".no",   # Oslo
    ".HE": ".fi",   # Helsinki
    ".L": ".uk",    # Londres
    ".SW": ".ch",   # Suiza
    ".WA": ".pl",   # Varsovia
}

# Indices, que en Stooq tienen nombre propio y no siguen ninguna regla.
_INDEX_MAP = {
    "^GSPC": "^spx", "^NDX": "^ndx", "^IXIC": "^ndq", "^DJI": "^dji",
    "^RUT": "^rut", "^VIX": "^vix", "^STOXX50E": "^stx50", "^GDAXI": "^dax",
    "^FCHI": "^cac", "^IBEX": "^ibex", "^FTSE": "^ukx", "^N225": "^nkx",
}


def _to_stooq(ticker: str) -> str | None:
    """Traduce un ticker canonico (estilo Yahoo) al simbolo de Stooq.

    Devuelve None cuando no hay equivalencia conocida, que es preferible a
    inventarse un simbolo y gastar una peticion en un 404.
    """
    if not ticker:
        return None

    if ticker in _INDEX_MAP:
        return _INDEX_MAP[ticker]
    if ticker.startswith("^"):
        return None

    # Cripto: Stooq usa el par junto y en minuscula.
    if ticker.endswith("-USD"):
        return f"{ticker[:-4]}usd".lower()

    # Futuros y divisas de Yahoo no tienen equivalencia fiable.
    if ticker.endswith(("=F", "=X")):
        return None

    if "." in ticker:
        base, _, suffix = ticker.rpartition(".")
        stooq_suffix = _MARKET_SUFFIX.get(f".{suffix}")
        if stooq_suffix is None:
            return None
        return f"{base}{stooq_suffix}".lower()

    # Sin sufijo se asume Estados Unidos, que es la convencion del universo.
    return f"{ticker}.us".lower()


class StooqProvider:
    """Implementa PriceProvider. NO implementa FundamentalsProvider."""

    name = "stooq"

    def __init__(self) -> None:
        cfg = get_settings().ingest
        sleep_range = cfg.get("sleep_between_batches", [1.5, 3.5])
        # Stooq no publica limites, asi que se va mas despacio de lo necesario
        # en lugar de arriesgarse a que corte el acceso.
        self.sleep_min = float(sleep_range[0]) / 3
        self.sleep_max = float(sleep_range[1]) / 3
        self.max_requests = int(cfg.get("max_requests_per_run", 400))
        self.requests_used = 0
        self._session = requests.Session()

    def supports(self, ticker: str) -> bool:
        return _to_stooq(ticker) is not None

    def _pause(self) -> None:
        time.sleep(random.uniform(self.sleep_min, self.sleep_max))

    # ------------------------------------------------------------------
    # Precios
    # ------------------------------------------------------------------
    def fetch_ohlcv(
        self, tickers: list[str], start: date, end: date, interval: str = "1d"
    ) -> pd.DataFrame:
        if interval != "1d":
            raise NotSupportedError("Stooq solo sirve datos diarios.")

        frames: list[pd.DataFrame] = []
        failed: list[str] = []

        for i, ticker in enumerate(tickers):
            if self.requests_used >= self.max_requests:
                failed.extend(tickers[i:])
                break

            symbol = _to_stooq(ticker)
            if symbol is None:
                failed.append(ticker)
                continue

            try:
                frame = self._fetch_one(symbol, start, end)
                self.requests_used += 1
            except RateLimitError:
                # Insistir cuando ya estan limitando solo empeora el bloqueo.
                failed.extend(tickers[i:])
                break
            except ProviderError:
                failed.append(ticker)
                continue

            if frame.empty:
                failed.append(ticker)
            else:
                frame["ticker"] = ticker
                frames.append(frame)

            if i + 1 < len(tickers):
                self._pause()

        result = normalize_ohlcv(
            pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(),
            self.name,
        )
        result.attrs["failed_tickers"] = failed
        result.attrs["requests_used"] = self.requests_used
        return result

    def _fetch_one(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        params = {
            "s": symbol,
            "d1": start.strftime("%Y%m%d"),
            "d2": end.strftime("%Y%m%d"),
            "i": "d",
        }
        try:
            response = self._session.get(_BASE_URL, params=params, timeout=_TIMEOUT)
        except requests.RequestException as exc:
            raise ProviderError(f"Stooq no responde para {symbol}: {exc}") from exc

        if response.status_code == 429:
            raise RateLimitError("Stooq esta limitando las peticiones.")
        if response.status_code != 200:
            raise ProviderError(f"Stooq devolvio {response.status_code} para {symbol}")

        return self._parse_csv(response.text)

    @staticmethod
    def _parse_csv(text: str) -> pd.DataFrame:
        """Convierte el CSV de Stooq al esquema canonico.

        Un simbolo desconocido no da error HTTP: devuelve el texto
        'No data', asi que hay que detectarlo por contenido.
        """
        head = text.lstrip()[:64].lower()
        if not head.startswith("date"):
            return pd.DataFrame()

        frame = pd.read_csv(io.StringIO(text))
        frame.columns = [str(c).strip().lower() for c in frame.columns]
        if "date" not in frame.columns or "close" not in frame.columns:
            return pd.DataFrame()

        # Stooq ajusta por splits pero NO por dividendos. Se copia el cierre a
        # `adj_close` porque el esquema lo exige, pero no significa lo mismo que
        # el `adj_close` de Yahoo. Mezclar ambas fuentes en una misma serie
        # introduce un salto artificial el dia del cambio, y ese salto se
        # propaga a los retornos y a todos los indicadores. Por eso la ingesta
        # detecta las series con fuentes mezcladas y las reconstruye enteras
        # desde una sola (ver `run_ingest.repair_mixed_sources`).
        frame["adj_close"] = frame["close"]
        if "volume" not in frame.columns:
            frame["volume"] = 0
        return frame

    # ------------------------------------------------------------------
    # Fundamentales: no los hay
    # ------------------------------------------------------------------
    def fetch_snapshot(self, tickers: list[str]) -> pd.DataFrame:  # noqa: ARG002
        raise NotSupportedError("Stooq no sirve fundamentales.")

    def fetch_metadata(self, tickers: list[str]) -> pd.DataFrame:  # noqa: ARG002
        raise NotSupportedError("Stooq no sirve metadatos.")
