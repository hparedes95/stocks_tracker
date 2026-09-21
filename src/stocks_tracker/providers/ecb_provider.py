"""Tipos de cambio oficiales del Banco Central Europeo.

El BCE publica cuantos USD, GBP, CHF, etc. vale un euro. Esa es exactamente
la convencion de los simbolos ``EURUSD=X`` que usa el resto del programa, por
lo que no hay que invertir ni aproximar el tipo.

No necesita cuenta ni clave. Solo cubre divisas: acciones, indices y materias
primas pasan al siguiente proveedor de la cadena.
"""

from __future__ import annotations

from datetime import date
from io import StringIO

import pandas as pd
import requests

from .base import NotSupportedError, ProviderError, RateLimitError, normalize_ohlcv

_BASE_URL = "https://data-api.ecb.europa.eu/service/data/EXR"
_TIMEOUT = 30

# Simbolos que tambien entiende core.fx. Mantener el mapa aqui hace que el
# proveedor pueda validarse por separado y evita aceptar cualquier ``=X`` que
# el BCE no publique contra el euro.
TICKER_TO_CURRENCY = {
    "EURUSD=X": "USD",
    "EURGBP=X": "GBP",
    "EURCHF=X": "CHF",
    "EURJPY=X": "JPY",
    "EURSEK=X": "SEK",
    "EURNOK=X": "NOK",
    "EURDKK=X": "DKK",
    "EURCAD=X": "CAD",
    "EURAUD=X": "AUD",
}
CURRENCY_TO_TICKER = {v: k for k, v in TICKER_TO_CURRENCY.items()}


class EcbFxProvider:
    """Proveedor diario oficial para los pares con base EUR."""

    name = "ecb"

    def __init__(self, session: requests.Session | None = None) -> None:
        self._session = session or requests.Session()

    def supports(self, ticker: str) -> bool:
        return ticker in TICKER_TO_CURRENCY

    def fetch_ohlcv(
        self, tickers: list[str], start: date, end: date, interval: str = "1d"
    ) -> pd.DataFrame:
        if interval != "1d":
            raise NotSupportedError("El BCE solo se usa para tipos diarios.")

        requested = list(dict.fromkeys(t for t in tickers if self.supports(t)))
        if not requested:
            return normalize_ohlcv(pd.DataFrame(), self.name)

        currencies = [TICKER_TO_CURRENCY[t] for t in requested]
        key = f"D.{'+'.join(currencies)}.EUR.SP00.A"
        try:
            response = self._session.get(
                f"{_BASE_URL}/{key}",
                params={
                    "startPeriod": start.isoformat(),
                    "endPeriod": end.isoformat(),
                    "format": "csvdata",
                    "detail": "dataonly",
                },
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise ProviderError(f"El BCE no responde: {exc}") from exc

        if response.status_code == 429:
            raise RateLimitError("El BCE esta limitando temporalmente las peticiones.")
        if response.status_code != 200:
            raise ProviderError(f"El BCE devolvio HTTP {response.status_code}.")

        frame = interpretar_csv(response.text, requested)
        out = normalize_ohlcv(frame, self.name)
        served = set(out["ticker"]) if not out.empty else set()
        out.attrs["failed_tickers"] = [t for t in requested if t not in served]
        out.attrs["requests_used"] = 1
        return out


def interpretar_csv(text: str, requested: list[str]) -> pd.DataFrame:
    """Convierte el CSV SDMX del BCE al contrato OHLCV del proyecto."""
    if not text or not text.strip():
        return pd.DataFrame()
    try:
        raw = pd.read_csv(StringIO(text))
    except Exception as exc:  # noqa: BLE001
        raise ProviderError("El BCE no devolvio un CSV legible.") from exc

    columns = {str(c).upper(): c for c in raw.columns}
    required = {"TIME_PERIOD", "OBS_VALUE", "CURRENCY"}
    missing = required - set(columns)
    if missing:
        raise ProviderError(
            f"El CSV del BCE ha cambiado: faltan {', '.join(sorted(missing))}."
        )

    data = pd.DataFrame({
        "date": raw[columns["TIME_PERIOD"]],
        "close": pd.to_numeric(raw[columns["OBS_VALUE"]], errors="coerce"),
        "currency": raw[columns["CURRENCY"]].astype("string").str.upper(),
    })
    data["ticker"] = data["currency"].map(CURRENCY_TO_TICKER)
    data = data[data["ticker"].isin(set(requested)) & data["close"].notna()].copy()
    if data.empty:
        return pd.DataFrame()

    # Un tipo de referencia diario no tiene OHLC ni volumen. Repetir el valor
    # es explicito y permite usar el contrato comun sin inventar volatilidad.
    for column in ("open", "high", "low", "adj_close"):
        data[column] = data["close"]
    data["volume"] = 0
    return data[[
        "ticker", "date", "open", "high", "low", "close", "adj_close", "volume"
    ]]
