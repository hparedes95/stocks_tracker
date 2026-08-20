"""Twelve Data: tercera fuente de precios, para contrastar.

NO ESTA VERIFICADO CONTRA LA API REAL

Se escribio sin poder salir a internet, asi que lo unico comprobado es que
interpreta bien las respuestas que Twelve Data documenta. Que la API se comporte
como su documentacion es una suposicion, y no es una suposicion pequena: los
proveedores gratuitos cambian formatos sin avisar y ese es literalmente el
motivo por el que existe este modulo.

Por eso el proveedor se declara NO COMPROBADO hasta que sirva datos de verdad
una vez (`ha_respondido`), y el panel de integridad lo pinta en gris y no en
verde mientras tanto. Un proveedor de contraste en el que no se puede confiar
todavia es peor que no tenerlo, porque su voto cuenta en el consenso.

PARA QUE SIRVE, Y PARA QUE NO

Sirve para ser el TERCERO. Con dos fuentes que discrepan no hay forma de saber
cual falla y el veredicto es INVALIDO; con tres, dos que concuerden hacen
mayoria y la discrepante queda nombrada. Ese salto es lo que convierte el
consenso de un detector de problemas en algo que ademas los resuelve.

No sirve para descargar el universo. El plan gratuito da 800 peticiones al dia y
8 por minuto: alcanza para la cartera, las senales y la muestra de la auditoria
cruzada, que es exactamente para lo que se pide. Pedirle 600 valores lo agota en
la primera pasada.

LA CLAVE

Solo por variable de entorno (`TWELVE_DATA_API_KEY`) o `.env`, nunca en el YAML:
`config/` esta en el repositorio y el repositorio es publico. Sin clave, el
proveedor no falla: dice que no esta configurado y la cadena sigue sin el.
"""

from __future__ import annotations

import os
import time
from datetime import date

import pandas as pd
import requests

from ..core.timeutils import utcnow
from .base import (
    NotSupportedError,
    ProviderError,
    RateLimitError,
    normalize_ohlcv,
)

_BASE_URL = "https://api.twelvedata.com/time_series"
_TIMEOUT = 20

# Plan gratuito: 8 peticiones por minuto. Se va por debajo a proposito —un
# proveedor que te bloquea no sirve de contraste— y con pausa fija, no
# aleatoria: aqui el limite es por minuto y lo que importa es no pasarse.
PAUSA_SEGUNDOS = 8.0

# Y 800 al dia. La auditoria cruzada pide cartera + senales + 50 de muestra, que
# cabe de sobra; este tope existe para que un bucle accidental no se coma la
# cuota del dia en un minuto.
MAX_PETICIONES = 120

VARIABLE_DE_ENTORNO = "TWELVE_DATA_API_KEY"


def api_key() -> str | None:
    clave = os.environ.get(VARIABLE_DE_ENTORNO, "").strip()
    return clave or None


