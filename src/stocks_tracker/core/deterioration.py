"""Qué ha cambiado a peor en una posición DESDE QUE LA COMPRASTE.

La diferencia con las banderas rojas de `flags.py` es la referencia. Allí se
compara un valor contra unos umbrales fijos: un payout del 110 % es malo lo
tengas o no. Aquí se compara contra el día en que compraste, y eso cambia la
pregunta: no es "¿está caro?" sino "¿sigue siendo lo que compre?".

Un margen del 14 % no dice gran cosa. Un margen del 14 % en una empresa que
tenía el 22 % cuando la compraste dice que la tesis que te llevo a comprarla ya
no se sostiene, y lo dice antes de que el precio lo refleje del todo.

**Esto no predice nada.** No dice que vaya a bajar ni recomienda vender: dice
que algo concreto ha empeorado y cuanto, con el número delante, para que la
decisión la tomes tu mirando el motivo y no el color. Un valor puede
deteriorarse y subir, y puede estar impecable y caer un 40 %.

Y al reves importa igual: verde significa "se ha mirado y no hay nada", no
"está todo bien". Cuando no hay datos para mirar, el nivel es GRIS y lo dice,
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

# Cuanto tiene que EMPEORAR una condicion respecto al dia de la compra para que
# cuente. Cinco puntos porcentuales: por debajo es el ruido de medir lo mismo
# dos dias distintos, y contarlo convertiria cualquier oscilacion en deterioro.
EMPEORAMIENTO_MINIMO = 0.05

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


GRUPO_PRECIO = "precio"


@dataclass(frozen=True)
class Senal:
    """Un motivo concreto, con el número que lo sostiene.

    `grupo` marca las señales que son LA MISMA NOTICIA contada de varias
    formas. Ver `Diagnostico.puntos`: dentro de un grupo solo puntúa la peor.
    """

    clave: str
    grave: bool
    texto: str
    grupo: str = ""

    @property
    def puntos(self) -> int:
        return 2 if self.grave else 1


@dataclass(frozen=True)
class Diagnostico:
    ticker: str
    senales: list[Senal]
    comparado_con: Any = None       # fecha de referencia, o None
    hay_datos: bool = True
    # Si habia datos del DIA DE LA COMPRA con los que comparar. Sin ellos solo
    # se ejecutan las comprobaciones que miran el presente —payout, caida desde
    # maximos— y las de "ha cambiado a peor" no llegan a correr.
    comparado: bool = True
    # Si el instrumento TIENE fundamentales que mirar. Un ETF, un indice o una
    # cripto no los tienen, y ahi el precio es el unico diagnostico posible.
    # Lo usa el asesor para decidir si una senal de precio puede por si sola
    # cambiar un veredicto. Ver `advice.sobre_una_posicion`.
    con_fundamentales: bool = False

    @property
    def puntos(self) -> int:
        """Los puntos, contando UNA VEZ lo que es una sola noticia.

        EL FALLO QUE ESTO ARREGLA, REPORTADO DESDE EL USO REAL

        Antes esto era `sum(s.puntos for s in self.senales)`. Con eso, una
        empresa de calidad que corrige acumulaba cuatro señales de precio
        —cae desde máximos, pierde la MM200, cruce de la muerte, se queda por
        detrás del índice— y llegaba a ROJO con los fundamentales INTACTOS.

        Reproducido: margen 35 %, ROE 40 %, deuda 0,5x y crecimiento 15 %,
        idénticos al día de la compra. Cuatro puntos, rojo, y el asesor decía
        REDUCIR. Es el caso de MSFT y NVDA que llegó desde el uso real.

        Y no eran cuatro hechos. Cuando una acción cae un 30 % pierde la MM200
        y cruza a la baja POR CONSTRUCCIÓN, y si el índice no cayó igual queda
        por detrás. Son cuatro maneras de decir que el precio bajó. Cuatro
        confirmaciones de lo mismo suben la confianza en que el precio bajó,
        no la evidencia de que la empresa esté peor.

        Ahora dentro de cada grupo puntúa solo la peor señal. Los
        fundamentales siguen sumando entre sí porque el margen, la deuda y el
        crecimiento SÍ son hechos distintos sobre el negocio.
        """
        sueltas = sum(s.puntos for s in self.senales if not s.grupo)
        grupos: dict[str, int] = {}
        for s in self.senales:
            if s.grupo:
                grupos[s.grupo] = max(grupos.get(s.grupo, 0), s.puntos)
        return sueltas + sum(grupos.values())

    @property
    def solo_es_precio(self) -> bool:
        """Si todo lo encontrado viene de la cotización y no del negocio.

        Lo usa el asesor: con el horizonte en meses y la regla de vender solo
        si la tesis se rompe, que el precio haya caído no es que la tesis se
        haya roto. Para eso está el stop.
        """
        return bool(self.senales) and all(
            s.grupo == GRUPO_PRECIO for s in self.senales)

    @property
    def nivel(self) -> Nivel:
        if not self.hay_datos:
            return Nivel.GRIS
        if self.puntos >= PUNTOS_ROJO:
            return Nivel.ROJO
        if self.puntos >= PUNTOS_AMBAR:
            return Nivel.AMBAR
        # Sin nada del dia de la compra, las comprobaciones que comparan no han
        # llegado a ejecutarse: decir "sin cambios a peor" seria afirmar que no
        # ha cambiado nada cuando lo unico cierto es que no se ha podido mirar.
        # Es el mismo verde por falta de datos que este modulo existe para
        # evitar, colado por la puerta de atras. Pasa con toda posicion
        # comprada antes de que empezara a guardarse el historico.
        if not self.comparado:
            return Nivel.GRIS
        return Nivel.VERDE

    @property
    def graves(self) -> list[Senal]:
        return [s for s in self.senales if s.grave]


# ---------------------------------------------------------------------------
# Lectura defensiva
# ---------------------------------------------------------------------------
def _crudo(origen: Any, campo: str) -> Any:
    """El valor tal cual, o None si no hay forma de leerlo.

    Los ausentes llegan de tres formas distintas según por donde vengan:
    `None` en un diccionario escrito a mano, `float('nan')` de numpy y `pd.NA`
    de una columna booleana de DuckDB. Los tres significan lo mismo y aquí se
    unifican; `pd.NA` además revienta con `TypeError` en cuanto se le aplica un
    `bool()`, así que colarse tumbaría la página de la cartera entera.
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
    """Un número utilizable, o None.

    Todo lo que sigue depende de esto: un `NaN` que se colara como número
    haría que las comparaciones dieran False en silencio y el semáforo saldría
    verde por no haber podido mirar. Aquí `None` significa "no se sabe" y quien
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
    """Lo que ha empeorado en el negocio, no en la cotización.

    Es la parte que llega antes: los márgenes se estrechan y la deuda sube
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
                f"La rentabilidad sobre recursos propios ha caído un "
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
    """Lo que ha empeorado en la cotización DESDE QUE COMPRASTE.

    Llega más tarde que los fundamentales, pero es lo único disponible cuando
    no hay balances que mirar —índices, ETF, cripto—.

    LO QUE YA ERA CIERTO EL DÍA QUE COMPRASTE NO ES UN DETERIORO

    Este bloque medía el PRESENTE y lo presentaba como un cambio a peor. Con el
    mismo estado de hoy, comprar en máximos daba 5 señales y comprar en el
    suelo —cuando la acción ya estaba peor— daba 4. Deberían ser 0 en el
    segundo caso: si compraste algo que ya caía un 45 % y hoy cae un 35 %, ha
    MEJORADO desde tu compra.

    Ahora cada condición se compara contra la del día de la compra donde hay
    dato, y donde no lo hay se dice que es una condición del presente. Sin la
    foto de entonces se sigue avisando —es mejor que callar— pero el texto no
    afirma que haya cambiado nada.

    Todas las señales de aquí llevan `grupo=GRUPO_PRECIO`: son la misma noticia
    contada de varias formas y no deben sumarse entre sí. Ver
    `Diagnostico.puntos`.
    """
    fuera: list[Senal] = []

    encima_hoy = _bool(hoy, "above_sma200")
    encima_antes = _bool(entonces, "above_sma200")
    if encima_antes is True and encima_hoy is False:
        fuera.append(Senal(
            "mm200", False,
            "Cotiza por debajo de su media de 200 sesiones; cuando compraste "
            "estaba por encima. Es el cambio de tendencia de fondo más seguido.",
            GRUPO_PRECIO,
        ))

    # El cruce de la muerte solo es noticia si NO estaba ya cruzado al comprar.
    if _bool(hoy, "death_cross") is True and _bool(entonces, "death_cross") is not True:
        fuera.append(Senal(
            "cruce", False,
            "La media de 50 sesiones ha cortado a la baja la de 200 (cruce de "
            "la muerte). Llega tarde por construcción, pero mucha gente lo mira.",
            GRUPO_PRECIO,
        ))

    caida = _num(hoy, "drawdown")
    caida_antes = _num(entonces, "drawdown")
    if caida is not None and caida <= CAIDA_VIGILAR:
        # Si ya caía tanto o más cuando compraste, no ha empeorado: compraste
        # una acción castigada y sigue castigada. Eso no es un deterioro, es la
        # tesis que tenías.
        empeoro = caida_antes is None or caida < caida_antes - EMPEORAMIENTO_MINIMO
        if empeoro:
            desde = (f" Cuando compraste caía un {_pct(abs(caida_antes))}."
                     if caida_antes is not None else
                     " (No hay dato del día de tu compra con el que comparar.)")
            fuera.append(Senal(
                "caida", caida <= CAIDA_GRAVE,
                f"Cae un {_pct(abs(caida))} desde su máximo. Para volver al "
                f"máximo tiene que subir un "
                f"{abs(caida) / (1 + caida) * 100:.0f} %: una caída grande "
                f"necesita una subida mayor solo para empatar.{desde}",
                GRUPO_PRECIO,
            ))

    relativa = _num(hoy, "rs_vs_bench_3m")
    relativa_antes = _num(entonces, "rs_vs_bench_3m")
    if relativa is not None and relativa <= RETRASO_INDICE_3M:
        empeoro = (relativa_antes is None
                   or relativa < relativa_antes - EMPEORAMIENTO_MINIMO)
        if empeoro:
            fuera.append(Senal(
                "relativa", False,
                f"Se queda {_pct(abs(relativa))} por detrás de su índice a tres "
                "meses. Si el mercado sube y este no, el problema es de este "
                "valor.",
                GRUPO_PRECIO,
            ))

    vol_corta, vol_larga = _num(hoy, "realized_vol_20"), _num(hoy, "realized_vol_252")
    if (vol_corta is not None and vol_larga is not None and vol_larga > 0
            and vol_corta / vol_larga >= SALTO_VOLATILIDAD):
        # Y tampoco es noticia si ya se movía asi cuando compraste.
        antes_corta = _num(entonces, "realized_vol_20")
        antes_larga = _num(entonces, "realized_vol_252")
        ya_estaba = (antes_corta is not None and antes_larga is not None
                     and antes_larga > 0
                     and antes_corta / antes_larga >= SALTO_VOLATILIDAD)
        if not ya_estaba:
            fuera.append(Senal(
                "volatilidad", False,
                f"Se mueve {vol_corta / vol_larga:.1f} veces más de lo habitual "
                "en el último mes. Que la volatilidad se dispare suele "
                "significar que hay algo que el mercado todavía esta digiriendo.",
                GRUPO_PRECIO,
            ))

    return fuera


