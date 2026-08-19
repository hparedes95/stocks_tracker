"""Metricas de evaluacion. Funciones puras sobre arrays de retornos.

Toda metrica que se muestre debe ir acompanada de `n` y, cuando aplique, de su
significancia. Los retornos financieros pueden estar autocorrelacionados y
solapados, por lo que la significancia usa un error estandar HAC.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

SESSIONS_YEAR = 252
MIN_OBSERVATIONS = 100


@dataclass
class EventMetrics:
    n_obs: int
    hit_rate: float
    hit_rate_vs_benchmark: float
    avg_return: float
    median_return: float
    avg_excess: float
    std_return: float
    t_stat: float
    best: float
    worst: float
    benchmark_avg: float

    @property
    def is_significant(self) -> bool:
        """Muestra suficiente y exceso con significancia HAC."""
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


def hac_t_statistic(returns, max_lag: int | None = None) -> float:
    """t de la media con error estandar Newey-West/HAC.

    Los estudios de eventos usan horizontes solapados (5/10/21/63 sesiones),
    por lo que asumir independencia subestima el error estandar. El ancho de
    banda se elige automaticamente con la regla de Andrews y queda limitado a
    n-1. La estimacion se hace sobre la media, que es el contraste que necesita
    el estudio de eventos.
    """
    values = _clean(returns)
    n = len(values)
    if n < 3:
        return float("nan")

    centered = values - values.mean()
    gamma0 = float(np.dot(centered, centered) / n)
    if not np.isfinite(gamma0) or gamma0 <= 1e-24:
        return float("nan")

    if max_lag is None:
        max_lag = int(np.floor(4.0 * (n / 100.0) ** (2.0 / 9.0)))
    max_lag = max(0, min(int(max_lag), n - 1))

    long_run_var = gamma0
    for lag in range(1, max_lag + 1):
        gamma = float(np.dot(centered[lag:], centered[:-lag]) / n)
        weight = 1.0 - lag / (max_lag + 1.0)
        long_run_var += 2.0 * weight * gamma

    # La estimacion de varianza de la media es LRV / n.
    if not np.isfinite(long_run_var) or long_run_var <= 1e-24:
        return float("nan")
    se = np.sqrt(long_run_var / n)
    return float(values.mean() / se) if se > 0 else float("nan")


def t_statistic(returns) -> float:
    """t de la media con correccion HAC para dependencia temporal."""
    return hac_t_statistic(returns)


def sharpe(returns, periods_per_year: int = SESSIONS_YEAR) -> float:
    values = _clean(returns)
    if len(values) < 2:
        return float("nan")
    std = values.std(ddof=1)
    if std == 0:
        return float("nan")
    return float(values.mean() / std * np.sqrt(periods_per_year))


def sortino(returns, periods_per_year: int = SESSIONS_YEAR) -> float:
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
    values = _clean(equity)
    if len(values) < 2:
        return float("nan")
    peak = np.maximum.accumulate(values)
    return float(np.min(values / peak - 1.0))


def calmar(equity, years: float) -> float:
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
    series = pd.Series(returns).astype(float).fillna(0.0)
    return initial * (1.0 + series).cumprod()


def information_coefficient(scores, forward_returns, method: str = "spearman") -> float:
    frame = pd.DataFrame({"score": scores, "fwd": forward_returns}).dropna()
    if len(frame) < 5 or frame["score"].nunique() < 3:
        return float("nan")
    return float(frame["score"].corr(frame["fwd"], method=method))


def ic_information_ratio(ic_series) -> float:
    values = _clean(ic_series)
    if len(values) < 3:
        return float("nan")
    std = values.std(ddof=1)
    if std == 0:
        return float("nan")
    return float(values.mean() / std)


def summarize_event(returns, benchmark_returns=None) -> EventMetrics:
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
        t_stat=hac_t_statistic(excess),
        best=float(values.max()),
        worst=float(values.min()),
        benchmark_avg=float(bench.mean()),
    )


def apply_costs(returns, cost_bps: float, roundtrip: bool = True) -> np.ndarray:
    values = np.asarray(returns, dtype=float)
    multiplier = 2.0 if roundtrip else 1.0
    return values - (cost_bps / 10_000.0) * multiplier
