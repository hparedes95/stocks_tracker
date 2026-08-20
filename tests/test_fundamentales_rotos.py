"""Un dato imposible no puede entrar en el ranking.

La revision de coherencia existia desde antes y solo MIRABA. Pintaba la pagina
de "Fundamentales que se contradicen" —64 valores en la primera instalacion
real— y ni uno solo de esos valores imposibles dejaba de entrar en el ranking.
El aviso estaba, y el numero roto puntuaba igual.

La diferencia importa con dinero de por medio. Una rentabilidad por dividendo
de 3,5 (o sea, del 350 %) no es un dato extremo que haya que ponderar con
cuidado: describe una empresa que no existe. Con ese numero dentro, el valor
sube en el ranking de dividendo por encima de todos los demas y se queda ahi
con la misma pinta que un dato bueno.

Vaciar no es adivinar: el campo se queda a nulo, la cobertura de ese valor baja
y puntua con un dato menos. Lo que NO se hace es sustituirlo por la mediana del
sector, que lo devolveria al ranking disfrazado de dato.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from stocks_tracker.compute import run_compute
from stocks_tracker.compute.run_compute import _descartar_fundamentales_rotos
from stocks_tracker.core import db
from stocks_tracker.core.consistency import CAMPO_SISTEMATICO, campos_repetidos, campos_rotos


def fila(**campos) -> pd.Series:
    base = {"ticker": "AAA", "trailing_pe": 18.0, "roe": 0.15,
            "profit_margin": 0.12, "gross_margin": 0.40,
            "operating_margin": 0.20, "dividend_yield": 0.03,
            "payout_ratio": 0.4, "price_to_book": 2.0}
    base.update(campos)
    return pd.Series(base)


# ---------------------------------------------------------------------------
# Que cuenta como roto
# ---------------------------------------------------------------------------

def test_una_rentabilidad_por_dividendo_en_porcentaje_es_un_dato_roto():
    """El caso concreto: el proveedor cambia la unidad y publica 3,5 donde
    antes publicaba 0,035."""
    assert campos_rotos(fila(dividend_yield=3.5)) == {"dividend_yield"}
    assert campos_rotos(fila(dividend_yield=0.035)) == set()


def test_un_margen_neto_por_encima_del_bruto_es_un_dato_roto():
    assert "profit_margin" in campos_rotos(fila(profit_margin=0.55, gross_margin=0.40))


def test_lo_que_solo_no_cuadra_no_cuenta_como_roto():
    """El ROE por debajo del ROA en una empresa endeudada es DUDOSO: no se sabe
    cual de los dos falla. Descartar el que se nombra primero seria elegir a
    cara o cruz, y por eso ese sigue entrando en el ranking."""
    dudoso = fila(roe=0.05, roa=0.10, debt_to_equity=120.0)

    assert campos_rotos(dudoso) == set()


def test_en_un_banco_el_margen_bruto_no_dice_nada():
    """Un banco no tiene coste de las ventas: el margen bruto no existe como
    concepto y el numero que publica el proveedor no es comparable.

    Sin esta excepcion la identidad salta en practicamente todos los bancos, y
    como los datos rotos se vacian antes de puntuar, el sector financiero
    entero se quedaria sin margenes en el ranking por una comprobacion mal
    aplicada. Un falso positivo que borra datos buenos hace mas dano que la
    fuente que pretendia vigilar.
    """
    banco = fila(profit_margin=0.55, gross_margin=0.40)

    assert campos_rotos(banco, "Financials") == set()
    assert campos_rotos(banco, "Financial Services") == set(), (
        "yfinance escribe el sector con su propia taxonomia"
    )
    # Y en una industrial sigue saltando: la excepcion es del sector, no de la
    # comprobacion.
    assert "profit_margin" in campos_rotos(banco, "Industrials")


def test_lo_que_no_depende_del_margen_bruto_sigue_saltando_en_un_banco():
    """La excepcion no puede convertir a los bancos en intocables."""
    assert campos_rotos(fila(dividend_yield=3.5), "Financials") == {"dividend_yield"}


def test_un_valor_negativo_legitimo_no_cuenta_como_roto():
    """Una empresa que pierde dinero y mantiene el dividendo tiene el payout
    negativo de verdad. Es una senal para mirar, no un dato roto."""
    assert campos_rotos(fila(payout_ratio=-2.0)) == set()


# ---------------------------------------------------------------------------
# Que hace el calculo con ellos
# ---------------------------------------------------------------------------

def test_el_campo_roto_se_vacia_antes_de_puntuar():
    frame = pd.DataFrame([fila(ticker="AAA", dividend_yield=3.5),
                          fila(ticker="BBB", dividend_yield=0.04)])

    salida = _descartar_fundamentales_rotos(frame)

    assert pd.isna(salida.loc[0, "dividend_yield"]), (
        "el dato imposible sigue entrando en el ranking"
    )
    assert salida.loc[1, "dividend_yield"] == pytest.approx(0.04), (
        "se ha vaciado tambien el dato bueno del valor de al lado"
    )


def test_solo_se_vacia_el_campo_roto_y_no_la_fila():
    """Un valor con un campo malo sigue puntuando con los demas. Tirar la fila
    entera lo sacaria del ranking por un dato de siete."""
    frame = pd.DataFrame([fila(dividend_yield=3.5)])

    salida = _descartar_fundamentales_rotos(frame)

    assert len(salida) == 1
    assert salida.loc[0, "trailing_pe"] == pytest.approx(18.0)
    assert salida.loc[0, "roe"] == pytest.approx(0.15)


def test_no_se_rellena_con_nada():
    """Sustituirlo por la mediana del sector lo devolveria al ranking
    disfrazado de dato."""
    frame = pd.DataFrame([fila(ticker="AAA", trailing_pe=9000.0),
                          fila(ticker="BBB", trailing_pe=20.0),
                          fila(ticker="CCC", trailing_pe=22.0)])

    salida = _descartar_fundamentales_rotos(frame)

    assert pd.isna(salida.loc[0, "trailing_pe"])
    assert not np.isclose(float(salida.loc[1, "trailing_pe"]), 21.0), (
        "se ha tocado un valor que estaba bien"
    )


def test_sin_nada_roto_el_frame_sale_igual():
    frame = pd.DataFrame([fila(ticker="AAA"), fila(ticker="BBB")])

    salida = _descartar_fundamentales_rotos(frame)

    pd.testing.assert_frame_equal(salida, frame)


def test_no_modifica_el_frame_de_entrada():
    frame = pd.DataFrame([fila(dividend_yield=3.5)])
    _descartar_fundamentales_rotos(frame)

    assert frame.loc[0, "dividend_yield"] == pytest.approx(3.5)


def test_el_calculo_le_pasa_el_sector_a_la_comprobacion():
    """Guardarrail. `campos_rotos` acepta el sector con valor por defecto, asi
    que olvidarse de pasarlo no da ningun error: simplemente vaciaria los
    margenes de todos los bancos del ranking, en silencio."""
    frame = pd.DataFrame([
        {"ticker": "BANCO", "gics_sector": "Financials",
         "profit_margin": 0.55, "gross_margin": 0.40},
        {"ticker": "FABRICA", "gics_sector": "Industrials",
         "profit_margin": 0.55, "gross_margin": 0.40},
    ])

    salida = _descartar_fundamentales_rotos(frame)

    assert salida.loc[0, "profit_margin"] == pytest.approx(0.55), (
        "al banco se le ha vaciado el margen por una identidad que no le aplica"
    )
    assert pd.isna(salida.loc[1, "profit_margin"])


def test_un_campo_roto_que_no_esta_en_el_frame_no_rompe_nada():
    """`campos_rotos` puede nombrar un campo que el ranking no lleva."""
    frame = pd.DataFrame([{"ticker": "AAA", "beta": 40.0, "roe": 0.15}])

    salida = _descartar_fundamentales_rotos(frame)

    assert pd.isna(salida.loc[0, "beta"])
    assert salida.loc[0, "roe"] == pytest.approx(0.15)


# ---------------------------------------------------------------------------
# 64 filas que son un solo problema
# ---------------------------------------------------------------------------

def test_el_campo_que_se_repite_sale_el_primero():
    afectados = [["dividend_yield", "profit_margin"], ["dividend_yield"],
                 ["dividend_yield", "market_cap"], ["roe"]]

    assert campos_repetidos(afectados)[0] == ("dividend_yield", 3)


def test_un_campo_repetido_dentro_del_mismo_valor_cuenta_una_vez():
    """Si contara por aviso y no por valor, un campo que falla en dos
    comprobaciones del mismo ticker pareceria mas extendido de lo que esta."""
    assert campos_repetidos([["roe", "roe", "roe"]]) == [("roe", 1)]


def test_un_campo_en_un_tercio_del_universo_es_del_proveedor():
    """El umbral tiene que separar los dos casos, no dejar pasar ninguno ni
    marcarlos todos."""
    afectados = [["dividend_yield"]] * 30 + [[f"campo_{i}"] for i in range(60)]
    conteo = dict(campos_repetidos(afectados))
    total = len(afectados)

    assert conteo["dividend_yield"] >= total * CAMPO_SISTEMATICO
    assert conteo["campo_0"] < total * CAMPO_SISTEMATICO


def test_los_campos_vacios_no_cuentan():
    """`revisar` devuelve un aviso con el campo en blanco cuando no hay
    fundamentales; contarlo pondria "" como el problema mas extendido."""
    assert campos_repetidos([[""], ["", "roe"]]) == [("roe", 1)]


# ---------------------------------------------------------------------------
# Y el ranking de verdad lo usa
# ---------------------------------------------------------------------------
# Los de arriba prueban la funcion. Este prueba que alguien la LLAMA: sin el, la
# funcion podia quedarse desconectada del calculo —que es exactamente el estado
# anterior, una revision de coherencia que solo miraba— sin que ningun test se
# quejara.

DIA = date(2026, 8, 7)


@pytest.fixture
def warehouse(tmp_path, monkeypatch):
    class Stub:
        warehouse_path = tmp_path / "test.duckdb"
        compute: dict = {"weights_preset": "balanced"}
        raw: dict = {}

    monkeypatch.setattr(db, "get_settings", lambda: Stub())
    db.migrate()
    return Stub.warehouse_path


def sembrar_universo(margen_neto_de_aaa: float) -> None:
    """Doce valores normales y uno cuyo margen neto supera al bruto.

    Se usa la IDENTIDAD CONTABLE y no un valor fuera de rango a proposito: los
    fuera de rango ya los tira `scoring.sanitize` con los `max_valid` de
    `factors.yaml`, asi que un test montado sobre esos pasaria igual con el
    arreglo y sin el. Un margen neto del 95 % con un bruto del 40 % es
    imposible y sin embargo cae DENTRO del rango (-2, 1) de la submetrica: es
    justo lo que ningun rango puede cazar.
    """
    tickers = ["AAA"] + [f"T{i}" for i in range(12)]
    with db.connect() as conn:
        conn.executemany(
            "INSERT INTO instruments (ticker, asset_class, gics_sector, is_active) "
            "VALUES (?, 'equity', 'Tecnologia', TRUE)", [(t,) for t in tickers],
        )
        ind = pd.DataFrame({"ticker": tickers, "date": DIA})
        for columna, valor in (("close", 100.0), ("atr14", 1.0), ("rsi14", 50.0),
                               ("realized_vol_252", 0.2), ("mom_12_1", 0.1),
                               ("above_sma200", True)):
            ind[columna] = valor
        db.upsert_df(conn, "indicators_daily", ind, keys=["ticker", "date"])

        fund = pd.DataFrame({
            "ticker": tickers,
            "as_of": DIA,
            "gross_margin": 0.40,
            "profit_margin": [margen_neto_de_aaa]
                             + [0.05 + 0.01 * i for i in range(12)],
            "operating_margin": 0.20,
            "roe": 0.15,
            "trailing_pe": 18.0,
        })
        db.upsert_df(conn, "fundamentals_snapshot", fund, keys=["ticker", "as_of"])


def calidad_z() -> pd.Series:
    return db.query(
        "SELECT ticker, quality_z FROM factor_scores"
    ).set_index("ticker")["quality_z"]


def test_el_dato_roto_no_encabeza_el_ranking(warehouse):
    """La prueba con dinero de por medio. Se comprobo sobre el calculo real:
    sin descartarlo, AAA sale el primero del universo entero —percentil 1,00—
    por un margen que el proveedor calculo mal."""
    sembrar_universo(margen_neto_de_aaa=0.95)
    run_compute.compute_factor_scores(preset="balanced")

    z = calidad_z()
    assert pd.isna(z["AAA"]) or z["AAA"] <= z.drop("AAA").max(), (
        "el dato imposible sigue encabezando el ranking de calidad"
    )


def test_el_escenario_reproduce_el_problema_sin_el_arreglo(warehouse):
    """Guardarrail del test de arriba. Con un margen POSIBLE de 0,35 —el mas
    alto del universo pero por debajo del bruto— AAA tiene que salir el primero.
    Si no saliera, el test hermano pasaria por un motivo que no tiene nada que
    ver con el arreglo."""
    sembrar_universo(margen_neto_de_aaa=0.35)
    run_compute.compute_factor_scores(preset="balanced")

    z = calidad_z()
    assert z["AAA"] == z.max(), (
        "el escenario no coloca a AAA arriba ni con un dato bueno: el test "
        "hermano no probaria nada"
    )


def test_los_rangos_del_ranking_no_pueden_ser_mas_flojos_que_los_imposibles():
    """Guardarrail de las DOS listas de rangos que hay en el proyecto.

    `consistency.IMPOSIBLES` dice que es imposible y `factors.yaml` dice que
    entra en el ranking. Hoy la segunda es mas estricta en todos los campos que
    comparten, y por eso un dato fuera de rango no llega a puntuar. El dia que
    alguien afloje un `max_valid` sin mirar la otra lista, ese valor imposible
    empezaria a puntuar en silencio: no daria ningun error y nadie lo veria.
    """
    from stocks_tracker.core.config import get_factor_config
    from stocks_tracker.core.consistency import IMPOSIBLES

    rangos = {
        sub.field: (sub.min_valid, sub.max_valid)
        for spec in get_factor_config().factors.values()
        for sub in spec.submetrics
    }
    comunes = set(rangos) & set(IMPOSIBLES)
    assert comunes, "las dos listas ya no comparten ningun campo: revisa el test"

    for campo in sorted(comunes):
        minimo, maximo = IMPOSIBLES[campo]
        min_valid, max_valid = rangos[campo]
        assert min_valid is not None and min_valid >= minimo, (
            f"{campo}: el ranking acepta por abajo mas de lo que "
            f"`consistency` considera posible ({min_valid} < {minimo})"
        )
        assert max_valid is not None and max_valid <= maximo, (
            f"{campo}: el ranking acepta por arriba mas de lo que "
            f"`consistency` considera posible ({max_valid} > {maximo})"
        )
