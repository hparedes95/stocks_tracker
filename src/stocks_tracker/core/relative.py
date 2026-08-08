"""Fuerza relativa, rotacion sectorial y correlaciones.

Un sector puede subir y aun asi estar perdiendo terreno frente al mercado. La
fuerza relativa mide eso: no cuanto sube, sino si lo hace mejor o peor que su
referencia. Es la base del analisis de rotacion.

Aviso de encuadre: la posicion en el grafico de rotacion describe DONDE ESTA un
sector ahora, no hacia donde va. Los cuadrantes son una forma de ordenar lo que
ya ha pasado.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# Cuadrantes del grafico de rotacion (estilo RRG).
LEADING = "Lidera"
WEAKENING = "Se debilita"
LAGGING = "Rezagado"
IMPROVING = "Mejora"


def relative_strength(series: pd.Series, benchmark: pd.Series,
                      base: float = 100.0) -> pd.Series:
    """Ratio serie/referencia normalizado a 100 en el primer dato comun.

    Una linea plana significa "se mueve igual que el mercado". Subiendo, mejor;
    bajando, peor. El nivel absoluto no dice nada: solo la pendiente.
    """
    if series.empty or benchmark.empty:
        return pd.Series(dtype=float)

    aligned_bench = benchmark.reindex(series.index).ffill()
    ratio = series / aligned_bench.replace(0.0, np.nan)
    ratio = ratio.dropna()
    if ratio.empty:
        return pd.Series(dtype=float)
    return base * ratio / ratio.iloc[0]


def rs_ratio(series: pd.Series, benchmark: pd.Series, window: int = 63) -> pd.Series:
    """Fuerza relativa normalizada, centrada en 100.

    Se normaliza con media y desviacion de la propia ventana para que sectores
    con distinta volatilidad sean comparables entre si.
    """
    rs = relative_strength(series, benchmark)
    if rs.empty or len(rs) < window:
        return pd.Series(dtype=float)
    mean = rs.rolling(window, min_periods=window // 2).mean()
    std = rs.rolling(window, min_periods=window // 2).std(ddof=0)
    return 100.0 + (rs - mean) / std.replace(0.0, np.nan)


def rs_momentum(ratio: pd.Series, window: int = 21) -> pd.Series:
    """Momentum de la fuerza relativa: si la ventaja se acelera o se agota."""
    if ratio.empty or len(ratio) < window + 2:
        return pd.Series(dtype=float)
    change = ratio.diff(window)
    mean = change.rolling(window, min_periods=window // 2).mean()
    std = change.rolling(window, min_periods=window // 2).std(ddof=0)
    return 100.0 + (change - mean) / std.replace(0.0, np.nan)


def quadrant(ratio: float, momentum: float) -> str:
    """Cuadrante de rotacion a partir de fuerza relativa y su momentum."""
    if not (np.isfinite(ratio) and np.isfinite(momentum)):
        return "Sin datos"
    if ratio >= 100 and momentum >= 100:
        return LEADING
    if ratio >= 100 and momentum < 100:
        return WEAKENING
    if ratio < 100 and momentum < 100:
        return LAGGING
    return IMPROVING


def rotation_table(
    prices: pd.DataFrame, benchmark: pd.Series, tail: int = 10
) -> pd.DataFrame:
    """Tabla de rotacion con la estela reciente de cada serie.

    `prices` es un DataFrame ancho: una columna por sector o ETF, indexado por
    fecha. Devuelve una fila por serie con su posicion actual y el recorrido de
    las ultimas `tail` observaciones semanales.
    """
    if prices.empty or benchmark.empty:
        return pd.DataFrame()

    rows = []
    for name in prices.columns:
        series = prices[name].dropna()
        if len(series) < 130:
            continue
        ratio = rs_ratio(series, benchmark)
        if ratio.empty:
            continue
        momentum = rs_momentum(ratio)
        combined = pd.DataFrame({"ratio": ratio, "momentum": momentum}).dropna()
        if combined.empty:
            continue

        # Estela semanal: diaria seria un garabato sin informacion.
        weekly = combined.iloc[::-5][::-1].tail(tail)
        last = combined.iloc[-1]
        rows.append(
            {
                "nombre": name,
                "ratio": float(last["ratio"]),
                "momentum": float(last["momentum"]),
                "cuadrante": quadrant(float(last["ratio"]), float(last["momentum"])),
                "estela_ratio": weekly["ratio"].tolist(),
                "estela_momentum": weekly["momentum"].tolist(),
            }
        )

    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values("ratio", ascending=False).reset_index(drop=True)


def average_pairwise_correlation(
    returns: pd.DataFrame, window: int = 60, min_series: int = 5
) -> pd.Series:
    """Correlacion media entre todos los pares, en ventana movil.

    Correlacion alta significa que el mercado se mueve en bloque, empujado por
    factores macro. En ese regimen elegir valores concretos aporta poco: casi
    todo sube o baja junto. Es una medida util de cuando el stock-picking tiene
    sentido y cuando no.
    """
    if returns.empty or returns.shape[1] < min_series:
        return pd.Series(dtype=float)

    values: list[float] = []
    index: list = []
    n_series = returns.shape[1]
    # Numero de pares distintos, para promediar solo la parte superior.
    n_pairs = n_series * (n_series - 1) / 2

    for end in range(window, len(returns) + 1):
        chunk = returns.iloc[end - window : end]
        corr = chunk.corr()
        if corr.isna().all().all():
            continue
        upper = corr.to_numpy()[np.triu_indices(n_series, k=1)]
        upper = upper[np.isfinite(upper)]
        if len(upper) < n_pairs * 0.5:
            continue
        values.append(float(np.mean(upper)))
        index.append(returns.index[end - 1])

    return pd.Series(values, index=index, name="avg_pairwise_corr")


def support_resistance(
    high: pd.Series, low: pd.Series, order: int = 5, tolerance: float = 0.01,
    max_levels: int = 4,
) -> tuple[list[float], list[float]]:
    """Niveles de soporte y resistencia a partir de pivotes locales.

    Se buscan maximos y minimos locales y se agrupan los que estan a menos de
    `tolerance` de distancia: un nivel tocado tres veces vale mas que tres
    niveles casi iguales. Devuelve (soportes, resistencias) ordenados por
    numero de toques.
    """
    if high.empty or low.empty or len(high) < order * 2 + 1:
        return [], []

    def _pivots(series: pd.Series, is_high: bool) -> list[float]:
        values = series.to_numpy(dtype=float)
        found = []
        for i in range(order, len(values) - order):
            window = values[i - order : i + order + 1]
            center = values[i]
            if not np.isfinite(center):
                continue
            if is_high and center == np.nanmax(window):
                found.append(center)
            elif not is_high and center == np.nanmin(window):
                found.append(center)
        return found

    def _cluster(levels: list[float]) -> list[float]:
        if not levels:
            return []
        levels = sorted(levels)
        clusters: list[list[float]] = [[levels[0]]]
        for level in levels[1:]:
            reference = np.mean(clusters[-1])
            if abs(level - reference) / max(abs(reference), 1e-9) <= tolerance:
                clusters[-1].append(level)
            else:
                clusters.append([level])
        # Ordenados por cuantas veces se ha tocado el nivel.
        ranked = sorted(clusters, key=len, reverse=True)
        return [float(np.mean(c)) for c in ranked[:max_levels]]

    resistances = _cluster(_pivots(high, is_high=True))
    supports = _cluster(_pivots(low, is_high=False))
    return supports, resistances


def nearest_levels(
    price: float, supports: list[float], resistances: list[float],
    max_distance: float = 0.20,
) -> tuple[float | None, float | None]:
    """Soporte inmediatamente por debajo y resistencia por encima del precio.

    Se descartan los niveles a mas de `max_distance` del precio actual. Un
    "soporte" un 44 % por debajo no es un soporte: es un minimo historico que no
    condiciona nada de lo que pase esta semana, y mostrarlo como referencia
    induce a error. Preferimos no dar nivel a dar uno inutil.
    """
    if not np.isfinite(price) or price <= 0:
        return None, None

    floor = price * (1 - max_distance)
    ceiling = price * (1 + max_distance)

    below = [s for s in supports if floor <= s < price]
    above = [r for r in resistances if price < r <= ceiling]
    return (max(below) if below else None, min(above) if above else None)
