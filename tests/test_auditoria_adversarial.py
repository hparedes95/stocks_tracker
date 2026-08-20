"""Datos disenados a mala fe: numeros creibles y falsos.

QUE ES ESTO Y EN QUE SE DIFERENCIA DEL RESTO DE LOS TESTS

Los demas tests comprueban que el programa hace bien lo que se le pide. Este
hace de atacante: construye datos DISENADOS para pasar todas las puertas y
producir un numero plausible y equivocado.

La diferencia importa porque los dos encuentran cosas distintas. Un test normal
prueba las hipotesis de quien lo escribio; un ataque prueba las que no se le
ocurrieron. Los datos rotos evidentes —un precio negativo, un cierre por encima
del maximo, una fecha del ano que viene— ya los para todo. Los peligrosos son
los que no rompen ninguna regla: series impecables que describen un mercado que
no existe.

LOS TRES ATAQUES SE COLARON ENTEROS LA PRIMERA VEZ

Los tres pasaban las diez comprobaciones de calidad sin que ninguna dijera nada,
y los tres llegaban al ranking. Las defensas que hay ahora —`serie_sin_ruido` y
`volumen_cambia_de_escala`— se escribieron a partir de aqui, no al reves.

POR QUE AVISAN Y NO BLOQUEAN

Es una leccion aprendida dos veces en este proyecto: parar el calculo entero
porque cuatro valores tienen datos raros castiga a los 597 que estan bien, y
acaba con alguien desactivando la comprobacion. La proteccion por valor ya
existe donde importa —sin ATR el riesgo veta la orden— asi que aqui lo util es
NOMBRAR al valor sospechoso, no apagar el programa.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from stocks_tracker.core import quality
from stocks_tracker.trading import sizing

HOY = date(2026, 8, 20)


def _serie(ticker: str, cierres: list[float], volumen=5_000_000) -> pd.DataFrame:
    filas = []
    n = len(cierres)
    for i, c in enumerate(cierres):
        v = volumen[i] if isinstance(volumen, list) else volumen
        filas.append({
            "ticker": ticker, "date": HOY - timedelta(days=n - 1 - i),
            "open": c, "high": c * 1.001, "low": c * 0.999, "close": c,
            "adj_close": c, "volume": v, "source": "yfinance",
        })
    return pd.DataFrame(filas)


def _checks(precios: pd.DataFrame) -> set[str]:
    return {h.check for h in quality.evaluar(precios, hoy=HOY)}


def _detalle(precios: pd.DataFrame, check: str) -> str:
    return next(h.detail for h in quality.evaluar(precios, hoy=HOY)
                if h.check == check)


# ---------------------------------------------------------------------------
# Ataque 1: la serie perfecta
# ---------------------------------------------------------------------------
def _fabricada(diario: float = 0.004, sesiones: int = 300) -> pd.DataFrame:
    cierres = [100.0]
    for _ in range(sesiones - 1):
        cierres.append(cierres[-1] * (1 + diario))
    return _serie("PERFECT", cierres)


def test_una_serie_inventada_no_pasa_por_buena():
    """EL ATAQUE QUE MAS DANO HARIA.

    Subir un 0,4 % exacto cada dia no viola nada: el OHLC es coherente, no hay
    ningun salto de x10, no faltan sesiones, no hay nulos y las fechas son
    perfectas. Y el resultado es el mejor momento del universo, el menor
    drawdown y la volatilidad mas baja: primer puesto del ranking. Ademas, con
    un ATR minimo el dimensionamiento por volatilidad concede la posicion mas
    grande que permite el mandato.

    O sea: la mayor apuesta de la cartera sobre datos que nadie ha comprobado.
    """
    checks = _checks(_fabricada())

    assert "serie_sin_ruido" in checks, (
        "una serie inventada pasa las diez comprobaciones de calidad y llega al "
        "ranking con el mejor momento del universo"
    )


def test_lo_que_la_delata_se_dice_en_el_aviso():
    """Un aviso que no explica que mirar entrena a ignorar los avisos."""
    detalle = _detalle(_fabricada(), "serie_sin_ruido")

    assert "1 retornos distintos" in detalle
    assert "fabricada" in detalle


def test_una_subida_igual_de_fuerte_pero_con_ruido_no_se_toca():
    """La contraprueba, y es la mitad que decide si esto sirve.

    Lo sospechoso NO es subir mucho: eso lo hacen los valores que interesan. Es
    subir sin ruido. Una comprobacion que ademas senalara a los ganadores de
    verdad seria un filtro de momento disfrazado de control de calidad, y
    acabaria desconectada la primera semana.
    """
    rng = np.random.default_rng(11)
    cierres = [100.0]
    for _ in range(299):
        cierres.append(round(cierres[-1] * (1 + 0.004 + rng.normal(0, 0.012)), 2))

    assert "serie_sin_ruido" not in _checks(_serie("REAL", cierres))
    # Y de verdad sube tanto como la inventada.
    assert cierres[-1] > cierres[0] * 2


# ---------------------------------------------------------------------------
# Ataque 2: el volumen que miente
# ---------------------------------------------------------------------------
def test_un_cambio_de_unidad_en_el_volumen_se_detecta():
    """El unico filtro que impide comprar algo que despues no se puede vender es
    el minimo de liquidez, y se calcula con el volumen. Nadie comprobaba el
    volumen.

    Un proveedor que cambia de unidad —acciones a lotes, o al reves— o que cruza
    el volumen de un ticker con el de otro multiplica esa cifra por cien sin
    tocar ni un precio. La serie sigue impecable, y un valor que negocia treinta
    mil euros al dia parece negociar tres millones.
    """
    cierres = [round(100.0 + i * 0.05, 2) for i in range(300)]
    volumen = [30_000] * 150 + [3_000_000] * 150

    checks = _checks(_serie("UNIDAD", cierres, volumen))

    assert "volumen_cambia_de_escala" in checks


def test_el_escalon_se_ve_aunque_haya_ocurrido_hace_meses():
    """Comparando solo los dos ultimos trimestres, un cambio de unidad de hace
    medio ano queda DENTRO de los dos lados de la comparacion: los dos dicen
    tres millones y el cociente sale uno. El escalon sigue ahi y sigue
    falseando el filtro de liquidez todos los dias."""
    cierres = [round(100.0 + i * 0.05, 2) for i in range(400)]
    # El salto ocurre en la sesion 100 de 400: muy lejos del tramo final.
    volumen = [30_000] * 100 + [3_000_000] * 300

    assert "volumen_cambia_de_escala" in _checks(_serie("VIEJO", cierres, volumen))


def test_un_trimestre_movido_no_es_un_cambio_de_escala():
    """LA CONTRAPRUEBA QUE FIJA EL UMBRAL, y la primera version no lo fijaba.

    Escrita con un pico suelto de volumen el dia de resultados, la comprobacion
    seguia pasando aunque se bajara el umbral de x20 a x2: un dia aislado no
    mueve una mediana de tres meses, asi que el test no medía el umbral, solo su
    propia inmunidad. Verificado mutandolo.

    Lo que de verdad hay que tolerar es que la MEDIANA respire. Un valor triplica
    su volumen medio entre un trimestre tranquilo y uno movido —una noticia, la
    entrada en un indice, una temporada— y eso pasa todos los anos. Con el
    umbral en x2 esto saltaria cuatro veces al ano por valor, que es la manera
    segura de que alguien acabe apagando la comprobacion.
    """
    cierres = [round(100.0 + i * 0.05, 2) for i in range(480)]
    volumen: list[float] = []
    # El escalon mas fuerte de esta serie es x7 (de 500.000 a 3.500.000), que es
    # el peor caso observado al medir 500 series simuladas. Fija el umbral por
    # abajo: si alguien lo bajara a x4 o a x2, este test lo dice.
    for nivel in (1_000_000, 3_000_000, 1_200_000, 500_000, 3_500_000,
                  900_000, 2_500_000, 1_500_000):
        volumen += [float(nivel)] * 60

    assert "volumen_cambia_de_escala" not in _checks(
        _serie("TRIMESTRES", cierres, volumen))


def test_un_volumen_que_crece_solo_no_salta():
    """Otra contraprueba: un valor que en dos anos triplica su volumen lo hace
    poco a poco. Comparar tramos CONTIGUOS es lo que distingue un escalon de un
    crecimiento."""
    cierres = [round(100.0 + i * 0.05, 2) for i in range(500)]
    volumen = list(np.linspace(500_000, 4_000_000, 500))

    assert "volumen_cambia_de_escala" not in _checks(_serie("CRECE", cierres, volumen))


# ---------------------------------------------------------------------------
# Ataque 3: la serie congelada
# ---------------------------------------------------------------------------
def test_una_serie_congelada_no_parece_un_valor_tranquilo():
    """Cuando un proveedor deja de actualizar un valor y repite su ultima barra,
    el programa lo ensena como un valor tranquilo pegado a maximos anuales: sin
    caidas, sin volatilidad y con `dist_52w_high` a cero. Lo que hay es una
    averia de datos."""
    cierres = [round(100.0 + i * 0.05, 2) for i in range(260)] + [113.0] * 40

    detalle = _detalle(_serie("FROZEN", cierres), "serie_sin_ruido")

    assert "CONGELADA" in detalle
    assert "39 sesiones sin moverse" in detalle


def test_congelada_y_fabricada_se_diagnostican_distinto():
    """Las dos tienen poca variedad y se arreglan de forma opuesta: una hay que
    volver a descargarla, la otra hay que dejar de usarla. Un mensaje que las
    confundiera mandaria a reparar lo que no tiene arreglo."""
    fabricada = _detalle(_fabricada(), "serie_sin_ruido")

    assert "CONGELADA" not in fabricada


# ---------------------------------------------------------------------------
# Ataques que el programa YA repelia
# ---------------------------------------------------------------------------
def test_un_atr_de_cero_no_concede_una_posicion_infinita():
    """El ataque mas goloso: con ATR cero, `riesgo / distancia_stop` es una
    division por cero y el tamano teorico, infinito.

    Ya estaba defendido, y se comprueba aqui para que siga estandolo: es el tipo
    de guarda que desaparece en una refactorizacion sin que nada mas se entere.
    """
    r = sizing.size_by_atr(
        equity=1000.0, price=100.0, atr14=0.0, cash_available=1000.0,
        regime="risk_on", risk_per_trade_pct=1.0, atr_stop_mult=2.5,
        max_position_pct=20.0, target_position_pct=15.0, min_cash_pct=5.0,
        min_notional=1.0,
    )

    assert not r.ok
    assert r.reason_code == "NO_SIZING_INPUTS"
    assert r.notional == 0.0


def test_un_atr_diminuto_tampoco_se_lleva_la_cartera_entera():
    """La version que la guarda de arriba NO cubre: un ATR de 0,0001 no es cero,
    asi que pasa el filtro y el tamano teorico se dispara. Lo que lo para es que
    el teorico es solo UNO de los topes, y siempre gana el menor."""
    equity = 1000.0
    r = sizing.size_by_atr(
        equity=equity, price=100.0, atr14=0.0001, cash_available=equity,
        regime="risk_on", risk_per_trade_pct=1.0, atr_stop_mult=2.5,
        max_position_pct=20.0, target_position_pct=15.0, min_cash_pct=5.0,
        min_notional=1.0,
    )

    assert r.ok
    assert r.notional <= equity * 0.20, (
        f"{r.notional:.2f} EUR sobre {equity:.2f} de cartera: el ATR diminuto "
        "ha saltado el tope por activo"
    )
    assert r.capped_by != "riesgo_por_operacion"


def test_las_comprobaciones_nuevas_estan_en_el_registro():
    """Una comprobacion que se ejecuta pero no se registra no se puede auditar
    despues, y en `data_quality` su ausencia se lee igual que "paso": es como
    la puerta de calidad tapaba un hallazgo bloqueante durante semanas."""
    assert "serie_sin_ruido" in quality.COMPROBACIONES_DEL_ALMACEN
    assert "volumen_cambia_de_escala" in quality.COMPROBACIONES_DEL_ALMACEN