class TwelveDataProvider:
    """Implementa PriceProvider. NO implementa FundamentalsProvider."""

    name = "twelve_data"

    def __init__(self, clave: str | None = None) -> None:
        self._clave = clave or api_key()
        self.requests_used = 0
        # Se pone a True la primera vez que la API sirve una fila util. Es lo
        # que distingue "configurado" de "comprobado", y son cosas distintas:
        # una clave escrita en el .env no demuestra que la API responda ni que
        # su formato siga siendo el que este codigo entiende.
        self.ha_respondido = False
        self._session = requests.Session()

    @property
    def configurado(self) -> bool:
        return self._clave is not None

    def supports(self, ticker: str) -> bool:
        # Cubre acciones y ETF de mercados grandes. Los sufijos raros y las
        # divisas se dejan fuera: pedirlos gasta cuota para recibir un error.
        if not self.configurado or not ticker:
            return False
        return not ticker.startswith("^") and "=" not in ticker

    def fetch_ohlcv(self, tickers: list[str], start: date, end: date,
                    interval: str = "1d") -> pd.DataFrame:
        if interval != "1d":
            raise NotSupportedError("Solo se pide el diario a Twelve Data.")
        if not self.configurado:
            raise ProviderError(
                f"Twelve Data no esta configurado: falta {VARIABLE_DE_ENTORNO} "
                "en el entorno o en el .env."
            )

        frames: list[pd.DataFrame] = []
        failed: list[str] = []

        for i, ticker in enumerate(tickers):
            if self.requests_used >= MAX_PETICIONES:
                failed.extend(tickers[i:])
                break
            if not self.supports(ticker):
                failed.append(ticker)
                continue

            try:
                frame = self._una(ticker, start, end)
                self.requests_used += 1
            except RateLimitError:
                # Insistir cuando ya estan limitando solo empeora el bloqueo, y
                # aqui ademas se gasta la cuota del dia.
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
                time.sleep(PAUSA_SEGUNDOS)

        salida = normalize_ohlcv(
            pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(),
            self.name,
        )
        if not salida.empty:
            self.ha_respondido = True
        salida.attrs["failed_tickers"] = failed
        salida.attrs["requests_used"] = self.requests_used
        return salida

    def _una(self, ticker: str, start: date, end: date) -> pd.DataFrame:
        try:
            respuesta = self._session.get(
                _BASE_URL,
                params={
                    "symbol": ticker,
                    "interval": "1day",
                    "start_date": start.strftime("%Y-%m-%d"),
                    "end_date": end.strftime("%Y-%m-%d"),
                    "format": "JSON",
                    "apikey": self._clave,
                },
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise ProviderError(f"Twelve Data no responde para {ticker}: {exc}") from exc

        if respuesta.status_code == 429:
            raise RateLimitError("Twelve Data esta limitando las peticiones.")
        if respuesta.status_code != 200:
            raise ProviderError(
                f"Twelve Data devolvio {respuesta.status_code} para {ticker}")

        try:
            datos = respuesta.json()
        except ValueError as exc:
            raise ProviderError(f"Twelve Data no devolvio JSON para {ticker}") from exc

        return interpretar(datos, ticker)


def interpretar(datos: dict, ticker: str) -> pd.DataFrame:
    """Convierte la respuesta al esquema canonico.

    Se separa de la clase para poder probarla sin red, que es lo unico que se
    puede probar de este modulo desde aqui.

    OJO CON EL CODIGO 200. Twelve Data devuelve los errores DENTRO del JSON con
    HTTP 200: `{"code": 429, "status": "error", "message": "..."}`. Fiarse del
    codigo HTTP hace que un "has agotado la cuota" se lea como una respuesta
    vacia, y una cuota agotada tratada como "no hay datos" es un DEGRADADO
    silencioso justo cuando hace falta el contraste.
    """
    if not isinstance(datos, dict):
        raise ProviderError(f"Twelve Data devolvio algo que no es un objeto: {ticker}")

    if str(datos.get("status", "")).lower() == "error":
        codigo = datos.get("code")
        mensaje = datos.get("message", "sin detalle")
        if codigo in (429, "429"):
            raise RateLimitError(f"Twelve Data: {mensaje}")
        raise ProviderError(f"Twelve Data ({codigo}) para {ticker}: {mensaje}")

    valores = datos.get("values")
    if not valores:
        return pd.DataFrame()

    frame = pd.DataFrame(valores)
    if "datetime" not in frame.columns:
        raise ProviderError(f"Twelve Data no manda 'datetime' para {ticker}")

    frame = frame.rename(columns={"datetime": "date"})
    for columna in ("open", "high", "low", "close", "volume"):
        if columna in frame.columns:
            frame[columna] = pd.to_numeric(frame[columna], errors="coerce")

    # Twelve Data NO da precio ajustado en el plan gratuito. Se copia el cierre
    # para cumplir el esquema, igual que hace Stooq, y por el mismo motivo hay
    # que tener cuidado: este `adj_close` no es comparable con el de Yahoo. El
    # consenso compara `close` justamente por esto.
    frame["adj_close"] = frame.get("close")
    if "volume" not in frame.columns:
        frame["volume"] = 0
    frame["fetched_at"] = utcnow()
    return frame
