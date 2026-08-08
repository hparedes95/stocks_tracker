"""Indicadores tecnicos. Funciones puras: DataFrame entra, DataFrame sale.

Sin red, sin base de datos, sin estado. Es donde vive el 90% del riesgo de bugs
silenciosos (un `shift()` mal puesto = look-ahead bias), asi que todo aqui es
testeable con series sinteticas.

Regla que atraviesa el modulo: el valor en el dia `t` solo puede depender de
datos hasta `t` incluido. Nunca de `t+1`. `tests/test_no_lookahead.py` lo
verifica perturbando el futuro y comprobando que el pasado no cambia.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# Sesiones bursatiles aproximadas por periodo.
SESSIONS_MONTH = 21
SESSIONS_QUARTER = 63
SESSIONS_HALF = 126
SESSIONS_YEAR = 252


# --------------------------------------------------------------------------
# Medias y tendencia
# --------------------------------------------------------------------------
def sma(series: pd.Series, window: int) -> pd.Series:
    """Media movil simple.

    `min_periods=window` de forma deliberada: con `min_periods=1` los primeros
    valores serian una media de 1, 2, 3... observaciones, que parece un numero
    valido pero no lo es. Preferimos NaN honesto.
    """
    return series.rolling(window=window, min_periods=window).mean()


def ema(series: pd.Series, span: int) -> pd.Series:
    """Media movil exponencial.

    `adjust=False` reproduce la formula recursiva clasica, que es la que usan
    las plataformas de trading. Con `adjust=True` los primeros valores difieren.
    """
    return series.ewm(span=span, adjust=False, min_periods=span).mean()


def wilder_smooth(series: pd.Series, period: int) -> pd.Series:
    """Suavizado de Wilder, usado por RSI, ATR y ADX.

    Equivale a una EMA con alpha = 1/period, no 2/(period+1). Confundir ambas
    es el error mas comun al implementar RSI a mano.
    """
    return series.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def macd(
    series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> pd.DataFrame:
    ema_fast = ema(series, fast)
    ema_slow = ema(series, slow)
    line = ema_fast - ema_slow
    sig = line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return pd.DataFrame({"macd": line, "macd_signal": sig, "macd_hist": line - sig})


# --------------------------------------------------------------------------
# Osciladores
# --------------------------------------------------------------------------
def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Indice de fuerza relativa con suavizado de Wilder."""
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = wilder_smooth(gain, period)
    avg_loss = wilder_smooth(loss, period)
    # Sin perdidas medias, el RSI es 100 por definicion.
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - (100.0 / (1.0 + rs))
    out = out.where(avg_loss != 0.0, 100.0)
    out = out.where(avg_gain.notna() & avg_loss.notna(), np.nan)
    return out


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    ranges = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    )
    return ranges.max(axis=1)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    return wilder_smooth(true_range(high, low, close), period)


