"""El asesor: que diga lo que las reglas implican, y solo eso.

QUE PERSIGUEN ESTAS PRUEBAS

No que el asesor acierte —eso no lo puede comprobar ningun test, y lo mide el
marcador con el tiempo—, sino que sea CONSISTENTE y HONESTO:

1. Que no invente una opinion cuando no tiene datos. `SIN_OPINION` existe para
   eso y es la forma mas facil de que una pantalla mienta: rellenar el hueco
   con "mantener" suena a "lo he mirado y esta bien".

2. Que no se salte un limite de la cartera para colocar una compra bonita. El
   dia que un asesor relaja un tope para que quepa una idea, deja de ser un
   asesor.

3. Que no rote. La decision del usuario fue explicita: vender solo si la tesis
   se rompe. La rotacion por "hay algo mejor" es donde se evapora la
   rentabilidad del particular en comisiones e impuestos, y es la tentacion que
   este modulo tiene que resistir.

4. Que toda recomendacion accionable traiga TAMANO, STOP y QUE LA DESMENTIRIA.
   Un consejo sin las tres cosas no se puede ejecutar ni discutir.
"""

from __future__ import annotations

import pytest

from stocks_tracker.core import advice
from stocks_tracker.core import deterioration as det
from stocks_tracker.core.advice import Conviccion, Veredicto

# Una cartera de referencia con la que casi todo cabe, para que cada test solo
# mueva la variable que esta probando.
CARTERA = {"equity": 10_000.0, "caja": 5_000.0, "regimen": "neutral",
           "n_posiciones": 2}


def _diagnostico(*senales: tuple[str, bool]) -> det.Diagnostico:
    return det.Diagnostico(
        ticker="AAA",
        senales=[det.Senal(clave=c, grave=g, texto=f"senal {c}")
                 for c, g in senales],
        hay_datos=True, comparado=True,
    )


def _sano() -> det.Diagnostico:
    return det.Diagnostico(ticker="AAA", senales=[], hay_datos=True,
                           comparado=True)


# ===========================================================================
# LADO DE VENTA
# ===========================================================================
def test_el_stop_perforado_manda_sobre_todo_lo_demas():
    """El stop se fijo el dia de la compra, sin dinero en juego y con la cabeza
    fria. Renegociarlo con la posicion en perdidas es la forma mas comun de
    convertir una perdida acotada en una grande.

    Va el PRIMERO de todas las reglas: aunque los fundamentales esten
    impecables, el precio ya ha dicho que la entrada fue mala.
    """
    r = advice.sobre_una_posicion(
        "AAA", diagnostico=_sano(), precio=90.0, stop=95.0, titulos=10)

    assert r.veredicto is Veredicto.VENDER
    assert r.conviccion is Conviccion.ALTA
    assert "95.00" in " ".join(r.motivos)


def test_una_tesis_rota_es_venta():
    """Dos senales graves valen 4 puntos: ROJO en `deterioration.py`. La
    empresa ya no es la que compraste."""
    r = advice.sobre_una_posicion(
        "AAA", diagnostico=_diagnostico(("margen", True), ("deuda", True)),
        precio=100.0, stop=80.0, titulos=10)

    assert r.veredicto is Veredicto.VENDER
    assert r.conviccion is Conviccion.ALTA


def test_una_sola_senal_grave_no_puede_salir_como_un_mantener_blando():
    """FALLO DE DISENO MIO, ENCONTRADO POR ESTE TEST.

    La primera version leia solo `diagnostico.nivel`. Pero en
    `deterioration.py` una senal grave vale 2 puntos y ROJO pide 4, asi que UNA
    sola senal grave —el margen desplomado— se queda en AMBAR... y salia como
    "Mantener, algo ha cambiado". Un margen hundido merecido un veredicto, no
    una nota al pie.

    Ahora el nivel decide la gravedad y las senales graves deciden si hay que
    actuar. La duda cae del lado de REDUCIR, no de no hacer nada.
    """
    r = advice.sobre_una_posicion(
        "AAA", diagnostico=_diagnostico(("margen", True)),
        precio=100.0, stop=80.0, titulos=10)

    assert r.veredicto is Veredicto.REDUCIR, (
        "una senal grave vuelve a salir como un mantener blando"
    )
    assert r.titulos_a_soltar == pytest.approx(5.0)
    assert "pasa a ser una venta" in " ".join(r.desmentiria)


