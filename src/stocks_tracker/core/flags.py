"""Banderas rojas.

Independientes del score y SIEMPRE visibles, aunque el valor puntue alto. Un
score alto con el dividendo sin cubrir y la deuda disparada sigue siendo un
riesgo, y esconderlo detrás de un número bonito sería justo lo contrario de lo
que esta herramienta pretende.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import get_explanations
from .safe_eval import evaluate


def red_flags(row: pd.Series) -> list[str]:
    """Lista de avisos aplicables a un valor."""
    cfg = get_explanations()
    rules = cfg.get("red_flags") or []

    variables: dict = {}
    for key, value in row.items():
        if isinstance(value, (np.integer, np.floating)):
            fval = float(value)
            if not np.isfinite(fval):
                continue
            variables[str(key)] = fval
        elif isinstance(value, (np.bool_, bool)):
            variables[str(key)] = bool(value)
        elif value is not None:
            variables[str(key)] = value

    out: list[str] = []
    for rule in rules:
        when, text = rule.get("when"), rule.get("text")
        if when and text and evaluate(when, variables):
            out.append(text)
    return out


def earnings_soon(row: pd.Series, days: int = 5) -> str | None:
    """Aviso si hay resultados a la vuelta de la esquina.

    Publicar resultados es el evento que más gaps de apertura produce, y un gap
    salta cualquier stop. Merece un aviso propio.
    """
    next_report = row.get("next_earnings_days")
    if next_report is None:
        return None
    try:
        d = float(next_report)
    except (TypeError, ValueError):
        return None
    if np.isfinite(d) and 0 <= d <= days:
        return f"Presenta resultados en {int(d)} días"
    return None
