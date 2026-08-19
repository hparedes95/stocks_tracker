"""Resumen diario del mercado en lenguaje natural.

Plantillas deterministas, sin LLM: la misma entrada produce siempre el mismo
texto y se puede testear frase a frase.

Regla de encuadre innegociable: el resumen describe lo que HA PASADO, nunca lo
que va a pasar. Nada de futuros ni de recomendaciones. `tests/test_narrative.py`
lo verifica buscando verbos prohibidos en la salida.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np

MAX_SENTENCES = 5

# Palabras que no pueden aparecer nunca en el resumen. El guardarrail existe
# porque es facil colar un "va a" sin darse cuenta al anadir una plantilla.
FORBIDDEN_TERMS = [
    "subira", "subirá", "bajara", "bajará", "va a subir", "va a bajar",
    "predice", "prediccion", "predicción", "recomendamos", "recomendacion",
    "recomendación", "deberías comprar", "deberías comprar", "garantiza",
]


@dataclass
class MarketContext:
    date: date | None = None
    universe: str = ""
    sector_leaders: list[tuple[str, float]] = field(default_factory=list)
    sector_laggards: list[tuple[str, float]] = field(default_factory=list)
    n_breakouts_high: int = 0
    n_breakouts_low: int = 0
    pct_above_sma200: float | None = None
    pct_above_sma200_prev_week: float | None = None
    advances: int = 0
    declines: int = 0
    index_ret_1d: float | None = None
    regime: str | None = None
    risk_score: float | None = None
    risk_score_prev: float | None = None
    vix: float | None = None
    vix_pctile: float | None = None
    n_volume_spikes: int = 0
    top_signal_counts: dict[str, int] = field(default_factory=dict)


def _finite(value) -> bool:
    return value is not None and np.isfinite(float(value))


def render_market_summary(ctx: MarketContext, signal_labels: dict | None = None) -> list[str]:
    """Frases del resumen, en orden de prioridad y como máximo cinco."""
    labels = signal_labels or {}
    out: list[str] = []

    # LEAD: siempre, si hay datos sectoriales.
    if ctx.sector_leaders and ctx.sector_laggards:
        lead_name, lead_ret = ctx.sector_leaders[0]
        lag_name, lag_ret = ctx.sector_laggards[0]
        out.append(
            f"Hoy lidera **{lead_name}** ({lead_ret:+.1%}) y se queda atrás "
            f"**{lag_name}** ({lag_ret:+.1%})."
        )

    # DIVERGENCE: el indice sube pero la mayoria de valores cae.
    if (
        _finite(ctx.index_ret_1d)
        and ctx.index_ret_1d > 0
        and ctx.declines > ctx.advances > 0
    ):
        out.append(
            f"El índice sube ({ctx.index_ret_1d:+.1%}) pero **caen más valores de los "
            f"que suben** ({ctx.declines} frente a {ctx.advances}): la subida esta "
            "concentrada en pocos nombres."
        )

    # BREADTH_EXTREME tiene prioridad sobre BREADTH_TREND.
    if _finite(ctx.pct_above_sma200):
        pct = float(ctx.pct_above_sma200)
        if pct < 30 or pct > 80:
            zona = "extrema baja" if pct < 30 else "de euforia"
            out.append(
                f"Amplitud en zona **{zona}** ({pct:.0f} % de los valores sobre su "
                "MM200); históricamente estas lecturas coinciden con movimientos "
                "amplios en ambos sentidos."
            )
        elif _finite(ctx.pct_above_sma200_prev_week):
            delta = pct - float(ctx.pct_above_sma200_prev_week)
            if abs(delta) > 4:
                verbo = "mejora" if delta > 0 else "se deteriora"
                out.append(
                    f"La amplitud **{verbo}**: {pct:.0f} % de los valores esta sobre "
                    f"su MM200 ({delta:+.0f} puntos en una semana)."
                )

    # REGIME_FLIP
    if ctx.regime and _finite(ctx.risk_score) and _finite(ctx.risk_score_prev):
        if abs(float(ctx.risk_score) - float(ctx.risk_score_prev)) > 20:
            out.append(
                f"El semáforo de riesgo está en **{ctx.regime}** "
                f"(score {float(ctx.risk_score):+.0f}, antes "
                f"{float(ctx.risk_score_prev):+.0f})."
            )

    # BREAKOUTS
    if ctx.n_breakouts_high >= 3 or ctx.n_breakouts_low >= 3:
        out.append(
            f"{ctx.n_breakouts_high} valores rompen máximos anuales frente a "
            f"{ctx.n_breakouts_low} en mínimos."
        )

    # VIX en extremo
    if _finite(ctx.vix) and _finite(ctx.vix_pctile):
        p = float(ctx.vix_pctile)
        if p > 0.85 or p < 0.15:
            out.append(
                f"El VIX está en {float(ctx.vix):.1f}, percentil {p:.0%} del último año."
            )

    # SIGNALS con concentracion
    if ctx.top_signal_counts:
        sig, count = max(ctx.top_signal_counts.items(), key=lambda kv: kv[1])
        if count >= 5:
            out.append(f"Destacan {count} casos de *{labels.get(sig, sig)}*.")

    # VOLUME
    if ctx.n_volume_spikes >= 5:
        out.append(
            f"{ctx.n_volume_spikes} valores negocian más del doble de su volumen habitual."
        )

    # QUIET: si no ha disparado nada, decirlo es mas util que callar.
    if not out:
        out.append("Sesión sin movimientos destacables: nada relevante que revisar hoy.")

    return out[:MAX_SENTENCES]


def render_sector_summary(sector: str, ret_1d: float, ret_1m: float,
                          pct_above_sma200: float | None = None) -> str:
    parts = [f"**{sector}**: {ret_1d:+.1%} hoy, {ret_1m:+.1%} en el mes"]
    if _finite(pct_above_sma200):
        parts.append(f"{float(pct_above_sma200):.0f} % de sus valores sobre la MM200")
    return ", ".join(parts) + "."


def contains_forbidden(text: str) -> list[str]:
    """Terminos prohibidos presentes en el texto. Usado por el test guardarrail."""
    low = text.lower()
    return [term for term in FORBIDDEN_TERMS if term in low]
