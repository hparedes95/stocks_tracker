"""Tests de la ingesta de cripto: que las velas acaben donde el resto las lee.

Un proveedor correcto no basta. Si las velas entran en `prices_daily` pero el
par no queda dado de alta en `instruments`, el ranking las ignora y el bot no
ve nada: es exactamente el fallo que dejo el ranking de acciones vacio, con
todos los precios descargados y ni un solo candidato en pantalla.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from stocks_tracker.core import db
from stocks_tracker.ingest import ingest_crypto


@pytest.fixture
def warehouse(tmp_path, monkeypatch):
    class Stub:
        warehouse_path = tmp_path / "test.duckdb"

    monkeypatch.setattr(db, "get_settings", lambda: Stub())
    db.migrate()
    return Stub.warehouse_path


@pytest.fixture
def universo(monkeypatch):
    monkeypatch.setattr(ingest_crypto, "whitelist",
                        lambda: ["BTC/EUR", "ETH/EUR", "SOL/EUR"])


class FakeProvider:
    """Devuelve velas para todos los pares menos los que se le digan."""

    def __init__(self, dias: int = 30, fallan: tuple[str, ...] = (), truncados=()):
        self.dias = dias
        self.fallan = fallan
        self.truncados = list(truncados)
        self.pedidos: list = []

    def fetch_ohlcv(self, tickers, start, end, interval="1d"):
        import pandas as pd

        from stocks_tracker.providers.base import OHLCV_COLUMNS

        self.pedidos.append((list(tickers), start, end))
        filas = []
        fallidos = []
        for t in tickers:
            if t in self.fallan:
                fallidos.append(t)
                continue
            for i in range(self.dias):
                d = end - timedelta(days=self.dias - i)
                filas.append({
                    "ticker": t, "date": d, "open": 100.0 + i, "high": 102.0 + i,
                    "low": 98.0 + i, "close": 101.0 + i, "adj_close": 101.0 + i,
                    "volume": 1000,
                })
        df = pd.DataFrame(filas, columns=OHLCV_COLUMNS)
        df.attrs["failed_tickers"] = fallidos
        df.attrs["truncated_tickers"] = self.truncados
        return df


@pytest.fixture
def proveedor(monkeypatch):
    fake = FakeProvider()
    monkeypatch.setattr(ingest_crypto, "KrakenPriceProvider", lambda: fake)
    return fake


# ---------------------------------------------------------------------------
def test_the_pairs_are_registered_as_instruments(warehouse, universo):
    """Sin ficha en `instruments`, el ranking ignora las velas: el scoring
    cruza por tipo de activo. Quedarian los precios descargados y ni un solo
    candidato en pantalla, sin ningun error."""
    ingest_crypto.register_instruments(["BTC/EUR", "ETH/EUR"])
    with db.connect(read_only=True) as conn:
        filas = conn.execute(
            "SELECT ticker, asset_class, currency FROM instruments ORDER BY ticker"
        ).fetchall()
    assert filas == [("BTC/EUR", "crypto", "EUR"), ("ETH/EUR", "crypto", "EUR")]


def test_the_pairs_join_a_universe(warehouse, universo):
    """Para poder pedir "los de cripto" sin conocer la lista de memoria."""
    ingest_crypto.register_instruments(["BTC/EUR"])
    with db.connect(read_only=True) as conn:
        filas = conn.execute(
            "SELECT universe, ticker FROM universe_membership"
        ).fetchall()
    assert filas == [(ingest_crypto.UNIVERSE, "BTC/EUR")]


def test_registering_twice_does_not_duplicate(warehouse, universo):
    ingest_crypto.register_instruments(["BTC/EUR"])
    ingest_crypto.register_instruments(["BTC/EUR"])
    with db.connect(read_only=True) as conn:
        n = conn.execute("SELECT COUNT(*) FROM instruments").fetchone()[0]
        m = conn.execute("SELECT COUNT(*) FROM universe_membership").fetchone()[0]
    assert (n, m) == (1, 1)


def test_the_candles_are_stored_with_their_source(warehouse, universo, proveedor):
    """`source` es lo que permite distinguir un precio real de uno inventado.
    Sin el, la puerta no puede bloquear un backtest sobre datos sinteticos."""
    ingest_crypto.ingest_crypto_prices()
    with db.connect(read_only=True) as conn:
        fuentes = conn.execute(
            "SELECT DISTINCT source FROM prices_daily"
        ).fetchall()
    assert fuentes == [("kraken",)]


def test_the_ingest_registers_the_instruments_itself(warehouse, universo, proveedor):
    """No basta con que `register_instruments` funcione: la ingesta tiene que
    llamarlo. Si se cae esa llamada, las velas entran igual y no falla nada —
    el ranking simplemente sale vacio, con todo descargado y ni un candidato
    en pantalla."""
    ingest_crypto.ingest_crypto_prices()
    with db.connect(read_only=True) as conn:
        fichas = conn.execute(
            "SELECT COUNT(*) FROM instruments WHERE asset_class = 'crypto'"
        ).fetchone()[0]
        precios = conn.execute(
            "SELECT COUNT(DISTINCT ticker) FROM prices_daily"
        ).fetchone()[0]
    assert fichas == precios, "hay velas de pares que no tienen ficha"


def test_the_whole_run_is_logged(warehouse, universo, proveedor):
    ingest_crypto.ingest_crypto_prices()
    with db.connect(read_only=True) as conn:
        fila = conn.execute(
            "SELECT task, status, rows_written FROM ingest_log"
        ).fetchone()
    assert fila[0] == "crypto_prices"
    assert fila[1] == "OK"
    assert fila[2] > 0


def test_a_failed_pair_marks_the_run_as_partial(warehouse, universo, monkeypatch):
    """"OK" con un par sin datos diria que la descarga fue completa cuando el
    bot va a operar con un universo mas pequeno del que cree."""
    fake = FakeProvider(fallan=("SOL/EUR",))
    monkeypatch.setattr(ingest_crypto, "KrakenPriceProvider", lambda: fake)
    ingest_crypto.ingest_crypto_prices()
    with db.connect(read_only=True) as conn:
        estado, error = conn.execute(
            "SELECT status, error FROM ingest_log"
        ).fetchone()
    assert estado == "PARTIAL"
    assert "1 pares fallidos" in error


def test_an_incremental_run_does_not_start_from_scratch(warehouse, universo, proveedor):
    """Rehacer los dos anos enteros en cada arranque es media hora de espera y
    cientos de peticiones para traer lo mismo."""
    ingest_crypto.ingest_crypto_prices()
    primera = proveedor.pedidos[0][1]
    ingest_crypto.ingest_crypto_prices()
    segunda = proveedor.pedidos[1][1]
    assert segunda > primera, "vuelve a descargar todo el historico"


def test_a_full_run_goes_back_to_the_api_limit(warehouse, universo, proveedor):
    from stocks_tracker.providers.kraken_provider import earliest_available

    ingest_crypto.ingest_crypto_prices()
    ingest_crypto.ingest_crypto_prices(full=True)
    assert proveedor.pedidos[-1][1] == earliest_available()


def test_reingesting_does_not_duplicate_candles(warehouse, universo, proveedor):
    ingest_crypto.ingest_crypto_prices()
    with db.connect(read_only=True) as conn:
        antes = conn.execute("SELECT COUNT(*) FROM prices_daily").fetchone()[0]
    ingest_crypto.ingest_crypto_prices(full=True)
    with db.connect(read_only=True) as conn:
        despues = conn.execute("SELECT COUNT(*) FROM prices_daily").fetchone()[0]
    assert antes == despues


def test_coverage_reports_what_is_actually_stored(warehouse, universo, proveedor):
    """La puerta lo mira para saber si la muestra da para concluir algo."""
    ingest_crypto.ingest_crypto_prices()
    cobertura = ingest_crypto.coverage()
    assert set(cobertura) == {"BTC/EUR", "ETH/EUR", "SOL/EUR"}
    assert all(c["velas"] == 30 for c in cobertura.values())
    assert all(c["hasta"] <= date.today() for c in cobertura.values())


def test_without_a_configured_universe_nothing_happens(warehouse, monkeypatch):
    """Y no revienta: el dashboard funciona sin bot."""
    monkeypatch.setattr(ingest_crypto, "whitelist", list)
    assert ingest_crypto.ingest_crypto_prices() == 0


def test_the_whitelist_comes_from_the_mandate():
    """No se descubre el universo: con 25 EUR y un minimo de orden de 5 EUR no
    caben mas de cuatro posiciones, y anadir monedas pequenas mete riesgo de
    liquidez sin diversificar nada."""
    pares = ingest_crypto.whitelist()
    assert "BTC/EUR" in pares
    assert all("/" in p for p in pares)
