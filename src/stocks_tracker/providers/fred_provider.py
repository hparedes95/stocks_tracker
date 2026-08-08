"""Series macroeconomicas de FRED (Reserva Federal de St. Louis).

Requiere una clave gratuita en `FRED_API_KEY`. Sin ella, la pagina de macro se
degrada: muestra lo que se puede sacar de los precios (VIX, oro, cobre, dolar) y
avisa de que faltan las series de tipos y actividad. Ningun elemento del nucleo
depende de esta clave.

Se usa la API HTTP directamente en lugar de `fredapi`: son treinta lineas, y
evita una dependencia mas en la cadena de datos.
"""

from __future__ import annotations

import os
from datetime import date

import pandas as pd
import requests

from .base import ProviderError, RateLimitError

_BASE_URL = "https://api.stlouisfed.org/fred/series/observations"
_TIMEOUT = 30


class FredProvider:
    name = "fred"

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or os.environ.get("FRED_API_KEY", "").strip()

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def fetch_series(
        self, series_ids: list[str], start: date | None = None
    ) -> pd.DataFrame:
        """Descarga varias series. Devuelve series_id, date, value, source.

        Si una serie falla, se registra y se continua con las demas: perder una
        serie macro no debe dejar la pagina entera sin datos.
        """
        if not self.available:
            raise ProviderError(
                "Falta FRED_API_KEY. Consigue una clave gratuita en "
                "https://fred.stlouisfed.org/docs/api/api_key.html"
            )

        frames: list[pd.DataFrame] = []
        failed: list[str] = []

        for series_id in series_ids:
            try:
                frames.append(self._one(series_id, start))
            except RateLimitError:
                failed.extend(series_ids[series_ids.index(series_id):])
                break
            except Exception:  # noqa: BLE001
                failed.append(series_id)

        result = (
            pd.concat(frames, ignore_index=True)
            if frames
            else pd.DataFrame(columns=["series_id", "date", "value", "source"])
        )
        result.attrs["failed_series"] = failed
        return result

    def _one(self, series_id: str, start: date | None) -> pd.DataFrame:
        params = {
            "series_id": series_id,
            "api_key": self.api_key,
            "file_type": "json",
        }
        if start:
            params["observation_start"] = start.isoformat()

        response = requests.get(_BASE_URL, params=params, timeout=_TIMEOUT)
        if response.status_code == 429:
            raise RateLimitError(f"FRED limita las peticiones ({series_id})")
        response.raise_for_status()

        observations = response.json().get("observations", [])
        if not observations:
            return pd.DataFrame(columns=["series_id", "date", "value", "source"])

        df = pd.DataFrame(observations)[["date", "value"]]
        # FRED marca los huecos con un punto, no con vacio.
        df["value"] = pd.to_numeric(df["value"].replace(".", None), errors="coerce")
        df = df.dropna(subset=["value"])
        df["date"] = pd.to_datetime(df["date"]).dt.date
        df.insert(0, "series_id", series_id)
        df["source"] = self.name
        return df
