"""Tests del lector de constituyentes.

Existe por una averia que costo dias: la descarga fallaba SIEMPRE, en cualquier
maquina, por un cambio de pandas 3.0, y el respaldo lo convertia en un discreto
"manual (fallo la descarga)". El universo se quedaba en 50 valores y la causa
parecia de red.

Dos lecciones, una por test:
1. Hay que ejercitar el parseo de verdad, no solo la funcion que lo envuelve.
2. Un respaldo que se traga el motivo del fallo convierte un bug de una linea
   en una investigacion.
"""

from __future__ import annotations

import io

import pandas as pd
import pytest

from stocks_tracker.providers import universe_provider as up
from stocks_tracker.providers.base import ProviderError

TABLE = """
<html><body>
<table class="wikitable">
  <tr><th>Symbol</th><th>Security</th><th>GICS Sector</th></tr>
  <tr><td>AAPL</td><td>Apple Inc.</td><td>Information Technology</td></tr>
  <tr><td>BRK.B</td><td>Berkshire Hathaway</td><td>Financials</td></tr>
  <tr><td>MSFT</td><td>Microsoft</td><td>Information Technology</td></tr>
</table>
</body></html>
"""


class FakeResponse:
    def __init__(self, text: str, status: int = 200):
        self.text = text
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"{self.status_code} Client Error")


@pytest.fixture
def wikipedia_ok(monkeypatch):
    monkeypatch.setattr(up.requests, "get",
                        lambda *a, **k: FakeResponse(TABLE))


# ---------------------------------------------------------------------------
# El fallo que se escapo
# ---------------------------------------------------------------------------
def test_read_html_is_not_given_a_bare_string():
    """pandas 3.0 dejo de aceptar HTML literal y trata la cadena como una RUTA
    DE FICHERO. El sintoma era un FileNotFoundError con el HTML entero dentro
    del mensaje."""
    with pytest.raises((FileNotFoundError, ValueError)):
        pd.read_html(TABLE)

    # Envuelto, funciona en pandas 2 y en pandas 3.
    assert pd.read_html(io.StringIO(TABLE))


def test_constituents_are_actually_parsed(wikipedia_ok):
    """El test que faltaba: ejercitar el parseo de punta a punta."""
    out = up.UniverseProvider().fetch_constituents("SP500")

    assert list(out["ticker"]) == ["AAPL", "BRK-B", "MSFT"], (
        "no se han extraido los tickers, o no se ha normalizado BRK.B"
    )
    assert out.loc[0, "name"] == "Apple Inc."
    assert out.loc[0, "gics_sector"] == "Information Technology"


def test_the_reason_for_falling_back_is_printed(monkeypatch, capsys):
    """Sin el motivo, un 403, un timeout, un cambio de formato y un bug nuestro
    son indistinguibles desde la consola."""
    def boom(*args, **kwargs):
        raise RuntimeError("403 Client Error: Forbidden")

    monkeypatch.setattr(up.requests, "get", boom)
    tickers, origin = up.resolve_universe("SP500", ["AAPL"], source="wikipedia")

    assert tickers == ["AAPL"]
    assert origin == "manual (fallo la descarga)"
    assert "403" in capsys.readouterr().out, "el motivo del fallo se ha perdido"


# ---------------------------------------------------------------------------
# Respaldo
# ---------------------------------------------------------------------------
def test_a_successful_download_merges_with_the_manual_list(monkeypatch):
    big = TABLE.replace(
        "</table>",
        "".join(f"<tr><td>T{i}</td><td>N{i}</td><td>S</td></tr>" for i in range(25))
        + "</table>",
    )
    monkeypatch.setattr(up.requests, "get", lambda *a, **k: FakeResponse(big))

    tickers, origin = up.resolve_universe("SP500", ["SOLOMIO"], source="wikipedia")
    assert origin == "wikipedia"
    assert "AAPL" in tickers
    assert "SOLOMIO" in tickers, "la lista manual deberia sumarse, no perderse"


def test_a_short_download_is_treated_as_incomplete(wikipedia_ok):
    """Tres tickers no son el S&P 500: es mejor el respaldo que un indice roto."""
    tickers, origin = up.resolve_universe("SP500", ["AAPL", "MSFT"],
                                          source="wikipedia")
    assert origin == "manual (descarga incompleta)"
    assert tickers == ["AAPL", "MSFT"]


def test_a_manual_universe_does_not_hit_the_network(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("no deberia salir a la red")

    monkeypatch.setattr(up.requests, "get", boom)
    tickers, origin = up.resolve_universe("IBEX35", ["SAN.MC"], source="manual")
    assert origin == "manual"
    assert tickers == ["SAN.MC"]


def test_an_unknown_universe_is_refused():
    with pytest.raises(ProviderError, match="Sin fuente configurada"):
        up.UniverseProvider().fetch_constituents("DESCONOCIDO")


def test_a_page_without_the_expected_column_is_refused(monkeypatch):
    """Si Wikipedia cambia la cabecera, mejor el respaldo que columnas vacias."""
    monkeypatch.setattr(
        up.requests, "get",
        lambda *a, **k: FakeResponse("<table><tr><th>Otra</th></tr>"
                                     "<tr><td>x</td></tr></table>"),
    )
    with pytest.raises(ProviderError):
        up.UniverseProvider().fetch_constituents("SP500")


def test_the_user_agent_identifies_the_tool_and_a_contact():
    """La politica de Wikimedia devuelve 403 a los agentes anonimos."""
    agent = up._HEADERS["User-Agent"]
    assert "stocks-tracker" in agent
    assert "http" in agent, "el User-Agent no ofrece punto de contacto"
