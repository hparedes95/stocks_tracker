"""Tests de arquitectura, ingesta idempotente y contrato de proveedores."""

from __future__ import annotations

import ast
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from stocks_tracker.core.config import get_factor_config, get_universes, project_root
from stocks_tracker.providers.base import (
    OHLCV_COLUMNS,
    FundamentalsProvider,
    PriceProvider,
    completeness,
    normalize_ohlcv,
)
from stocks_tracker.providers.registry import get_price_provider
from stocks_tracker.providers.synthetic_provider import SyntheticProvider

SRC = project_root() / "src"
ALLOWED_YFINANCE = {SRC / "stocks_tracker" / "providers" / "yfinance_provider.py"}


def _imports_of(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def test_yfinance_is_confined_to_its_provider():
    """yfinance es una API NO oficial que se rompe sola.

    Si su import se cuela fuera del adaptador, sustituirla dejaria de ser
    escribir un fichero nuevo y pasaria a ser refactorizar medio proyecto.
    """
    offenders = []
    for path in SRC.rglob("*.py"):
        if path in ALLOWED_YFINANCE:
            continue
        if "yfinance" in _imports_of(path):
            offenders.append(str(path.relative_to(SRC)))
    assert not offenders, f"yfinance importado fuera de su adaptador: {offenders}"


def test_ui_does_not_import_providers_directly():
    """La interfaz lee del almacen, nunca de la red."""
    app_dir = SRC / "stocks_tracker" / "app"
    offenders = []
    for path in app_dir.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "providers.registry" in text or "import yfinance" in text:
            offenders.append(str(path.relative_to(SRC)))
    assert not offenders, f"La UI accede a proveedores directamente: {offenders}"


# ---------------------------------------------------------------------------
# Contrato de proveedores
# ---------------------------------------------------------------------------
def test_synthetic_provider_satisfies_protocols():
    provider = SyntheticProvider()
    assert isinstance(provider, PriceProvider)
    assert isinstance(provider, FundamentalsProvider)


def test_synthetic_provider_returns_canonical_schema():
    provider = SyntheticProvider()
    end = date(2024, 6, 28)
    df = provider.fetch_ohlcv(["AAA", "BBB"], end - timedelta(days=200), end)

    assert not df.empty
    for col in OHLCV_COLUMNS:
        assert col in df.columns
    assert set(df["ticker"]) == {"AAA", "BBB"}
    assert (df["adj_close"] > 0).all()
    assert df["high"].ge(df["low"]).all()


def test_synthetic_series_are_deterministic():
    """El mismo ticker debe dar siempre la misma serie: si no, los tests no valen."""
    provider = SyntheticProvider()
    window = (date(2024, 1, 1), date(2024, 6, 1))
    first = provider.fetch_ohlcv(["AAA"], *window)
    second = provider.fetch_ohlcv(["AAA"], *window)
    pd.testing.assert_series_equal(first["adj_close"], second["adj_close"])


def test_normalize_drops_rows_without_price():
    raw = pd.DataFrame(
        {
            "ticker": ["A", "A", "A"],
            "date": pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"]),
            "open": [10.0, 11.0, 12.0], "high": [11.0, 12.0, 13.0],
            "low": [9.0, 10.0, 11.0], "close": [10.5, 11.5, 12.5],
            "adj_close": [10.5, None, 0.0], "volume": [100, 200, 300],
        }
    )
    result = normalize_ohlcv(raw, "test")
    assert len(result) == 1
    assert result.iloc[0]["adj_close"] == 10.5


def test_normalize_deduplicates():
    raw = pd.DataFrame(
        {
            "ticker": ["A", "A"], "date": ["2024-01-02", "2024-01-02"],
            "open": [1.0, 1.0], "high": [1.0, 1.0], "low": [1.0, 1.0],
            "close": [1.0, 1.0], "adj_close": [1.0, 1.0], "volume": [1, 1],
        }
    )
    assert len(normalize_ohlcv(raw, "test")) == 1


def test_completeness_measures_available_fields():
    """Es lo que evita que un valor europeo con la mitad de los campos vacios
    compita de tu a tu con uno estadounidense que los tiene todos."""
    row = pd.Series({"a": 1.0, "b": None, "c": 3.0, "d": None})
    assert completeness(row, ["a", "b", "c", "d"]) == 0.5
    assert completeness(row, []) == 0.0


def test_registry_can_force_a_provider():
    provider = get_price_provider("synthetic")
    assert provider.name == "synthetic"


def test_registry_rejects_unknown_provider():
    from stocks_tracker.providers.base import ProviderError

    with pytest.raises(ProviderError):
        get_price_provider("no_existe")


# ---------------------------------------------------------------------------
# Almacen
# ---------------------------------------------------------------------------
def test_upsert_is_idempotent(tmp_path, monkeypatch):
    """Ejecutar el mismo lote dos veces no puede duplicar filas."""
    import duckdb

    from stocks_tracker.core import db

    warehouse = tmp_path / "test.duckdb"
    conn = duckdb.connect(str(warehouse))
    conn.execute(db.schema_path().read_text(encoding="utf-8"))

    payload = pd.DataFrame(
        {
            "ticker": ["AAA", "BBB"],
            "date": [date(2024, 1, 2)] * 2,
            "open": [1.0, 2.0], "high": [1.5, 2.5], "low": [0.5, 1.5],
            "close": [1.2, 2.2], "adj_close": [1.2, 2.2], "volume": [100, 200],
            "source": ["test"] * 2, "ingested_at": [pd.Timestamp("2024-01-02")] * 2,
        }
    )

    first = db.upsert_df(conn, "prices_daily", payload, keys=["ticker", "date"])
    second = db.upsert_df(conn, "prices_daily", payload, keys=["ticker", "date"])
    total = conn.execute("SELECT COUNT(*) FROM prices_daily").fetchone()[0]

    assert first == second == 2
    assert total == 2

    # Y un upsert con valores nuevos debe reemplazar, no acumular.
    payload.loc[0, "close"] = 9.9
    db.upsert_df(conn, "prices_daily", payload, keys=["ticker", "date"])
    updated = conn.execute(
        "SELECT close FROM prices_daily WHERE ticker = 'AAA'"
    ).fetchone()[0]
    assert updated == 9.9
    assert conn.execute("SELECT COUNT(*) FROM prices_daily").fetchone()[0] == 2
    conn.close()
    monkeypatch.undo()


def test_upsert_rejects_missing_key_columns(tmp_path):
    import duckdb

    from stocks_tracker.core import db

    conn = duckdb.connect(str(tmp_path / "t.duckdb"))
    conn.execute(db.schema_path().read_text(encoding="utf-8"))
    with pytest.raises(ValueError, match="Faltan columnas clave"):
        db.upsert_df(conn, "prices_daily", pd.DataFrame({"ticker": ["A"]}), keys=["ticker", "date"])
    conn.close()


# ---------------------------------------------------------------------------
# Configuracion
# ---------------------------------------------------------------------------
def test_preset_weights_sum_to_one():
    cfg = get_factor_config()
    for name, weights in cfg.presets.items():
        assert sum(weights.values()) == pytest.approx(1.0), (
            f"Los pesos del preset '{name}' no suman 1"
        )


def test_preset_factors_exist_in_catalog():
    cfg = get_factor_config()
    known = set(cfg.factors)
    for name, weights in cfg.presets.items():
        unknown = set(weights) - known
        assert not unknown, f"El preset '{name}' referencia factores inexistentes: {unknown}"


def test_universes_have_tickers():
    for key, spec in get_universes().items():
        assert spec.tickers, f"El universo '{key}' esta vacio"
        assert all(" " not in t for t in spec.tickers), (
            f"Ticker con espacios en '{key}': revisa el formato compacto del YAML"
        )