def adx(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14
) -> pd.DataFrame:
    """ADX y DI+/DI-.

    Mide la FUERZA de la tendencia, no su direccion. Es el filtro que decide si
    conviene mirar el RSI (mercado en rango) o el MACD (mercado en tendencia).
    """
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=high.index
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=high.index
    )

    tr_smooth = wilder_smooth(true_range(high, low, close), period)
    plus_di = 100.0 * wilder_smooth(plus_dm, period) / tr_smooth.replace(0.0, np.nan)
    minus_di = 100.0 * wilder_smooth(minus_dm, period) / tr_smooth.replace(0.0, np.nan)

    denom = (plus_di + minus_di).replace(0.0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / denom
    return pd.DataFrame(
        {"adx14": wilder_smooth(dx, period), "plus_di": plus_di, "minus_di": minus_di}
    )


def bollinger(series: pd.Series, window: int = 20, num_std: float = 2.0) -> pd.DataFrame:
    mid = sma(series, window)
    std = series.rolling(window=window, min_periods=window).std(ddof=0)
    upper = mid + num_std * std
    lower = mid - num_std * std
    width = (upper - lower) / mid.replace(0.0, np.nan)
    pctb = (series - lower) / (upper - lower).replace(0.0, np.nan)
    return pd.DataFrame(
        {"bb_upper": upper, "bb_lower": lower, "bb_width": width, "bb_pctb": pctb}
    )


# --------------------------------------------------------------------------
# Volumen
# --------------------------------------------------------------------------
def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    """On-Balance Volume: acumula volumen con el signo del movimiento del dia."""
    direction = np.sign(close.diff().fillna(0.0))
    return (direction * volume.fillna(0.0)).cumsum()


def relative_volume(volume: pd.Series, window: int = 20) -> pd.Series:
    """Volumen del dia frente a su media reciente. >2 = algo esta pasando."""
    avg = volume.rolling(window=window, min_periods=window).mean()
    return volume / avg.replace(0.0, np.nan)


# --------------------------------------------------------------------------
# Momentum, riesgo y posicion en el rango
# --------------------------------------------------------------------------
def roc(series: pd.Series, periods: int) -> pd.Series:
    """Rendimiento simple sobre N sesiones."""
    return series.pct_change(periods=periods)


def momentum_12_1(series: pd.Series) -> pd.Series:
    """Momentum academico: rendimiento a 12 meses excluyendo el ultimo mes.

    Excluir el mes mas reciente evita capturar la reversion a corto plazo, que
    tiende a ir en contra. Es el nucleo del factor momentum.
    """
    return (series.shift(SESSIONS_MONTH) / series.shift(SESSIONS_YEAR)) - 1.0


def realized_volatility(series: pd.Series, window: int) -> pd.Series:
    """Volatilidad realizada anualizada, sobre retornos logaritmicos."""
    log_ret = np.log(series / series.shift(1))
    return log_ret.rolling(window=window, min_periods=window).std(ddof=0) * np.sqrt(
        SESSIONS_YEAR
    )


def distance_to_high(series: pd.Series, window: int = SESSIONS_YEAR) -> pd.Series:
    """Distancia relativa al maximo de la ventana. 0 = en maximos."""
    rolling_max = series.rolling(window=window, min_periods=window // 2).max()
    return series / rolling_max.replace(0.0, np.nan) - 1.0


def distance_to_low(series: pd.Series, window: int = SESSIONS_YEAR) -> pd.Series:
    rolling_min = series.rolling(window=window, min_periods=window // 2).min()
    return series / rolling_min.replace(0.0, np.nan) - 1.0


def drawdown(series: pd.Series) -> pd.Series:
    """Caida desde el maximo historico acumulado hasta la fecha."""
    running_max = series.cummax()
    return series / running_max.replace(0.0, np.nan) - 1.0


def max_drawdown(series: pd.Series, window: int = SESSIONS_YEAR) -> pd.Series:
    """Peor caida dentro de una ventana movil. Valor negativo."""
    def _mdd(values: np.ndarray) -> float:
        peak = np.maximum.accumulate(values)
        return float(np.min(values / peak - 1.0))

    return series.rolling(window=window, min_periods=window // 2).apply(_mdd, raw=True)


def consecutive_true(flags: pd.Series) -> pd.Series:
    """Cuantas observaciones lleva la condicion siendo cierta, sin interrupcion.

    Se usa para "lleva 84 sesiones por encima de la MM200", que es mucho mas
    informativo que un booleano.
    """
    truthy = flags.fillna(False).astype(bool)
    groups = (~truthy).cumsum()
    return truthy.groupby(groups).cumsum().astype("Int64")


# --------------------------------------------------------------------------
# Ensamblado
# --------------------------------------------------------------------------
def compute_all(df: pd.DataFrame, benchmark_close: pd.Series | None = None) -> pd.DataFrame:
    """Calcula todos los indicadores para UN ticker.

    `df` debe venir ordenado por fecha e indexado por ella, con columnas
    open/high/low/close/adj_close/volume.

    Se usa `adj_close` para todo lo que sea retorno o tendencia (esta ajustado
    por splits y dividendos) y `close` solo para mostrar precio de pantalla.
    """
    if df.empty:
        return pd.DataFrame()

    out = pd.DataFrame(index=df.index)
    price = df["adj_close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)
    volume = df["volume"].astype(float)

    out["close"] = close

    # Tendencia
    out["sma20"] = sma(price, 20)
    out["sma50"] = sma(price, 50)
    out["sma200"] = sma(price, 200)
    out["ema12"] = ema(price, 12)
    out["ema26"] = ema(price, 26)
    out = out.join(macd(price))

    # Osciladores y volatilidad
    out["rsi14"] = rsi(price, 14)
    out = out.join(adx(high, low, close, 14))
    out["atr14"] = atr(high, low, close, 14)
    out["atr_pct"] = out["atr14"] / close.replace(0.0, np.nan)
    out = out.join(bollinger(price, 20, 2.0))
    out["realized_vol_20"] = realized_volatility(price, 20)
    out["realized_vol_60"] = realized_volatility(price, 60)
    out["realized_vol_252"] = realized_volatility(price, SESSIONS_YEAR)

    # Volumen
    out["obv"] = obv(close, volume)
    out["rel_volume_20"] = relative_volume(volume, 20)

    # Momentum
    out["roc_1m"] = roc(price, SESSIONS_MONTH)
    out["roc_3m"] = roc(price, SESSIONS_QUARTER)
    out["roc_6m"] = roc(price, SESSIONS_HALF)
    out["roc_12m"] = roc(price, SESSIONS_YEAR)
    out["mom_12_1"] = momentum_12_1(price)
    out["ret_1d"] = price.pct_change(1)
    out["ret_5d"] = price.pct_change(5)

    # Posicion en el rango y riesgo
    out["dist_52w_high"] = distance_to_high(price)
    out["dist_52w_low"] = distance_to_low(price)
    out["drawdown"] = drawdown(price)
    out["max_dd_1y"] = max_drawdown(price)

    # Estado de tendencia y cruces
    above200 = price > out["sma200"]
    out["above_sma200"] = above200.where(out["sma200"].notna())
    out["above_sma50"] = (price > out["sma50"]).where(out["sma50"].notna())
    cross_up = (out["sma50"] > out["sma200"]) & (
        out["sma50"].shift(1) <= out["sma200"].shift(1)
    )
    cross_down = (out["sma50"] < out["sma200"]) & (
        out["sma50"].shift(1) >= out["sma200"].shift(1)
    )
    valid_cross = out["sma200"].notna() & out["sma200"].shift(1).notna()
    out["golden_cross"] = (cross_up & valid_cross).fillna(False)
    out["death_cross"] = (cross_down & valid_cross).fillna(False)
    out["days_above_sma200"] = consecutive_true(above200 & out["sma200"].notna())

    # Fuerza relativa frente al indice de referencia
    if benchmark_close is not None and not benchmark_close.empty:
        bench = benchmark_close.reindex(out.index).ffill()
        bench_roc = bench.pct_change(SESSIONS_QUARTER)
        out["rs_vs_bench_3m"] = out["roc_3m"] - bench_roc
    else:
        out["rs_vs_bench_3m"] = np.nan

    # Soporte y resistencia mas cercanos, calculados sobre el ultimo ano.
    # Solo tiene sentido el nivel vigente hoy: guardarlos dia a dia
    # multiplicaria el coste sin que nadie mire los historicos.
    supports, resistances = _recent_levels(high, low)
    last_close = float(close.iloc[-1]) if len(close) else np.nan
    support, resistance = _nearest(last_close, supports, resistances)
    out["support_near"] = np.nan
    out["resistance_near"] = np.nan
    if len(out):
        out.iloc[-1, out.columns.get_loc("support_near")] = support
        out.iloc[-1, out.columns.get_loc("resistance_near")] = resistance

    return out


def _recent_levels(
    high: pd.Series, low: pd.Series, window: int = SESSIONS_YEAR
) -> tuple[list[float], list[float]]:
    """Soportes y resistencias del ultimo ano.

    Importado aqui para evitar una dependencia circular: `relative` usa tipos de
    este modulo en otras funciones.
    """
    from .relative import support_resistance

    return support_resistance(high.tail(window), low.tail(window))


def _nearest(
    price: float, supports: list[float], resistances: list[float]
) -> tuple[float, float]:
    from .relative import nearest_levels

    support, resistance = nearest_levels(price, supports, resistances)
    return (
        support if support is not None else np.nan,
        resistance if resistance is not None else np.nan,
    )
