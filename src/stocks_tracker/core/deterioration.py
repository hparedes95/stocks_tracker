"""Que ha cambiado a peor en una posicion DESDE QUE LA COMPRASTE.

La diferencia con las banderas rojas de `flags.py` es la referencia. Alli se
compara un valor contra unos umbrales fijos: un payout del 110 % es malo lo
tengas o no. Aqui se compara contra el dia en que compraste, y eso cambia la
pregunta: no es "¿esta caro?" sino "¿sigue siendo lo que compre?".

Un margen del 14 % no dice gran cosa. Un margen del 14 % en una empresa que
tenia el 22 % cuando la compraste dice que la tesis que te llevo a comprarla ya
no se sostiene, y lo dice antes de que el precio lo refleje del todo.

**Esto no predice nada.** No dice que vaya a bajar ni recomienda vender: dice
que algo concreto ha empeorado y cuanto, con el numero delante, para que la
decision la tomes tu mirando el motivo y no el color. Un valor puede
deteriorarse y subir, y puede estar impecable y caer un 40 %.

Y al reves importa igual: verde significa "se ha mirado y no hay nada", no
"esta todo bien". Cuando no hay datos para mirar, el nivel es GRIS y lo dice,
porque un verde por falta de datos es la peor de las mentiras posibles: la que
tranquiliza.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Umbrales
# ---------------------------------------------------------------------------
# Puestos para que salten con un cambio que un analista describiria como "esto
# ya no es lo mismo", no con el ruido de un trimestre. Un umbral demasiado fino
# convierte el semaforo en una luz siempre encendida, y una luz que siempre
# esta encendida no se mira.

# Margen neto: caidas en PUNTOS porcentuales, no relativas. Pasar del 4 % al
# 3 % es un -25 % relativo y casi nada en absoluto; del 22 % al 14 % son ocho
# puntos y es otra empresa.
CAIDA_MARGEN_VIGILAR_PP = 3.0
CAIDA_MARGEN_GRAVE_PP = 6.0

# ROE: aqui si tiene sentido lo relativo, porque el nivel normal varia mucho
# entre sectores (un banco y una tecnologica no comparten escala).
CAIDA_ROE_RELATIVA = 0.30

# Crecimiento de ingresos: la senal fuerte es el cambio de signo. Una empresa
# que crecia y ha dejado de crecer es un caso distinto de una que crece menos.
CAIDA_CRECIMIENTO_VIGILAR_PP = 10.0

# Deuda: subir apalancamiento solo preocupa de verdad cuando el nivel ya es
# alto. Pasar de 0,5x a 1,2x es ruido; de 3x a 4x es otra cosa.
SUBIDA_DEUDA_X = 1.0
DEUDA_ALTA_X = 3.0

# Dividendo: pagar mas de lo que se gana no es sostenible por definicion. Se
# deja margen porque un trimestre malo puede pasar del 100 % sin que se recorte.
PAYOUT_INSOSTENIBLE = 1.10

# Precio: la caida desde maximos y el retraso frente al indice.
CAIDA_VIGILAR = -0.30
CAIDA_GRAVE = -0.50
RETRASO_INDICE_3M = -0.10

# Volatilidad: que la de las ultimas 20 sesiones doble a la del ano significa
# que al mercado le ha pasado algo con este valor.
SALTO_VOLATILIDAD = 2.0

# Puntos para encender cada color. `grave` vale 2 y `vigilar` vale 1, asi que
# hacen falta dos motivos graves —o uno grave y dos leves— para el rojo. Un
# solo motivo, por serio que sea, deja el semaforo en ambar: merece mirarlo, no
# es una alarma.
#
# El ambar salta con UN punto, no con dos, para que el verde signifique
# exactamente "no se ha encontrado nada". Con el umbral en dos, una posicion
# con un motivo real salia verde y el motivo quedaba escondido detras del color
# que precisamente invita a no mirar.
PUNTOS_ROJO = 4
PUNTOS_AMBAR = 1


# Los campos que mira cada bloque. Sirven para distinguir "se ha mirado y no
# hay nada" de "no habia nada que mirar", que es la distincion que justifica
# que exista el color gris.
CAMPOS_FUNDAMENTALES = ("profit_margin", "roe", "revenue_growth_yoy",
                        "net_debt_to_ebitda", "payout_ratio")
CAMPOS_PRECIO = ("above_sma200", "death_cross", "drawdown", "rs_vs_bench_3m",
                 "realized_vol_20", "realized_vol_252")


class Nivel(StrEnum):
    VERDE = "verde"
    AMBAR = "ambar"
    ROJO = "rojo"
    GRIS = "gris"        # no hay datos para mirar; no es lo mismo que verde


ETIQUETA = {
    Nivel.VERDE: "Sin cambios a peor",
    Nivel.AMBAR: "Algo ha cambiado",
    Nivel.ROJO: "Varias cosas a peor",
    Nivel.GRIS: "Sin datos para comparar",
}


@dataclass(frozen=True)
class Senal:
    """Un motivo concreto, con el numero que lo sostiene."""

    clave: str
    grave: bool
    texto: str

    @property
    def puntos(self) -> int:
        return 2 if self.grave else 1


@dataclass(frozen=True)
class Diagnostico:
    ticker: str
    senales: list[Senal]
    comparado_con: Any = None       # fecha de referencia, o None
    hay_datos: bool = True

    @property
    def puntos(self) -> int:
        return sum(s.puntos for s in self.senales)

    @property
    def nivel(self) -> Nivel:
        if not self.hay_datos:
            return Nivel.GRIS
        if self.puntos >= PUNTOS_ROJO:
            return Nivel.ROJO
        if self.puntos >= PUNTOS_AMBAR:
            return Nivel.AMBAR
        return Nivel.VERDE

    @property
    def graves(self) -> list[Senal]:
        return [s for s in self.senales if s.grave]


# ---------------------------------------------------------------------------
# Lectura defensiva
# ---------------------------------------------------------------------------
def _crudo(origen: Any, campo: str) -> Any:
    """El valor tal cual, o None si no hay forma de leerlo.

    Los ausentes llegan de tres formas distintas segun por donde vengan:
    `None` en un diccionario escrito a mano, `float('nan')` de numpy y `pd.NA`
    de una columna booleana de DuckDB. Los tres significan lo mismo y aqui se
    unifican; `pd.NA` ademas revienta con `TypeError` en cuanto se le aplica un
    `bool()`, asi que colarse tumbaria la pagina de la cartera entera.
    """
    if origen is None:
        return None
    try:
        valor = origen.get(campo)
    except AttributeError:
        return None            # no es un mapa: una lista, una cadena, lo que sea
    if valor is None:
        return None
    try:
        if bool(pd.isna(valor)):
            return None
    except (TypeError, ValueError):
        pass                   # no es escalar; se deja pasar y lo filtra quien mira
    return valor


def _num(origen: Any, campo: str) -> float | None:
    """Un numero utilizable, o None.

    Todo lo que sigue depende de esto: un `NaN` que se colara como numero
    haria que las comparaciones dieran False en silencio y el semaforo saldria
    verde por no haber podido mirar. Aqui `None` significa "no se sabe" y quien
    llama tiene que tratarlo como tal.
    """
    valor = _crudo(origen, campo)
    if valor is None:
        return None
    try:
        f = float(valor)
    except (TypeError, ValueError):
        return None
    return f if np.isfinite(f) else None


def _bool(origen: Any, campo: str) -> bool | None:
    valor = _crudo(origen, campo)
    return None if valor is None else bool(valor)


def _pct(x: float) -> str:
    return f"{x * 100:.1f} %"


# ---------------------------------------------------------------------------
# Las comprobaciones
# ---------------------------------------------------------------------------
def _fundamentales(hoy: Any, entonces: Any) -> list[Senal]:
    """Lo que ha empeorado en el negocio, no en la cotizacion.

    Es la parte que llega antes: los margenes se estrechan y la deuda sube
    varios trimestres antes de que el mercado lo descuente del todo.
    """
    fuera: list[Senal] = []

    margen_hoy, margen_antes = _num(hoy, "profit_margin"), _num(entonces, "profit_margin")
    if margen_hoy is not None and margen_antes is not None:
        caida_pp = (margen_antes - margen_hoy) * 100.0
        if caida_pp >= CAIDA_MARGEN_VIGILAR_PP:
            fuera.append(Senal(
                "margen", caida_pp >= CAIDA_MARGEN_GRAVE_PP,
                f"El margen neto ha bajado de {_pct(margen_antes)} a "
                f"{_pct(margen_hoy)}: {caida_pp:.1f} puntos menos que cuando "
                "compraste. Gana menos por cada euro que vende.",
            ))

    roe_hoy, roe_antes = _num(hoy, "roe"), _num(entonces, "roe")
    if roe_hoy is not None and roe_antes is not None and roe_antes > 0:
        caida = (roe_antes - roe_hoy) / roe_antes
        if caida >= CAIDA_ROE_RELATIVA:
            fuera.append(Senal(
                "roe", False,
                f"La rentabilidad sobre recursos propios ha caido un "
                f"{caida * 100:.0f} %, de {_pct(roe_antes)} a {_pct(roe_hoy)}.",
            ))

    crec_hoy = _num(hoy, "revenue_growth_yoy")
    crec_antes = _num(entonces, "revenue_growth_yoy")
    if crec_hoy is not None and crec_antes is not None:
        if crec_antes > 0 and crec_hoy < 0:
            fuera.append(Senal(
                "ingresos", True,
                f"Los ingresos han pasado de crecer un {_pct(crec_antes)} a "
                f"caer un {_pct(abs(crec_hoy))}. No es que crezca menos: es que "
                "ha dejado de crecer.",
            ))
        elif (crec_antes - crec_hoy) * 100.0 >= CAIDA_CRECIMIENTO_VIGILAR_PP:
            fuera.append(Senal(
                "ingresos", False,
                f"El crecimiento de ingresos se ha frenado de {_pct(crec_antes)} "
                f"a {_pct(crec_hoy)}.",
            ))

    deuda_hoy, deuda_antes = (_num(hoy, "net_debt_to_ebitda"),
                              _num(entonces, "net_debt_to_ebitda"))
    if deuda_hoy is not None and deuda_antes is not None:
        if (deuda_hoy - deuda_antes) >= SUBIDA_DEUDA_X and deuda_hoy > DEUDA_ALTA_X:
            fuera.append(Senal(
                "deuda", True,
                f"La deuda neta ha subido de {deuda_antes:.1f} a {deuda_hoy:.1f} "
                "veces el EBITDA. Con la deuda alta y subiendo, una mala racha "
                "deja de ser un mal trimestre.",
            ))

    payout = _num(hoy, "payout_ratio")
    if payout is not None and payout > PAYOUT_INSOSTENIBLE:
        fuera.append(Senal(
            "payout", True,
            f"Reparte el {_pct(payout)} de lo que gana. Un dividendo que no "
            "cubre con beneficios se paga con deuda o con caja, y eso tiene "
            "fecha de caducidad.",
        ))

    return fuera


def _precio(hoy: Any, entonces: Any) -> list[Senal]:
    """Lo que ha empeorado en la cotizacion.

    Llega mas tarde que lo anterior, pero es lo unico disponible cuando no hay
    fundamentales —indices, ETF, cripto—.
    """
    fuera: list[Senal] = []

    encima_hoy = _bool(hoy, "above_sma200")
    encima_antes = _bool(entonces, "above_sma200")
    if encima_antes is True and encima_hoy is False:
        fuera.append(Senal(
            "mm200", False,
            "Cotiza por debajo de su media de 200 sesiones; cuando compraste "
            "estaba por encima. Es el cambio de tendencia de fondo mas seguido.",
        ))

    if _bool(hoy, "death_cross") is True:
        fuera.append(Senal(
            "cruce", False,
            "La media de 50 sesiones ha cortado a la baja la de 200 (cruce de "
            "la muerte). Llega tarde por construccion, pero mucha gente lo mira.",
        ))

    caida = _num(hoy, "drawdown")
    if caida is not None and caida <= CAIDA_VIGILAR:
        fuera.append(Senal(
            "caida", caida <= CAIDA_GRAVE,
            f"Cae un {_pct(abs(caida))} desde su maximo. Para volver al maximo "
            f"tiene que subir un {abs(caida) / (1 + caida) * 100:.0f} %: una "
            "caida grande necesita una subida mayor solo para empatar.",
        ))

    relativa = _num(hoy, "rs_vs_bench_3m")
    if relativa is not None and relativa <= RETRASO_INDICE_3M:
        fuera.append(Senal(
            "relativa", False,
            f"Se queda {_pct(abs(relativa))} por detras de su indice a tres "
            "meses. Si el mercado sube y este no, el problema es de este valor.",
        ))

    vol_corta, vol_larga = _num(hoy, "realized_vol_20"), _num(hoy, "realized_vol_252")
    if (vol_corta is not None and vol_larga is not None and vol_larga > 0
            and vol_corta / vol_larga >= SALTO_VOLATILIDAD):
        fuera.append(Senal(
            "volatilidad", False,
            f"Se mueve {vol_corta / vol_larga:.1f} veces mas de lo habitual en "
            "el ultimo mes. Que la volatilidad se dispare suele significar que "
            "hay algo que el mercado todavia esta digiriendo.",
        ))

    return fuera


def diagnosticar(ticker: str, *, fund_hoy: Any = None, fund_entonces: Any = None,
                 ind_hoy: Any = None, ind_entonces: Any = None,
                 comparado_con: Any = None) -> Diagnostico:
    """Que ha cambiado a peor entre la compra y hoy.

    `*_entonces` son los datos que habia el dia de la compra, no los de hoy:
    con los de hoy en los dos lados no habria nada que comparar y todo saldria
    verde. Se obtienen con la union punto-en-el-tiempo, la misma que evita que
    el ranking historico se sepa el futuro.

    Sin datos de hoy no se diagnostica: el nivel sale GRIS. Sin datos de
    entonces se puede diagnosticar a medias —lo que solo mira el presente, como
    el payout o la caida desde maximos— y se dice comparando con nada.
    """
    senales = _fundamentales(fund_hoy, fund_entonces) + _precio(ind_hoy, ind_entonces)
    # Haber encontrado algo ya demuestra que habia datos. Sin esa salida
    # rapida, una posicion con motivos de sobra salia GRIS si ninguno de ellos
    # estaba en la lista de campos que se sondean.
    hay_datos = bool(senales) or any(
        _num(fund_hoy, campo) is not None for campo in CAMPOS_FUNDAMENTALES
    ) or any(
        _num(ind_hoy, campo) is not None or _bool(ind_hoy, campo) is not None
        for campo in CAMPOS_PRECIO
    )
    return Diagnostico(ticker=ticker, senales=senales,
                       comparado_con=comparado_con, hay_datos=hay_datos)
