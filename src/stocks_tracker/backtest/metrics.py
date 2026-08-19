"""Metricas de evaluacion. Funciones puras sobre arrays de retornos.

Toda metrica que se muestre debe ir acompanada de `n` y, cuando aplique, de su
significancia.

Sobre el numero que decide si una senal se valida, y por que no es el numero de
eventos. Un estudio de eventos tiene DOS dependencias, y son distintas:

1. **Entre fechas**: los horizontes se solapan (5, 10, 21, 63 sesiones), asi
   que el retorno de hoy y el de manana comparten dias. Se corrige con un
   error estandar HAC entre fechas.
2. **Dentro de una fecha**: el mismo dia se disparan cientos de eventos en
   valores distintos, y ese dia el mercado entero sube o baja. Esos eventos no
   son observaciones independientes: son *una* observacion vista muchas veces.

La segunda es la grande, y ninguna correccion de series temporales la arregla,
porque no es un problema de orden sino de recuento. La unica forma honesta es
**agrupar por fecha**: promediar el exceso de todos los eventos del dia y
tratar cada dia como una observacion. `clustered_hac_t` hace las dos cosas —
agrupa por fecha y aplica HAC entre fechas—, y es el estadistico que gobierna
`is_significant`.

Cuanto importa: sobre 50 valores y 400 dias que solo comparten el movimiento
del mercado (ninguna senal, cero informacion), tratar los 20.000 eventos como
independientes da |t| = 5,0. Agrupando por fecha da 0,7, que es la verdad.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd
from scipy import stats

SESSIONS_YEAR = 252
MIN_OBSERVATIONS = 100

# Fechas distintas minimas para creerse un HAC. Por debajo, la varianza a largo
# plazo se estima con tan pocos datos que su propio error domina el contraste.
MIN_DATES = 30

# Nivel de los intervalos que se muestran.
CONFIDENCE = 0.95


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
    # Observaciones efectivas: fechas distintas, no eventos. Es el `n` que de
    # verdad sostiene el contraste.
    n_dates: int = 0
    # Media por evento. `avg_excess` es la media por FECHA, que es la que
    # sostiene el t y el intervalo. Se guardan las dos porque contestan
    # preguntas distintas y confundirlas fue un fallo real.
    avg_excess_evento: float = float("nan")
    p_value: float = float("nan")
    ci_low: float = float("nan")
    ci_high: float = float("nan")

    @property
    def is_significant(self) -> bool:
        """Fechas suficientes y exceso con significancia HAC agrupada.

        El minimo se aplica sobre `n_dates` y no sobre `n_obs`: mil eventos
        repartidos en diez dias son diez observaciones, y exigir cien eventos
        deja pasar exactamente ese caso.
        """
        return (
            self.n_dates >= MIN_DATES
            and self.n_obs >= MIN_OBSERVATIONS
            and abs(self.t_stat) > 2.0
        )

    @property
    def ci_excludes_zero(self) -> bool:
        """El intervalo no toca el cero.

        Equivale a `abs(t) > 1,96` por construccion: sale del mismo error
        estandar. Es deliberado —dos numeros en pantalla que pudieran
        contradecirse serian una fabrica de confusion—.
        """
        if not (np.isfinite(self.ci_low) and np.isfinite(self.ci_high)):
            return False
        return self.ci_low > 0 or self.ci_high < 0

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


def hac_standard_error(returns, max_lag: int | None = None) -> float:
    """Error estandar de la media con correccion Newey-West/HAC.

    Los estudios de eventos usan horizontes solapados (5/10/21/63 sesiones),
    por lo que asumir independencia subestima el error estandar. El ancho de
    banda se elige automaticamente con la regla de Andrews y queda limitado a
    n-1.

    Es una funcion aparte, y no un detalle dentro del t, para que el
    estadistico y el intervalo de confianza salgan del MISMO numero. Si cada
    uno estimase su error por su cuenta podrian discrepar —un intervalo que
    excluye el cero junto a un t que no llega a 2— y no habria forma de saber
    cual de los dos creerse.
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
    return float(np.sqrt(long_run_var / n))


def hac_t_statistic(returns, max_lag: int | None = None) -> float:
    """t de la media con error estandar HAC.

    Cuidado al llamarla directamente: supone que el array llega ORDENADO EN EL
    TIEMPO y con una observacion por periodo. Para un estudio de eventos, donde
    el mismo dia hay muchos valores, lo correcto es `clustered_hac_t`.
    """
    values = _clean(returns)
    se = hac_standard_error(values, max_lag)
    if not np.isfinite(se) or se <= 0:
        return float("nan")
    return float(values.mean() / se)


def t_statistic(returns) -> float:
    """t de la media con correccion HAC para dependencia temporal."""
    return hac_t_statistic(returns)


def by_date(values, dates) -> pd.Series:
    """Una observacion por fecha: la media de ese dia, ordenada en el tiempo.

    El paso que convierte un monton de eventos correlacionados en una serie
    temporal sobre la que un HAC significa algo.
    """
    frame = pd.DataFrame({"v": np.asarray(values, dtype=float), "d": pd.to_datetime(dates)})
    frame = frame[np.isfinite(frame["v"])]
    if frame.empty:
        return pd.Series(dtype=float)
    return frame.groupby("d")["v"].mean().sort_index()


