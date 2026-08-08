"""Deteccion de senales tecnicas discretas.

Cada senal es una regla nombrada con direccion y fuerza. Se guardan en la tabla
`signals` para poder responder a "que se movio hoy" y para marcar los graficos.

Aviso de encuadre: que una senal se dispare NO significa comprar. Hasta que la
fase 3 no valide cada una contra su historico (`signal_evidence`), son solo
observaciones. La UI las muestra en gris mientras no esten validadas.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

BULLISH = "bullish"
BEARISH = "bearish"

# Senales que la pagina "que se mueve hoy" considera cambios de tendencia.
TREND_CHANGE_SIGNALS = [
    "GOLDEN_CROSS",
    "DEATH_CROSS",
    "MACD_BULL_CROSS",
    "MACD_BEAR_CROSS",
    "RSI_OVERSOLD_REVERSAL",
    "HIGH_52W_BREAKOUT",
    "LOW_52W_BREAKDOWN",
    "PULLBACK_IN_UPTREND",
    "NEW_DOWNTREND",
]


def _prev(series: pd.Series) -> pd.Series:
    return series.shift(1)


def detect(ind: pd.DataFrame) -> pd.DataFrame:
    """Detecta senales para UN ticker a partir de sus indicadores.

    Devuelve un DataFrame largo: date, signal_id, direction, strength, detail.
    """
    if ind.empty:
        return pd.DataFrame(columns=["date", "signal_id", "direction", "strength", "detail"])

    rows: list[pd.DataFrame] = []

    def add(mask: pd.Series, signal_id: str, direction: str, strength: pd.Series | float,
            detail: str = "") -> None:
        mask = mask.fillna(False)
        if not mask.any():
            return
        idx = ind.index[mask]
        s = strength if isinstance(strength, pd.Series) else pd.Series(strength, index=ind.index)
        rows.append(
            pd.DataFrame(
                {
                    "date": idx,
                    "signal_id": signal_id,
                    "direction": direction,
                    "strength": s.reindex(idx).clip(0.0, 1.0).fillna(0.5).to_numpy(),
                    "detail": detail,
                }
            )
        )

    rsi14 = ind.get("rsi14")
    adx14 = ind.get("adx14")
    above200 = ind.get("above_sma200")
    macd_hist = ind.get("macd_hist")
    rel_vol = ind.get("rel_volume_20")
    dist_high = ind.get("dist_52w_high")
    dist_low = ind.get("dist_52w_low")
    bb_width = ind.get("bb_width")

    # --- Cruces de medias: cambio de regimen de tendencia largo ---
    add(ind.get("golden_cross", pd.Series(False, index=ind.index)),
        "GOLDEN_CROSS", BULLISH, 0.7)
    add(ind.get("death_cross", pd.Series(False, index=ind.index)),
        "DEATH_CROSS", BEARISH, 0.7)

    # --- MACD ---
    if macd_hist is not None:
        add((macd_hist > 0) & (_prev(macd_hist) <= 0), "MACD_BULL_CROSS", BULLISH, 0.5)
        add((macd_hist < 0) & (_prev(macd_hist) >= 0), "MACD_BEAR_CROSS", BEARISH, 0.5)

    # --- Correccion dentro de tendencia alcista ---
    # El patron mas util para buscar entradas: la tendencia de fondo sigue
    # intacta (por encima de la MM200, con ADX confirmando) pero el precio ha
    # retrocedido lo suficiente como para no comprar en maximos.
    if rsi14 is not None and above200 is not None and adx14 is not None:
        pullback = (above200.fillna(False)) & (rsi14 < 40) & (adx14 > 20)
        strength = ((40 - rsi14) / 25).clip(0, 1)
        add(pullback, "PULLBACK_IN_UPTREND", BULLISH, strength)

    # --- Rebote desde sobreventa ---
    if rsi14 is not None:
        add((rsi14 > 30) & (_prev(rsi14) <= 30), "RSI_OVERSOLD_REVERSAL", BULLISH, 0.45)

    # --- Rupturas de rango anual ---
    if dist_high is not None and rel_vol is not None:
        breakout = (dist_high >= -0.002) & (_prev(dist_high) < -0.002) & (rel_vol > 1.5)
        add(breakout, "HIGH_52W_BREAKOUT", BULLISH, (rel_vol / 4).clip(0, 1))
    if dist_low is not None:
        breakdown = (dist_low <= 0.002) & (_prev(dist_low) > 0.002)
        add(breakdown, "LOW_52W_BREAKDOWN", BEARISH, 0.6)

    # --- Volumen inusual: es un evento, no una direccion ---
    if rel_vol is not None:
        ret = ind.get("ret_1d", pd.Series(0.0, index=ind.index))
        spike = rel_vol > 3.0
        add(spike & (ret >= 0), "VOLUME_SPIKE", BULLISH, (rel_vol / 6).clip(0, 1))
        add(spike & (ret < 0), "VOLUME_SPIKE", BEARISH, (rel_vol / 6).clip(0, 1))

    # --- Compresion de volatilidad: suele preceder a una expansion ---
    if bb_width is not None:
        pct = bb_width.rolling(252, min_periods=60).rank(pct=True)
        add(pct < 0.10, "BB_SQUEEZE", "neutral", 0.4)

    # --- Entrada en tendencia bajista ---
    if above200 is not None:
        lost = (~above200.fillna(True)) & (_prev(above200).fillna(False))
        add(lost, "NEW_DOWNTREND", BEARISH, 0.6)

    if not rows:
        return pd.DataFrame(columns=["date", "signal_id", "direction", "strength", "detail"])
    return pd.concat(rows, ignore_index=True)


def technical_score(ind_row: pd.Series, active_signals: list[str]) -> float:
    """Puntuacion tecnica agregada de un valor, usada como factor `technical`.

    Combina el estado de la tendencia con las senales activas del dia. Escala
    aproximada -1..+1; el z-score posterior la normaliza dentro del sector.
    """
    score = 0.0

    if bool(ind_row.get("above_sma200", False)):
        score += 0.30
    else:
        score -= 0.30
    if bool(ind_row.get("above_sma50", False)):
        score += 0.15

    rsi = ind_row.get("rsi14")
    if rsi is not None and np.isfinite(rsi):
        if 40 <= rsi <= 65:
            score += 0.10          # zona sana
        elif rsi > 78:
            score -= 0.20          # euforia
        elif rsi < 25:
            score -= 0.05          # cuchillo cayendo

    hist = ind_row.get("macd_hist")
    if hist is not None and np.isfinite(hist):
        score += 0.10 if hist > 0 else -0.10

    dist = ind_row.get("dist_52w_high")
    if dist is not None and np.isfinite(dist):
        if dist > -0.05:
            score += 0.15          # cerca de maximos: liderazgo
        elif dist < -0.40:
            score -= 0.10

    bullish = {"GOLDEN_CROSS", "PULLBACK_IN_UPTREND", "MACD_BULL_CROSS",
               "HIGH_52W_BREAKOUT", "RSI_OVERSOLD_REVERSAL"}
    bearish = {"DEATH_CROSS", "MACD_BEAR_CROSS", "LOW_52W_BREAKDOWN", "NEW_DOWNTREND"}
    for sig in active_signals:
        if sig in bullish:
            score += 0.12
        elif sig in bearish:
            score -= 0.12

    return float(np.clip(score, -1.5, 1.5))
