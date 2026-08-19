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


# ---------------------------------------------------------------------------
# La pagina cambia de formato
# ---------------------------------------------------------------------------
NASDAQ_CON_SYMBOL = """
<table><tr><th>Company</th><th>Symbol</th><th>GICS Sector</th></tr>
<tr><td>Apple</td><td>AAPL</td><td>Tech</td></tr></table>
"""


def test_the_nasdaq_table_is_found_under_either_column_name(monkeypatch):
    """Wikipedia ha usado 'Ticker' y 'Symbol' para la misma tabla. Con un solo
    nombre aceptado, un retoque de la pagina dejaba el universo en 20 valores y
    el mensaje era 'No se encontro la tabla de constituyentes'."""
    monkeypatch.setattr(up.requests, "get",
                        lambda *a, **k: FakeResponse(NASDAQ_CON_SYMBOL))
    out = up.UniverseProvider().fetch_constituents("NASDAQ100")
    assert list(out["ticker"]) == ["AAPL"]

    con_ticker = NASDAQ_CON_SYMBOL.replace("<th>Symbol</th>", "<th>Ticker</th>")
    monkeypatch.setattr(up.requests, "get",
                        lambda *a, **k: FakeResponse(con_ticker))
    assert list(up.UniverseProvider().fetch_constituents("NASDAQ100")["ticker"]) == ["AAPL"]


def test_the_error_says_which_columns_there_were(monkeypatch):
    """Para no tener que adivinar como ha cambiado la pagina."""
    monkeypatch.setattr(
        up.requests, "get",
        lambda *a, **k: FakeResponse("<table><tr><th>Empresa</th></tr>"
                                     "<tr><td>x</td></tr></table>"),
    )
    with pytest.raises(ProviderError, match="Empresa"):
        up.UniverseProvider().fetch_constituents("NASDAQ100")


# ---------------------------------------------------------------------------
# Reconocer la tabla por su contenido
# ---------------------------------------------------------------------------
def _page_with_odd_headers(n: int = 60) -> str:
    """Una pagina como la que devolvio Wikipedia: tablas de adorno primero y la
    de constituyentes con una cabecera que no reconocemos."""
    ruido = (
        "<table><tr><th>Year</th><th>Closing level</th></tr>"
        "<tr><td>2024</td><td>21012</td></tr></table>"
        "<table><tr><th>Category</th><th>All-Time Highs[8]</th></tr>"
        "<tr><td>x</td><td>y</td></tr></table>"
    )
    # Tickers de verdad: solo letras. Los de EE. UU. no llevan digitos, y el
    # reconocimiento por contenido cuenta con ello.
    filas = "".join(
        f"<tr><td>Empresa {i}</td>"
        f"<td>{chr(65 + i // 26)}{chr(65 + i % 26)}CO</td></tr>"
        for i in range(n)
    )
    buena = f"<table><tr><th>Nombre</th><th>Codigo</th></tr>{filas}</table>"
    return f"<html><body>{ruido}{buena}</body></html>"


def test_the_constituents_table_is_found_by_its_content(monkeypatch):
    """Wikipedia ya ha cambiado dos veces la cabecera y una vez la estructura.
    Perseguir nombres de columna es perseguir algo que no controlamos."""
    monkeypatch.setattr(up.requests, "get",
                        lambda *a, **k: FakeResponse(_page_with_odd_headers()))

    out = up.UniverseProvider().fetch_constituents("NASDAQ100")
    assert len(out) >= 50
    assert all(t.isupper() for t in out["ticker"])


def test_a_column_of_years_is_not_mistaken_for_tickers():
    """El reconocimiento por contenido no puede tragarse cualquier columna."""
    anios = pd.Series([str(y) for y in range(1950, 2030)])
    assert not up._looks_like_tickers(anios)

    frases = pd.Series(["Apple Inc."] * 80)
    assert not up._looks_like_tickers(frases)


def test_a_short_table_is_not_mistaken_for_the_index():
    """Cinco tickers no son un indice: mejor el respaldo que una tabla de
    adorno que casualmente tenga codigos."""
    pocos = pd.Series(["AAPL", "MSFT", "NVDA", "AMZN", "META"])
    assert not up._looks_like_tickers(pocos)


