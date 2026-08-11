"""Historico de cripto desde Kraken, publico y gratuito.

Implementa el mismo `PriceProvider` que Yahoo o Stooq, asi que las velas de
bitcoin entran en `prices_daily` por el mismo camino que las de Apple y el
motor de indicadores las trata igual sin saber que son cripto. Eso es lo que
permite tener RSI, ATR y medias de BTC sin escribir un segundo pipeline.

No necesita credenciales: el endpoint OHLC de Kraken es publico. Se puede
descargar el historico y validar la estrategia antes de que exista la cuenta.

**Limite que hay que saber antes de leer un backtest de cripto.** Kraken
devuelve como mucho 720 velas por peticion, y el parametro `since` no permite
paginar hacia atras: mueve el principio, pero el tope sigue siendo 720. En
velas diarias eso son unos dos anos, y no hay forma de sacar mas de esta API.

Dos anos de bitcoin es poco: cabe un ciclo alcista y poco mas. Un backtest
sobre esa ventana puede salir estupendo porque coincidio con una subida, no
porque la estrategia sirva. El proveedor anota la cobertura real en
`df.attrs` para que la puerta pueda contarlo como lo que es —una muestra
corta— en vez de tratarla como diez anos de datos.
"""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta

import pandas as pd
import requests

from ..core.kraken_symbols import canonical_pair, kraken_pair
from .base import OHLCV_COLUMNS, ProviderError, RateLimitError

_BASE = "https://api.kraken.com/0/public/OHLC"
_TIMEOUT = 30
_MIN_SECONDS_BETWEEN_CALLS = 1.0

# Tope duro de la API. No es una eleccion nuestra.
MAX_CANDLES = 720

# Solo pares contra euro: es la divisa de la cuenta del mandato cripto, y
# mezclar EUR con USD en la misma cartera mete riesgo de cambio sin pedirlo.
_SUPPORTED_QUOTES = ("EUR",)


class KrakenPriceProvider:
    """Velas diarias de cripto. Publico: no usa ni pide credenciales."""

    name = "kraken"

    def __init__(self, session: requests.Session | None = None):
        self.session = session or requests.Session()
        self._last_call = 0.0

    # ------------------------------------------------------------------
    def supports(self, ticker: str) -> bool:
        """Solo pares cripto contra euro, con la barra puesta.

        Se exige la barra a proposito: sin ella, "ADAEUR" y un hipotetico
        valor llamado "ADAEUR" serian indistinguibles, y el proveedor
        intentaria descargar de Kraken algo que no es cripto.
        """
        if "/" not in ticker:
            return False
        canonico = canonical_pair(ticker)
        return bool(canonico) and canonico.split("/")[-1] in _SUPPORTED_QUOTES

    def _throttle(self) -> None:
        espera = _MIN_SECONDS_BETWEEN_CALLS - (time.monotonic() - self._last_call)
        if espera > 0:
            time.sleep(espera)
        self._last_call = time.monotonic()

    def _fetch_one(self, ticker: str, start: date) -> list[dict]:
        self._throttle()
        params = {
            "pair": kraken_pair(ticker),
            "interval": 1440,                       # diario
            "since": int(datetime.combine(start, datetime.min.time()).timestamp()),
        }
        try:
            response = self.session.get(_BASE, params=params, timeout=_TIMEOUT)
        except requests.RequestException as exc:
            raise ProviderError(f"Kraken no responde: {exc}") from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderError("Kraken ha devuelto algo que no es JSON") from exc

        errores = payload.get("error") or []
        if errores:
            primero = str(errores[0])
            if "Rate limit" in primero:
                raise RateLimitError(primero)
            raise ProviderError(primero)

        result = payload.get("result") or {}
        for clave, filas in result.items():
            if clave == "last" or not isinstance(filas, list):
                continue
            # Se comprueba que la respuesta es del par que se pidio. Kraken
            # devuelve su propio nombre ("XXBTZEUR"), y quedarse con la primera
            # clave que aparezca guardaria el precio de otra moneda bajo este
            # ticker: un error que no se ve hasta que el bot compra.
            if canonical_pair(clave) != canonical_pair(ticker):
                continue
            return [_vela(ticker, f) for f in filas]
        return []

    def fetch_ohlcv(
        self, tickers: list[str], start: date, end: date, interval: str = "1d"
    ) -> pd.DataFrame:
        if interval != "1d":
            raise ProviderError(
                f"Kraken solo se usa en velas diarias aqui, no '{interval}'"
            )

        filas: list[dict] = []
        fallidos: list[str] = []
        truncados: list[str] = []

        for ticker in tickers:
            if not self.supports(ticker):
                fallidos.append(ticker)
                continue
            try:
                velas = self._fetch_one(ticker, start)
            except RateLimitError:
                raise
            except ProviderError:
                fallidos.append(ticker)
                continue
            if not velas:
                fallidos.append(ticker)
                continue
            if len(velas) >= MAX_CANDLES:
                # Se ha topado con el limite: hay historico anterior que esta
                # API no entrega. Quien lea el backtest tiene que saberlo.
                truncados.append(ticker)
            filas.extend(velas)

        df = pd.DataFrame(filas, columns=OHLCV_COLUMNS)
        if not df.empty:
            df["date"] = pd.to_datetime(df["date"]).dt.date
            df = df[(df["date"] >= start) & (df["date"] <= end)]

        df.attrs["failed_tickers"] = fallidos
        df.attrs["truncated_tickers"] = truncados
        return df


def _vela(ticker: str, fila: list) -> dict:
    """Una vela de Kraken: [hora, apertura, max, min, cierre, vwap, volumen, n].

    `adj_close` es el cierre sin mas: en cripto no hay dividendos ni splits que
    ajustar. Se rellena igualmente porque es la columna que usan los
    indicadores, y dejarla vacia daria retornos nulos sin ningun error.
    """
    cierre = float(fila[4])
    return {
        "ticker": ticker,
        "date": datetime.fromtimestamp(int(fila[0])).date(),
        "open": float(fila[1]),
        "high": float(fila[2]),
        "low": float(fila[3]),
        "close": cierre,
        "adj_close": cierre,
        "volume": int(float(fila[6])),
    }


def earliest_available(today: date | None = None) -> date:
    """La fecha mas antigua que esta API puede dar en velas diarias."""
    return (today or date.today()) - timedelta(days=MAX_CANDLES)
