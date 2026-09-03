"""Cuando el scrapeo de constituyentes falla, y la cripto en la ruta de Yahoo.

Los dos salieron de la salida real de una instalacion del usuario, el mismo
dia. Ninguno da error: uno deja el ranking calculado sobre otro universo y el
otro escupe seis trazas de Yahoo en cada descarga.
"""

from __future__ import annotations

import pathlib

import duckdb

from stocks_tracker.core import membership
from stocks_tracker.providers import universe_provider as up
from stocks_tracker.providers.base import ProviderError

_ESQUEMA = (pathlib.Path(__file__).resolve().parents[1]
            / "src/stocks_tracker/core/schema.sql")

# Los veinte de `universe.yaml`. La lista manual del NASDAQ100, tal cual.
MANUAL = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "AVGO", "TSLA",
          "COST", "NFLX", "AMD", "PEP", "ADBE", "CSCO", "INTU", "TXN",
          "QCOM", "AMGN", "ISRG", "BKNG"]
# Y los cien que ya estaban guardados de la ultima vez que si se leyo.
GUARDADOS = MANUAL + [f"Z{i:03d}" for i in range(80)]


def _falla(*a, **k):
    raise ProviderError("No se encontro la tabla de constituyentes")


# ---------------------------------------------------------------------------
# El universo que se encoge sin decirlo
# ---------------------------------------------------------------------------
def test_si_falla_el_scrapeo_manda_lo_ultimo_conocido(monkeypatch):
    """EL CASO REAL, CON SU SALIDA.

        NASDAQ100: No se encontro la tabla de constituyentes [...]
        NASDAQ100: 20 tickers (manual (fallo la descarga))

    Veinte tickers donde habia cien. En un ranking transversal eso no es un
    universo mas pequeno: es OTRO universo. Cada z-score sale de la mediana de
    los valores presentes, asi que la lista de oportunidades cambia entera
    aunque los precios sean identicos.

    Es lo que hacia que dos ordenadores del mismo usuario no coincidieran: en
    uno el scrapeo funciono y en el otro no, y nada lo decia salvo una linea
    perdida entre cientos.
    """
    monkeypatch.setattr(up.UniverseProvider, "fetch_constituents", _falla)

    miembros, origen = up.resolve_universe(
        "NASDAQ100", MANUAL, "wikipedia", ultimos_conocidos=GUARDADOS)

    assert len(miembros) == 100, "se conservan los cien, no se cae a veinte"
    assert origen == "ultimos conocidos (fallo la descarga)"


def test_el_origen_dice_que_el_universo_no_es_de_hoy(monkeypatch):
    """El respaldo no puede pasar por una descarga buena.

    `origenes` decide si se pueden CERRAR intervalos de composicion: con una
    lista de respaldo no se puede, porque los que faltan no es que hayan salido
    del indice, es que no se han podido leer. Si el origen dijera "wikipedia",
    el historico de composicion se corromperia solo.
    """
    monkeypatch.setattr(up.UniverseProvider, "fetch_constituents", _falla)

    _, origen = up.resolve_universe(
        "NASDAQ100", MANUAL, "wikipedia", ultimos_conocidos=GUARDADOS)

    assert origen != "wikipedia"
    assert "fallo la descarga" in origen


def test_la_primera_vez_no_hay_nada_guardado_y_se_usa_la_manual(monkeypatch):
    """El almacen vacio es el caso de la instalacion recien hecha. Ahi la lista
    manual es lo unico que hay, y sigue siendo mejor que ningun universo."""
    monkeypatch.setattr(up.UniverseProvider, "fetch_constituents", _falla)

    miembros, origen = up.resolve_universe(
        "NASDAQ100", MANUAL, "wikipedia", ultimos_conocidos=[])

    assert miembros == MANUAL
    assert origen == "manual (fallo la descarga)"


def test_un_almacen_con_menos_que_la_lista_manual_no_manda(monkeypatch):
    """Si lo guardado es MAS pobre que la lista manual -por ejemplo porque la
    ejecucion anterior tambien fallo y guardo los veinte-, no se elige por ser
    mas reciente: se elige lo que cubra mas mercado."""
    monkeypatch.setattr(up.UniverseProvider, "fetch_constituents", _falla)

    miembros, origen = up.resolve_universe(
        "NASDAQ100", MANUAL, "wikipedia", ultimos_conocidos=["AAPL", "MSFT"])

    assert miembros == MANUAL
    assert "manual" in origen


