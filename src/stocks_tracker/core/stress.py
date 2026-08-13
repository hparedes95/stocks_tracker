"""Que le habria pasado a TU cartera en caidas que de verdad ocurrieron.

No hay modelo ni simulacion: para cada escenario se toma lo que de verdad hizo
cada valor entre dos fechas reales y se aplica a los pesos de hoy. Un modelo
con betas normales dice siempre la misma mentira comoda —en las caidas fuertes
las correlaciones se van hacia uno y las betas de tiempos tranquilos se quedan
cortas—, y se equivoca justo en la direccion que tranquiliza.

**Esto no es el peor caso.** El peor caso siempre es peor que lo peor que ha
pasado: en 2007 nadie tenia 2008 en su lista de escenarios. Sirve para ver
donde esta concentrado el riesgo, no para poner un suelo a las perdidas.

Y no es una prediccion de nada. Tu cartera de hoy no existia entonces; lo que
se calcula es "si aquello volviera a pasar igual, con lo que llevo ahora".
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from functools import lru_cache
from typing import Any

import numpy as np

from .config import _load_yaml


@lru_cache(maxsize=1)
def get_stress_config() -> dict[str, Any]:
    return _load_yaml("stress.yaml")


class Fuente(StrEnum):
    """De donde sale el retorno que se aplica a una posicion."""

    PROPIA = "propia"       # el valor cotizaba y tenemos su historico
    SECTOR = "sector"       # no cotizaba: se usa su ETF sectorial
    MERCADO = "mercado"     # ni sector: se usa el indice
    NINGUNA = "ninguna"     # no se puede estimar; queda fuera


ETIQUETA_FUENTE = {
    Fuente.PROPIA: "su propio historico",
    Fuente.SECTOR: "su sector (el valor no cotizaba)",
    Fuente.MERCADO: "el indice (sin sector conocido)",
    Fuente.NINGUNA: "sin datos",
}


@dataclass(frozen=True)
class Escenario:
    id: str
    nombre: str
    desde: date
    hasta: date
    que_paso: str = ""


@dataclass(frozen=True)
class ImpactoPosicion:
    ticker: str
    valor: float               # lo que vale hoy la posicion
    retorno: float             # lo que habria hecho en el escenario
    fuente: Fuente

    @property
    def perdida(self) -> float:
        """En euros de hoy. Negativo es perder."""
        return self.valor * self.retorno

    @property
    def estimado(self) -> bool:
        return self.fuente in (Fuente.SECTOR, Fuente.MERCADO)


@dataclass(frozen=True)
class Impacto:
    escenario: Escenario
    posiciones: list[ImpactoPosicion]
    retorno_mercado: float | None = None

    @property
    def valor_total(self) -> float:
        return sum(p.valor for p in self.posiciones)

    @property
    def perdida(self) -> float:
        return sum(p.perdida for p in self.posiciones)

    @property
    def retorno(self) -> float:
        total = self.valor_total
        return self.perdida / total if total > 0 else 0.0

    @property
    def cobertura(self) -> float:
        """Que fraccion del dinero se estima con el historico del propio valor.

        Es el numero que dice si fiarse: con cobertura baja, esto describe el
        mercado de entonces mas que tu cartera de ahora.
        """
        total = self.valor_total
        if total <= 0:
            return 0.0
        propias = sum(p.valor for p in self.posiciones if p.fuente is Fuente.PROPIA)
        return propias / total

    @property
    def fiable(self) -> bool:
        minimo = float(get_stress_config().get("cobertura_minima", 0.6))
        return self.cobertura >= minimo

    @property
    def peores(self) -> list[ImpactoPosicion]:
        """Las que mas dinero se llevan, en euros y no en porcentaje.

        Un valor que cae un 70 % pesando el 1 % de la cartera duele menos que
        uno que cae un 25 % pesando el 40 %, y es el segundo el que hay que
        mirar.
        """
        return sorted(self.posiciones, key=lambda p: p.perdida)

    @property
    def peor_que_el_mercado(self) -> float | None:
        if self.retorno_mercado is None:
            return None
        return self.retorno - self.retorno_mercado


def frase_peor(res: Impacto) -> str:
    """El titular del peor escenario, con el verbo que corresponde.

    Existe como funcion y no como f-string en la pantalla porque el caso raro
    —que el peor escenario sea una subida— sale mal con un `abs()` delante: la
    frase diria "habria caido un 10,9 %" sobre una cartera que habria ganado.
    Pasa con carteras defensivas y con cualquiera que lleve algo inverso.
    """
    nombre = res.escenario.nombre
    if res.retorno >= 0:
        return (
            f"Ni en el peor de los escenarios con datos (**{nombre}**) habria "
            f"perdido esta cartera: habria ganado un **{res.retorno:.1%}**. "
            "Antes de tomarlo como una buena noticia conviene mirar cuantas "
            "posiciones se han estimado con su sector, y recordar que el peor "
            "caso siempre es peor que lo peor que ha pasado."
        )
    return (
        f"El peor de los escenarios con datos es **{nombre}**: tu cartera de "
        f"hoy habria caido un **{abs(res.retorno):.1%}**, unos "
        f"**{abs(res.perdida):,.0f} EUR**. La pregunta util no es si lo ves "
        "probable, sino si podrias aguantarlo sin vender."
    )


def escenarios() -> list[Escenario]:
    fuera = []
    for crudo in get_stress_config().get("escenarios") or []:
        try:
            fuera.append(Escenario(
                id=str(crudo["id"]), nombre=str(crudo["nombre"]),
                desde=crudo["desde"], hasta=crudo["hasta"],
                que_paso=str(crudo.get("que_paso", "")).strip(),
            ))
        except (KeyError, TypeError):
            continue        # un escenario mal escrito no tumba los demas
    return fuera


def impacto(escenario: Escenario, posiciones: list[dict],
            retornos: dict, retornos_sector: dict,
            retorno_mercado: float | None = None) -> Impacto:
    """Aplica los retornos reales del escenario a los pesos de hoy.

    `posiciones` son diccionarios con `ticker`, `valor` y `sector`. Las tres
    fuentes se prueban en orden de preferencia: el historico del propio valor,
    el de su sector, y el del indice. Una posicion sin ninguna de las tres se
    queda FUERA en vez de contar como que no se movio: suponer cero es la
    unica hipotesis que garantiza equivocarse en la direccion tranquilizadora.
    """
    fuera: list[ImpactoPosicion] = []
    for p in posiciones:
        ticker = str(p.get("ticker", ""))
        valor = float(p.get("valor") or 0.0)
        if valor <= 0:
            continue

        propio = retornos.get(ticker)
        sector = retornos_sector.get(p.get("sector"))
        if propio is not None and math.isfinite(propio):
            retorno, fuente = propio, Fuente.PROPIA
        elif sector is not None and math.isfinite(sector):
            retorno, fuente = sector, Fuente.SECTOR
        elif retorno_mercado is not None and math.isfinite(retorno_mercado):
            retorno, fuente = retorno_mercado, Fuente.MERCADO
        else:
            continue

        fuera.append(ImpactoPosicion(ticker=ticker, valor=valor,
                                     retorno=float(retorno), fuente=fuente))

    return Impacto(escenario=escenario, posiciones=fuera,
                   retorno_mercado=retorno_mercado)


# ---------------------------------------------------------------------------
# La diversificacion que desaparece
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Diversificacion:
    """Cuantas apuestas independientes tienes de verdad.

    Diez valores que se mueven todos igual son una apuesta, no diez. El numero
    efectivo vale N cuando no tienen nada que ver entre si y 1 cuando se mueven
    como uno solo.
    """

    n_posiciones: int
    efectivas_hoy: float
    efectivas_en_crisis: float
    correlacion_media: float
    correlacion_crisis: float

    @property
    def se_pierde(self) -> float:
        """Cuanta diversificacion desaparece cuando llega el momento malo."""
        return self.efectivas_hoy - self.efectivas_en_crisis

    @property
    def ya_esta_concentrada(self) -> bool:
        """Si ya hoy, en calma, no llega ni a la mitad de lo que aparenta."""
        return self.efectivas_hoy < self.n_posiciones / 2


def _efectivas(pesos: np.ndarray, vols: np.ndarray, corr: np.ndarray) -> float:
    """Numero efectivo de apuestas independientes.

    (suma de riesgos por separado)^2 / (riesgo de la cartera)^2. Vale N cuando
    no hay correlacion y 1 cuando la correlacion es total, que es justo lo que
    se quiere decir con "tienes diez valores pero se comportan como dos".
    """
    individuales = float(np.sum(pesos * vols))
    if individuales <= 0:
        return 0.0
    cov = corr * np.outer(vols, vols)
    varianza = float(pesos @ cov @ pesos)
    if varianza <= 0:
        return 0.0
    return individuales ** 2 / varianza


def diversificacion(pesos: dict, correlaciones, volatilidades: dict | None = None,
                    correlacion_crisis: float | None = None) -> Diversificacion | None:
    """Cuantas apuestas independientes hay hoy y cuantas quedarian en una caida.

    `correlaciones` es la matriz de correlaciones (un DataFrame). Si no se dan
    volatilidades se toman todas iguales: el resultado sigue siendo valido —lo
    que se mide es co-movimiento— y evita quedarse sin numero por falta de un
    dato secundario.
    """
    tickers = [t for t in pesos if t in getattr(correlaciones, "columns", [])]
    if len(tickers) < 2:
        return None

    w = np.array([float(pesos[t]) for t in tickers], dtype=float)
    if w.sum() <= 0:
        return None
    w = w / w.sum()

    vols = np.array([float((volatilidades or {}).get(t, 1.0) or 1.0)
                     for t in tickers], dtype=float)
    if not np.all(np.isfinite(vols)) or np.any(vols <= 0):
        vols = np.ones(len(tickers))

    corr = correlaciones.loc[tickers, tickers].to_numpy(dtype=float)
    corr = np.nan_to_num(corr, nan=0.0)
    np.fill_diagonal(corr, 1.0)

    rho = float(correlacion_crisis if correlacion_crisis is not None
                else get_stress_config().get("correlacion_en_crisis", 0.9))
    crisis = np.full_like(corr, rho)
    np.fill_diagonal(crisis, 1.0)

    fuera_diag = ~np.eye(len(tickers), dtype=bool)
    return Diversificacion(
        n_posiciones=len(tickers),
        efectivas_hoy=_efectivas(w, vols, corr),
        efectivas_en_crisis=_efectivas(w, vols, crisis),
        correlacion_media=float(corr[fuera_diag].mean()),
        correlacion_crisis=rho,
    )
