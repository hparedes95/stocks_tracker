"""Tests de la unidad de `dividend_yield`.

DOSCIENTOS DIECINUEVE VALORES DE QUINIENTOS VEINTINUEVE, Y NINGUN ERROR.

    Fundamentales imposibles descartados del ranking: dividend_yield (219)

Yahoo cambio la unidad del campo sin cambiarle el nombre: donde antes publicaba
0.023 para un 2,3 %, ahora publica 2.3. El numero sigue teniendo buena pinta, no
lanza nada y se guarda tan tranquilo. Lo unico que se ve es que el limite de
cordura -`consistency.IMPOSIBLES`, (0, 0.50)- empieza a vaciar el campo en todo
el que reparta mas de un 0,5 %, que son cuatro de cada diez valores del universo.

Esa es la firma del problema y por eso el arreglo NO es ensanchar el limite: con
el limite ancho, un 2.3 leido como fraccion pasa por bueno y la ficha del valor
enseña "Rentabilidad por dividendo: 230,00 %".

Toda la aplicacion espera FRACCION -"{:.2%}" en la ficha, x100 en oportunidades
y en el panel de costes, `max_valid: 0.20` en factors.yaml-, asi que la unidad se
fija en el adaptador del proveedor, que es el unico que sabe de donde viene el
numero.
"""

from __future__ import annotations

import pytest

from stocks_tracker.core.consistency import IMPOSIBLES, campos_rotos
from stocks_tracker.providers.yfinance_provider import rendimiento_dividendo

# Un pagador de dividendos corriente: 1,15 al año sobre una cotizacion de 50 es
# un 2,3 %. Los tres campos salen del mismo `info` de Yahoo.
PAGADOR = {"dividendRate": 1.15, "currentPrice": 50.0}


# ---------------------------------------------------------------------------
# El fallo
# ---------------------------------------------------------------------------
def test_el_porcentaje_de_yahoo_se_convierte_en_fraccion():
    """Lo que rompia: 2.3 significa 2,3 %, no 230 %."""
    assert rendimiento_dividendo({**PAGADOR, "dividendYield": 2.3}) == pytest.approx(0.023)


def test_la_fraccion_de_siempre_se_deja_como_esta():
    """Contrapeso, y el que impide 'arreglarlo' dividiendo entre cien a ciegas.

    Yahoo ya publico este campo en fraccion y puede volver a hacerlo; una
    division incondicional convertiria un 2,3 % en un 0,023 %, que es el mismo
    fallo con el decimal en el otro lado.
    """
    assert rendimiento_dividendo({**PAGADOR, "dividendYield": 0.023}) == pytest.approx(0.023)


def test_el_valor_convertido_ya_no_es_imposible():
    """La comprobacion de punta a punta: el dato arreglado ATRAVIESA el filtro
    que antes lo tiraba. Sin esto, los dos tests de arriba pueden pasar y el
    campo seguir vaciandose en el ranking."""
    fila = {"dividend_yield": rendimiento_dividendo({**PAGADOR, "dividendYield": 2.3})}
    assert "dividend_yield" not in campos_rotos(fila)

    # Y el crudo, sin convertir, si que lo era. Es lo que veia el usuario.
    assert "dividend_yield" in campos_rotos({"dividend_yield": 2.3})


def test_el_limite_de_cordura_no_se_ha_tocado():
    """El arreglo es la unidad, no el rango.

    Ensanchar `IMPOSIBLES` habria hecho desaparecer los 219 avisos dejando
    entrar el dato malo, que es peor que el sintoma: un 230 % de rentabilidad
    por dividendo puntuaria el primero del universo en el perfil 'dividend'.
    """
    assert IMPOSIBLES["dividend_yield"] == (0.0, 0.50)


