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
    """Sustituye la fuente por defecto (Yahoo).

    Se parchea `_yahoo_history` y no el proveedor de dentro: si el parcheo
    fallara, la prueba saldria a internet de verdad —ya paso— y el conftest
    promete que ningun test toca la red.
    """
    fake = FakeProvider()
    monkeypatch.setattr(
        ingest_crypto, "_yahoo_history",
        lambda pairs, inicio, hoy: fake.fetch_ohlcv(pairs, inicio, hoy),
    )
    return fake


@pytest.fixture
def sin_red(monkeypatch):
    """Cualquier intento de salir a la red en estos tests es un fallo."""
    def prohibido(*a, **k):
        raise AssertionError("un test ha intentado salir a internet")

    monkeypatch.setattr(ingest_crypto, "KrakenPriceProvider", prohibido)
    monkeypatch.setattr(ingest_crypto, "_yahoo_history", prohibido)


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
    assert fuentes == [("yfinance",)]


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
    monkeypatch.setattr(
        ingest_crypto, "_yahoo_history",
        lambda pairs, inicio, hoy: fake.fetch_ohlcv(pairs, inicio, hoy),
    )
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


def test_a_full_run_goes_back_as_far_as_the_source_allows(warehouse, universo, proveedor):
    """Yahoo da anos; Kraken solo dos. `--full` tiene que llegar al tope de la
    fuente que se este usando, no a uno fijo."""
    from datetime import date

    ingest_crypto.ingest_crypto_prices()
    ingest_crypto.ingest_crypto_prices(full=True, years=8)
    inicio = proveedor.pedidos[-1][1]
    assert (date.today() - inicio).days >= 365 * 7, (
        "no retrocede lo que Yahoo puede dar"
    )


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


# ---------------------------------------------------------------------------
# Una sola fuente por serie
# ---------------------------------------------------------------------------
def test_mixing_two_sources_in_one_series_is_refused(warehouse, universo, proveedor):
    """Yahoo y Kraken no coinciden al centimo. Empalmarlas deja un salto
    artificial en la fecha de union, y un salto es exactamente lo que una
    estrategia de momentum lee como senal: saldria una operacion que nunca
    existio, siempre en la misma fecha, contada como ganancia real.

    Falla en vez de avisar porque un backtest empalmado tiene el mismo aspecto
    que uno bueno.
    """
    ingest_crypto.ingest_crypto_prices()  # Yahoo
    with pytest.raises(ingest_crypto.MixedSourceError, match="salto"):
        ingest_crypto.ingest_crypto_prices(source="kraken")


def test_reingesting_from_the_same_source_is_fine(warehouse, universo, proveedor):
    """El rechazo es a mezclar, no a repetir."""
    ingest_crypto.ingest_crypto_prices()
    ingest_crypto.ingest_crypto_prices(full=True)


def test_the_error_says_how_to_get_out_of_it(warehouse, universo, proveedor):
    """Un error que dice que algo esta mal y no que hacer deja al usuario
    igual de atascado que sin el mensaje."""
    ingest_crypto.ingest_crypto_prices()
    with pytest.raises(ingest_crypto.MixedSourceError, match="DELETE FROM prices_daily"):
        ingest_crypto.ingest_crypto_prices(source="kraken")


