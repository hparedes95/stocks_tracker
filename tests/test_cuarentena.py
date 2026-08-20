"""Barras imposibles: ni bloquear para siempre ni tragarselas.

Averia real de la primera instalacion: CUATRO barras de 2021 con el cierre
fuera del rango del dia hacian que el programa no pudiera calcular nada. Y no
era un bloqueo que el usuario pudiera resolver, porque en la siguiente descarga
Yahoo manda exactamente la misma barra mala: el programa quedaba inutilizado
para siempre por cuatro filas de hace cinco anos.

Aqui se prueba QUE se aparta. La barra mala pierde el rango del dia y conserva
el cierre, y el resto del almacen no se toca. Esto ultimo no es evidente:
filtrar por los tickers afectados y por las fechas afectadas por separado —que
es como sale si no se piensa— borra miles de barras buenas.

CUANDO se para el calculo se prueba en `test_ohlc_tolerancia`, y ya no depende
de la antiguedad de la barra: la primera version anadio una excepcion por
antiguedad y a la instalacion siguiente tres barras RECIENTES en DE, LMT y
XLRE volvieron a bloquearlo todo. Lo que fallaba era la idea, no el umbral.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from stocks_tracker.core import db, quarantine

PRIMERA = date(2021, 3, 4)


def serie(ticker: str, dias: int, desde: date = date(2026, 1, 5)) -> pd.DataFrame:
    fechas = pd.bdate_range(desde, periods=dias)
    return pd.DataFrame({
        "ticker": ticker,
        "date": fechas,
        "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5,
        "volume": 1_000_000.0,
    })


def romper(precios: pd.DataFrame, ticker: str, cuando) -> pd.DataFrame:
    """Deja el cierre por encima del maximo en una sesion concreta."""
    fila = (precios["ticker"] == ticker) & (pd.to_datetime(precios["date"]) == pd.Timestamp(cuando))
    assert fila.any(), "el escenario no toca ninguna fila real"
    salida = precios.copy()
    salida.loc[fila, "close"] = 500.0
    return salida


# ---------------------------------------------------------------------------
# Que se aparta exactamente
# ---------------------------------------------------------------------------

def test_apartar_una_barra_no_toca_las_demas():
    """El fallo facil: filtrar por ticker y por fecha por separado borra el
    rango de todos los dias de ese ticker y de todos los tickers ese dia."""
    precios = pd.concat([serie("AAA", 20), serie("BBB", 20)], ignore_index=True)
    dias = sorted(precios["date"].unique())
    # DOS barras de tickers y dias distintos, no una. Con una sola, filtrar por
    # ticker y por fecha por separado da exactamente el mismo resultado que
    # hacerlo bien, y el test no probaria nada: son las parejas cruzadas
    # —(AAA, dia_de_BBB)— las que delatan el fallo.
    dia, otro = dias[5], dias[9]
    cuarentena = pd.DataFrame({"ticker": ["AAA", "BBB"], "date": [dia, otro]})

    salida = quarantine.aplicar(precios, cuarentena)

    apartada = (((salida["ticker"] == "AAA") & (salida["date"] == dia))
                | ((salida["ticker"] == "BBB") & (salida["date"] == otro)))
    assert salida.loc[apartada, "high"].isna().all()
    assert salida.loc[apartada, "low"].isna().all()
    assert salida.loc[apartada, "open"].isna().all()
    # El cierre se queda: la incoherencia esta casi siempre en el rango, y sin
    # cierre la serie tendria un agujero, que es un problema peor.
    assert salida.loc[apartada, "close"].notna().all()

    resto = ~apartada
    assert salida.loc[resto, ["open", "high", "low"]].notna().all().all(), (
        "apartar una barra ha vaciado el rango de barras que estaban bien"
    )
    assert len(salida) == len(precios), "se ha perdido alguna fila"


def test_apartar_no_modifica_el_original():
    """Las comprobaciones de calidad tienen que ver los datos como llegaron."""
    precios = pd.concat([serie("AAA", 20)], ignore_index=True)
    dia = precios["date"].iloc[3]
    quarantine.aplicar(precios, pd.DataFrame({"ticker": ["AAA"], "date": [dia]}))

    assert precios["high"].notna().all(), "se ha modificado el DataFrame de entrada"


def test_sin_cuarentena_los_precios_pasan_enteros():
    precios = serie("AAA", 20)
    salida = quarantine.aplicar(precios, pd.DataFrame(columns=["ticker", "date"]))
    assert salida["high"].notna().all()


# ---------------------------------------------------------------------------
# El registro en la base de datos
# ---------------------------------------------------------------------------

@pytest.fixture
def warehouse(tmp_path, monkeypatch):
    class Stub:
        warehouse_path = tmp_path / "test.duckdb"
        compute: dict = {"weights_preset": "balanced"}
        raw: dict = {}

    monkeypatch.setattr(db, "get_settings", lambda: Stub())
    db.migrate()
    return Stub.warehouse_path


def malas(n: int = 2) -> pd.DataFrame:
    return pd.DataFrame({
        "ticker": [f"T{i}" for i in range(n)],
        "date": [PRIMERA + timedelta(days=i) for i in range(n)],
        "motivo": ["cierre por encima del maximo"] * n,
    })


def test_registrar_es_idempotente(warehouse):
    """El proveedor manda la misma barra mala en cada descarga. Sin esto, cada
    ejecucion duplicaria las filas y el recuento no significaria nada."""
    with db.connect() as conn:
        primera = quarantine.registrar(conn, malas(), "run-1")
        segunda = quarantine.registrar(conn, malas(), "run-2")

    assert primera == 2
    assert segunda == 0, "la segunda pasada ha vuelto a insertar las mismas barras"
    assert len(db.query("SELECT * FROM prices_quarantine")) == 2


def test_se_conserva_desde_cuando_arrastramos_la_barra(warehouse):
    """Saber si una barra mala es de hoy o de 2021 es justo lo que distingue
    "el proveedor acaba de romper algo" de "esto lleva ahi anos"."""
    with db.connect() as conn:
        quarantine.registrar(conn, malas(1), "run-1")
        antes = conn.execute(
            "SELECT detected_at, run_id FROM prices_quarantine").fetchdf().iloc[0]
        quarantine.registrar(conn, malas(1), "run-2")

    despues = db.query("SELECT detected_at, run_id FROM prices_quarantine").iloc[0]
    assert despues["run_id"] == antes["run_id"] == "run-1", (
        "la segunda deteccion ha pisado la primera"
    )


def test_el_resumen_agrupa_por_valor(warehouse):
    with db.connect() as conn:
        quarantine.registrar(conn, malas(3), "run-1")
        extra = pd.DataFrame({
            "ticker": ["T0"], "date": [PRIMERA + timedelta(days=30)],
            "motivo": ["maximo por debajo del minimo"],
        })
        quarantine.registrar(conn, extra, "run-1")
        vista = quarantine.resumen(conn)

    fila = vista.set_index("ticker").loc["T0"]
    assert int(fila["barras"]) == 2
    assert vista["ticker"].is_unique
    assert list(vista["ticker"])[0] == "T0", "no esta ordenado por barras"


def test_barras_devuelve_lo_que_aplicar_necesita(warehouse):
    """Guardarrail del contrato entre las dos mitades: lo que sale de la base
    de datos tiene que poder entrar en `aplicar` tal cual."""
    with db.connect() as conn:
        quarantine.registrar(conn, malas(1), "run-1")
        cuarentena = quarantine.barras(conn)

    precios = serie("T0", 20, desde=PRIMERA)
    salida = quarantine.aplicar(precios, cuarentena)

    apartada = pd.to_datetime(salida["date"]) == pd.Timestamp(PRIMERA)
    assert apartada.any(), "el escenario no solapa ninguna fecha"
    assert salida.loc[apartada, "high"].isna().all(), (
        "lo que devuelve `barras` no case con lo que espera `aplicar`"
    )