def test_una_descarga_buena_sigue_mandando_sobre_lo_guardado(monkeypatch):
    """Contrapeso. El respaldo es un respaldo: en cuanto Wikipedia responde,
    manda ella. Si no, el universo se congelaria en la primera lectura buena y
    nunca reflejaria una entrada o salida del indice."""
    import pandas as pd

    frescos = pd.DataFrame({"ticker": [f"N{i:03d}" for i in range(101)]})
    monkeypatch.setattr(up.UniverseProvider, "fetch_constituents",
                        lambda self, u: frescos)

    miembros, origen = up.resolve_universe(
        "NASDAQ100", MANUAL, "wikipedia", ultimos_conocidos=GUARDADOS)

    assert origen == "wikipedia"
    assert "N000" in miembros


def test_los_vigentes_salen_del_almacen():
    """El lector, contra la tabla de verdad. Un miembro con `valid_to` puesto
    ya NO esta en el indice, y colarlo resucitaria a los que salieron."""
    conn = duckdb.connect(":memory:")
    try:
        conn.execute(_ESQUEMA.read_text())
        conn.execute(
            "INSERT INTO universe_membership (universe, ticker, valid_from, "
            "valid_to) VALUES ('NASDAQ100', 'AAPL', DATE '2024-01-01', NULL), "
            "('NASDAQ100', 'MSFT', DATE '2024-01-01', NULL), "
            "('NASDAQ100', 'VIEJA', DATE '2024-01-01', DATE '2025-06-01'), "
            "('SP500', 'OTRA', DATE '2024-01-01', NULL)")
        assert membership.vigentes(conn, "NASDAQ100") == ["AAPL", "MSFT"]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# La cripto pidiendose a Yahoo con la barra
# ---------------------------------------------------------------------------
def test_la_cripto_no_entra_por_la_descarga_de_acciones(tmp_path, monkeypatch):
    """SEIS DESCARGAS FALLIDAS EN CADA EJECUCION, CON SU TRAZA.

        Failed to get ticker 'BTC/EUR' reason: ...
        ['BTC/EUR']: YFException(... HTTP 404 Not Found)
        ['ETH/EUR', 'LINK/EUR', 'ADA/EUR', 'DOT/EUR', 'SOL/EUR']:
            possibly delisted; no timezone found

    Los pares se llaman 'BTC/EUR' porque asi los conoce Kraken, que es con
    quien opera el bot. Yahoo los llama 'BTC-EUR'.

    Y no es solo ruido: si esos precios llegaran a entrar, sobrescribirian con
    datos de Yahoo unos precios que el bot usa para operar contra Kraken.
    `ingest_crypto` ya los trae, con su conversion de simbolo.
    """
    from stocks_tracker.core import db
    from stocks_tracker.ingest import run_ingest

    class Stub:
        warehouse_path = tmp_path / "w.duckdb"
        ui: dict = {}
        raw: dict = {}
        ingest: dict = {}

    monkeypatch.setattr(db, "get_settings", lambda: Stub())
    db.migrate()
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO instruments (ticker, name, asset_class, is_active) "
            "VALUES ('AAPL', 'Apple', 'equity', TRUE), "
            "('SPY', 'SPDR', 'etf', TRUE), "
            "('BTC/EUR', 'Bitcoin', 'crypto', TRUE), "
            "('ETH/EUR', 'Ether', 'crypto', TRUE)")

    tickers = run_ingest._tickers_to_download()

    assert "AAPL" in tickers and "SPY" in tickers
    assert not [t for t in tickers if "/" in t], (
        "ningun par de Kraken puede llegar al proveedor de acciones"
    )


def test_un_instrumento_sin_clase_declarada_si_se_descarga(tmp_path, monkeypatch):
    """Contrapeso, y el que evita vaciar el universo por un NULL.

    `asset_class` puede venir sin rellenar en almacenes antiguos. Un filtro
    ingenuo -`asset_class <> 'crypto'`- descarta los NULL en SQL, y con eso se
    dejarian de descargar precisamente los valores mas viejos del almacen.
    """
    from stocks_tracker.core import db
    from stocks_tracker.ingest import run_ingest

    class Stub:
        warehouse_path = tmp_path / "w2.duckdb"
        ui: dict = {}
        raw: dict = {}
        ingest: dict = {}

    monkeypatch.setattr(db, "get_settings", lambda: Stub())
    db.migrate()
    with db.connect() as conn:
        conn.execute("INSERT INTO instruments (ticker, name, is_active) "
                     "VALUES ('VIEJA', 'Sin clase', TRUE)")

    assert "VIEJA" in run_ingest._tickers_to_download()
