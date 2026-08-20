"""Regresion financiera: mismos datos dentro, mismos numeros fuera.

El agujero que cierra este fichero es el que ningun otro test cubre. Los demas
comprueban PROPIEDADES: que el RSI este entre 0 y 100, que un stop no baje, que
un dato imposible se descarte. Todas esas propiedades pueden seguir siendo
ciertas despues de que un cambio haya movido el score de un valor de 82,4 a
79,1. Nada da error, nada se pone rojo, y el dashboard ensena el numero nuevo
con la misma cara de siempre.

Aqui hay un juego de precios y fundamentales FIJOS —dos CSV commiteados— y los
resultados que producen, congelados en `esperado.json`.

QUE HACER SI ESTE FICHERO SE PONE ROJO

No significa que hayas metido un fallo. Significa que has cambiado un numero
financiero. A veces sera correcto: se arregla una formula y los resultados
cambian, faltaria mas.

  1. Mira QUE ha cambiado. El mensaje del fallo lista todas las diferencias.
  2. Comprueba que el cambio es el que pretendias. Si has tocado el RSI y se
     mueven los scores de valor, algo no cuadra.
  3. `make oro` reescribe la referencia.
  4. El diff de `esperado.json` entra en la revision como cualquier otro codigo.

Lo que NO se puede hacer es actualizar la referencia sin mirar. Ese atajo
convierte este fichero en un sello de goma.
"""

from __future__ import annotations

import pytest

from stocks_tracker.core import golden


@pytest.fixture(scope="module")
def obtenido() -> dict:
    """Se calcula UNA vez: el pipeline entero sobre 2.600 barras no es gratis."""
    return golden.calcular()


@pytest.fixture(scope="module")
def esperado() -> dict:
    return golden.cargar_referencia()


# ---------------------------------------------------------------------------
# La comprobacion
# ---------------------------------------------------------------------------

def test_ningun_numero_financiero_ha_cambiado(esperado, obtenido):
    """LA prueba. Todo lo demas de este fichero la sostiene."""
    cambios = golden.diferencias(esperado, obtenido)

    assert not cambios, (
        f"\n\nREGRESION FINANCIERA: {len(cambios)} numeros han cambiado con los "
        "mismos datos de entrada.\n\n"
        + "\n".join(f"  {c}" for c in cambios[:40])
        + (f"\n  ... y {len(cambios) - 40} mas" if len(cambios) > 40 else "")
        + "\n\nSi el cambio es intencionado, revisalo y ejecuta `make oro` para "
          "actualizar la referencia.\n"
    )


def test_las_tres_secciones_siguen_estando(esperado, obtenido):
    """Si una seccion desapareciera, el test de arriba pasaria comparando dos
    diccionarios vacios y nadie se enteraria de que ha dejado de vigilar."""
    assert set(esperado) == {"indicadores", "senales", "scores"}
    assert set(obtenido) == set(esperado)
    for seccion in esperado:
        assert obtenido[seccion], f"la seccion '{seccion}' ha salido vacia"


# ---------------------------------------------------------------------------
# Que la referencia no sea trivial
# ---------------------------------------------------------------------------
# Una referencia de tres numeros pasa siempre y no vigila nada. Estos tests
# comprueban que hay bastante superficie cubierta como para que el de arriba
# signifique algo.

def test_la_referencia_cubre_los_cinco_perfiles(esperado):
    """Cada perfil pondera distinto: un cambio puede mover uno y no los demas."""
    assert set(esperado["scores"]) >= {"balanced", "value", "growth", "momentum",
                                       "dividend", "bot_core"}


def test_la_referencia_cubre_indicadores_de_sobra(esperado):
    for ticker, valores in esperado["indicadores"].items():
        numericos = [v for v in valores.values() if isinstance(v, (int, float))]
        assert len(numericos) >= 25, (
            f"{ticker} solo congela {len(numericos)} indicadores: la referencia "
            "se ha quedado corta y ya no vigila casi nada"
        )