def test_deterioro_rojo_sin_nada_grave_reduce_en_vez_de_vender():
    """LA DECISION DEL USUARIO, comprobada. Ante la duda, recortar.

    Una venta es irreversible y activa impuestos; un recorte deja la puerta
    abierta. Cuatro senales leves suman rojo pero ninguna dice por si sola que
    la empresa ya no sea la que compraste.
    """
    r = advice.sobre_una_posicion(
        "AAA",
        diagnostico=_diagnostico(("a", False), ("b", False), ("c", False),
                                 ("d", False)),
        precio=100.0, stop=80.0, titulos=10)

    assert r.veredicto is Veredicto.REDUCIR
    assert r.titulos_a_soltar == pytest.approx(5.0), (
        "un recorte tiene que decir CUANTO se suelta, o no es accionable"
    )


def test_que_haya_algo_mejor_NO_es_motivo_para_vender():
    """LA TENTACION QUE ESTE MODULO TIENE QUE RESISTIR.

    Una posicion sana que ha caido al percentil 10 sigue siendo MANTENER. Que
    el ranking prefiera otra cosa no dice que esta se haya roto, y cambiarla
    cuesta comision, cambio de divisa e impuestos.

    Si esto se convierte algun dia en VENDER, el asesor habra empezado a rotar
    y la decision explicita del usuario se habra perdido.
    """
    r = advice.sobre_una_posicion(
        "AAA", diagnostico=_sano(), precio=100.0, stop=80.0,
        percentil=0.10, titulos=10)

    assert r.veredicto is Veredicto.MANTENER, (
        "el asesor ha empezado a rotar por ranking"
    )
    assert "no es motivo para vender" in " ".join(r.motivos).lower()


def test_una_posicion_demasiado_grande_se_recorta_sin_culpar_a_la_empresa():
    """El texto importa tanto como el veredicto. Un REDUCIR sin la frase que
    lo explica se lee como "esto va mal", y la proxima vez se desconfia del
    valor en vez de la concentracion."""
    r = advice.sobre_una_posicion(
        "AAA", diagnostico=_sano(), precio=100.0, stop=80.0,
        peso_pct=30.0, titulos=100)

    assert r.veredicto is Veredicto.REDUCIR
    texto = " ".join(r.motivos).lower()
    assert "no dice nada malo de la empresa" in texto
    # De 30 % a 22 % hay que soltar el 26,7 % de los titulos.
    assert r.titulos_a_soltar == pytest.approx(100 * (1 - 22 / 30), rel=1e-3)


def test_sin_datos_de_la_compra_no_se_dice_que_todo_va_bien():
    """El fallo mas facil de cometer en una pantalla de consejos: rellenar el
    hueco con MANTENER. Suena a "lo he mirado y esta bien" cuando lo unico
    cierto es que no se ha podido mirar."""
    gris = det.Diagnostico(ticker="AAA", senales=[], hay_datos=True,
                           comparado=False)

    r = advice.sobre_una_posicion("AAA", diagnostico=gris, precio=100.0,
                                  stop=80.0)

    assert r.veredicto is Veredicto.SIN_OPINION
    assert "sonaria a que se ha mirado" in " ".join(r.motivos)


def test_sin_precio_no_hay_opinion():
    r = advice.sobre_una_posicion("AAA", diagnostico=_sano(), precio=None)

    assert r.veredicto is Veredicto.SIN_OPINION


def test_una_posicion_sana_se_mantiene_con_su_condicion_de_salida():
    r = advice.sobre_una_posicion(
        "AAA", diagnostico=_sano(), precio=100.0, stop=80.0, percentil=0.85)

    assert r.veredicto is Veredicto.MANTENER
    assert r.conviccion is Conviccion.ALTA
    assert "80.00" in " ".join(r.desmentiria), (
        "un MANTENER sin condicion de salida no se puede revisar despues"
    )