# ---------------------------------------------------------------------------
# Como se decide la unidad
# ---------------------------------------------------------------------------
def test_la_unidad_se_decide_con_un_ancla_y_no_con_el_tamaño():
    """El ancla es el dividendo por accion entre el precio: dos importes en la
    misma divisa, asi que su cociente es una fraccion pase lo que pase con el
    campo en disputa.

    Un 0,44 % ilustra por que el tamaño no sirve: 0.44 es una lectura valida
    como porcentaje (0,44 %) Y como fraccion (44 %), y ningun umbral las separa.
    """
    barato = {"dividendRate": 0.22, "currentPrice": 50.0}   # 0,44 %
    assert rendimiento_dividendo({**barato, "dividendYield": 0.44}) == pytest.approx(0.0044)

    caro = {"dividendRate": 22.0, "currentPrice": 50.0}     # 44 %
    assert rendimiento_dividendo({**caro, "dividendYield": 0.44}) == pytest.approx(0.44)


def test_el_precio_puede_venir_por_cualquiera_de_sus_tres_nombres():
    """Yahoo no manda siempre los tres, y sin precio no hay ancla."""
    for campo in ("currentPrice", "regularMarketPrice", "previousClose"):
        info = {"dividendRate": 1.15, campo: 50.0, "dividendYield": 2.3}
        assert rendimiento_dividendo(info) == pytest.approx(0.023), campo


def test_sin_importe_por_accion_vale_el_rendimiento_de_los_doce_meses_pasados():
    """`trailingAnnualDividendYield` es OTRO campo, y ese Yahoo lo ha servido
    siempre en fraccion. Mira al pasado, asi que decide la unidad pero no
    sustituye al declarado: el que se devuelve sigue siendo el declarado."""
    info = {"dividendYield": 2.3, "trailingAnnualDividendYield": 0.021}
    assert rendimiento_dividendo(info) == pytest.approx(0.023)


def test_sin_ancla_solo_se_corrige_lo_que_es_imposible():
    """La banda ambigua se deja quieta a proposito.

    Sin nada con que contrastar, un 0.44 puede ser las dos cosas y elegir seria
    tirar una moneda: se deja pasar y que lo vea `consistency`, que para eso
    esta. Lo que si es seguro es que nadie reparte mas del 100 % de su
    cotizacion, asi que ahi no hay duda que resolver.
    """
    assert rendimiento_dividendo({"dividendYield": 0.44}) == pytest.approx(0.44)
    assert rendimiento_dividendo({"dividendYield": 2.3}) == pytest.approx(0.023)


# ---------------------------------------------------------------------------
# Huecos
# ---------------------------------------------------------------------------
def test_quien_no_reparte_dividendo_no_se_inventa_uno():
    assert rendimiento_dividendo({}) is None
    assert rendimiento_dividendo({"dividendYield": None}) is None
    assert rendimiento_dividendo({"dividendYield": 0.0}) == 0.0


def test_una_basura_del_proveedor_no_tumba_la_descarga():
    """`info` es un diccionario sin contrato: llega lo que Yahoo quiera."""
    assert rendimiento_dividendo({"dividendYield": "n/a"}) is None
    assert rendimiento_dividendo({"dividendYield": float("nan")}) is None
    assert rendimiento_dividendo({"dividendYield": float("inf")}) is None
    # Un precio a cero no puede ser el denominador de nada.
    assert rendimiento_dividendo(
        {"dividendYield": 2.3, "dividendRate": 1.15, "currentPrice": 0.0}
    ) == pytest.approx(0.023)


def test_con_ancla_pero_sin_declarado_se_usa_el_ancla():
    """Que el campo en disputa falte no es motivo para tirar un dato que se
    puede calcular con los otros dos."""
    assert rendimiento_dividendo(PAGADOR) == pytest.approx(0.023)


# ---------------------------------------------------------------------------
# El guardarrail
# ---------------------------------------------------------------------------
def test_el_proveedor_no_copia_el_campo_crudo():
    """Sobre el codigo real. `info.get("dividendYield")` a pelo es el fallo
    original, y es lo primero que reaparece en un merge mal resuelto."""
    from stocks_tracker.core.config import project_root

    src = (project_root()
           / "src/stocks_tracker/providers/yfinance_provider.py").read_text("utf-8")
    tabla = src[src.index("def fetch_snapshot"):]

    assert '"dividend_yield": rendimiento_dividendo(info)' in tabla
    assert '"dividend_yield": info.get("dividendYield")' not in tabla
