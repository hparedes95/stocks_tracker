"""Reutilizacion de los metadatos entre ingestas.

Dos problemas en el mismo sitio, uno visible y otro no:

- Visible: `fetch_metadata` hace una peticion por ticker. Con 617 son entre
  cinco y quince minutos EN CADA ejecucion, para traer un nombre y un sector
  que no cambian de un dia para otro.
- Invisible y peor: el presupuesto de peticiones corta a los 400, y los ~217
  restantes se guardaban con la ficha en blanco, pisando lo que ya tenian. Cada
  noche se rellenaban unos y se vaciaban otros, sin avanzar nunca.
"""

from __future__ import annotations

import pandas as pd
import pytest

from stocks_tracker.core import db
from stocks_tracker.ingest import run_ingest


@pytest.fixture
def warehouse(tmp_path, monkeypatch):
    class Stub:
        warehouse_path = tmp_path / "test.duckdb"

    monkeypatch.setattr(db, "get_settings", lambda: Stub())
    db.migrate()
    return Stub.warehouse_path


class FakeProvider:
    """Devuelve metadatos solo de los primeros `budget` tickers, como Yahoo."""

    def __init__(self, budget: int = 2):
        self.budget = budget
        self.asked: list[list[str]] = []

    def fetch_metadata(self, tickers):
        self.asked.append(list(tickers))
        served = list(tickers)[: self.budget]
        return pd.DataFrame([
            {"ticker": t, "name": f"Empresa {t}", "asset_class": "equity",
             "exchange": "NMS", "currency": "USD", "country": "US",
             "gics_sector": "Tecnologia", "gics_industry": "Software",
             "market_cap": 1e9}
            for t in served
        ])


@pytest.fixture
def three_tickers(monkeypatch):
    from stocks_tracker.core import config

    def fake_resolve(universe, manual, source="manual"):
        return (["AAA", "BBB", "CCC"], "manual") if universe == "SP500" else ([], "manual")

    monkeypatch.setattr(run_ingest, "resolve_universe", fake_resolve)
    monkeypatch.setattr(run_ingest, "get_active_universes", lambda: ["SP500"])
    spec = config.UniverseSpec(
        key="SP500", name="S&P 500", source="manual", benchmark="^GSPC",
        currency="USD", asset_class="equity", tickers=["AAA", "BBB", "CCC"],
    )
    monkeypatch.setattr(run_ingest, "get_universes", lambda: {"SP500": spec})


def test_a_budget_cut_does_not_blank_what_was_already_known(warehouse, three_tickers,
                                                            monkeypatch):
    """El fallo silencioso: la tercera ficha se guardaba vacia y borraba la
    anterior. Dos pasadas tenian que dejar las tres completas, no turnarse."""
    provider = FakeProvider(budget=2)
    monkeypatch.setattr(run_ingest, "get_price_provider", lambda name=None: provider)

    run_ingest.ingest_universe()
    primera = db.query("SELECT ticker, gics_sector FROM instruments ORDER BY ticker")
    assert primera["gics_sector"].notna().sum() == 2, "no se ha reproducido el corte"

    run_ingest.ingest_universe()
    segunda = db.query("SELECT ticker, gics_sector FROM instruments ORDER BY ticker")

    assert len(segunda) == 3
    assert segunda["gics_sector"].notna().sum() == 3, (
        "la segunda pasada ha borrado lo que sabia la primera en lugar de "
        "completar lo que faltaba"
    )


def test_the_second_pass_only_asks_for_what_is_missing(warehouse, three_tickers,
                                                       monkeypatch):
    """Es lo que convierte quince minutos en unos segundos."""
    provider = FakeProvider(budget=2)
    monkeypatch.setattr(run_ingest, "get_price_provider", lambda name=None: provider)

    run_ingest.ingest_universe()
    assert provider.asked[0] == ["AAA", "BBB", "CCC"]

    run_ingest.ingest_universe()
    assert provider.asked[1] == ["CCC"], (
        f"se han vuelto a pedir {provider.asked[1]} teniendo ya dos resueltos"
    )


def test_nothing_is_asked_when_everything_is_fresh(warehouse, three_tickers,
                                                   monkeypatch):
    provider = FakeProvider(budget=10)
    monkeypatch.setattr(run_ingest, "get_price_provider", lambda name=None: provider)

    run_ingest.ingest_universe()
    run_ingest.ingest_universe()

    assert len(provider.asked) == 1, (
        f"se ha vuelto a consultar {provider.asked[1:]} teniendolo al dia"
    )


def test_every_universe_ticker_gets_a_row_even_without_metadata(warehouse,
                                                                three_tickers,
                                                                monkeypatch):
    """Sin ficha no existen para el ranking, aunque tengan precios."""
    provider = FakeProvider(budget=0)
    monkeypatch.setattr(run_ingest, "get_price_provider", lambda name=None: provider)

    run_ingest.ingest_universe()
    rows = db.query("SELECT ticker, asset_class FROM instruments ORDER BY ticker")
    assert list(rows["ticker"]) == ["AAA", "BBB", "CCC"]
    assert (rows["asset_class"] == "equity").all(), (
        "sin clase de activo el ranking los ignora"
    )
