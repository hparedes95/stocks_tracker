"""Cuando el scrapeo de constituyentes falla, y la cripto en la ruta de Yahoo.

Los dos salieron de la salida real de una instalacion del usuario, el mismo
dia. Ninguno da error: uno deja el ranking calculado sobre otro universo y el
otro escupe seis trazas de Yahoo en cada descarga.
"""

from __future__ import annotations

import pathlib
from datetime import date

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


# ---------------------------------------------------------------------------
# El universo que encoge de un dia para otro
# ---------------------------------------------------------------------------
def test_el_aviso_de_encogimiento_dice_CUALES_faltan(tmp_path, monkeypatch,
                                                      capsys):
    """"72 menos" no se puede accionar.

    Salio del uso real: el usuario vio

        El universo ha encogido: 601 -> 529 valores (72 menos).

    y no habia forma de saber cuales sin abrir la base de datos. No es lo mismo
    que falte un indice entero, que un mercado cerrara antes ese dia, o que
    Yahoo diera tres tickers por deslistados: cada una se arregla distinto y
    desde fuera se ven igual.
    """
    import pandas as pd

    from stocks_tracker.compute import run_compute as rc
    from stocks_tracker.core import db

    class Stub:
        warehouse_path = tmp_path / "w3.duckdb"
        compute: dict = {"weights_preset": "balanced"}
        raw: dict = {}
        ui: dict = {}

    monkeypatch.setattr(db, "get_settings", lambda: Stub())
    db.migrate()

    ayer, hoy = date(2026, 9, 1), date(2026, 9, 2)
    antes = [f"T{i:03d}" for i in range(100)]
    ahora = antes[:60]                     # cuarenta se caen

    with db.connect() as conn:
        for t in antes:
            conn.execute(
                "INSERT INTO factor_scores (ticker, date, weights_hash, "
                "composite) VALUES (?, ?, 'w1', 1.0)", [t, ayer])
        rc._registrar_universo(conn, ayer, "w1", antes,
                               pd.Series(["Tech"] * len(antes)))
        rc._registrar_universo(conn, hoy, "w1", ahora,
                               pd.Series(["Tech"] * len(ahora)))

    salida = capsys.readouterr().out
    assert "100 -> 60" in salida
    assert "Los que faltan" in salida
    assert "T060" in salida, "el primero que se cayo tiene que nombrarse"


def test_no_se_listan_seiscientos_tickers_en_la_consola(tmp_path, monkeypatch,
                                                        capsys):
    """Un volcado de seiscientos nombres no se lee, y ademas tapa el resto de
    la salida. Se ensenan quince y se dice cuantos quedan."""
    import pandas as pd

    from stocks_tracker.compute import run_compute as rc
    from stocks_tracker.core import db

    class Stub:
        warehouse_path = tmp_path / "w4.duckdb"
        compute: dict = {"weights_preset": "balanced"}
        raw: dict = {}
        ui: dict = {}

    monkeypatch.setattr(db, "get_settings", lambda: Stub())
    db.migrate()

    ayer, hoy = date(2026, 9, 1), date(2026, 9, 2)
    antes = [f"T{i:03d}" for i in range(600)]
    with db.connect() as conn:
        for t in antes:
            conn.execute(
                "INSERT INTO factor_scores (ticker, date, weights_hash, "
                "composite) VALUES (?, ?, 'w1', 1.0)", [t, ayer])
        rc._registrar_universo(conn, ayer, "w1", antes,
                               pd.Series(["Tech"] * len(antes)))
        rc._registrar_universo(conn, hoy, "w1", antes[:100],
                               pd.Series(["Tech"] * 100))

    salida = capsys.readouterr().out
    assert "y 485 mas" in salida
    assert salida.count("T1") < 60, "no se vuelca la lista entera"


def test_sin_encogimiento_no_se_nombra_a_nadie(tmp_path, monkeypatch, capsys):
    """El contrapeso. Que entren y salgan dos valores es la vida normal de un
    indice: un aviso que sale cada dia deja de leerse."""
    import pandas as pd

    from stocks_tracker.compute import run_compute as rc
    from stocks_tracker.core import db

    class Stub:
        warehouse_path = tmp_path / "w5.duckdb"
        compute: dict = {"weights_preset": "balanced"}
        raw: dict = {}
        ui: dict = {}

    monkeypatch.setattr(db, "get_settings", lambda: Stub())
    db.migrate()

    ayer, hoy = date(2026, 9, 1), date(2026, 9, 2)
    antes = [f"T{i:03d}" for i in range(100)]
    with db.connect() as conn:
        rc._registrar_universo(conn, ayer, "w1", antes,
                               pd.Series(["Tech"] * len(antes)))
        rc._registrar_universo(conn, hoy, "w1", antes[:99],
                               pd.Series(["Tech"] * 99))

    assert "ha encogido" not in capsys.readouterr().out


