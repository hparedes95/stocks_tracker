"""Metricas de evaluacion. Funciones puras sobre arrays de retornos.

Toda metrica que se muestre debe ir acompanada de `n` y, cuando aplique, de su
significancia. Los retornos financieros pueden estar autocorrelacionados y
solapados, por lo que la significancia usa un error estandar HAC.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd
from scipy import stats

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
    p_value: float
    best: float
    worst: float
    benchmark_avg: float

    @property
    def is_significant(self) -> bool:
        """Significancia individual sin correccion por multiples hipotesis."""
        return self.n_obs >= MIN_OBSERVATIONS and np.isfinite(self.p_value) and self.p_value < 0.05

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
    """t de la media con error estandar Newey-West/HAC."""
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

    if not np.isfinite(long_run_var) or long_run_var <= 1e-24:
        return float("nan")
    se = np.sqrt(long_run_var / n)
    return float(values.mean() / se) if se > 0 else float("nan")


def mean_p_value(returns, max_lag: int | None = None) -> float:
    """p bilateral del contraste de media usando el t HAC.

    El estadistico HAC no tiene exactamente una distribucion t finita bajo
    dependencia. Usamos la aproximacion normal para no fingir grados de
    libertad que el estimador HAC no proporciona.
    """
    t_stat = hac_t_statistic(returns, max_lag=max_lag)
    if not np.isfinite(t_stat):
        return float("nan")
    return float(2.0 * stats.norm.sf(abs(t_stat)))


def benjamini_hochberg(p_values) -> np.ndarray:
    """q-values BH/FDR, conservando la posicion original de cada hipotesis.

    NaN permanece NaN. Los valores devueltos son monotonicamente no decrecientes
    al ordenar por p y nunca menores que el p-value original.
    """
    p = np.asarray(p_values, dtype=float)
    out = np.full(p.shape, np.nan, dtype=float)
    valid = np.isfinite(p)
    if not valid.any():
        return out
    pv = p[valid]
    m = len(pv)
    order = np.argsort(pv)
    ranked = pv[order]
    q_ranked = ranked * m / np.arange(1, m + 1)
    q_ranked = np.minimum.accumulate(q_ranked[::-1])[::-1]
    q_ranked = np.clip(q_ranked, 0.0, 1.0)
    restored = np.empty(m, dtype=float)
    restored[order] = q_ranked
    out[valid] = restored
    return out


def t_statistic(returns) -> float:
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
        return EventMetrics(0, nan, nan, nan, nan, nan, nan, nan, nan, nan, nan, nan)

    if benchmark_returns is None:
        bench = np.zeros_like(values)
    else:
        bench = np.asarray(benchmark_returns, dtype=float)
        mask = np.isfinite(np.asarray(returns, dtype=float)) & np.isfinite(bench)
        values = np.asarray(returns, dtype=float)[mask]
        bench = bench[mask]

    excess = values - bench
    t_stat = hac_t_statistic(excess)
    return EventMetrics(
        n_obs=int(len(values)),
        hit_rate=float((values > 0).mean()),
        hit_rate_vs_benchmark=float((excess > 0).mean()),
        avg_return=float(values.mean()),
        median_return=float(np.median(values)),
        avg_excess=float(excess.mean()),
        std_return=float(values.std(ddof=1)) if len(values) > 1 else float("nan"),
        t_stat=t_stat,
        p_value=mean_p_value(excess),
        best=float(values.max()),
        worst=float(values.min()),
        benchmark_avg=float(bench.mean()),
    )


def apply_costs(returns, cost_bps: float, roundtrip: bool = True) -> np.ndarray:
    values = np.asarray(returns, dtype=float)
    multiplier = 2.0 if roundtrip else 1.0
    return values - (cost_bps / 10_000.0) * multiplier
