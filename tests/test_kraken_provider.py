"""Tests del historico de cripto de Kraken. Sin red y sin credenciales.

El endpoint OHLC es publico, asi que lo unico que no se ejercita aqui es la
llamada en si. Lo que se comprueba es lo que convierte una respuesta correcta
en un almacen equivocado: guardar el precio de otra moneda bajo este ticker,
perder la columna que usan los indicadores, o dar dos anos de datos por diez.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from stocks_tracker.core.kraken_symbols import canonical_pair, kraken_pair
from stocks_tracker.providers.base import OHLCV_COLUMNS, ProviderError, RateLimitError
from stocks_tracker.providers.kraken_provider import (
    MAX_CANDLES,
    KrakenPriceProvider,
    earliest_available,
)


class FakeSession:
    def __init__(self, payload):
        self.payload = payload
        self.calls: list[dict] = []

    def get(self, url, params=None, timeout=None):
        self.calls.append(params or {})
        carga = self.payload(params) if callable(self.payload) else self.payload
        return _Response(carga)


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


def velas(n: int, inicio: date, precio: float = 100.0) -> list[list]:
    import datetime as dt

    out = []
    for i in range(n):
        ts = int(dt.datetime.combine(inicio + timedelta(days=i), dt.time()).timestamp())
        p = precio + i
        out.append([ts, str(p), str(p + 2), str(p - 2), str(p + 1), str(p), "12.5", 30])
    return out


def proveedor(payload) -> KrakenPriceProvider:
    p = KrakenPriceProvider(session=FakeSession(payload))
    p._last_call = -1e9
    return p


AYER = date.today() - timedelta(days=5)


# ---------------------------------------------------------------------------
# Nombres
# ---------------------------------------------------------------------------
def test_bitcoin_is_asked_for_as_xbt():
    """Kraken devuelve "XXBT" pero no reconoce "BTC" en la peticion: para el,
    bitcoin sigue siendo XBT. Pedirlo mal devuelve un error de par
    desconocido."""
    assert kraken_pair("BTC/EUR") == "XBTEUR"
    assert kraken_pair("SOL/EUR") == "SOLEUR", "no toca lo que no hace falta"


def test_only_crypto_pairs_against_the_euro_are_accepted():
    """Mezclar EUR con USD en la misma cartera mete riesgo de cambio sin
    pedirlo, y el mandato cripto tiene la cuenta en euros."""
    p = proveedor({})
    assert p.supports("BTC/EUR")
    assert not p.supports("BTC/USD")


def test_a_ticker_without_a_slash_is_not_crypto():
    """Sin la barra, "ADAEUR" y un valor que se llamara asi serian
    indistinguibles, y el proveedor iria a Kraken a por una accion."""
    p = proveedor({})
    assert not p.supports("AAPL")
    assert not p.supports("ADAEUR")


# ---------------------------------------------------------------------------
# Las velas
# ---------------------------------------------------------------------------
def test_the_candles_come_back_in_the_canonical_schema():
    """Si el esquema no es el de siempre, `upsert_df` guardaria columnas que no
    existen o dejaria fuera las que si."""
    p = proveedor({"error": [], "result": {"XXBTZEUR": velas(10, AYER - timedelta(days=10))}})
    df = p.fetch_ohlcv(["BTC/EUR"], AYER - timedelta(days=30), date.today())
    assert list(df.columns) == OHLCV_COLUMNS
    assert len(df) == 10


def test_adj_close_is_filled_in():
    """`adj_close` es la columna que usan los indicadores. En cripto no hay
    splits ni dividendos que ajustar, pero dejarla vacia daria retornos nulos
    sin lanzar ningun error: el ranking saldria plano y parecería correcto."""
    p = proveedor({"error": [], "result": {"XXBTZEUR": velas(5, AYER - timedelta(days=5))}})
    df = p.fetch_ohlcv(["BTC/EUR"], AYER - timedelta(days=30), date.today())
    assert df["adj_close"].notna().all()
    assert (df["adj_close"] == df["close"]).all()


def test_a_response_for_another_pair_is_not_stored_under_this_ticker():
    """Quedarse con la primera clave que venga guardaria el precio de ethereum
    bajo BTC/EUR. No lanza nada, no se ve en pantalla, y el bot compra."""
    p = proveedor({"error": [], "result": {"XETHZEUR": velas(5, AYER - timedelta(days=5))}})
    df = p.fetch_ohlcv(["BTC/EUR"], AYER - timedelta(days=30), date.today())
    assert df.empty
    assert "BTC/EUR" in df.attrs["failed_tickers"]


def test_the_requested_window_is_respected():
    p = proveedor({"error": [], "result": {
        "XXBTZEUR": velas(30, date.today() - timedelta(days=40))}})
    desde = date.today() - timedelta(days=20)
    df = p.fetch_ohlcv(["BTC/EUR"], desde, date.today())
    assert df["date"].min() >= desde


# ---------------------------------------------------------------------------
# El limite de 720 velas
# ---------------------------------------------------------------------------
def test_hitting_the_candle_limit_is_reported():
    """Kraken entrega 720 velas como mucho y `since` no permite paginar mas
    atras. Dos anos de bitcoin caben dentro de una sola subida: un backtest ahi
    puede salir estupendo sin que la estrategia valga nada. Callarlo es
    presentar una muestra corta como si fuera larga."""
    p = proveedor({"error": [], "result": {
        "XXBTZEUR": velas(MAX_CANDLES, date.today() - timedelta(days=MAX_CANDLES))}})
    df = p.fetch_ohlcv(["BTC/EUR"], earliest_available(), date.today())
    assert df.attrs["truncated_tickers"] == ["BTC/EUR"]


def test_a_short_history_is_not_reported_as_truncated():
    p = proveedor({"error": [], "result": {"XXBTZEUR": velas(50, AYER - timedelta(days=50))}})
    df = p.fetch_ohlcv(["BTC/EUR"], AYER - timedelta(days=90), date.today())
    assert df.attrs["truncated_tickers"] == []


def test_the_earliest_date_matches_the_api_limit():
    hoy = date(2026, 8, 11)
    assert (hoy - earliest_available(hoy)).days == MAX_CANDLES


# ---------------------------------------------------------------------------
# Errores
# ---------------------------------------------------------------------------
def test_a_rate_limit_stops_the_run_instead_of_dropping_pairs():
    """Seguir pidiendo tras un 'rate limit' alarga el bloqueo y deja la
    descarga a medias con aspecto de completa."""
    p = proveedor({"error": ["EAPI:Rate limit exceeded"], "result": {}})
    with pytest.raises(RateLimitError):
        p.fetch_ohlcv(["BTC/EUR", "ETH/EUR"], AYER, date.today())


def test_a_pair_that_fails_is_listed_not_silently_dropped():
    p = proveedor({"error": ["EQuery:Unknown asset pair"], "result": {}})
    df = p.fetch_ohlcv(["BTC/EUR"], AYER, date.today())
    assert df.empty
    assert df.attrs["failed_tickers"] == ["BTC/EUR"]


def test_intraday_is_refused_rather_than_silently_daily():
    """Pedir velas de una hora y recibir diarias descuadraria los indicadores
    sin que nada lo diga."""
    p = proveedor({})
    with pytest.raises(ProviderError, match="diarias"):
        p.fetch_ohlcv(["BTC/EUR"], AYER, date.today(), interval="1h")


def test_one_bad_pair_does_not_lose_the_good_ones():
    def por_par(params):
        if params.get("pair") == "XBTEUR":
            return {"error": [], "result": {"XXBTZEUR": velas(5, AYER - timedelta(days=5))}}
        return {"error": ["EQuery:Unknown asset pair"], "result": {}}

    p = proveedor(por_par)
    df = p.fetch_ohlcv(["BTC/EUR", "INVENTADA/EUR"], AYER - timedelta(days=30), date.today())
    assert set(df["ticker"]) == {"BTC/EUR"}
    assert df.attrs["failed_tickers"] == ["INVENTADA/EUR"]


def test_the_canonical_pair_matches_both_namings():
    assert canonical_pair("XXBTZEUR") == canonical_pair("BTC/EUR") == "BTC/EUR"
