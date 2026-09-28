"""Escenarios históricos de tendencia. Sin órdenes ni probabilidades calibradas.

Cada observación usa solo información disponible en su fecha. Se escogen
episodios con el mismo estado de tendencia que hoy y ventanas no solapadas.
Los cuantiles describen esos episodios: no son intervalos de confianza de una
predicción ni evidencia de rentabilidad futura.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Forecast:
    as_of: pd.Timestamp | None
    horizon: int
    trend: str
    observations: int = 0
    low: float | None = None
    median: float | None = None
    high: float | None = None
    historical_up_frequency: float | None = None
    reason: str = ""
    episodes: pd.DataFrame = field(default_factory=pd.DataFrame)


def scenarios(prices: pd.DataFrame, *, horizon: int = 63,
              as_of=None, min_episodes: int = 8) -> Forecast:
    if horizon not in (21, 63, 126):
        raise ValueError("Horizonte admitido: 21, 63 o 126 sesiones.")
    if prices.empty:
        return Forecast(None, horizon, "sin datos", reason="No hay histórico descargado.")
    frame = prices.copy()
    if not {"date", "adj_close", "source"}.issubset(frame.columns):
        return Forecast(None, horizon, "sin datos", reason="Faltan fecha, precio o procedencia.")
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    if frame["date"].isna().any():
        return Forecast(None, horizon, "sin datos", reason="Hay fechas inválidas en la serie.")
    cutoff = pd.Timestamp(as_of) if as_of is not None else pd.Timestamp.today().normalize()
    frame = frame.loc[frame["date"] <= cutoff].sort_values("date").reset_index(drop=True)
    if frame.empty:
        return Forecast(None, horizon, "sin datos", reason="No hay precios anteriores al corte.")
    last = pd.Timestamp(frame["date"].iloc[-1])
    def unavailable(reason):
        return Forecast(last, horizon, "sin datos", reason=reason)

    if frame["date"].duplicated().any():
        return unavailable("Hay sesiones duplicadas; revisa la serie.")
    if "source" not in frame or frame["source"].isna().any():
        return unavailable("No consta la procedencia de todos los precios.")
    sources = set(frame["source"].astype(str))
    if any(not source.strip() for source in sources):
        return unavailable("No consta la procedencia de todos los precios.")
    if "synthetic" in sources:
        return unavailable("Los datos sintéticos no permiten estudiar el mercado.")
    if len(sources) != 1:
        return unavailable("La serie mezcla fuentes; revisa su continuidad antes de estimar.")
    values = pd.to_numeric(frame["adj_close"], errors="coerce")
    if not np.isfinite(values).all() or (values <= 0).any():
        return unavailable("La serie contiene precios ajustados inválidos.")
    if (cutoff - last).days > 7:
        return unavailable("La serie está desactualizada; actualiza los datos.")
    if len(values) < 200 + horizon + 2:
        return unavailable("Falta histórico para comparar tendencias de 200 sesiones.")
    trend = values > values.rolling(200, min_periods=200).mean()
    current = bool(trend.iloc[-1])
    label = "sobre la media de 200 sesiones" if current else "bajo la media de 200 sesiones"
    episodes = []
    i = len(frame) - horizon - 2
    while i >= 199:
        if bool(trend.iloc[i]) == current:
            entry, end = i + 1, i + 1 + horizon
            episodes.append({
                "signal_date": frame["date"].iloc[i],
                "entry_date": frame["date"].iloc[entry],
                "exit_date": frame["date"].iloc[end],
                "return": float(values.iloc[end] / values.iloc[entry] - 1),
            })
            i -= horizon + 1
        else:
            i -= 1
    sample = pd.DataFrame(episodes)
    n = len(sample)
    if n < min_episodes:
        return Forecast(last, horizon, label, n,
                        reason=f"Solo {n} episodios comparables; mínimo {min_episodes}. "
                               "No se presenta una estimación numérica.", episodes=sample)
    low, median, high = sample["return"].quantile([.1, .5, .9]).to_numpy()
    frequency = float((sample["return"] > 0).mean()) if n >= 20 else None
    return Forecast(last, horizon, label, n, float(low), float(median), float(high),
                    frequency, episodes=sample)
