"""Convierte z-scores en frases legibles.

Es lo que separa un ranking util de una tabla de numeros opacos: cada candidato
aparece con sus motivos en castellano y con sus pegas, no con un score a secas.

Sin LLM: plantillas deterministas de `config/explanations.yaml`. La misma
entrada produce siempre la misma frase, y se puede testear.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .config import get_explanations
from .safe_eval import evaluate, safe_format

MAX_PROS = 5
MAX_CONS = 3

INSUFFICIENT_DATA = (
    "Aparece por su puntuacion tecnica agregada; "
    "datos fundamentales insuficientes para justificarlo mejor."
)


@dataclass
class Reasons:
    pros: list[str] = field(default_factory=list)
    cons: list[str] = field(default_factory=list)
    signals: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (self.pros or self.cons or self.signals)


def _row_variables(row: pd.Series, medians: pd.Series | None = None) -> dict:
    """Variables disponibles para las condiciones de las plantillas."""
    out: dict = {}
    for key, value in row.items():
        if isinstance(value, (np.integer, np.floating)):
            value = float(value)
            if not np.isfinite(value):
                continue
        elif isinstance(value, np.bool_):
            value = bool(value)
        elif value is None or (isinstance(value, float) and not np.isfinite(value)):
            continue
        out[str(key)] = value
    if medians is not None:
        out["_medians"] = medians
    return out


def _render_rule(rule: dict, field_name: str, row_vars: dict,
                 median: float | None, zscore: float | None) -> str | None:
    """Evalua una regla y devuelve la frase, o None si no aplica."""
    when = rule.get("when")
    text = rule.get("text")
    if not when or not text:
        return None

    local = dict(row_vars)
    x = row_vars.get(field_name)
    if x is None:
        return None
    local["x"] = x
    local["z"] = zscore if zscore is not None and np.isfinite(zscore) else 0.0
    local["median"] = median if median is not None and np.isfinite(median) else float("nan")

    if not evaluate(when, local):
        return None
    return safe_format(text, local)


def build_reasons(
    row: pd.Series,
    contributions: pd.DataFrame | None = None,
    active_signals: list[str] | None = None,
    sector_medians: pd.Series | None = None,
    zscores: dict[str, float] | None = None,
) -> Reasons:
    """Motivos a favor y en contra de un valor.

    `contributions` ordena por importancia (el factor que mas suma se explica
    primero); si no se pasa, se recorre el catalogo en orden de fichero.
    """
    cfg = get_explanations()
    submetrics: dict = cfg.get("submetrics") or {}
    labels: dict = cfg.get("signal_labels") or {}

    row_vars = _row_variables(row)
    zscores = zscores or {}
    reasons = Reasons()

    # Orden: las sub-metricas de los factores que mas contribuyen, primero.
    ordered_fields = list(submetrics.keys())
    if contributions is not None and not contributions.empty:
        weight_by_factor = (
            contributions.set_index("factor")["contribution"].abs().to_dict()
        )
        from .config import get_factor_config

        factor_cfg = get_factor_config()
        field_priority: dict[str, float] = {}
        for fname, spec in factor_cfg.factors.items():
            w = float(weight_by_factor.get(fname, 0.0))
            for sub in spec.submetrics:
                field_priority[sub.field] = max(field_priority.get(sub.field, 0.0), w)
        ordered_fields.sort(key=lambda f: field_priority.get(f, 0.0), reverse=True)

    used_pro: set[str] = set()
    used_con: set[str] = set()

    for field_name in ordered_fields:
        rules = submetrics.get(field_name) or {}
        median = None
        if sector_medians is not None and field_name in sector_medians.index:
            median = float(sector_medians[field_name])
        z = zscores.get(field_name)

        if len(reasons.pros) < MAX_PROS and field_name not in used_pro:
            phrase = _render_rule(rules.get("pro", {}), field_name, row_vars, median, z)
            if phrase:
                reasons.pros.append(phrase)
                used_pro.add(field_name)
                continue  # una metrica no puede ser pro y contra a la vez

        if len(reasons.cons) < MAX_CONS and field_name not in used_con:
            phrase = _render_rule(rules.get("con", {}), field_name, row_vars, median, z)
            if phrase:
                reasons.cons.append(phrase)
                used_con.add(field_name)

    for sig in active_signals or []:
        reasons.signals.append(labels.get(sig, sig))

    return reasons


def render_summary(ticker: str, row: pd.Series, reasons: Reasons) -> str:
    """Resumen de una linea, para exportaciones y logs."""
    score = row.get("composite")
    pctile = row.get("composite_pctile")
    head = f"{ticker}"
    if score is not None and np.isfinite(score):
        head += f" - score {score:+.2f}"
    if pctile is not None and np.isfinite(pctile):
        head += f" (percentil {pctile:.0%})"
    if reasons.pros:
        head += " | A favor: " + "; ".join(reasons.pros[:3])
    if reasons.cons:
        head += " | A vigilar: " + "; ".join(reasons.cons[:2])
    return head