def test_la_referencia_incluye_los_indicadores_que_mas_importan(esperado):
    """Guardarrail explicito sobre los que alimentan decisiones de dinero: el
    ATR pone los stops, el momentum y el percentil deciden el ranking."""
    imprescindibles = {"atr14", "rsi14", "mom_12_1", "realized_vol_252",
                       "max_dd_1y", "dist_52w_high", "dist_52w_low"}
    faltan = imprescindibles - set(esperado["indicadores"]["ALZA"])
    assert not faltan, f"la referencia ya no vigila: {sorted(faltan)}"


def test_la_referencia_tiene_senales_disparadas(esperado):
    """Con cero senales, la seccion pasaria comparando nada contra nada."""
    total = sum(s["veces"] for v in esperado["senales"].values() for s in v.values())
    assert total >= 20, f"solo {total} senales en la referencia"


def test_los_datos_de_partida_son_un_fichero_y_no_un_generador(esperado):
    """Un generador con semilla produce numeros distintos si cambia la version
    de numpy, y entonces la referencia se mueve sola sin que nadie haya tocado
    el calculo. El CSV commiteado es la verdad."""
    precios = golden.cargar_precios()

    assert (golden.carpeta() / "precios_oro.csv").exists()
    assert len(precios) >= 2000
    assert precios["ticker"].nunique() >= 5


def test_el_ajustado_no_es_el_cierre_en_los_datos_de_partida():
    """Si `adj_close` fuese igual a `close`, un cambio que confundiera las dos
    columnas —el fallo mas caro que hay en este proyecto— no movería ni un
    numero de la referencia."""
    precios = golden.cargar_precios()
    iguales = (precios["adj_close"] == precios["close"]).mean()

    assert iguales < 0.01, (
        "el ajustado y el cierre coinciden en los datos de referencia: "
        "confundirlos no se notaria"
    )


# ---------------------------------------------------------------------------
# El comparador
# ---------------------------------------------------------------------------

def test_una_diferencia_de_verdad_se_detecta():
    a = {"x": {"y": 1.0}}
    b = {"x": {"y": 1.1}}

    assert golden.diferencias(a, b) == ["x.y: 1.0 -> 1.1"]


def test_el_ruido_del_ultimo_bit_no_se_detecta():
    """El CI corre en otra maquina con otra version de numpy. Sin tolerancia,
    este fichero se pondria rojo por motivos que no son de nadie, y un test que
    falla sin motivo se acaba desactivando."""
    a = {"x": 1.0}
    b = {"x": 1.0 + 1e-15}

    assert golden.diferencias(a, b) == []


def test_la_tolerancia_esta_lejos_de_cualquier_cambio_real():
    """Cambiar una media de 20 a 21 sesiones mueve los numeros en la primera
    cifra significativa. La tolerancia tiene que quedar ordenes de magnitud por
    debajo o estaria tapando cambios de verdad."""
    assert golden.TOLERANCIA <= 1e-8


def test_se_listan_todas_las_diferencias_y_no_la_primera():
    """Con la primera sola, un cambio que mueve treinta numeros obliga a
    arreglar y reejecutar treinta veces. Y la FORMA del cambio —todos los de un
    perfil, todos los de un ticker— es lo que dice si fue intencionado."""
    a = {"p": 1.0, "q": 2.0, "r": 3.0}
    b = {"p": 9.0, "q": 9.0, "r": 9.0}

    assert len(golden.diferencias(a, b)) == 3


def test_un_numero_que_desaparece_se_detecta():
    """Borrar un indicador es un cambio financiero tan grande como moverlo."""
    cambios = golden.diferencias({"x": 1.0, "y": 2.0}, {"x": 1.0})

    assert cambios == ["y: ha desaparecido (valia 2.0)"]


def test_un_numero_nuevo_se_detecta():
    cambios = golden.diferencias({"x": 1.0}, {"x": 1.0, "z": 3.0})

    assert cambios == ["z: es nuevo (vale 3.0)"]


def test_un_nan_que_pasa_a_tener_valor_se_detecta():
    """Que un indicador no exista es un hecho, y que empiece a existir tambien.
    Los NaN se guardan como null justamente para poder verlo."""
    assert golden.diferencias({"x": None}, {"x": 1.0}) == ["x: None -> 1.0"]


def test_un_booleano_que_cambia_se_detecta():
    """`above_sma200` decide entradas y no es un float."""
    assert golden.diferencias({"x": True}, {"x": False}) == ["x: True -> False"]
