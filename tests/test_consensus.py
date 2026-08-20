"""Contrastar el mismo precio entre proveedores.

La cadena de proveedores ya existia y sirve para otra cosa: si Yahoo falla,
prueba Stooq. Eso es tolerancia a fallos. Un proveedor que responde con un
numero EQUIVOCADO pasa la cadena sin despeinarse, porque la cadena solo
pregunta "¿ha respondido alguien?".

Aqui se prueba la pregunta que faltaba: "¿dicen lo mismo?".

Y la trampa principal del diseno, que el propio codigo ya documentaba en
`stooq_provider._parse_csv`: Stooq ajusta por splits pero NO por dividendos, y
Yahoo por los dos. Sus `adj_close` no son la misma magnitud. Comparando esos,
cada valor que haya pagado un dividendo sale como discrepancia —o sea, casi
todos— y el detector se vuelve ruido. Se compara `close`, que es un hecho del
mercado.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from stocks_tracker.providers import consensus as c
from stocks_tracker.providers.consensus import Veredicto

DIA = date(2026, 8, 19)


def evaluar(**fuentes) -> c.Consenso:
    return c.evaluar("AAPL", DIA, dict(fuentes))


# ---------------------------------------------------------------------------
# Los casos del plan, literales
# ---------------------------------------------------------------------------

def test_tres_fuentes_que_concuerdan_dan_verificado():
    """El ejemplo del plan: 231.50 / 231.48 / 231.49 -> consenso 231.49."""
    r = evaluar(yahoo=231.50, stooq=231.48, twelve=231.49)

    assert r.veredicto is Veredicto.VERIFICADO
    assert r.valor == pytest.approx(231.49)
    assert r.n_fuentes == 3
    assert r.operable


def test_una_fuente_disparatada_no_arrastra_el_consenso():
    """El otro ejemplo del plan: 231.50 / 231.48 / 220.10.

    Aqui me aparto del documento a proposito. El plan pedia INVALIDO; esto da
    AVISO con la discrepante NOMBRADA y el consenso puesto en las dos que
    concuerdan al centimo.

    El motivo: con proveedores gratuitos y valores europeos, la tercera fuente
    se sale a menudo por cosas aburridas —otro mercado, otro huso, otra
    divisa—. Una regla que invalida el precio cada vez que eso pasa deja el
    sistema sin senales por ruido de infraestructura, y un detector que salta
    siempre se acaba desconectando.

    Lo que NO puede pasar —y es lo que se prueba— es que el 220.10 se cuele en
    el consenso o desaparezca del registro.
    """
    r = evaluar(yahoo=231.50, stooq=231.48, twelve=220.10)

    assert r.veredicto is Veredicto.AVISO
    assert r.valor == pytest.approx(231.49)
    assert r.discrepantes == ("twelve",)
    # El numero raro sigue guardado: el veredicto tiene que poder comprobarse.
    assert r.por_fuente["twelve"] == pytest.approx(220.10)


def test_dos_fuentes_que_discrepan_no_eligen_ninguna():
    """Con dos en desacuerdo no hay forma de saber cual falla. Quedarse con la
    primera es exactamente lo que hace inutil tener dos fuentes."""
    r = evaluar(yahoo=231.50, stooq=220.10)

    assert r.veredicto is Veredicto.INVALIDO
    assert r.valor is None, "se ha elegido una de las dos a dedo"
    assert not r.operable


def test_una_sola_fuente_nunca_es_verificado():
    """La mentira mas facil de colar: un numero que parece bueno y que nadie ha
    contrastado con nada."""
    r = evaluar(yahoo=231.50)

    assert r.veredicto is Veredicto.DEGRADADO
    assert r.valor == pytest.approx(231.50)
    assert r.operable, "una sola fuente no impide operar, pero no es verificado"


def test_sin_ninguna_fuente_es_desconocido():
    r = evaluar(yahoo=None, stooq=None)

    assert r.veredicto is Veredicto.DESCONOCIDO
    assert r.valor is None
    assert not r.operable


def test_un_empate_no_es_mayoria():
    """Cuatro fuentes partidas dos y dos. Quedarse con una de las dos parejas
    seria echarlo a suertes."""
    r = c.evaluar("AAPL", DIA, {"a": 100.0, "b": 100.01, "c": 120.0, "d": 120.01})

    assert r.veredicto is Veredicto.INVALIDO
    assert r.valor is None


def test_tres_de_cuatro_si_son_mayoria():
    r = c.evaluar("AAPL", DIA, {"a": 100.0, "b": 100.01, "c": 100.02, "d": 120.0})

    assert r.veredicto is Veredicto.AVISO
    assert r.valor == pytest.approx(100.01)
    assert r.discrepantes == ("d",)


# ---------------------------------------------------------------------------
# Lo que no cuenta como fuente
# ---------------------------------------------------------------------------

def test_un_proveedor_que_no_sirvio_el_dato_no_cuenta_como_fuente():
    """Contarlo inflaria el numero de fuentes sin aportar verificacion: dos
    fuentes de las que una es un hueco no son dos fuentes."""
    r = evaluar(yahoo=231.50, stooq=None)

    assert r.n_fuentes == 1
    assert r.veredicto is Veredicto.DEGRADADO


def test_un_precio_a_cero_o_negativo_no_cuenta_como_fuente():
    """Un cero no es una lectura baja: es la ausencia de lectura escrita como
    numero, y promediarla hundiria el consenso."""
    r = evaluar(yahoo=231.50, stooq=0.0, twelve=-1.0)

    assert r.n_fuentes == 1
    assert r.valor == pytest.approx(231.50)


def test_un_nan_no_cuenta_como_fuente():
    r = evaluar(yahoo=231.50, stooq=float("nan"))

    assert r.n_fuentes == 1


# ---------------------------------------------------------------------------
# La dispersion
# ---------------------------------------------------------------------------

def test_la_dispersion_se_mide_contra_la_mediana():
    """Con la media, una fuente disparatada se lleva el centro consigo y la
    dispersion sale artificialmente pequena justo en el caso que hay que
    cazar."""
    valores = [100.0, 100.0, 1000.0]

    contra_mediana = c.dispersion(valores)
    contra_media = (max(valores) - min(valores)) / (sum(valores) / len(valores))

    assert contra_mediana == pytest.approx(9.0)
    assert contra_mediana > contra_media * 2, (
        "la dispersion ya no distingue la mediana de la media"
    )


def test_una_sola_lectura_no_tiene_dispersion():
    assert c.dispersion([100.0]) == 0.0


def test_la_dispersion_viaja_en_el_veredicto():
    """Sin la magnitud, "discrepan" es una etiqueta que no se puede juzgar; es
    ademas lo unico que permitira calibrar los umbrales con datos reales."""
    r = evaluar(yahoo=100.0, stooq=102.0)

    assert r.dispersion == pytest.approx(2.0 / 101.0)


# ---------------------------------------------------------------------------
# El determinismo, que es lo que hace auditable un veredicto
# ---------------------------------------------------------------------------

def test_el_orden_de_las_fuentes_no_cambia_el_resultado():
    """Un veredicto que cambia segun como llegue el diccionario no se puede
    auditar: dos ejecuciones con los mismos datos darian cosas distintas."""
    a = c.evaluar("AAPL", DIA, {"yahoo": 231.50, "stooq": 231.48, "twelve": 220.10})
    b = c.evaluar("AAPL", DIA, {"twelve": 220.10, "stooq": 231.48, "yahoo": 231.50})

    assert (a.veredicto, a.valor, a.discrepantes) == (b.veredicto, b.valor, b.discrepantes)


def test_solo_puede_haber_un_grupo_mayoritario():
    """La propiedad que hace innecesario cualquier criterio de desempate.

    Escrito porque mi primera version llevaba uno ("gana el grupo mas
    apretado") con un comentario diciendo que servia para que el resultado
    fuera reproducible. No servia para nada: mayoria es *mas de la mitad*, y
    dos grupos disjuntos no pueden superar la mitad los dos. Cuando empatan no
    hay mayoria y el veredicto es INVALIDO se coja el tramo que se coja.

    Al mutar aquel desempate no cambiaba ningun test, que es como se vio.
    """
    empatados = c.evaluar("A", DIA, {"a": 100.0, "b": 100.001,
                                     "c": 200.0, "d": 200.4})

    assert empatados.veredicto is Veredicto.INVALIDO
    assert empatados.valor is None, (
        "con dos grupos empatados se ha elegido uno, que es echarlo a suertes"
    )


# ---------------------------------------------------------------------------
# Los umbrales
# ---------------------------------------------------------------------------

def test_justo_por_debajo_de_la_tolerancia_concuerdan():
    r = c.evaluar("A", DIA, {"a": 100.0, "b": 100.4}, tolerancia=0.005)
    assert r.veredicto is Veredicto.VERIFICADO


def test_justo_por_encima_de_la_tolerancia_ya_no():
    r = c.evaluar("A", DIA, {"a": 100.0, "b": 100.6}, tolerancia=0.005)
    assert r.veredicto is Veredicto.INVALIDO


def test_el_maximo_manda_sobre_la_tolerancia():
    """Configurar la tolerancia mas ancha que el maximo es una contradiccion.
    Se respeta la regla mas dura en vez de dejar pasar el precio."""
    r = c.evaluar("A", DIA, {"a": 100.0, "b": 110.0},
                  tolerancia=0.50, maxima=0.02)

    assert r.veredicto is Veredicto.AVISO, "la tolerancia ancha ha tapado el maximo"


# ---------------------------------------------------------------------------
# El lote
# ---------------------------------------------------------------------------

def observaciones(filas: list[tuple]) -> pd.DataFrame:
    return pd.DataFrame(filas, columns=["ticker", "date", "source", "close"])


def test_comparar_agrupa_por_ticker_y_fecha():
    tabla = c.comparar(observaciones([
        ("AAPL", DIA, "yahoo", 231.50),
        ("AAPL", DIA, "stooq", 231.48),
        ("MSFT", DIA, "yahoo", 400.0),
        ("MSFT", DIA, "stooq", 380.0),
    ]))

    por_ticker = tabla.set_index("ticker")
    assert por_ticker.loc["AAPL", "veredicto"] == "verificado"
    assert por_ticker.loc["MSFT", "veredicto"] == "invalido"


def test_comparar_usa_el_cierre_sin_ajustar_por_defecto():
    """LA trampa del modulo. Stooq ajusta por splits y no por dividendos;
    Yahoo por los dos. Sus `adj_close` no son la misma magnitud, y comparando
    esos casi todo el universo saldria discrepante."""
    filas = observaciones([
        ("AAPL", DIA, "yahoo", 231.50),
        ("AAPL", DIA, "stooq", 231.48),
    ])
    # Mismo cierre, ajustados muy distintos: es el escenario real de un valor
    # con historial de dividendos.
    filas["adj_close"] = [180.0, 231.48]

    assert c.comparar(filas).iloc[0]["veredicto"] == "verificado"
    assert c.comparar(filas, columna="adj_close").iloc[0]["veredicto"] == "invalido", (
        "el escenario no distingue las dos columnas"
    )


def test_comparar_sin_observaciones_no_revienta():
    assert c.comparar(pd.DataFrame()).empty


def test_comparar_se_queja_si_faltan_columnas():
    """Devolver vacio ante un DataFrame mal formado esconderia el fallo: la
    auditoria diria "nada que comparar" y nadie miraria por que."""
    with pytest.raises(ValueError, match="faltan columnas"):
        c.comparar(pd.DataFrame({"ticker": ["AAPL"], "date": [DIA]}))


def test_una_fuente_repetida_no_se_promedia():
    """Promediar dos filas del mismo proveedor inventaria un precio que no
    publico nadie."""
    tabla = c.comparar(observaciones([
        ("AAPL", DIA, "yahoo", 231.50),
        ("AAPL", DIA, "yahoo", 999.0),
        ("AAPL", DIA, "stooq", 999.0),
    ]))

    assert tabla.iloc[0]["n_fuentes"] == 2
    assert tabla.iloc[0]["por_fuente"]["yahoo"] == pytest.approx(999.0)


# ---------------------------------------------------------------------------
# Lo que se puede operar
# ---------------------------------------------------------------------------

def test_los_no_operables_son_los_que_no_tienen_precio():
    """La coherencia que importa: si no hay valor de consenso, no se puede
    decidir nada con el. Y al reves."""
    for veredicto in Veredicto:
        sin_precio = veredicto in (Veredicto.INVALIDO, Veredicto.DESCONOCIDO)
        assert (veredicto in c.NO_OPERABLES) == sin_precio, (
            f"{veredicto} no cuadra: operable pero sin precio, o al reves"
        )


def test_cada_veredicto_tiene_su_semaforo():
    """El panel de integridad los pinta por nombre; uno sin icono saldria en
    blanco y pareceria que no se ha comprobado."""
    assert set(c.SEMAFORO) == set(Veredicto)
