"""El diario de decisiones: por que compraste, escrito antes de saber el final.

El sesgo retrospectivo no se corrige con buena intencion. Cuando algo sale
bien, el recuerdo del motivo se reescribe solo para que encaje —"ya decia yo
que"— y se aprende una leccion que nunca ocurrio. Cuando sale mal, pasa lo
mismo al reves. La unica defensa conocida es dejarlo por escrito ANTES y
releerlo despues sin retocarlo.

De ahi las tres reglas que gobiernan este modulo:

1. **El resultado no es el veredicto.** Que una compra suba no dice que la
   decision fuera buena: dice que subio. Aqui se calcula el resultado —un
   numero— y se ofrecen los cuatro veredictos, pero NINGUNA funcion deduce el
   veredicto del resultado. Esa parte la tiene que poner una persona, porque es
   justo la que el sesgo se lleva por delante.

2. **Las decisiones de no hacer nada cuentan.** No comprar y esperar son la
   mitad de las decisiones que se toman y no dejan rastro en ninguna parte. Un
   diario que solo guarda compras solo puede registrar aciertos.

3. **La foto del momento no se teclea.** Precio, percentil, RSI y regimen se
   guardan solos: es lo que de verdad se sabia ese dia, no lo que se recuerda
   haber sabido.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum

HORIZONTE_POR_DEFECTO = 90

# Margen despues del horizonte antes de pedir la revision. Sin el, una decision
# a 90 dias aparece para revisar el dia 90 y nunca antes: el aviso llega tarde
# para lo unico que sirve, que es releer lo escrito.
DIAS_DE_GRACIA = 0


class Accion(StrEnum):
    COMPRAR = "comprar"
    VENDER = "vender"
    NO_COMPRAR = "no_comprar"
    ESPERAR = "esperar"


ETIQUETA_ACCION = {
    Accion.COMPRAR: "Compre",
    Accion.VENDER: "Vendi",
    Accion.NO_COMPRAR: "Decidi NO comprar",
    Accion.ESPERAR: "Decidi esperar",
}

# Las decisiones en las que ganar el valor es MALO para ti: no lo tienes, o lo
# acabas de soltar. El signo se invierte y es el error mas facil de cometer:
# sin invertirlo, no comprar algo que se desploma saldria como un fracaso.
ACCIONES_INVERSAS = frozenset({Accion.VENDER, Accion.NO_COMPRAR, Accion.ESPERAR})


class Veredicto(StrEnum):
    ACIERTO = "acierto"
    SUERTE = "suerte"
    MALA_SUERTE = "mala_suerte"
    ERROR = "error"


# El 2x2 que hace que esto ensene algo: resultado (bien/mal) contra motivo (el
# que dijiste / otro). Sin separar las dos cosas, un acierto por suerte se
# archiva como metodo que funciona y se repite hasta que deja de funcionar.
DESCRIPCION_VEREDICTO = {
    Veredicto.ACIERTO: (
        "Salio bien Y por el motivo que escribiste",
        "El proceso funciono. Es el unico caso en el que repetirlo tiene sentido.",
    ),
    Veredicto.SUERTE: (
        "Salio bien pero por otra cosa",
        "El resultado no valida el metodo. Es el mas peligroso de los cuatro: "
        "se archiva como acierto y se repite hasta que deja de funcionar.",
    ),
    Veredicto.MALA_SUERTE: (
        "Salio mal por algo que no podias saber",
        "El proceso esta bien. Cambiarlo por este resultado seria aprender lo "
        "contrario de lo que paso.",
    ),
    Veredicto.ERROR: (
        "Salio mal por algo que estaba delante",
        "Aqui es donde se aprende. Merece la pena escribir que se paso por alto.",
    ),
}

BUENOS = frozenset({Veredicto.ACIERTO, Veredicto.SUERTE})
PROCESO_BUENO = frozenset({Veredicto.ACIERTO, Veredicto.MALA_SUERTE})


@dataclass(frozen=True)
class Entrada:
    """Una decision registrada. Lo escrito no se toca; la revision se anade."""

    id: str
    created_at: datetime
    ticker: str
    accion: Accion
    tesis: str = ""
    que_me_haria_salir: str = ""
    horizonte_dias: int = HORIZONTE_POR_DEFECTO
    conviccion: int = 3
    precio: float | None = None
    precio_mercado: float | None = None
    revisado_at: datetime | None = None
    veredicto: Veredicto | None = None
    nota_revision: str = ""

    @property
    def fecha(self) -> date:
        return self.created_at.date() if hasattr(self.created_at, "date") \
            else self.created_at

    @property
    def revisada(self) -> bool:
        return self.veredicto is not None

    def dias_desde(self, hoy: date | None = None) -> int:
        return ((hoy or date.today()) - self.fecha).days

    def vence_el(self) -> date:
        return self.fecha + timedelta(days=max(0, self.horizonte_dias))

    def toca_revisar(self, hoy: date | None = None) -> bool:
        """Si ya se cumplio el plazo que TU te diste y sigue sin revisar."""
        if self.revisada:
            return False
        return (hoy or date.today()) >= self.vence_el() + timedelta(
            days=DIAS_DE_GRACIA)

    # -- El resultado, que NO es el veredicto -------------------------------
    def movimiento(self, precio_hoy: float | None) -> float | None:
        """Cuanto se ha movido el valor desde el dia de la decision."""
        if precio_hoy is None or self.precio is None or self.precio <= 0:
            return None
        if not math.isfinite(precio_hoy) or not math.isfinite(self.precio):
            return None
        return precio_hoy / self.precio - 1.0

    def resultado(self, precio_hoy: float | None) -> float | None:
        """Lo que la DECISION te ha dado, con el signo que le corresponde.

        Para una compra es lo que subio el valor. Para una venta o un "no
        compro" es lo contrario: si el valor se desploma despues de que decidas
        no comprarlo, la decision fue buena aunque el numero del valor sea
        negativo. Sin invertir el signo, esquivar una ruina se archivaria como
        fracaso.
        """
        movido = self.movimiento(precio_hoy)
        if movido is None:
            return None
        return -movido if self.accion in ACCIONES_INVERSAS else movido

    def resultado_relativo(self, precio_hoy: float | None,
                           mercado_hoy: float | None) -> float | None:
        """El resultado descontando lo que hizo el mercado en el mismo periodo.

        Comprar bien en un mercado que sube un 30 % no demuestra nada, y no
        comprar en uno que cae tampoco. Es el mismo motivo que en la atribucion:
        sin descontar la marea, cada decision se califica por el ano que le toco.
        """
        propio = self.resultado(precio_hoy)
        if propio is None or mercado_hoy is None or self.precio_mercado is None:
            return None
        if self.precio_mercado <= 0 or not math.isfinite(mercado_hoy):
            return None
        mercado = mercado_hoy / self.precio_mercado - 1.0
        if self.accion in ACCIONES_INVERSAS:
            mercado = -mercado
        return propio - mercado


def pendientes(entradas: list[Entrada], hoy: date | None = None) -> list[Entrada]:
    """Las que ya cumplieron su plazo y siguen sin revisar, la mas vieja antes."""
    return sorted((e for e in entradas if e.toca_revisar(hoy)),
                  key=lambda e: e.vence_el())


@dataclass(frozen=True)
class Balance:
    """Lo que dice el diario cuando ya hay unas cuantas revisiones."""

    revisadas: list[Entrada]

    @property
    def total(self) -> int:
        return len(self.revisadas)

    def _cuenta(self, veredicto: Veredicto) -> int:
        return sum(1 for e in self.revisadas if e.veredicto is veredicto)

    @property
    def reparto(self) -> dict:
        return {v: self._cuenta(v) for v in Veredicto}

    @property
    def buenos_resultados(self) -> int:
        return sum(1 for e in self.revisadas if e.veredicto in BUENOS)

    @property
    def buen_proceso(self) -> int:
        return sum(1 for e in self.revisadas if e.veredicto in PROCESO_BUENO)

    @property
    def por_suerte(self) -> float | None:
        """Que fraccion de lo que salio bien fue por otra cosa.

        Es el numero mas incomodo del diario y el que mas ensena: si la mayoria
        de los aciertos son por motivos que no habias escrito, lo que funciona
        no es el metodo.
        """
        if not self.buenos_resultados:
            return None
        return self._cuenta(Veredicto.SUERTE) / self.buenos_resultados


def calibracion_por_conviccion(revisadas: list[Entrada]) -> dict:
    """Si tus decisiones muy convencidas salen mejor que las dudosas.

    Si no salen mejor, la conviccion no esta midiendo nada y conviene saberlo:
    es lo que hace apostar mas fuerte justo donde no toca.
    """
    fuera: dict = {}
    for nivel in (1, 2, 3, 4, 5):
        grupo = [e for e in revisadas if e.conviccion == nivel]
        if grupo:
            fuera[nivel] = {
                "n": len(grupo),
                "acierta": sum(1 for e in grupo if e.veredicto in BUENOS) / len(grupo),
                "proceso": sum(1 for e in grupo
                               if e.veredicto in PROCESO_BUENO) / len(grupo),
            }
    return fuera
