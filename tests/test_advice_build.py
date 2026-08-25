"""Del almacen al motor: la traduccion, que es donde se cuelan los fallos mudos.

Aqui no hay reglas de decision —viven en `advice.py` y se prueban aparte—. Lo
que se prueba es la traduccion, y tiene dos trampas que no dan ningun error:

1. Pasar la misma fila como "hoy" y como "entonces". No hay nada que comparar,
   todo sale en verde y el asesor calla justo cuando tenia que hablar.
2. Confundir `atr_pct` (porcentaje del precio) con el ATR en euros. El stop sale
   cien veces mas estrecho y el tamano cien veces mayor, y los dos numeros
   tienen buena pinta.
"""

from __future__ import annotations

import pandas as pd
import pytest

from stocks_tracker.core import advice_build as build
from stocks_tracker.core.advice import Veredicto


def test_no_se_compara_hoy_con_hoy():
    """LA TRAMPA MUDA. Una posicion cuyo margen se ha desplomado del 22 % al
    14 % tiene que salir con veredicto, no en verde.

    Si alguien vuelve a pasar la fila entera por los dos lados, no habra nada
    que comparar, `deterioration` no encontrara nada y esto pasara a MANTENER
    sin que falle ningun otro test.
    """
    salud = pd.DataFrame([{
        "ticker": "AAA", "opened_at": pd.Timestamp("2026-01-05"),
        "profit_margin": 14.0, "profit_margin_entonces": 22.0,
        "net_debt_to_ebitda": 4.5, "net_debt_to_ebitda_entonces": 1.2,
    }])
    posiciones = pd.DataFrame([{"ticker": "AAA", "close": 100.0, "qty": 10,
                                "gics_sector": "Tech"}])

    r = build.de_la_cartera(salud, posiciones, stops={"AAA": 80.0})[0]

    assert r.veredicto is not Veredicto.MANTENER, (
        "se esta comparando la fila de hoy consigo misma: todo sale en verde"
    )


def test_el_atr_en_porcentaje_se_convierte_a_euros():
    """LA OTRA TRAMPA MUDA. `atr_pct` del 2 % sobre un precio de 100 son 2,00
    EUR de ATR, y el stop cae en 100 - 2,5 x 2 = 95.

    Sin convertir, `size_by_atr` recibiria 2 (por ciento) como si fueran euros
    —que en este caso coincide— o, con un precio de 400, recibiria 2 en vez de
    8: el stop saldria en 395 en lugar de 380 y el tamano seria cuatro veces
    mayor. Todo con muy buena pinta.
    """
    ranking = pd.DataFrame([{
        "ticker": "AAA", "composite_pctile": 0.96, "coverage": 0.9,
        "close": 400.0, "atr_pct": 2.0, "gics_sector": "Tech",
    }])

    r = build.de_los_candidatos(ranking, equity=100_000.0, caja=50_000.0)[0]

    assert r.veredicto is Veredicto.COMPRAR
    assert r.stop == pytest.approx(400.0 - 2.5 * 8.0), (
        "el ATR se esta pasando en porcentaje en vez de en euros"
    )


def test_los_motivos_nombran_los_dos_factores_que_mas_empujan():
    """Dos y no siete: un consejo con siete motivos no tiene ninguno."""
    ranking = pd.DataFrame([{
        "ticker": "AAA", "composite_pctile": 0.96, "coverage": 0.9,
        "close": 100.0, "atr_pct": 2.0, "gics_sector": "Tech",
        "value_z": 2.4, "quality_z": 1.8, "momentum_z": 1.1, "growth_z": 0.2,
    }])

    motivos = " ".join(build.de_los_candidatos(
        ranking, equity=100_000.0, caja=50_000.0)[0].motivos)

    assert "valoracion" in motivos and "calidad" in motivos
    assert "momentum" not in motivos, "esta nombrando mas de dos factores"
    assert "crecimiento" not in motivos, "nombra un factor por debajo de 1 sigma"


def test_una_cartera_vacia_no_da_recomendaciones():
    assert build.de_la_cartera(pd.DataFrame(), pd.DataFrame()) == []
    assert build.de_los_candidatos(pd.DataFrame(), equity=0, caja=0) == []


def test_sin_datos_de_la_compra_el_veredicto_es_sin_opinion():
    """Una posicion comprada antes de que existiera el historico no se puede
    juzgar, y decirlo es mejor que un MANTENER que suena a "esta bien"."""
    salud = pd.DataFrame([{"ticker": "AAA", "opened_at": pd.Timestamp("2024-01-05"),
                           "profit_margin": 14.0}])
    posiciones = pd.DataFrame([{"ticker": "AAA", "close": 100.0, "qty": 10,
                                "gics_sector": "Tech"}])

    r = build.de_la_cartera(salud, posiciones, stops={"AAA": 80.0})[0]

    assert r.veredicto is Veredicto.SIN_OPINION


def test_un_factor_mediocre_no_se_nombra_aunque_sobre_sitio():
    """AGUJERO ENCONTRADO POR LA BATERIA DE MUTACION.

    El test de arriba solo comprobaba que se cogen DOS, y con dos factores
    fuertes disponibles el umbral no llegaba a actuar nunca: bajarlo a cero
    pasaba en verde.

    Aqui solo hay un factor por encima de 1 sigma. El segundo hueco tiene que
    quedarse vacio en vez de rellenarse con un +0,3 que no distingue esta
    empresa de la media de su sector.
    """
    ranking = pd.DataFrame([{
        "ticker": "AAA", "composite_pctile": 0.96, "coverage": 0.9,
        "close": 100.0, "atr_pct": 2.0, "gics_sector": "Tech",
        "value_z": 2.4, "quality_z": 0.3, "momentum_z": 0.1, "growth_z": -0.2,
    }])

    motivos = " ".join(build.de_los_candidatos(
        ranking, equity=100_000.0, caja=50_000.0)[0].motivos)

    assert "valoracion" in motivos
    assert "calidad" not in motivos, "nombra un factor de +0,3 sigma como si destacara"
    assert "sigma" in motivos and motivos.count("sigma") == 1
