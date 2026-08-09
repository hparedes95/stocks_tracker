"""Tests del respaldo de proveedores.

El fallo que estos tests vigilan no es que Yahoo devuelva un error, sino que
devuelva la mitad de lo pedido sin quejarse. Es la forma real en que una API no
oficial se rompe, y la que dejaba el almacen congelado sin que nadie se
enterase.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from stocks_tracker.providers.base import (
    NotSupportedError,
    PriceProvider,
    ProviderError,
    normalize_ohlcv,
)
from stocks_tracker.providers.chain import ChainPriceProvider
from stocks_tracker.providers.stooq_provider import StooqProvider, _to_stooq

START, END = date(2024, 1, 2), date(2024, 1, 10)


class FakeProvider:
    """Proveedor de mentira que sirve solo los tickers que se le indiquen."""

    def __init__(self, name: str, serves: set[str], raises: bool = False):
        self.name = name
        self.serves = serves
        self.raises = raises
        self.asked: list[list[str]] = []

    def supports(self, ticker: str) -> bool:
        return True

    def fetch_ohlcv(self, tickers, start, end, interval="1d"):
        self.asked.append(list(tickers))
        if self.raises:
            raise ProviderError(f"{self.name} caido")
        served = [t for t in tickers if t in self.serves]
        if not served:
            return normalize_ohlcv(pd.DataFrame(), self.name)
        rows = [
            {
                "ticker": t, "date": start, "open": 10.0, "high": 11.0,
                "low": 9.0, "close": 10.5, "adj_close": 10.5, "volume": 100,
            }
            for t in served
        ]
        df = normalize_ohlcv(pd.DataFrame(rows), self.name)
        df.attrs["requests_used"] = 1
        return df


# ---------------------------------------------------------------------------
# Cadena
# ---------------------------------------------------------------------------
def test_chain_relays_only_what_the_first_could_not_bring():
    primary = FakeProvider("principal", {"AAA"})
    backup = FakeProvider("respaldo", {"BBB", "CCC"})
    chain = ChainPriceProvider([primary, backup])

    df = chain.fetch_ohlcv(["AAA", "BBB", "CCC"], START, END)

    assert set(df["ticker"]) == {"AAA", "BBB", "CCC"}
    # Al respaldo solo se le piden los que faltaban, no los tres.
    assert backup.asked == [["BBB", "CCC"]]


def test_chain_keeps_the_real_source_of_each_row():
    """Sin esto no se pueden detectar despues las series mezcladas."""
    chain = ChainPriceProvider(
        [FakeProvider("principal", {"AAA"}), FakeProvider("respaldo", {"BBB"})]
    )
    df = chain.fetch_ohlcv(["AAA", "BBB"], START, END)

    sources = dict(zip(df["ticker"], df["source"], strict=True))
    assert sources == {"AAA": "principal", "BBB": "respaldo"}


def test_chain_reports_which_tickers_were_relayed():
    chain = ChainPriceProvider(
        [FakeProvider("principal", {"AAA"}), FakeProvider("respaldo", {"BBB"})]
    )
    df = chain.fetch_ohlcv(["AAA", "BBB"], START, END)

    # Lo que sirvio el primero no es un relevo; lo del segundo, si.
    assert df.attrs["relayed_tickers"] == {"BBB": "respaldo"}


def test_chain_survives_a_provider_that_raises():
    chain = ChainPriceProvider(
        [FakeProvider("roto", set(), raises=True), FakeProvider("respaldo", {"AAA"})]
    )
    df = chain.fetch_ohlcv(["AAA"], START, END)
    assert set(df["ticker"]) == {"AAA"}


def test_chain_reports_what_nobody_could_serve():
    chain = ChainPriceProvider(
        [FakeProvider("principal", {"AAA"}), FakeProvider("respaldo", {"BBB"})]
    )
    df = chain.fetch_ohlcv(["AAA", "BBB", "ZZZ"], START, END)
    assert df.attrs["failed_tickers"] == ["ZZZ"]


def test_chain_does_not_ask_for_tickers_a_provider_declares_unsupported():
    class Picky(FakeProvider):
        def supports(self, ticker):
            return ticker.startswith("A")

    picky = Picky("exigente", {"AAA"})
    chain = ChainPriceProvider([picky, FakeProvider("respaldo", {"BBB"})])
    chain.fetch_ohlcv(["AAA", "BBB"], START, END)

    assert picky.asked == [["AAA"]]


def test_chain_stops_early_when_the_first_serves_everything():
    primary = FakeProvider("principal", {"AAA", "BBB"})
    backup = FakeProvider("respaldo", {"AAA", "BBB"})
    ChainPriceProvider([primary, backup]).fetch_ohlcv(["AAA", "BBB"], START, END)

    assert backup.asked == [], "No hay que molestar al respaldo si no hace falta"


def test_empty_chain_is_rejected():
    with pytest.raises(ProviderError):
        ChainPriceProvider([])


# ---------------------------------------------------------------------------
# Stooq
# ---------------------------------------------------------------------------
def test_stooq_satisfies_the_price_protocol():
    assert isinstance(StooqProvider(), PriceProvider)


@pytest.mark.parametrize(
    ("ticker", "expected"),
    [
        ("AAPL", "aapl.us"),
        ("BRK-B", "brk-b.us"),
        ("SAN.MC", "san.es"),
        ("BMW.DE", "bmw.de"),
        ("ASML.AS", "asml.nl"),
        ("NOVO-B.CO", "novo-b.dk"),
        ("^GSPC", "^spx"),
        ("^VIX", "^vix"),
        ("BTC-USD", "btcusd"),
    ],
)
def test_stooq_symbol_mapping(ticker, expected):
    assert _to_stooq(ticker) == expected


@pytest.mark.parametrize("ticker", ["GC=F", "EURUSD=X", "^UNKNOWNINDEX", "FOO.XX", ""])
def test_stooq_declines_what_it_cannot_map(ticker):
    """Devolver None es mejor que inventarse un simbolo y gastar la peticion."""
    assert _to_stooq(ticker) is None
    assert StooqProvider().supports(ticker) is False


def test_stooq_parses_a_normal_csv():
    csv = (
        "Date,Open,High,Low,Close,Volume\n"
        "2024-01-02,10.0,11.0,9.5,10.5,1000\n"
        "2024-01-03,10.5,11.5,10.0,11.0,1200\n"
    )
    frame = StooqProvider._parse_csv(csv)
    assert len(frame) == 2
    # Stooq no ajusta por dividendos: adj_close es una copia del cierre.
    assert frame["adj_close"].tolist() == [10.5, 11.0]


def test_stooq_treats_no_data_as_empty_not_as_a_crash():
    """Un simbolo desconocido devuelve 200 con el texto 'No data'."""
    assert StooqProvider._parse_csv("No data").empty
    assert StooqProvider._parse_csv("").empty


def test_stooq_has_no_fundamentals_and_says_so():
    """Debe lanzar NotSupportedError para que la cadena pase al siguiente."""
    provider = StooqProvider()
    with pytest.raises(NotSupportedError):
        provider.fetch_snapshot(["AAPL"])
    with pytest.raises(NotSupportedError):
        provider.fetch_metadata(["AAPL"])


def test_stooq_rejects_intraday():
    with pytest.raises(NotSupportedError):
        StooqProvider().fetch_ohlcv(["AAPL"], START, END, interval="1h")


def test_stooq_never_calls_the_network_in_these_tests(monkeypatch):
    """Guardarrail: si alguien anade una llamada real, este test lo caza."""
    import requests

    def explode(*args, **kwargs):
        raise AssertionError("Un test ha intentado salir a la red")

    monkeypatch.setattr(requests.Session, "get", explode)
    provider = StooqProvider()
    # Todos son intraducibles, asi que no debe intentar ninguna peticion.
    df = provider.fetch_ohlcv(["GC=F", "EURUSD=X"], START, END)
    assert df.empty
    assert df.attrs["failed_tickers"] == ["GC=F", "EURUSD=X"]
