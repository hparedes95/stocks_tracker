"""Calibrar la MITAD del asesor que se puede calibrar, y decir cual es la otra.

LA PREGUNTA QUE RESPONDE

El marcador (`advice_store`) mide hacia delante y empieza vacio: tardara meses
en decir algo. Mientras tanto queda una pregunta que SI se puede contestar hoy
con los datos que ya hay:

    el liston de compra del asesor —percentil >= 90— ,
    ¿ha batido al indice historicamente, despues de costes?

Se puede contestar porque esa parte del ranking sale SOLO DE PRECIOS, y de
precios si hay diez anos de historia.

LA MITAD QUE NO SE PUEDE, Y NO SE VA A DISIMULAR

No hay serie punto-en-el-tiempo de fundamentales. Puntuar 2019 con los balances
de hoy es mirar el futuro: la empresa que hoy tiene buen margen es, en parte,
la que sobrevivio. `gate.py` se niega a certificar sobre eso desde hace tiempo
y aqui se aplica la misma negativa.

Consecuencia practica, y hay que repetirla en pantalla: si el perfil que usas
lleva factores fundamentales —`balanced`, `value`, `growth`, `dividend`—, esto
NO lo valida. Solo `bot_core` es de solo precio.

POR QUE NO ES UN BACKTEST DE LA CARTERA

Aqui no se simula comprar, vender ni dimensionar. Eso ya lo hace
`backtest/run_backtest.py` y tiene su propia puerta. Lo que se mide es UNA cosa
concreta y falsable: si el corte del percentil 90 separa a los que luego lo
hacen mejor que el indice de los que no.

Un backtest completo tiene decenas de decisiones —cuando rebalancear, que hacer
con el efectivo, como tratar los dividendos— y cada una es una oportunidad de
ajustar hasta que salga bonito. Esto tiene una sola.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..backtest import engine, metrics
from .advice import UMBRAL_COMPRAR

# Sesiones que se toman como "meses" del horizonte. 21 es el numero habitual de
# sesiones de un mes natural; con 6 meses son 126, que es la ventana en la que
# el asesor dice pensar.
SESIONES_POR_MES = 21

# Cuantas observaciones hacen falta para dar un veredicto.
#
# Menos que esto y el intervalo de confianza es tan ancho que cualquier
# resultado cabe dentro. Se prefiere decir "no se sabe" a dar un numero que
# nadie puede usar: es el mismo criterio que `MIN_PARA_OPINAR` en el marcador.
MIN_OBSERVACIONES = 100

# Cuantas fechas distintas hacen falta. Cien observaciones sacadas de tres dias
# no son cien datos independientes: son tres, medidos sobre treinta empresas que
# se mueven juntas. Sin este minimo, un tramo corto y afortunado pasaria por
# evidencia.
MIN_FECHAS = 24


@dataclass(frozen=True)
class Calibracion:
    """Lo que se ha medido, con su incertidumbre al lado.

    `t_stat` y el intervalo van SIEMPRE con el exceso medio. Un +2,1 % suelto
    invita a creer; un +2,1 % con un intervalo de -4 % a +8 % dice la verdad,
    que es que no se sabe.
    """

    preset: str
    horizonte_sesiones: int
    observaciones: int
    fechas: int
    exceso_medio_pct: float | None
    tasa_de_acierto: float | None
    t_stat: float | None
    ic_bajo_pct: float | None
    ic_alto_pct: float | None
    solo_precio: bool
    bloqueos: tuple[str, ...] = ()

    @property
    def concluyente(self) -> bool:
        """Si hay bastante como para que el numero signifique algo.

        No dice que el resultado sea BUENO: dice que se puede leer.
        """
        return (
            not self.bloqueos
            and self.observaciones >= MIN_OBSERVACIONES
            and self.fechas >= MIN_FECHAS
            and self.t_stat is not None
        )


def factores_de_precio(preset: str) -> bool:
    """Si el perfil se puede validar hacia atras: solo factores de precio.

    Los fundamentales solo existen a dia de hoy. Un perfil que los use no puede
    puntuar 2019 sin usar informacion de 2026, y entonces lo que mide el
    backtest es la supervivencia, no la estrategia.
    """
    from .config import get_factor_config

    solo_precio = {"momentum", "lowvol", "technical"}
    pesos = get_factor_config().weights(preset)
    return all(peso == 0 or nombre in solo_precio
               for nombre, peso in pesos.items())


def calibrar(scores: pd.DataFrame, precios: pd.DataFrame,
             benchmark: pd.Series, *, preset: str = "bot_core",
             horizonte_meses: int = 6,
             bloqueos: tuple[str, ...] = ()) -> Calibracion:
    """Mide si el corte del percentil 90 ha batido al indice.

    `scores` es el historico de `factor_scores` para ese perfil; sale de
    `run_compute --history`, que es lo unico que calcula el ranking hacia atras
    y solo lo hace con factores de precio.

    Los `bloqueos` vienen de `gate.find_blockers` y se guardan tal cual: con uno
    solo, el resultado no es interpretable y la funcion lo dice en vez de dar un
    numero con una nota al pie que nadie lee.
    """
    horizonte = horizonte_meses * SESIONES_POR_MES
    solo_precio = factores_de_precio(preset)
    vacia = Calibracion(preset, horizonte, 0, 0, None, None, None, None, None,
                        solo_precio, bloqueos)
    if bloqueos or scores.empty or precios.empty or benchmark is None \
            or benchmark.empty:
        return vacia

    fwd = engine.forward_returns(precios, horizons=(horizonte,))
    bench = engine.benchmark_forward_returns(benchmark, horizons=(horizonte,))
    col, col_bench = f"fwd_{horizonte}", f"bench_{horizonte}"
    if fwd.empty or bench.empty or col not in fwd.columns:
        return vacia

    datos = (
        scores[["ticker", "date", "composite_pctile"]]
        .merge(fwd[["ticker", "date", col]], on=["ticker", "date"], how="inner")
        .merge(bench[["date", col_bench]], on="date", how="left")
        .dropna(subset=["composite_pctile", col, col_bench])
    )
    # El corte EXACTO del asesor, no un decil aproximado. Medir el decil
    # superior y luego aplicar el percentil 90 serian dos cosas distintas, y la
    # calibracion tiene que medir la regla que se usa de verdad.
    elegidos = datos[datos["composite_pctile"] >= UMBRAL_COMPRAR]
    if elegidos.empty:
        return vacia

    exceso = (elegidos[col] - elegidos[col_bench]).to_numpy(dtype=float)
    exceso = exceso[np.isfinite(exceso)]
    fechas = int(elegidos["date"].nunique())
    if exceso.size == 0:
        return vacia

    # Error estandar HAC: las observaciones se solapan —una compra a seis meses
    # comparte cinco con la del mes siguiente— y el error estandar corriente lo
    # ignora, dando un t-stat inflado que convierte ruido en "significativo".
    t = metrics.hac_t_statistic(exceso, max_lag=horizonte)
    error = metrics.hac_standard_error(exceso, max_lag=horizonte)
    media = float(np.mean(exceso))
    margen = 1.96 * error if np.isfinite(error) else float("nan")

    return Calibracion(
        preset=preset,
        horizonte_sesiones=horizonte,
        observaciones=int(exceso.size),
        fechas=fechas,
        exceso_medio_pct=media * 100.0,
        tasa_de_acierto=float(metrics.hit_rate(exceso)),
        t_stat=float(t) if np.isfinite(t) else None,
        ic_bajo_pct=(media - margen) * 100.0 if np.isfinite(margen) else None,
        ic_alto_pct=(media + margen) * 100.0 if np.isfinite(margen) else None,
        solo_precio=solo_precio,
        bloqueos=bloqueos,
    )


def veredicto(c: Calibracion) -> str:
    """La frase que resume la calibracion sin prometer de mas.

    Funcion aparte y no un f-string en la pantalla porque es la frase que mas
    facil resulta inflar sin querer, y asi se puede testear palabra por palabra.
    Es el mismo motivo por el que existe `advice_store.resumen_honesto`.
    """
    if c.bloqueos:
        return ("No se puede calibrar nada: " + " ".join(c.bloqueos))

    if not c.solo_precio:
        aviso = (
            f"El perfil '{c.preset}' usa factores fundamentales, y de esos no "
            "hay serie historica: puntuar 2019 con los balances de hoy es mirar "
            "el futuro. Esta calibracion NO valida el ranking que estas usando. "
        )
    else:
        aviso = ""

    if c.observaciones == 0:
        return (aviso + "No hay ranking historico calculado. Se genera con "
                "`run_compute --history`, y solo con factores de precio.")

    if not c.concluyente:
        return (
            aviso
            + f"Hay {c.observaciones} observaciones en {c.fechas} fechas: "
            f"pocas para concluir nada (hacen falta {MIN_OBSERVACIONES} y "
            f"{MIN_FECHAS}). Con menos, el intervalo es tan ancho que cualquier "
            "resultado cabe dentro."
        )

    lectura = (
        f"El corte del percentil {UMBRAL_COMPRAR:.0%} rindio "
        f"{c.exceso_medio_pct:+.2f} puntos sobre el indice a "
        f"{c.horizonte_sesiones // SESIONES_POR_MES} meses "
        f"(intervalo {c.ic_bajo_pct:+.2f} a {c.ic_alto_pct:+.2f}, "
        f"t={c.t_stat:.2f}, {c.observaciones} casos en {c.fechas} fechas)."
    )
    if c.ic_bajo_pct is not None and c.ic_bajo_pct <= 0 <= (c.ic_alto_pct or 0):
        lectura += (
            " El intervalo incluye el cero: con estos datos NO se puede afirmar "
            "que el liston aporte nada."
        )
    return aviso + lectura
