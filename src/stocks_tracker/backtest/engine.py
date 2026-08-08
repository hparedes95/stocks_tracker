"""Motor de validacion. Deliberadamente simple y honesto.

La regla que atraviesa todo el modulo, y la unica que de verdad importa:

    Una senal detectada al cierre del dia `t` NO se puede comprar a ese cierre.
    La entrada es al cierre de `t+1` y la salida al cierre de `t+1+h`.

Ese desplazamiento esta centralizado en `forward_returns()`: es el unico sitio
donde se toca el eje temporal. Si se colara un `shift` en otro lado, los
resultados serian preciosos y las perdidas reales.

`tests/test_backtest.py` lo verifica con una serie de tendencia conocida, y
ademas comprueba que una version deliberadamente tramposa falla el test.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import metrics as mx

DEFAULT_HORIZONS = (5, 10, 21, 63)

# Ambitos de validacion. Una senal validada en acciones NO esta validada en
# cripto: los regimenes, la volatilidad y la microestructura no se parecen.
SCOPE_EQUITY_US = "equity_us"
SCOPE_EQUITY_EU = "equity_eu"
SCOPE_CRYPTO = "crypto"

# Etiquetas de evidencia que acaban en la tabla `signal_evidence`.
VALIDATED = "validada"
WEAK = "debil"
NOT_VALIDATED = "no_validada"
NO_DATA = "sin_datos"


@dataclass
class FoldResult:
    """Resultado de una ventana temporal."""

    label: str
    start: pd.Timestamp
    end: pd.Timestamp
    n_obs: int
    avg_excess: float
    hit_rate: float
    ic_mean: float
    ic_ir: float


@dataclass
class ValidationResult:
    """Veredicto completo de una senal en un horizonte."""

    signal_id: str
    scope: str
    horizon_days: int
    evidence: str
    event: mx.EventMetrics
    ic_mean: float
    ic_ir: float
    folds: list[FoldResult] = field(default_factory=list)
    costs_bps: float = 0.0
    oos_from: pd.Timestamp | None = None
    oos_to: pd.Timestamp | None = None
    reason: str = ""

    @property
    def positive_folds(self) -> int:
        return sum(1 for f in self.folds if f.avg_excess > 0)


def forward_returns(
    prices: pd.DataFrame, horizons: tuple[int, ...] = DEFAULT_HORIZONS
) -> pd.DataFrame:
    """Retornos futuros por ticker y fecha, con entrada retardada un dia.

    `prices` es el formato largo: ticker, date, adj_close.

    Para la fecha `t` y el horizonte `h`:
        entrada = adj_close[t+1]
        salida  = adj_close[t+1+h]
        retorno = salida / entrada - 1

    Es el UNICO punto del sistema donde se desplaza el eje temporal.
    """
    if prices.empty:
        return pd.DataFrame(columns=["ticker", "date"])

    frames: list[pd.DataFrame] = []

    for ticker, group in prices.groupby("ticker", sort=False):
        series = group.sort_values("date").reset_index(drop=True)
        close = series["adj_close"].astype(float)

        out = pd.DataFrame({"ticker": ticker, "date": series["date"]})
        # Precio de entrada: el cierre del dia SIGUIENTE a la senal.
        entry = close.shift(-1)
        for horizon in horizons:
            exit_price = close.shift(-1 - horizon)
            out[f"fwd_{horizon}"] = exit_price / entry.replace(0.0, np.nan) - 1.0
        frames.append(out)

    return pd.concat(frames, ignore_index=True)


def universe_forward_returns(
    fwd: pd.DataFrame, horizons: tuple[int, ...] = DEFAULT_HORIZONS
) -> pd.DataFrame:
    """Retorno medio del universo en cada fecha: la referencia correcta.

    Es la mediana de comparacion honesta para preguntar "¿esta senal aporta
    algo?". Si se compara contra un indice como el SPY, el exceso mezcla dos
    cosas distintas: lo que aporta la senal y la diferencia estructural entre
    las acciones del universo y el indice.

    Ese error tiene una firma reconocible: senales opuestas (MACD alcista y
    bajista) salen AMBAS con exceso positivo, porque las dos heredan la deriva
    del universo frente al indice. Comparando contra el propio universo, ese
    componente comun se cancela y solo queda lo que la senal anade.
    """
    if fwd.empty:
        return pd.DataFrame()

    columns = [f"fwd_{h}" for h in horizons if f"fwd_{h}" in fwd.columns]
    if not columns:
        return pd.DataFrame()

    grouped = fwd.groupby("date")[columns].mean().reset_index()
    return grouped.rename(columns={f"fwd_{h}": f"bench_{h}" for h in horizons})


def benchmark_forward_returns(
    benchmark: pd.Series, horizons: tuple[int, ...] = DEFAULT_HORIZONS
) -> pd.DataFrame:
    """Retornos futuros de un indice concreto, con el mismo retardo de entrada.

    Se conserva para comparar contra un indice cuando interese esa lectura, pero
    NO es la referencia por defecto de la validacion: ver
    `universe_forward_returns()`.
    """
    if benchmark is None or benchmark.empty:
        return pd.DataFrame()

    series = benchmark.sort_index()
    out = pd.DataFrame({"date": series.index})
    entry = series.shift(-1).to_numpy()
    for horizon in horizons:
        exit_price = series.shift(-1 - horizon).to_numpy()
        with np.errstate(divide="ignore", invalid="ignore"):
            out[f"bench_{horizon}"] = exit_price / np.where(entry == 0, np.nan, entry) - 1.0
    return out


def event_study(
    signals: pd.DataFrame,
    fwd: pd.DataFrame,
    horizon: int,
    bench_fwd: pd.DataFrame | None = None,
    cost_bps: float = 0.0,
) -> tuple[mx.EventMetrics, pd.DataFrame]:
    """Que ocurrio despues de cada disparo de una senal.

    Devuelve las metricas agregadas y el detalle evento a evento, que es lo que
    alimenta el histograma de la interfaz.
    """
    column = f"fwd_{horizon}"
    if signals.empty or fwd.empty or column not in fwd.columns:
        return mx.summarize_event([]), pd.DataFrame()

    merged = signals.merge(fwd[["ticker", "date", column]], on=["ticker", "date"], how="inner")
    merged = merged.dropna(subset=[column])
    if merged.empty:
        return mx.summarize_event([]), pd.DataFrame()

    merged["retorno"] = mx.apply_costs(merged[column].to_numpy(), cost_bps)

    if bench_fwd is not None and not bench_fwd.empty:
        bench_col = f"bench_{horizon}"
        if bench_col in bench_fwd.columns:
            merged = merged.merge(bench_fwd[["date", bench_col]], on="date", how="left")
            merged = merged.rename(columns={bench_col: "referencia"})
        else:
            merged["referencia"] = 0.0
    else:
        merged["referencia"] = 0.0

    result = mx.summarize_event(merged["retorno"], merged["referencia"])
    return result, merged


def rank_ic(
    scores: pd.DataFrame, fwd: pd.DataFrame, horizon: int, score_col: str = "composite"
) -> pd.Series:
    """Serie temporal del coeficiente de informacion, una observacion por fecha.

    Mide si el ORDEN que propone el score se corresponde con el orden de los
    retornos posteriores. Es la forma limpia de evaluar un ranking continuo,
    frente al estudio de eventos que evalua senales discretas.
    """
    column = f"fwd_{horizon}"
    if scores.empty or fwd.empty or column not in fwd.columns:
        return pd.Series(dtype=float)

    merged = scores.merge(fwd[["ticker", "date", column]], on=["ticker", "date"], how="inner")
    merged = merged.dropna(subset=[score_col, column])
    if merged.empty:
        return pd.Series(dtype=float)

    values, index = [], []
    for date, group in merged.groupby("date"):
        ic = mx.information_coefficient(group[score_col], group[column])
        if np.isfinite(ic):
            values.append(ic)
            index.append(date)
    return pd.Series(values, index=pd.to_datetime(index), name="ic").sort_index()


def decile_portfolios(
    scores: pd.DataFrame, fwd: pd.DataFrame, horizon: int,
    n_buckets: int = 10, score_col: str = "composite",
) -> pd.DataFrame:
    """Retorno medio por decil de puntuacion.

    Si el score aporta algo, el decil superior debe rendir mas que el inferior
    de forma sistematica. Es la prueba visual mas directa que existe.
    """
    column = f"fwd_{horizon}"
    if scores.empty or fwd.empty or column not in fwd.columns:
        return pd.DataFrame()

    merged = scores.merge(fwd[["ticker", "date", column]], on=["ticker", "date"], how="inner")
    merged = merged.dropna(subset=[score_col, column])
    if merged.empty:
        return pd.DataFrame()

    rows = []
    for date, group in merged.groupby("date"):
        if len(group) < n_buckets * 2:
            continue
        try:
            buckets = pd.qcut(group[score_col], n_buckets, labels=False, duplicates="drop")
        except ValueError:
            continue
        group = group.assign(decil=buckets + 1)
        for decile, sub in group.groupby("decil"):
            rows.append(
                {"date": date, "decil": int(decile), "retorno": float(sub[column].mean()),
                 "n": len(sub)}
            )

    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)


def decile_summary(deciles: pd.DataFrame) -> pd.DataFrame:
    """Resumen por decil, con el diferencial D10-D1 como lectura principal."""
    if deciles.empty:
        return pd.DataFrame()
    summary = (
        deciles.groupby("decil")
        .agg(retorno_medio=("retorno", "mean"), n_fechas=("retorno", "size"))
        .reset_index()
    )
    return summary.sort_values("decil")


def decile_spread(deciles: pd.DataFrame, n_buckets: int = 10) -> float:
    """Diferencia entre el decil superior y el inferior."""
    if deciles.empty:
        return float("nan")
    summary = decile_summary(deciles)
    top = summary[summary["decil"] == n_buckets]["retorno_medio"]
    bottom = summary[summary["decil"] == 1]["retorno_medio"]
    if top.empty or bottom.empty:
        return float("nan")
    return float(top.iloc[0] - bottom.iloc[0])


def walk_forward(
    events: pd.DataFrame, n_folds: int = 3, return_col: str = "retorno"
) -> list[FoldResult]:
    """Divide el periodo en ventanas consecutivas y evalua cada una.

    Aviso honesto sobre lo que esto mide y lo que no: nuestras senales no tienen
    parametros que ajustar, asi que aqui no hay "entrenamiento" del que
    protegerse. Lo que mide es **estabilidad entre regimenes**: una senal que
    solo funciona en un tramo del historico no es una senal, es una fotografia
    de ese tramo.
    """
    if events.empty or "date" not in events.columns:
        return []

    data = events.dropna(subset=[return_col]).copy()
    if data.empty:
        return []
    data["date"] = pd.to_datetime(data["date"])
    data = data.sort_values("date")

    boundaries = np.array_split(np.arange(len(data)), n_folds)
    folds: list[FoldResult] = []

    for i, positions in enumerate(boundaries, start=1):
        if len(positions) < 10:
            continue
        chunk = data.iloc[positions]
        excess = chunk[return_col] - chunk.get("referencia", 0.0)
        folds.append(
            FoldResult(
                label=f"Ventana {i}",
                start=chunk["date"].iloc[0],
                end=chunk["date"].iloc[-1],
                n_obs=len(chunk),
                avg_excess=float(excess.mean()),
                hit_rate=float((chunk[return_col] > 0).mean()),
                ic_mean=float("nan"),
                ic_ir=float("nan"),
            )
        )
    return folds


def classify_evidence(
    event: mx.EventMetrics, ic_ir: float, folds: list[FoldResult],
    min_obs: int = mx.MIN_OBSERVATIONS, min_ic_ir: float = 0.3,
) -> tuple[str, str]:
    """Etiqueta de evidencia y su motivo, en lenguaje llano.

    Criterios para `validada`, todos a la vez:
      - muestra suficiente,
      - exceso positivo sobre la referencia DESPUES de costes,
      - consistencia (IC-IR) por encima del umbral, o exceso significativo,
      - resultado positivo en al menos dos de cada tres ventanas.

    Se devuelve tambien el motivo para poder mostrarlo: una etiqueta sin
    explicacion invita a ignorarla.
    """
    if event.n_obs == 0:
        return NO_DATA, "Sin eventos en el periodo analizado."

    if event.n_obs < min_obs:
        return (
            NO_DATA,
            f"Solo {event.n_obs} eventos: por debajo de {min_obs} cualquier "
            "conclusion es anecdota.",
        )

    if not np.isfinite(event.avg_excess) or event.avg_excess <= 0:
        return (
            NOT_VALIDATED,
            f"No bate a la referencia: exceso medio {event.avg_excess:+.2%} "
            "despues de costes.",
        )

    n_folds = len(folds)
    positive = sum(1 for f in folds if f.avg_excess > 0)
    stable = n_folds == 0 or positive >= max(2, int(np.ceil(n_folds * 2 / 3)))

    consistent = (np.isfinite(ic_ir) and ic_ir > min_ic_ir) or event.is_significant

    if consistent and stable:
        return (
            VALIDATED,
            f"Exceso medio {event.avg_excess:+.2%} sobre la referencia en "
            f"{event.n_obs} eventos, positivo en {positive} de {n_folds} ventanas.",
        )

    if not stable:
        return (
            WEAK,
            f"Solo positiva en {positive} de {n_folds} ventanas: depende "
            "demasiado del tramo del historico.",
        )

    return (
        WEAK,
        f"Exceso positivo ({event.avg_excess:+.2%}) pero sin consistencia "
        f"suficiente (t = {event.t_stat:.1f}, IC-IR = {ic_ir:.2f}).",
    )


def validate_signal(
    signal_id: str,
    signals: pd.DataFrame,
    fwd: pd.DataFrame,
    horizon: int,
    scope: str = SCOPE_EQUITY_US,
    bench_fwd: pd.DataFrame | None = None,
    cost_bps: float = 10.0,
    n_folds: int = 3,
) -> ValidationResult:
    """Evalua una senal concreta y devuelve su veredicto."""
    subset = signals[signals["signal_id"] == signal_id]
    event, detail = event_study(subset, fwd, horizon, bench_fwd, cost_bps)
    folds = walk_forward(detail, n_folds=n_folds) if not detail.empty else []

    # El IC no aplica a senales discretas (todas valen lo mismo cuando se
    # disparan); se deja como no disponible y la clasificacion recae en el
    # exceso y su significancia.
    ic_mean, ic_ir = float("nan"), float("nan")

    evidence, reason = classify_evidence(event, ic_ir, folds)

    oos_from = pd.to_datetime(detail["date"]).min() if not detail.empty else None
    oos_to = pd.to_datetime(detail["date"]).max() if not detail.empty else None

    return ValidationResult(
        signal_id=signal_id, scope=scope, horizon_days=horizon, evidence=evidence,
        event=event, ic_mean=ic_mean, ic_ir=ic_ir, folds=folds,
        costs_bps=cost_bps, oos_from=oos_from, oos_to=oos_to, reason=reason,
    )
