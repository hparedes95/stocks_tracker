"""Tests de los estilos de inversion (presets de pesos).

Los scores de todos los estilos conviven en `factor_scores`, distinguidos por
`weights_hash`. El fallo que acecha aqui no es que falle una consulta, sino que
devuelva de mas: sin filtrar por el hash, cada valor aparece una vez por estilo
y el ranking se duplica en silencio.
"""

from __future__ import annotations

from datetime import date

import duckdb
import pandas as pd
import pytest

from stocks_tracker.core import db
from stocks_tracker.core.config import get_factor_config
from stocks_tracker.core.scoring import (
    PRESET_DESCRIPTIONS,
    PRESET_LABELS,
    preset_hash,
    preset_label,
    preset_names,
    weights_hash,
)

DAY = date(2024, 6, 28)


def test_every_preset_has_a_distinct_hash():
    hashes = {name: preset_hash(name) for name in preset_names()}
    assert len(set(hashes.values())) == len(hashes), (
        f"Dos estilos comparten hash y se pisarian en el almacen: {hashes}"
    )


def test_hash_is_stable_across_calls():
    """Si el hash cambiara entre procesos, cada noche se guardaria un juego
    nuevo y la tabla creceria sin limite."""
    assert preset_hash("balanced") == preset_hash("balanced")


def test_hash_ignores_key_order():
    a = weights_hash({"value": 0.6, "quality": 0.4})
    b = weights_hash({"quality": 0.4, "value": 0.6})
    assert a == b


def test_hash_changes_when_weights_change():
    assert weights_hash({"value": 0.6}) != weights_hash({"value": 0.61})


def test_balanced_is_offered_first():
    """Es el estilo por defecto: debe encabezar el selector."""
    assert preset_names()[0] == "balanced"


def test_every_preset_has_a_label_and_a_description():
    for name in preset_names():
        assert name in PRESET_LABELS, f"Falta el nombre visible de '{name}'"
        assert PRESET_DESCRIPTIONS.get(name), f"Falta la descripcion de '{name}'"


def test_unknown_preset_falls_back_to_its_own_name():
    assert preset_label("inventado") == "Inventado"


def test_unknown_preset_hash_is_rejected():
    with pytest.raises(KeyError):
        preset_hash("no_existe")


# ---------------------------------------------------------------------------
# El filtro por estilo en las consultas
# ---------------------------------------------------------------------------
@pytest.fixture
def warehouse_with_presets(tmp_path, monkeypatch):
    """Almacen con los mismos dos valores puntuados por tres estilos."""
    path = tmp_path / "presets.duckdb"
    conn = duckdb.connect(str(path))
    conn.execute(db.schema_path().read_text(encoding="utf-8"))

    conn.execute(
        """
        INSERT INTO instruments (ticker, name, gics_sector, asset_class, currency)
        VALUES ('AAA', 'Alfa', 'Tecnologia', 'equity', 'USD'),
               ('BBB', 'Beta', 'Banca', 'equity', 'USD')
        """
    )
    conn.execute(
        """
        INSERT INTO indicators_daily (ticker, date, close, ret_1d, above_sma200, rsi14)
        VALUES ('AAA', ?, 100.0, 0.01, TRUE, 55.0),
               ('BBB', ?, 50.0, -0.01, TRUE, 45.0)
        """,
        [DAY, DAY],
    )

    for i, name in enumerate(["balanced", "value", "momentum"]):
        for ticker in ("AAA", "BBB"):
            conn.execute(
                """
                INSERT INTO factor_scores
                    (ticker, date, weights_hash, composite, composite_pctile, coverage)
                VALUES (?, ?, ?, ?, ?, 0.9)
                """,
                [ticker, DAY, preset_hash(name), float(i), 0.5 + i * 0.1],
            )
            conn.execute(
                """
                INSERT INTO factor_contributions
                    (ticker, date, weights_hash, factor, zscore, weight, contribution)
                VALUES (?, ?, ?, 'value', 1.0, 0.2, ?)
                """,
                [ticker, DAY, preset_hash(name), float(i)],
            )
    conn.close()

    class _Settings:
        warehouse_path = path
        compute = {"weights_preset": "balanced"}
        raw: dict = {}

    monkeypatch.setattr(db, "get_settings", lambda: _Settings())
    return path


def _uncached(fn):
    """Salta la cache de Streamlit, que en tests solo estorba."""
    return getattr(fn, "__wrapped__", fn)


def test_query_returns_one_row_per_ticker_not_one_per_preset(warehouse_with_presets):
    """La prueba central: tres estilos guardados, dos valores, dos filas."""
    from stocks_tracker.app import data_access as da

    rows = _uncached(da.get_candidates)(preset="balanced")
    assert len(rows) == 2
    assert sorted(rows["ticker"]) == ["AAA", "BBB"]


def test_each_preset_returns_its_own_scores(warehouse_with_presets):
    from stocks_tracker.app import data_access as da

    balanced = _uncached(da.get_candidates)(preset="balanced")
    momentum = _uncached(da.get_candidates)(preset="momentum")

    assert float(balanced["composite"].iloc[0]) == 0.0
    assert float(momentum["composite"].iloc[0]) == 2.0