def test_el_aviso_fiscal_viaja_con_la_venta_y_no_la_bloquea():
    """LA DECISION DEL USUARIO: avisa fuerte, deja decidir."""
    r = advice.sobre_una_posicion(
        "AAA", diagnostico=_diagnostico(("margen", True), ("deuda", True)),
        precio=100.0, stop=80.0, titulos=10,
        aviso_fiscal="Vendiste AAA hace 20 dias con perdida.")

    assert r.veredicto is Veredicto.VENDER, "el aviso fiscal ha vetado la venta"
    assert "20 dias" in r.aviso_fiscal


# ===========================================================================
# LADO DE COMPRA
# ===========================================================================
def _candidato(**kw):
    base = dict(percentil=0.96, cobertura=0.9, precio=100.0, atr14=2.0, **CARTERA)
    base.update(kw)
    return advice.sobre_un_candidato("AAA", **base)


def test_una_compra_trae_importe_stop_y_riesgo():
    """Las tres cosas o no es accionable. Un consejo sin tamano deja la
    decision mas importante —cuanto— justo donde mas se falla."""
    r = _candidato()

    assert r.veredicto is Veredicto.COMPRAR
    assert r.importe_eur and r.importe_eur > 0
    assert r.stop == pytest.approx(100.0 - 2.5 * 2.0)
    assert r.riesgo_eur and r.riesgo_eur > 0
    assert r.titulos and r.titulos > 0


def test_la_compra_dice_que_la_desmentiria():
    """Sin esto es una opinion. Con esto es una afirmacion comprobable, y
    dentro de seis meses se puede saber quien tenia razon."""
    r = _candidato()

    texto = " ".join(r.desmentiria)
    assert "95.00" in texto, "no dice el precio al que la decision fue un error"
    assert "universo" in texto, (
        "no avisa de que el percentil es relativo al universo descargado"
    )


def test_una_bandera_roja_veta_la_compra_y_dice_cual():
    r = _candidato(banderas=["Payout del 140 %: el dividendo no lo cubren los beneficios"])

    assert r.veredicto is Veredicto.VETADA
    assert "140" in " ".join(r.motivos)


def test_sin_ATR_no_se_compra():
    """La regla 13 del gestor de riesgo, tambien aqui: sin ATR no hay stop, y
    sin stop no se sabe por donde se sale. Es como una posicion pequena se
    convierte en una grande sin que nadie decida nada."""
    r = _candidato(atr14=None)

    assert r.veredicto is Veredicto.VETADA
    assert "stop" in " ".join(r.motivos).lower()


def test_poca_cobertura_de_datos_no_da_opinion():
    """Un percentil 98 calculado con la mitad de los factores no es una
    conviccion alta: es un numero bonito sobre ruido."""
    r = _candidato(percentil=0.98, cobertura=0.3)

    assert r.veredicto is Veredicto.SIN_OPINION


def test_la_cartera_llena_veta_y_no_propone_rotar():
    """Con las siete plazas ocupadas, el asesor NO sugiere soltar algo para
    hacer sitio. Eso seria rotacion, que es justo lo que no hace."""
    r = _candidato(n_posiciones=7)

    assert r.veredicto is Veredicto.VETADA
    assert "no recomienda rotar" in " ".join(r.motivos).lower()


def test_un_percentil_mediocre_no_se_compra():
    r = _candidato(percentil=0.80)

    assert r.veredicto is Veredicto.SIN_OPINION


def test_el_sector_lleno_veta():
    r = _candidato(peso_sector_pct=40.0)

    assert r.veredicto is Veredicto.VETADA
    assert "sector" in " ".join(r.motivos).lower()


def test_sin_efectivo_el_veto_explica_que_no_es_culpa_de_la_empresa():
    """Un VETADA por caja leido como "esta empresa no vale" hace que la proxima
    vez se descarte un buen candidato por el motivo equivocado."""
    r = _candidato(caja=0.5, equity=10.0)

    assert r.veredicto is Veredicto.VETADA
    assert "no dice nada de la empresa" in " ".join(r.desmentiria).lower()


# ---------------------------------------------------------------------------
# Ampliar: el caso que separa a un asesor de un generador de senales
# ---------------------------------------------------------------------------
def test_se_amplia_lo_que_va_bien_y_pesa_poco():
    r = _candidato(percentil=0.85, peso_actual_pct=3.0)

    assert r.veredicto is Veredicto.AMPLIAR
    assert r.importe_eur and r.importe_eur > 0