def clustered_hac_t(values, dates) -> tuple[float, int]:
    """t agrupando por fecha y con HAC entre fechas. Devuelve (t, n_fechas).

    Es el contraste correcto para un estudio de eventos y el que decide si una
    senal se etiqueta como validada. Ver la cabecera del modulo para el porque
    y para cuanto se infla el t sin esto.
    """
    serie = by_date(values, dates)
    if len(serie) < 3:
        return float("nan"), len(serie)
    return hac_t_statistic(serie.to_numpy()), len(serie)


def clustered_mean_ci(values, dates, confidence: float = CONFIDENCE) -> tuple[float, float]:
    """Intervalo de confianza de la media, agrupando por fecha.

    Sale del mismo error estandar que `clustered_hac_t`, asi que el intervalo
    excluye el cero exactamente cuando |t| supera el valor critico.
    """
    serie = by_date(values, dates)
    if len(serie) < 3:
        return float("nan"), float("nan")
    array = serie.to_numpy()
    se = hac_standard_error(array)
    if not np.isfinite(se) or se <= 0:
        return float("nan"), float("nan")
    z = float(stats.norm.ppf(0.5 + confidence / 2.0))
    media = float(array.mean())
    return media - z * se, media + z * se


def p_value_from_t(t_stat: float) -> float:
    """p bilateral de un t, con la normal.

    Normal y no t de Student porque el error HAC es asintotico: su validez ya
    descansa en tener bastantes fechas, y ahi las dos distribuciones coinciden.
    Usar la t sugeriria una precision en muestras pequenas que el estimador no
    tiene.
    """
    if not np.isfinite(t_stat):
        return float("nan")
    return float(2.0 * stats.norm.sf(abs(t_stat)))


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


def summarize_event(returns, benchmark_returns=None, dates=None) -> EventMetrics:
    """Resumen de un estudio de eventos.

    `dates` no es opcional en la practica: sin ellas el t se calcula sobre los
    eventos sueltos y sale inflado, asi que quien la llame sin fechas obtiene
    `n_dates = 0` y por tanto `is_significant = False`. Se prefiere que la
    ausencia de fechas se manifieste como "no significativo" y no como un
    numero grande y falso.
    """
    values = _clean(returns)
    if len(values) == 0:
        nan = float("nan")
        return EventMetrics(0, nan, nan, nan, nan, nan, nan, nan, nan, nan, nan)

    if benchmark_returns is None:
        bench = np.zeros_like(values)
        mask = np.isfinite(np.asarray(returns, dtype=float))
    else:
        bench = np.asarray(benchmark_returns, dtype=float)
        mask = np.isfinite(np.asarray(returns, dtype=float)) & np.isfinite(bench)
        values = np.asarray(returns, dtype=float)[mask]
        bench = bench[mask]

    excess = values - bench

    # El exceso medio POR EVENTO. Se conserva porque contesta una pregunta
    # legitima —"cuanto rindio un disparo tipico"— pero NO es el numero que
    # acompana al intervalo: ver mas abajo.
    excess_por_evento = float(excess.mean())

    if dates is None:
        t_stat, n_dates = float("nan"), 0
        ci_low, ci_high = float("nan"), float("nan")
        avg_excess = excess_por_evento
    else:
        fechas = np.asarray(pd.to_datetime(pd.Series(dates)))[mask]
        t_stat, n_dates = clustered_hac_t(excess, fechas)
        ci_low, ci_high = clustered_mean_ci(excess, fechas)
        # El titular es la media POR FECHA, la misma que sostiene el t y el
        # intervalo. Con la media por evento, los dias con cientos de disparos
        # pesan cientos de veces mas —que es justo el sesgo que se quito del
        # estadistico—, y el punto y su intervalo salian de estimadores
        # distintos: se llego a ver un +4,81 % junto a un intervalo de
        # [+0,17 %, +0,82 %] que no lo contenia. Dos numeros contiguos que se
        # contradicen no informan de nada, solo hacen desconfiar de la pantalla.
        serie = by_date(excess, fechas)
        avg_excess = float(serie.mean()) if len(serie) else excess_por_evento

    return EventMetrics(
        n_obs=int(len(values)),
        hit_rate=float((values > 0).mean()),
        hit_rate_vs_benchmark=float((excess > 0).mean()),
        avg_return=float(values.mean()),
        median_return=float(np.median(values)),
        avg_excess=avg_excess,
        std_return=float(values.std(ddof=1)) if len(values) > 1 else float("nan"),
        t_stat=t_stat,
        best=float(values.max()),
        worst=float(values.min()),
        benchmark_avg=float(bench.mean()),
        n_dates=int(n_dates),
        avg_excess_evento=excess_por_evento,
        p_value=p_value_from_t(t_stat),
        ci_low=ci_low,
        ci_high=ci_high,
    )


def apply_costs(returns, cost_bps: float, roundtrip: bool = True) -> np.ndarray:
    values = np.asarray(returns, dtype=float)
    multiplier = 2.0 if roundtrip else 1.0
    return values - (cost_bps / 10_000.0) * multiplier