def test_multi_class_tickers_are_recognised():
    """BRK.B y BF.B tienen punto y siguen siendo tickers."""
    otros = [f"{chr(65 + i // 26)}{chr(65 + i % 26)}X" for i in range(60)]
    valores = pd.Series(["BRK.B", "BF.B"] + otros)
    assert up._looks_like_tickers(valores)


def test_the_error_lists_every_table_it_saw(monkeypatch):
    """Con solo las cinco primeras, la tabla buena podia quedar fuera del
    diagnostico justo cuando hacia falta verla."""
    muchas = "".join(
        f"<table><tr><th>Col{i}</th></tr><tr><td>x</td></tr></table>"
        for i in range(9)
    )
    monkeypatch.setattr(up.requests, "get", lambda *a, **k: FakeResponse(muchas))
    with pytest.raises(ProviderError, match="Col8"):
        up.UniverseProvider().fetch_constituents("NASDAQ100")


# ---------------------------------------------------------------------------
# Falta de lxml: un problema de instalacion que parecia uno de red
# ---------------------------------------------------------------------------
def test_a_missing_lxml_is_not_reported_as_a_download_failure(monkeypatch):
    """`pandas.read_html` necesita lxml, y lxml es una dependencia OPCIONAL.

    Sin distinguir este caso, la falta del paquete salia como "No se pudo leer
    <url>" y el respaldo la convertia en "manual (fallo la descarga)". El
    mensaje culpaba a la red de un problema de instalacion, asi que quien lo
    viera iria a mirar su conexion mientras el universo se quedaba reducido a
    la lista manual sin motivo aparente. Es el mismo error de diagnostico que
    ya costo dias con pandas 3.0.

    Esto es exactamente lo que ocurria en el flujo de CI, que instala `.[dev]`
    y no `.[data]`.
    """
    monkeypatch.setattr(up.requests, "get",
                        lambda *a, **k: FakeResponse("<table></table>"))

    def sin_lxml(*a, **k):
        raise ImportError("`Import lxml` failed.  Use pip or conda to install "
                          "the lxml package.")

    monkeypatch.setattr(up.pd, "read_html", sin_lxml)

    with pytest.raises(up.MissingParserError) as fallo:
        up.UniverseProvider().fetch_constituents("NASDAQ100")
    assert "lxml" in str(fallo.value)
    assert "NO es un problema de red" in str(fallo.value)


def test_a_missing_lxml_says_so_in_the_fallback_origin(monkeypatch):
    """El origen que se muestra tiene que decir la causa real. Con el mismo
    texto que un fallo de descarga, el unico sintoma seria un universo reducido
    y nadie sabria que se arregla instalando un paquete."""
    monkeypatch.setattr(up.requests, "get",
                        lambda *a, **k: FakeResponse("<table></table>"))
    monkeypatch.setattr(up.pd, "read_html",
                        lambda *a, **k: (_ for _ in ()).throw(ImportError("no lxml")))

    tickers, origen = up.resolve_universe("NASDAQ100", ["AAA", "BBB"], "wikipedia")
    assert tickers == ["AAA", "BBB"]
    assert origen == "manual (falta lxml)"


def test_a_real_download_failure_still_says_download_failure(monkeypatch):
    """La contraprueba: si todo dijera "falta lxml", el mensaje nuevo mandaria
    a instalar un paquete cada vez que Wikipedia devolviera un 403."""
    def cae_la_red(*a, **k):
        raise ConnectionError("timeout")

    monkeypatch.setattr(up.requests, "get", cae_la_red)
    _, origen = up.resolve_universe("NASDAQ100", ["AAA"], "wikipedia")
    assert origen == "manual (fallo la descarga)"


def test_the_missing_parser_error_is_still_a_provider_error():
    """Quien captura `ProviderError` a secas —el codigo que ya existia— tiene
    que seguir capturandolo. Si no, anadir el tipo nuevo convertiria un
    respaldo elegante en una excepcion sin capturar en mitad de la ingesta."""
    assert issubclass(up.MissingParserError, ProviderError)