def test_no_se_amplia_lo_que_ya_esta_en_su_objetivo():
    """Ampliar del 13 % al 15 % paga una comision para mover la cartera casi
    nada. Es operar por operar, que se siente productivo y cuesta dinero."""
    r = _candidato(percentil=0.96, peso_actual_pct=13.0)

    assert r.veredicto is Veredicto.MANTENER
    assert "comision" in " ".join(r.motivos).lower()


def test_ampliar_pide_menos_liston_que_comprar():
    """Y es deliberado: ampliar no gasta una de las siete plazas ni paga el
    coste de conocer una empresa nueva. Con el mismo liston, el asesor soltaria
    posiciones buenas para comprar otras parecidas."""
    tenida = _candidato(percentil=0.80, peso_actual_pct=2.0)
    nueva = _candidato(percentil=0.80)

    assert tenida.veredicto is Veredicto.AMPLIAR
    assert nueva.veredicto is not Veredicto.COMPRAR


# ---------------------------------------------------------------------------
# Conviccion y orden
# ---------------------------------------------------------------------------
def test_la_conviccion_baja_cuando_los_datos_son_flojos():
    """Mismo puesto en el ranking, distinta confianza. Si la cobertura no
    contara, el asesor sonaria igual de seguro con la mitad de los datos."""
    solida = _candidato(percentil=0.97, cobertura=0.95)
    floja = _candidato(percentil=0.97, cobertura=0.6)

    assert solida.conviccion is Conviccion.ALTA
    assert floja.conviccion is not Conviccion.ALTA


def test_primero_lo_que_ya_tienes():
    """Una venta por tesis rota puede costarte dinero HOY; una compra puede
    esperar a manana. Poner las compras arriba —que es lo que apetece leer— es
    como se acaba con una cartera llena de aciertos viejos sin vender."""
    lista = [
        advice.Recomendacion("C", Veredicto.COMPRAR, Conviccion.ALTA),
        advice.Recomendacion("M", Veredicto.MANTENER, Conviccion.ALTA),
        advice.Recomendacion("V", Veredicto.VENDER, Conviccion.ALTA),
        advice.Recomendacion("R", Veredicto.REDUCIR, Conviccion.ALTA),
    ]

    assert [r.ticker for r in advice.ordenar(lista)] == ["V", "R", "C", "M"]


def test_solo_lo_accionable_se_marca_como_tal():
    """El marcador de la fase D solo puede puntuar lo que pedia actuar. Contar
    un MANTENER como acierto porque el valor subio seria inflar el resultado
    con decisiones que nadie tomo."""
    for v in (Veredicto.COMPRAR, Veredicto.AMPLIAR, Veredicto.REDUCIR,
              Veredicto.VENDER):
        assert advice.Recomendacion("X", v, Conviccion.ALTA).accionable

    for v in (Veredicto.MANTENER, Veredicto.VETADA, Veredicto.SIN_OPINION):
        assert not advice.Recomendacion("X", v, Conviccion.ALTA).accionable


def test_ninguna_recomendacion_promete_el_futuro():
    """LA LINEA QUE EL PROYECTO TRAZO A PROPOSITO Y QUE NO SE MUEVE.

    `narrative.py` tiene un test que falla si el texto usa verbos de futuro. El
    asesor cruza la linea de recomendar, pero NO la de predecir: dice lo que
    tus reglas implican, nunca lo que el mercado va a hacer.
    """
    prohibidas = ["va a subir", "va a bajar", "subira", "bajara", "garantiza",
                  "seguro que", "sin riesgo"]

    casos = [
        _candidato(),
        _candidato(banderas=["algo"]),
        _candidato(atr14=None),
        advice.sobre_una_posicion("AAA", diagnostico=_sano(), precio=100.0,
                                  stop=80.0),
        advice.sobre_una_posicion(
            "AAA", diagnostico=_diagnostico(("m", True), ("n", True)),
            precio=100.0, stop=80.0, titulos=1),
        advice.sobre_una_posicion("AAA", diagnostico=_diagnostico(("m", True)),
                                  precio=100.0, stop=80.0, titulos=1),
    ]
    for r in casos:
        texto = " ".join(r.motivos + r.desmentiria).lower()
        for palabra in prohibidas:
            assert palabra not in texto, (
                f"{r.veredicto} promete el futuro: '{palabra}'"
            )