# ---------------------------------------------------------------------------
# El ticker que vuelve vacio sin que nadie proteste
# ---------------------------------------------------------------------------
# "MMC aparece como possibly delisted" y MMC cotiza: es Marsh & McLennan, del
# NYSE, y entra por la lista MANUAL de SP100, asi que no tiene nada que ver con
# que el scrapeo del NASDAQ100 falle. Lo que pasa es esto:
#
#     ['MMC']: possibly delisted; no timezone found
#
# `yf.download` no lanza cuando un ticker del lote no trae nada. Devuelve el
# lote sin sus columnas y escribe esa linea por su cuenta. El ticker se caia por
# un `continue` y no aparecia en ningun contador: se quedaba sin precio ese dia,
# salia del ranking y reaparecia dias despues dentro de un "72 menos".
class _YahooFalso:
    """El modulo yfinance, con lo justo para `fetch_ohlcv`."""

    def __init__(self, mudos: set[str]):
        self.mudos = mudos

    def download(self, tickers, start, end, **kwargs):
        import pandas as pd

        sirve = [t for t in tickers if t not in self.mudos]
        if not sirve:
            return pd.DataFrame()
        fechas = pd.to_datetime([date(2026, 9, 1), date(2026, 9, 2)])
        columnas = pd.MultiIndex.from_product(
            [sirve, ["Open", "High", "Low", "Close", "Adj Close", "Volume"]])
        return pd.DataFrame(1.0, index=fechas, columns=columnas).rename_axis("Date")


def _descarga(monkeypatch, tickers, mudos):
    from stocks_tracker.providers import yfinance_provider as yp

    monkeypatch.setattr(yp, "_import_yfinance", lambda: _YahooFalso(set(mudos)))
    monkeypatch.setattr(yp.YFinanceProvider, "_pause", lambda self: None)
    proveedor = yp.YFinanceProvider()
    return proveedor.fetch_ohlcv(tickers, date(2026, 9, 1), date(2026, 9, 3))


def test_un_ticker_que_vuelve_vacio_consta_como_fallido(monkeypatch):
    """El fallo: Yahoo sirve el lote menos MMC y MMC desaparece sin dejar rastro.

    Tiene que constar en `failed_tickers` y no en un mensaje de baja, porque es
    justo lo que la cadena le vuelve a pedir a Stooq. Un valor que sigue
    cotizando merece un segundo intento, no un certificado de defuncion.
    """
    salida = _descarga(monkeypatch, ["AAPL", "MMC", "MSFT"], mudos={"MMC"})

    assert set(salida["ticker"]) == {"AAPL", "MSFT"}
    assert salida.attrs["failed_tickers"] == ["MMC"], (
        "el ticker que Yahoo no sirvio se ha perdido sin contarse"
    )


def test_los_que_si_llegan_no_se_marcan_como_fallidos(monkeypatch):
    """El contrapeso. Marcar de mas dispararia el relevo a Stooq para el
    universo entero y mezclaria dos fuentes en cada serie."""
    salida = _descarga(monkeypatch, ["AAPL", "MSFT"], mudos=set())

    assert salida.attrs["failed_tickers"] == []


def test_un_lote_entero_mudo_tambien_se_nombra(monkeypatch):
    """Un festivo local deja mudo un mercado entero. Sigue siendo informacion:
    lo que no puede pasar es que se descarte en silencio."""
    salida = _descarga(monkeypatch, ["SAN.MC", "BBVA.MC"], mudos={"SAN.MC", "BBVA.MC"})

    assert set(salida.attrs["failed_tickers"]) == {"SAN.MC", "BBVA.MC"}


# ---------------------------------------------------------------------------
# Y que la consola diga CUALES
# ---------------------------------------------------------------------------
def test_la_ingesta_nombra_los_fallidos_y_no_solo_los_cuenta():
    """"12 tickers fallidos" no se puede accionar.

    No es lo mismo que falle un mercado entero por un festivo, que fallen tres
    valores que Yahoo da por deslistados sin estarlo, o que se agote el
    presupuesto de peticiones a mitad de la lista. Es el mismo criterio que el
    aviso de encogimiento del universo.
    """
    from stocks_tracker.ingest.run_ingest import _nombrar

    assert _nombrar(["MMC"]) == "MMC"
    assert _nombrar(["MSFT", "AAPL"]) == "AAPL, MSFT", "sin orden no se comparan"
    assert _nombrar(["MMC", "MMC"]) == "MMC", "el mismo ticker no es dos fallos"


def test_una_lista_larga_se_recorta():
    """Seiscientos nombres taparian todo lo demas que la ingesta tiene que
    decir, que es exactamente lo contrario de lo que se busca."""
    from stocks_tracker.ingest.run_ingest import _nombrar

    salida = _nombrar([f"T{i:03d}" for i in range(40)])

    assert salida.startswith("T000, T001")
    assert salida.endswith("y 25 mas")
    assert "T020" not in salida


def test_la_consola_recibe_los_nombres_y_no_solo_el_numero():
    """Guardarrail sobre el codigo real: volver a `f"{len(failed)} fallidos"` a
    secas es un cambio de una linea que ningun test de unidad detecta."""
    from stocks_tracker.core.config import project_root

    src = (project_root()
           / "src/stocks_tracker/ingest/run_ingest.py").read_text("utf-8")
    bloque = src[src.index("def ingest_prices"):src.index("def _nombrar")]

    assert "_nombrar(failed)" in bloque
