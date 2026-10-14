"""Modelos pequeños predefinidos, evaluación temporal y abstención por defecto.

No se elige el ganador retrospectivo: tendencia+volatilidad es el candidato
fijo; tendencia y mediana incondicional son comparadores. Las observaciones
de entrenamiento y prueba tienen retornos no solapados y etiquetas vencidas.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy.stats import t

from ..backtest.metrics import hac_standard_error
from .forecast import scenarios

VERSION = "trend-vol-walkforward-v2-prospective"


@dataclass(frozen=True)
class Validation:
    reason: str
    observations: int = 0
    qualified: bool = False
    lower_excess: float | None = None
    metrics: pd.DataFrame = field(default_factory=pd.DataFrame)
    trials: pd.DataFrame = field(default_factory=pd.DataFrame)
    status: str = 'evidencia_insuficiente'


def validate_models(prices: pd.DataFrame, *, horizon=63, as_of=None,
                    benchmark: pd.DataFrame | None = None,
                    roundtrip_cost: float = 0.01, family_size: int = 1,
                    holdout_start=None) -> Validation:
    if not np.isfinite(roundtrip_cost) or not 0 <= roundtrip_cost < 1:
        raise ValueError("Costes de ida y vuelta inválidos.")
    if not isinstance(family_size, int) or not 1 <= family_size <= 100000:
        raise ValueError("Número de comparaciones inválido.")
    check = scenarios(prices, horizon=horizon, as_of=as_of, min_episodes=1)
    if check.reason:
        return Validation(check.reason, status='datos_no_utilizables')
    p = prices.copy()
    p["date"] = pd.to_datetime(p["date"])
    p = p[p["date"] <= check.as_of].sort_values("date").reset_index(drop=True)
    price = p["adj_close"].astype(float)
    trend = (price > price.rolling(200).mean()).to_numpy()
    vol = price.pct_change(fill_method=None).rolling(63).std()
    volatile = (vol > vol.expanding(min_periods=200).median()).to_numpy()
    bench = None
    if benchmark is not None and not benchmark.empty:
        bcheck = scenarios(benchmark, horizon=horizon, as_of=as_of, min_episodes=1)
        if not bcheck.reason:
            b = benchmark.copy()
            b["date"] = pd.to_datetime(b["date"])
            bench = b.set_index("date")["adj_close"].reindex(p["date"]).to_numpy()
    pool, trials = [], []
    # El índice del episodio no depende de cuánto histórico se haya descargado
    # después: las pruebas anteriores no se desplazan al añadir nuevos precios.
    for i in range(263, len(p) - horizon - 1, horizon + 1):
        end = i + 1 + horizon
        outcome = float(price.iloc[end] / price.iloc[i + 1] - 1)
        trained = [x for x in pool if x['end'] <= i]
        matches = [x for x in trained if x['trend'] == trend[i]]
        similar = [x for x in matches if x['volatile'] == volatile[i]]
        if len(similar) >= 8 and len(trained) >= 12:
            groups = {"sin_cambio": None, "mediana_historica": trained,
                      "tendencia": matches, "tendencia_volatilidad": similar}
            market = np.nan
            if bench is not None and np.isfinite(bench[[i + 1, end]]).all() and bench[i + 1] > 0:
                market = float(bench[end] / bench[i + 1] - 1)
            for model, sample in groups.items():
                y = np.array([x['return'] for x in sample]) if sample else np.array([0.])
                prediction = float(np.median(y))
                probability = float(np.mean(y > 0)) if sample else .5
                low, high = np.quantile(y, [.1, .9])
                trials.append({"date": p['date'].iloc[i], "entry": p['date'].iloc[i + 1],
                               "exit": p['date'].iloc[end], "model": model,
                               "prediction": prediction, "actual": outcome,
                               "error": abs(prediction - outcome),
                               "brier": (probability - (outcome > 0)) ** 2,
                               "covered": low <= outcome <= high,
                               "excess": outcome - market,
                               "trend": bool(trend[i]), "volatile": bool(volatile[i])})
        pool.append({"end": end, "trend": trend[i], "volatile": volatile[i], "return": outcome})
    detail = pd.DataFrame(trials)
    if detail.empty:
        return Validation("Falta histórico independiente para entrenar y evaluar los modelos.")
    metrics = detail.groupby("model").agg(episodios=("actual", "size"),
                error_absoluto=("error", "mean"), brier=("brier", "mean"),
                cobertura_intervalo=("covered", "mean")).reset_index()
    primary = detail[detail['model'] == 'tendencia_volatilidad']
    conditioned = primary[(primary['trend'] == trend[-1]) &
                          (primary['volatile'] == volatile[-1])].dropna(subset=['excess'])
    n = len(conditioned)
    reason = "Sin evidencia suficiente de ventaja neta en el estado actual."
    lower = None
    qualified = False
    status = 'evidencia_insuficiente'
    if n >= 30:
        excess = conditioned['excess'].to_numpy()
        # Ventanas no solapadas NO garantizan independencia; exigimos además
        # estabilidad por tercios cronológicos y mostramos los episodios.
        error = max(hac_standard_error(excess), excess.std(ddof=1) / np.sqrt(n))
        lower = float(excess.mean() - t.ppf(1 - .025 / (family_size * 3), n - 1) * error)
        errors = metrics.set_index('model')['error_absoluto']
        improves = errors['tendencia_volatilidad'] < min(errors['sin_cambio'], errors['mediana_historica'])
        stable = all(block.mean() > roundtrip_cost for block in np.array_split(excess, 3))
        historical = bool(improves and stable and np.isfinite(lower) and lower > 2 * roundtrip_cost)
        status = 'historica_favorable' if historical else 'historica_desfavorable'
        reason = ('Evidencia histórica favorable bajo estos supuestos; seguimiento prospectivo pendiente.'
                  if historical else 'La ventaja histórica no supera todas las pruebas y el doble de costes.')
        # Nunca llamar independiente a una partición antigua que ya se ha mirado.
        # El último tramo empieza DESPUÉS de registrar el protocolo en este PC.
        reserved = conditioned[conditioned['date'] >= pd.Timestamp(holdout_start)] if holdout_start else conditioned.iloc[:0]
        if len(reserved) >= 30:
            x = reserved.excess.to_numpy()
            se = max(hac_standard_error(x), x.std(ddof=1) / np.sqrt(len(x)))
            limit = x.mean() - t.ppf(1 - .025 / (family_size * 3), len(x) - 1) * se
            stable_reserved = all(block.mean() > 2 * roundtrip_cost for block in np.array_split(x, 3))
            qualified = bool(historical and np.isfinite(limit) and limit > 2 * roundtrip_cost and stable_reserved)
            status = 'confirmada' if qualified else 'refutada'
            lower = float(limit)
            reason = ('Evidencia prospectiva favorable del modelo técnico, no del asesor completo.' if qualified
                      else 'El periodo reservado no confirma la ventaja; este protocolo queda refutado.')
    if bench is None:
        reason = "Comparación predictiva disponible; falta referencia válida para evaluar ventaja neta."
    return Validation(reason, n, qualified, lower, metrics, detail, status)