def test_the_yahoo_symbol_is_translated_both_ways(warehouse, universo, monkeypatch):
    """En Yahoo es 'BTC-EUR' y en Kraken 'BTC/EUR'. Si el ticker guardado
    quedara con el nombre de Yahoo, la estrategia elegiria 'BTC-EUR' y el bot
    intentaria comprar en Kraken algo que Kraken no conoce."""
    assert ingest_crypto.yahoo_symbol("BTC/EUR") == "BTC-EUR"

    vistos = {}

    class FakeYf:
        def fetch_ohlcv(self, tickers, start, end, interval="1d"):
            import pandas as pd

            from stocks_tracker.providers.base import OHLCV_COLUMNS

            vistos["pedidos"] = list(tickers)
            filas = [{
                "ticker": t, "date": end - timedelta(days=1), "open": 1.0,
                "high": 1.0, "low": 1.0, "close": 1.0, "adj_close": 1.0,
                "volume": 1,
            } for t in tickers]
            df = pd.DataFrame(filas, columns=OHLCV_COLUMNS)
            df.attrs["failed_tickers"] = []
            return df

    monkeypatch.setattr("stocks_tracker.providers.registry.build_provider",
                        lambda name: FakeYf())
    ingest_crypto.ingest_crypto_prices()

    assert vistos["pedidos"] == ["BTC-EUR", "ETH-EUR", "SOL-EUR"], (
        "se ha pedido a Yahoo con el nombre de Kraken"
    )
    with db.connect(read_only=True) as conn:
        guardados = [r[0] for r in conn.execute(
            "SELECT DISTINCT ticker FROM prices_daily ORDER BY ticker"
        ).fetchall()]
    assert guardados == ["BTC/EUR", "ETH/EUR", "SOL/EUR"], (
        "se ha guardado con el nombre de Yahoo: el broker no lo reconoceria"
    )


# ---------------------------------------------------------------------------
# Cuanto se separan las dos fuentes
# ---------------------------------------------------------------------------
def test_the_divergence_is_measured_relative_not_absolute(warehouse, universo, proveedor):
    """50 EUR de diferencia son ruido en bitcoin y un disparate en cardano."""
    ingest_crypto.ingest_crypto_prices()

    class KrakenCaro:
        """Devuelve lo mismo un 2 % mas caro."""

        def fetch_ohlcv(self, tickers, start, end, interval="1d"):
            df = proveedor.fetch_ohlcv(tickers, start, end)
            df = df.copy()
            df["adj_close"] = df["adj_close"] * 1.02
            return df

    informe = ingest_crypto.compare_sources(provider=KrakenCaro())
    assert informe, "no ha comparado nada"
    for datos in informe.values():
        assert datos["media_pct"] == pytest.approx(2.0, abs=0.1)


def test_comparing_does_not_store_the_second_source(warehouse, universo, proveedor):
    """Guardar Kraken al comparar seria empalmar las series por la puerta de
    atras, justo lo que el rechazo de mezcla impide por la de delante."""
    ingest_crypto.ingest_crypto_prices()
    ingest_crypto.compare_sources(provider=proveedor)
    with db.connect(read_only=True) as conn:
        fuentes = conn.execute("SELECT DISTINCT source FROM prices_daily").fetchall()
    assert fuentes == [("yfinance",)]


def test_a_large_divergence_is_called_out(warehouse):
    """Por encima del umbral, la serie con la que se valida ya no representa
    los precios a los que se opera."""
    texto = ingest_crypto.render_comparison({
        "BTC/EUR": {"dias": 90, "media_pct": 3.5, "peor_pct": 8.0},
        "ETH/EUR": {"dias": 90, "media_pct": 0.2, "peor_pct": 0.5},
    })
    # Se mira la FILA del par, no el texto entero: el pie tambien lleva un
    # asterisco, asi que buscarlo en todo el informe daba por bueno un
    # renderizado que ya no marcaba ninguna fila.
    filas = {linea.split()[0]: linea for linea in texto.splitlines()
             if linea.strip().startswith(("BTC/", "ETH/"))}
    assert filas["BTC/EUR"].rstrip().endswith("*"), "no marca el par que se desvia"
    assert not filas["ETH/EUR"].rstrip().endswith("*"), "marca uno que no se desvia"
    assert "no representa" in texto


def test_a_small_divergence_says_it_is_fine(warehouse):
    texto = ingest_crypto.render_comparison(
        {"BTC/EUR": {"dias": 90, "media_pct": 0.2, "peor_pct": 0.9}}
    )
    assert "se puede validar" in texto