SUFIJO_ENTONCES = "_entonces"


def partir(datos: Any) -> tuple[dict, dict]:
    """Parte una fila ancha de `get_position_health` en el hoy y el entonces.

    Vive aqui y no en la pantalla porque ya hay dos sitios que lo necesitan —el
    semaforo de salud y el asesor— y dos copias de esto se separan el dia que
    alguien anada una columna. Ademas es justo el paso donde se comete el error
    que este modulo existe para evitar: pasar la misma fila por los dos lados
    hace que no haya nada que comparar y que todo salga en verde.

    Devuelve `(hoy, entonces)`.
    """
    hoy, entonces = {}, {}
    items = datos.items() if hasattr(datos, "items") else []
    for col, valor in items:
        nombre = str(col)
        if nombre.endswith(SUFIJO_ENTONCES):
            entonces[nombre[: -len(SUFIJO_ENTONCES)]] = valor
        else:
            hoy[nombre] = valor
    return hoy, entonces


def diagnosticar(ticker: str, *, fund_hoy: Any = None, fund_entonces: Any = None,
                 ind_hoy: Any = None, ind_entonces: Any = None,
                 comparado_con: Any = None) -> Diagnostico:
    """Que ha cambiado a peor entre la compra y hoy.

    `*_entonces` son los datos que había el día de la compra, no los de hoy:
    con los de hoy en los dos lados no habría nada que comparar y todo saldría
    verde. Se obtienen con la union punto-en-el-tiempo, la misma que evita que
    el ranking histórico se sepa el futuro.

    Sin datos de hoy no se diagnóstica: el nivel sale GRIS. Sin datos del día
    de la compra se ejecuta lo que solo mira el presente —payout, caída desde
    máximos— y, si eso no encuentra nada, el nivel también sale GRIS: las
    comprobaciones que comparan no han llegado a correr, así que "sin cambios a
    peor" sería afirmar algo que nadie ha comprobado.
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
    comparado = any(
        _num(fund_entonces, campo) is not None for campo in CAMPOS_FUNDAMENTALES
    ) or any(
        _num(ind_entonces, campo) is not None
        or _bool(ind_entonces, campo) is not None
        for campo in CAMPOS_PRECIO
    )
    con_fundamentales = any(
        _num(fund_hoy, campo) is not None for campo in CAMPOS_FUNDAMENTALES
    )
    return Diagnostico(ticker=ticker, senales=senales,
                       comparado_con=comparado_con, hay_datos=hay_datos,
                       comparado=comparado,
                       con_fundamentales=con_fundamentales)
