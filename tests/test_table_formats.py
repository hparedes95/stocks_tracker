"""Tests de formato de tablas.

Streamlit formatea el valor CRUDO de cada celda: una fraccion 0,018 con formato
"%+.2f%%" se imprime como "+0.02%", cien veces menos de lo que es. El error es
invisible en los tests de logica y solo se ve mirando la pantalla, asi que aqui
se comprueba que las columnas de porcentaje llegan ya en escala 0-100.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pandas as pd
import pytest

from stocks_tracker.core.config import project_root

APP_DIR = project_root() / "src" / "stocks_tracker" / "app"

# Columnas cuyo valor es una fraccion (0..1) en el almacen y que, por tanto,
# deben multiplicarse por 100 antes de mostrarse con formato de porcentaje.
FRACTION_COLUMNS = {
    "ret_1d", "ret_5d", "roc_1m", "roc_3m", "roc_6m", "roc_12m",
    "composite_pctile", "coverage", "hit_rate", "avg_excess_ret",
    "dividend_yield", "desde_alta",
}


def _python_files() -> list[Path]:
    return sorted(APP_DIR.rglob("*.py"))


def test_percent_columns_are_scaled_before_display():
    """Toda columna de fraccion usada en un DataFrame de vista va multiplicada.

    Se busca el patron `"Etiqueta": algo["columna_fraccion"]` sin `* 100`
    inmediatamente despues.
    """
    offenders: list[str] = []
    pattern = re.compile(
        r'"[^"]+"\s*:\s*[\w\.\[\]"\']*\[\s*"(' + "|".join(FRACTION_COLUMNS) + r')"\s*\](?!\s*\*)'
    )

    for path in _python_files():
        text = path.read_text(encoding="utf-8")
        # Solo interesan los bloques donde se construye una vista de tabla.
        if "pd.DataFrame(" not in text:
            continue
        for match in pattern.finditer(text):
            line_no = text[: match.start()].count("\n") + 1
            snippet = text.splitlines()[line_no - 1].strip()
            # Las asignaciones a variables intermedias no son vistas de tabla.
            if snippet.startswith(("#", "ctx.", "return")):
                continue
            offenders.append(f"{path.name}:{line_no}: {snippet}")

    assert not offenders, (
        "Columnas de fraccion mostradas sin escalar a 0-100:\n  "
        + "\n  ".join(offenders)
    )


def test_progress_columns_use_a_consistent_scale():
    """`ProgressColumn` con formato de porcentaje debe ir de 0 a 100.

    Con `max_value=1.0` y formato "%.0f%%", un 0,62 se dibuja bien en la barra
    pero se imprime como "1%".
    """
    offenders: list[str] = []

    for path in _python_files():
        text = path.read_text(encoding="utf-8")
        if "ProgressColumn" not in text:
            continue
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = getattr(func, "attr", None)
            if name != "ProgressColumn":
                continue
            kwargs = {kw.arg: kw.value for kw in node.keywords}
            fmt = kwargs.get("format")
            max_value = kwargs.get("max_value")
            if not isinstance(fmt, ast.Constant) or "%%" not in str(fmt.value):
                continue
            if isinstance(max_value, ast.Constant) and float(max_value.value) <= 1.0:
                offenders.append(
                    f"{path.name}:{node.lineno}: formato {fmt.value!r} "
                    f"con max_value={max_value.value}"
                )

    assert not offenders, (
        "ProgressColumn con formato de porcentaje y escala 0-1:\n  "
        + "\n  ".join(offenders)
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(0.018, 1.8), (-0.025, -2.5), (0.62, 62.0)],
)
def test_scaling_produces_readable_numbers(raw, expected):
    """Comprobacion directa de la conversion que aplican las vistas."""
    series = pd.Series([raw])
    assert (series * 100).iloc[0] == pytest.approx(expected)