def test_contributions_are_scoped_to_the_preset(warehouse_with_presets):
    from stocks_tracker.app import data_access as da

    balanced = _uncached(da.get_contributions)("AAA", preset="balanced")
    momentum = _uncached(da.get_contributions)("AAA", preset="momentum")

    assert len(balanced) == 1 and len(momentum) == 1
    assert float(balanced["contribution"].iloc[0]) == 0.0
    assert float(momentum["contribution"].iloc[0]) == 2.0


def test_available_presets_lists_only_what_is_computed(warehouse_with_presets):
    from stocks_tracker.app import data_access as da

    available = _uncached(da.available_presets)()
    assert set(available) == {"balanced", "value", "momentum"}
    # `dividend` y `growth` existen en el YAML pero no se han calculado.
    assert "dividend" not in available


def test_watchlist_and_positions_do_not_multiply(warehouse_with_presets):
    """Estas dos consultas tambien cruzan factor_scores."""
    from stocks_tracker.app import data_access as da

    with db.connect() as conn:
        conn.execute(
            "INSERT INTO watchlist (ticker, list_name) VALUES ('AAA', 'default')"
        )
        conn.execute(
            """
            INSERT INTO positions (id, ticker, qty, avg_cost, currency, opened_at)
            VALUES ('p1', 'AAA', 10, 90.0, 'USD', ?)
            """,
            [DAY],
        )

    assert len(_uncached(da.get_watchlist)()) == 1
    assert len(_uncached(da.get_positions)()) == 1


def test_alert_context_does_not_multiply(warehouse_with_presets):
    """Sin el filtro, la misma regla generaria una alerta por estilo."""
    from stocks_tracker.alerts import evaluate as ev

    snapshot = ev._load_context()["snapshot"]
    assert len(snapshot) == 2
    assert sorted(snapshot["ticker"]) == ["AAA", "BBB"]


def test_preset_weights_all_sum_to_one():
    cfg = get_factor_config()
    for name in preset_names():
        assert sum(cfg.weights(name).values()) == pytest.approx(1.0)


def test_presets_actually_differ_from_each_other():
    """Dos estilos con los mismos pesos serian dos entradas para un solo
    ranking: ruido en el selector."""
    cfg = get_factor_config()
    seen: dict[tuple, str] = {}
    for name in preset_names():
        key = tuple(sorted(cfg.weights(name).items()))
        assert key not in seen, f"'{name}' repite los pesos de '{seen.get(key)}'"
        seen[key] = name


# ---------------------------------------------------------------------------
# Poda de scores obsoletos
# ---------------------------------------------------------------------------
def test_stale_scores_are_pruned(warehouse_with_presets):
    """Un ticker que deja de ser puntuable no puede quedarse en el ranking.

    Asi es como el indice del dolar acabo apareciendo como accion a comprar:
    el upsert actualiza y anade, pero nunca quita.
    """
    from stocks_tracker.compute.run_compute import _prune_stale_scores

    whash = preset_hash("balanced")
    with db.connect() as conn:
        removed = _prune_stale_scores(conn, DAY, whash, ["AAA"])
        remaining = conn.execute(
            "SELECT ticker FROM factor_scores WHERE date = ? AND weights_hash = ?",
            [DAY, whash],
        ).fetchdf()

    assert removed == 1
    assert remaining["ticker"].tolist() == ["AAA"]


def test_pruning_never_touches_other_presets(warehouse_with_presets):
    from stocks_tracker.compute.run_compute import _prune_stale_scores

    with db.connect() as conn:
        _prune_stale_scores(conn, DAY, preset_hash("balanced"), ["AAA"])
        others = conn.execute(
            "SELECT COUNT(*) FROM factor_scores WHERE weights_hash = ?",
            [preset_hash("momentum")],
        ).fetchone()[0]

    assert others == 2


def test_pruning_with_an_empty_score_set_deletes_nothing(warehouse_with_presets):
    """Si el calculo no produjo nada, borrar todo seria destruir el ranking."""
    from stocks_tracker.compute.run_compute import _prune_stale_scores

    with db.connect() as conn:
        assert _prune_stale_scores(conn, DAY, preset_hash("balanced"), []) == 0
        total = conn.execute("SELECT COUNT(*) FROM factor_scores").fetchone()[0]

    assert total == 6


def test_scores_table_keeps_one_row_per_ticker_and_preset(warehouse_with_presets):
    with db.connect(read_only=True) as conn:
        dupes = conn.execute(
            """
            SELECT ticker, date, weights_hash, COUNT(*) AS n
            FROM factor_scores GROUP BY 1, 2, 3 HAVING COUNT(*) > 1
            """
        ).fetchdf()
    assert dupes.empty


def test_pandas_reads_the_same_row_count_as_sql(warehouse_with_presets):
    """Guardarrail contra un join que se abra sin que nadie lo note."""
    from stocks_tracker.app import data_access as da

    with db.connect(read_only=True) as conn:
        expected = conn.execute(
            "SELECT COUNT(*) FROM factor_scores WHERE weights_hash = ?",
            [preset_hash("value")],
        ).fetchone()[0]

    assert len(_uncached(da.get_candidates)(preset="value")) == expected


def test_scores_frame_has_no_duplicate_tickers(warehouse_with_presets):
    from stocks_tracker.app import data_access as da

    rows = _uncached(da.get_candidates)(preset="value")
    assert not pd.Series(rows["ticker"]).duplicated().any()
