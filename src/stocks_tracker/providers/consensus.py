"""Contrastar el mismo precio entre varios proveedores.

La cadena de proveedores (`chain.py`) sirve para OTRA cosa: si Yahoo falla,
prueba Stooq. Es tolerancia a fallos, no verificacion. Un proveedor que
responde con un numero equivocado pasa la cadena sin despeinarse, porque la
cadena solo pregunta "¿ha respondido alguien?".

Esto responde a la pregunta que falta: "¿dicen lo mismo?".

QUE SE COMPARA, Y POR QUE NO ES OBVIO

Se compara `close`, el precio cotizado sin ajustar. NUNCA `adj_close`, y esto
es lo primero que hay que entender del modulo:

- Stooq ajusta por splits pero NO por dividendos.
- Yahoo ajusta por los dos.

O sea que sus `adj_close` NO son la misma magnitud. Comparandolos, cada valor
que haya pagado un dividendo en su historia sale como discrepancia, que son
casi todos: el detector se pasaria el dia gritando y se acabaria apagando.

El `close` sin ajustar, en cambio, es un hecho: el precio al que se cruzaron
ordenes ese dia en ese mercado. Dos proveedores serios tienen que coincidir al
centimo, y cuando no coinciden hay algo que mirar de verdad.

QUE PASA CUANDO NO COINCIDEN

Lo que NO se hace es quedarse con el primero y seguir. Eso es exactamente el
comportamiento que hace inutil una comprobacion: si el desacuerdo se resuelve
en silencio, tener tres fuentes no es mejor que tener una.

Con dos fuentes que discrepan no hay forma de saber cual falla, asi que no hay
consenso: INVALIDO, y quien use ese precio se entera.

Con tres o mas se puede hacer algo mejor que rendirse: si dos concuerdan al
centimo y la tercera se va un 5 %, lo razonable no es declarar el precio
inconocible, es decir "estas dos coinciden, esta otra se sale, y se llama asi".
Se toma la mediana del grupo mayoritario y se NOMBRA a la discrepante. No es
silencio —queda registrada, con nombre y magnitud— pero tampoco es paralisis.

Aqui me aparto a proposito del plan, que pedia INVALIDO en cuanto una fuente se
saliera. Con proveedores gratuitos y valores europeos, la tercera fuente se
sale a menudo por motivos aburridos (cotiza en otro mercado, otro huso, otra
divisa), y una regla que invalida el precio cada vez que eso ocurre deja el
sistema sin senales por ruido de infraestructura. Un detector que salta siempre
acaba desconectado, y entonces no detecta nada.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum

import numpy as np
import pandas as pd

# Fraccion del precio por debajo de la cual dos proveedores "dicen lo mismo".
# Dos fuentes serias sobre el mismo mercado y el mismo dia deberian coincidir
# al centimo (1e-4 sobre una accion de 100). El margen es mas ancho que eso
# porque el redondeo y las divisas mueven el ultimo decimal.
#
# ESTOS DOS NUMEROS NO ESTAN CALIBRADOS CONTRA DATOS REALES. No se puede desde
# aqui: el almacen de desarrollo es sintetico y no hay salida a Yahoo. Son un
# punto de partida razonado, y el proceso de auditoria guarda la dispersion
# observada de cada comparacion justamente para poder ajustarlos con datos.
TOLERANCIA_ACUERDO = 0.005      # 0,5 %: por debajo, concuerdan
MAX_DISCREPANCIA = 0.02         # 2 %: por encima, son datos incompatibles


class Veredicto(StrEnum):
    """Cuanta confianza merece un precio, y por que."""

    VERIFICADO = "verificado"      # dos o mas fuentes concuerdan
    AVISO = "aviso"                # hay mayoria, pero alguna fuente se sale
    DEGRADADO = "degradado"        # una sola fuente: no hay contra que contrastar
    INVALIDO = "invalido"          # sin mayoria: no se sabe cual es el bueno
    DESCONOCIDO = "desconocido"    # ninguna fuente ha servido el dato


# Los que NO permiten usar el precio para decidir nada. Se define aqui y no en
# cada sitio que lo consulte: la lista vivio duplicada en el gestor de riesgo y
# en el dashboard, y el dia que se anada un veredicto nuevo hay que acordarse
# de un solo sitio.
NO_OPERABLES = frozenset({Veredicto.INVALIDO, Veredicto.DESCONOCIDO})

SEMAFORO = {
    Veredicto.VERIFICADO: "🟢",
    Veredicto.AVISO: "🟡",
    Veredicto.DEGRADADO: "🟠",
    Veredicto.INVALIDO: "🔴",
    Veredicto.DESCONOCIDO: "⚪",
}


@dataclass(frozen=True)
class Consenso:
    """El veredicto sobre un (ticker, fecha), con todo lo que lo sostiene.

    `por_fuente` se guarda entero a proposito, tambien cuando todo concuerda:
    sin los numeros de partida, el veredicto es una opinion que no se puede
    comprobar despues, que es justo lo que este modulo existe para evitar.
    """

    ticker: str
    fecha: date
    valor: float | None
    veredicto: Veredicto
    dispersion: float
    por_fuente: dict[str, float] = field(default_factory=dict)
    discrepantes: tuple[str, ...] = ()

    @property
    def n_fuentes(self) -> int:
        return len(self.por_fuente)

    @property
    def operable(self) -> bool:
        return self.veredicto not in NO_OPERABLES

    def describir(self) -> str:
        icono = SEMAFORO[self.veredicto]
        if not self.por_fuente:
            return f"{icono} {self.ticker} {self.fecha}: ninguna fuente."
        detalle = ", ".join(f"{k} {v:,.4g}" for k, v in sorted(self.por_fuente.items()))
        cola = ""
        if self.discrepantes:
            cola = f" Se sale: {', '.join(self.discrepantes)}."
        return (
            f"{icono} {self.ticker} {self.fecha}: {detalle}. "
            f"Dispersion {self.dispersion:.3%}.{cola}"
        )


def dispersion(valores: list[float]) -> float:
    """Cuanto se separan entre si, en fraccion del nivel del precio.

    Se divide por la MEDIANA y no por la media: con tres fuentes y una
    disparatada, la media se va con ella y la dispersion sale artificialmente
    pequena justo en el caso que hay que cazar.
    """
    if len(valores) < 2:
        return 0.0
    centro = float(np.median(valores))
    if not np.isfinite(centro) or centro == 0:
        return float("inf")
    return float((max(valores) - min(valores)) / abs(centro))


def _mayoria(por_fuente: dict[str, float],
             tolerancia: float) -> tuple[list[str], list[str]]:
    """El grupo mas grande de fuentes que concuerdan, y las que se quedan fuera.

    Agrupar en una dimension es mas simple de lo que parece: ordenados los
    valores, cualquier grupo que concuerde es un tramo CONTIGUO de esa lista.
    Asi que basta con probar todos los tramos y quedarse con el mas largo cuyo
    ancho relativo cabe en la tolerancia; no hace falta ningun algoritmo de
    clustering.

    Ante dos tramos del mismo tamano gana el primero, y da IGUAL cual sea. No
    es una decision que se pueda observar desde fuera: quien llama solo usa este
    grupo cuando es mayoria, y mayoria es *mas de la mitad*, asi que solo puede
    haber uno. Cuando hay empate no hay mayoria y el veredicto es INVALIDO
    tomase el tramo que se tomase.

    Lo escribo porque la primera version llevaba un criterio de desempate —"gana
    el mas apretado"— con un comentario explicando que servia para que el
    resultado fuera reproducible. Era falso: el resultado ya era el mismo. Al
    mutarlo no cambiaba ningun test, que es como se vio.
    """
    orden = sorted(por_fuente.items(), key=lambda kv: kv[1])
    nombres = [k for k, _ in orden]
    valores = [v for _, v in orden]
    n = len(orden)

    mejor: tuple[int, int, int] = (0, 0, 0)
    for i in range(n):
        for j in range(i, n):
            if dispersion(valores[i : j + 1]) > tolerancia:
                continue
            if j - i + 1 > mejor[0]:
                mejor = (j - i + 1, i, j)

    _, ini, fin = mejor
    dentro = nombres[ini : fin + 1]
    fuera = [k for k in nombres if k not in set(dentro)]
    return dentro, fuera


def evaluar(ticker: str, fecha: date, por_fuente: dict[str, float | None],
            *, tolerancia: float = TOLERANCIA_ACUERDO,
            maxima: float = MAX_DISCREPANCIA) -> Consenso:
    """El veredicto sobre un precio, a partir de lo que dijo cada proveedor.

    `por_fuente` admite None: un proveedor que no supo servir ese dia no es lo
    mismo que uno que sirvio un numero raro, y contarlo como fuente inflaria el
    numero de fuentes sin aportar ninguna verificacion.
    """
    limpio = {
        nombre: float(valor)
        for nombre, valor in por_fuente.items()
        if valor is not None and np.isfinite(float(valor)) and float(valor) > 0
    }

    if not limpio:
        return Consenso(ticker, fecha, None, Veredicto.DESCONOCIDO, 0.0, {})

    if len(limpio) == 1:
        # Un solo proveedor no es "verificado" por mucho que el numero parezca
        # bueno: no se ha contrastado con nada. Llamarlo verificado seria la
        # mentira mas facil de colar de todo el modulo.
        (nombre, valor), = limpio.items()
        return Consenso(ticker, fecha, valor, Veredicto.DEGRADADO, 0.0,
                        {nombre: valor})

    total = dispersion(list(limpio.values()))
    dentro, fuera = _mayoria(limpio, tolerancia)

    # Mayoria ESTRICTA. Con cuatro fuentes partidas dos y dos no hay mayoria, y
    # quedarse con una de las dos parejas seria echarlo a suertes.
    hay_mayoria = len(dentro) * 2 > len(limpio)

    if not hay_mayoria:
        return Consenso(ticker, fecha, None, Veredicto.INVALIDO, total, limpio,
                        tuple(sorted(limpio)))

    valor = float(np.median([limpio[n] for n in dentro]))

    if not fuera:
        # Todas concuerdan. Aun asi puede quedar por encima del maximo si la
        # tolerancia se configuro mas ancha que el maximo, que es una
        # contradiccion; se respeta el maximo, que es la regla mas dura.
        veredicto = (Veredicto.VERIFICADO if total <= maxima else Veredicto.AVISO)
        return Consenso(ticker, fecha, valor, veredicto, total, limpio)

    return Consenso(ticker, fecha, valor, Veredicto.AVISO, total, limpio,
                    tuple(sorted(fuera)))


def comparar(observaciones: pd.DataFrame, *, columna: str = "close",
             tolerancia: float = TOLERANCIA_ACUERDO,
             maxima: float = MAX_DISCREPANCIA) -> pd.DataFrame:
    """Evalua un lote de (ticker, fecha) con las lecturas de varias fuentes.

    `observaciones` trae una fila por (ticker, fecha, source). La columna por
    defecto es `close` y no `adj_close` por el motivo de la cabecera del
    modulo: los ajustados de Yahoo y Stooq no son la misma magnitud.
    """
    columnas = ["ticker", "fecha", "valor", "veredicto", "dispersion",
                "n_fuentes", "por_fuente", "discrepantes"]
    if observaciones.empty:
        return pd.DataFrame(columns=columnas)

    faltan = {"ticker", "date", "source", columna} - set(observaciones.columns)
    if faltan:
        raise ValueError(f"faltan columnas en las observaciones: {sorted(faltan)}")

    datos = observaciones.copy()
    datos["date"] = pd.to_datetime(datos["date"]).dt.date
    datos[columna] = pd.to_numeric(datos[columna], errors="coerce")

    filas = []
    for (ticker, fecha), grupo in datos.groupby(["ticker", "date"], sort=True):
        # Si un proveedor manda dos filas para el mismo dia, la ultima gana:
        # sumarlas o promediarlas inventaria un precio que nadie publico.
        lecturas = {
            str(r.source): (None if pd.isna(getattr(r, columna)) else float(getattr(r, columna)))
            for r in grupo.itertuples()
        }
        c = evaluar(str(ticker), fecha, lecturas, tolerancia=tolerancia, maxima=maxima)
        filas.append({
            "ticker": c.ticker, "fecha": c.fecha, "valor": c.valor,
            "veredicto": str(c.veredicto), "dispersion": c.dispersion,
            "n_fuentes": c.n_fuentes, "por_fuente": c.por_fuente,
            "discrepantes": ", ".join(c.discrepantes),
        })
    return pd.DataFrame(filas, columns=columnas)


def resumen(consensos: pd.DataFrame) -> dict[str, int]:
    """Cuantos de cada veredicto, para la consola y el panel de integridad."""
    if consensos.empty:
        return {}
    return consensos["veredicto"].value_counts().to_dict()
