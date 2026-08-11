"""Tests de la trampa de los huecos de pandas.

Un hueco de un DataFrame no es None: es float('nan'), y `bool(nan)` es True.
Esa sola linea de comportamiento ha causado dos averias distintas en la
instalacion del usuario, con sintomas que no se parecian en nada entre si:

1. La ingesta moria con "'float' object has no attribute 'upper'" despues de
   resolver doscientos simbolos, sin descargar un precio.
2. La ingesta terminaba sin ningun error y el ranking se quedaba vacio, porque
   los instrumentos se guardaban sin clase de activo.

La segunda es la peligrosa: no hubo error que leer.
"""

from __future__ import annotations

import pandas as pd

from stocks_tracker.core.textutils import as_text, first_text, is_missing


def pandas_gap():
    """Un hueco tal y como lo entrega pandas, no un None escrito a mano.

    Hace falta una columna MIXTA: con un unico None pandas conserva el None y
    la trampa no aparece. Es justo lo que la hace dificil de reproducir a mano
    y facil de encontrarse en produccion, donde unas filas traen valor y otras
    no.
    """
    frame = pd.DataFrame({"asset_class": ["equity", None]})
    return frame.to_dict("records")[1]["asset_class"]


def test_the_trap_still_exists():
    """Si algun dia pandas dejase de hacer esto, estos tests sobran. Mientras
    tanto, dejar constancia de por que existe el modulo."""
    gap = pandas_gap()
    assert gap is not None
    assert bool(gap) is True, "el hueco ya no es verdadero; revisar textutils"


def test_is_missing_catches_what_bool_does_not():
    assert is_missing(pandas_gap())
    assert is_missing(None)
    assert is_missing(float("nan"))
    assert is_missing("")
    assert is_missing("   ")
    assert not is_missing("equity")
    assert not is_missing(0)


def test_as_text_never_produces_the_string_nan():
    """Un 'nan' guardado en la base pasa todos los filtros de 'tiene valor', y
    entonces el problema deja de ser detectable."""
    assert as_text(pandas_gap()) == ""
    assert str(pandas_gap()) == "nan", "el riesgo que se esta evitando"
    assert as_text("  equity  ") == "equity"
    assert as_text(123) == "123"


def test_first_text_skips_the_gaps():
    assert first_text(pandas_gap(), None, "", "equity") == "equity"
    assert first_text(pandas_gap(), None) == ""


# ---------------------------------------------------------------------------
# Las dos averias reales
# ---------------------------------------------------------------------------
def test_a_gap_in_exchange_does_not_kill_the_ingest():
    from stocks_tracker.core.symbols import resolve_all

    rows = resolve_all([
        {"ticker": "AAPL", "exchange": pandas_gap(), "asset_class": pandas_gap()},
        {"ticker": "MSFT", "exchange": "NMS", "asset_class": "equity"},
    ])
    assert len(rows) == 2
    assert rows[1]["tv_symbol"] == "NASDAQ:MSFT"


def test_a_gap_in_asset_class_falls_back_to_equity():
    """El fallo silencioso: sin clase de activo, el ranking filtra por
    'equity'/'etf' y no encuentra un solo instrumento que puntuar."""
    records = pd.DataFrame(
        {"ticker": ["AAPL", "MMC"], "asset_class": ["equity", None]}
    ).to_dict("records")

    for rec in records:
        declared = None
        inferred = rec.get("asset_class")
        if is_missing(inferred):
            rec["asset_class"] = declared or "equity"

    assert [r["asset_class"] for r in records] == ["equity", "equity"]


def test_the_ingest_uses_is_missing_and_not_a_bare_truth_test():
    """Guardarrail sobre el codigo real: `not inferred` volveria a romperlo."""
    from stocks_tracker.core.config import project_root

    src = (project_root() / "src/stocks_tracker/ingest/run_ingest.py").read_text("utf-8")
    block = src[src.index("declared in {\"etf\", \"index\"}"):]
    block = block[:block.index("enriched = pd.DataFrame")]
    assert "is_missing(inferred)" in block
    assert "elif not inferred:" not in block
