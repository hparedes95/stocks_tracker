"""Metricas de evaluacion. Funciones puras sobre arrays de retornos.

Toda metrica que se muestre debe ir acompanada de `n` y, cuando aplique, de su
significancia. Un acierto del 62 % no dice nada si el mercado subio el 65 % de
los dias: por eso el exceso sobre la referencia es obligatorio y no opcional.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

SESSIONS_YEAR = 252

# Por debajo de esta muestra, cualquier metrica es anecdota.
MIN_OBSERVATIONS = 100


@dataclass
class EventMetrics:
    """Resultado de un estudio de eventos."""

    n_obs: int
    hit_rate: float                 # % de eventos con retorno positivo
    hit_rate_vs_benchmark: float    # % que ademas bate a la referencia
    avg_return: float
    median_return: float
    avg_excess: float               # frente a la referencia, es lo que importa
    std_return: float
    t_stat: float
    best: float
    worst: float
    benchmark_avg: float

    @property
    def is_significant(self) -> bool:
        """Muestra suficiente y exceso distinto de cero con holgura.

        `|t| > 2` es el criterio habitual. No prueba que la senal funcione: solo
        que el resultado no se explica facilmente por azar EN ESTA MUESTRA.
        """
        return self.n_obs >= MIN_OBSERVATIONS and abs(self.t_stat) > 2.0

    def to_dict(self) -> dict:
        out = asdict(self)
        out["is_significant"] = self.is_significant
        return out


def _clean(values) -> np.ndarray:
    array = np.asarray(values, dtype=float)
    return array[np.isfinite(array)]


def hit_rate(returns) -> float:
    values = _clean(returns)
    return float((values > 0).mean()) if len(values) else float("nan")


def t_statistic(returns) -> float:
    """t de Student de la media frente a cero.

    Con retornos financieros el supuesto de independencia no se cumple del todo
    (hay autocorrelacion y solapamiento entre eventos), asi que el t-stat es
    optimista. Sirve para descartar lo obviamente aleatorio, no para certificar.
    """
    values = _clean(returns)
    if len(values) < 3:
        return float("nan")
    std = values.std(ddof=1)
    # Comparar con cero exacto no basta. Cuando todos los eventos dan el mismo
    # retorno —un solo valor en el universo, o el coste fijo dominandolo todo—
    # la desviacion no es 0 sino ruido de coma flotante del orden de 1e-18, y
    # la division producia t de 10^16. Un numero asi impreso en una tabla no es
    # un dato: es una forma de perder la confianza en toda la tabla.
    scale = float(np.abs(values).mean())
    if not np.isfinite(std) or std <= max(1e-12, scale * 1e-9):
        return float("nan")
    return float(values.mean() / (std / np.sqrt(len(values))))


def sharpe(returns, periods_per_year: int = SESSIONS_YEAR) -> float:
    """Sharpe anualizado, sin tipo libre de riesgo (exceso sobre cero)."""
    values = _clean(returns)
    if len(values) < 2:
        return float("nan")
    std = values.std(ddof=1)
    if std == 0:
        return float("nan")
    return float(values.mean() / std * np.sqrt(periods_per_year))


def sortino(returns, periods_per_year: int = SESSIONS_YEAR) -> float:
    """Como el Sharpe, pero penalizando solo la volatilidad a la baja."""
    values = _clean(returns)
    if len(values) < 2:
        return float("nan")
    downside = values[values < 0]
    if len(downside) == 0:
        return float("inf")
    dev = np.sqrt((downside**2).mean())
    if dev == 0:
        return float("nan")
    return float(values.mean() / dev * np.sqrt(periods_per_year))


def max_drawdown(equity) -> float:
    """Peor caida desde un maximo. Valor negativo."""
    values = _clean(equity)
    if len(values) < 2:
        return float("nan")
    peak = np.maximum.accumulate(values)
    return float(np.min(values / peak - 1.0))


def calmar(equity, years: float) -> float:
    """Retorno anualizado dividido por la peor caida."""
    values = _clean(equity)
    if len(values) < 2 or years <= 0:
        return float("nan")
    total = values[-1] / values[0]
    if total <= 0:
        return float("nan")
    annual = total ** (1.0 / years) - 1.0
    dd = abs(max_drawdown(values))
    return float(annual / dd) if dd > 0 else float("nan")


def equity_curve(returns, initial: float = 1.0) -> pd.Series:
    """Curva de capital compuesta a partir de retornos periodicos."""
    series = pd.Series(returns).astype(float).fillna(0.0)
    return initial * (1.0 + series).cumprod()


def information_coefficient(scores, forward_returns, method: str = "spearman") -> float:
    """Correlacion de rangos entre puntuacion y retorno posterior.

    Spearman y no Pearson: interesa si el ORDEN se mantiene, no si la relacion
    es lineal. Un IC de 0,03 ya es notable en la practica.
    """
    frame = pd.DataFrame({"score": scores, "fwd": forward_returns}).dropna()
    if len(frame) < 5 or frame["score"].nunique() < 3:
        return float("nan")
    return float(frame["score"].corr(frame["fwd"], method=method))


def ic_information_ratio(ic_series) -> float:
    """Media del IC dividida por su desviacion: mide CONSISTENCIA.

    Un IC medio alto conseguido con enormes vaivenes vale menos que uno modesto
    y estable. Es la metrica que decide si una senal se queda o se descarta.
    """
    values = _clean(ic_series)
    if len(values) < 3:
        return float("nan")
    std = values.std(ddof=1)
    if std == 0:
        return float("nan")
    return float(values.mean() / std)


def summarize_event(
    returns, benchmark_returns=None
) -> EventMetrics:
    """Resumen de un estudio de eventos, siempre contra una referencia."""
    values = _clean(returns)
    if len(values) == 0:
        nan = float("nan")
        return EventMetrics(0, nan, nan, nan, nan, nan, nan, nan, nan, nan, nan)

    if benchmark_returns is None:
        bench = np.zeros_like(values)
    else:
        bench = np.asarray(benchmark_returns, dtype=float)
        mask = np.isfinite(np.asarray(returns, dtype=float)) & np.isfinite(bench)
        values = np.asarray(returns, dtype=float)[mask]
        bench = bench[mask]

    excess = values - bench
    return EventMetrics(
        n_obs=int(len(values)),
        hit_rate=float((values > 0).mean()),
        hit_rate_vs_benchmark=float((excess > 0).mean()),
        avg_return=float(values.mean()),
        median_return=float(np.median(values)),
        avg_excess=float(excess.mean()),
        std_return=float(values.std(ddof=1)) if len(values) > 1 else float("nan"),
        t_stat=t_statistic(excess),
        best=float(values.max()),
        worst=float(values.min()),
        benchmark_avg=float(bench.mean()),
    )


def apply_costs(returns, cost_bps: float, roundtrip: bool = True) -> np.ndarray:
    """Descuenta comision y deslizamiento de cada operacion.

    Sin costes, cualquier estrategia de alta rotacion parece rentable. Por
    defecto se cobra la ida y la vuelta, que es lo que ocurre de verdad.
    """
    values = np.asarray(returns, dtype=float)
    multiplier = 2.0 if roundtrip else 1.0
    return values - (cost_bps / 10_000.0) * multiplier
