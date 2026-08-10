"""Tests de los widgets embebidos de TradingView.

Estos widgets fallan de la peor manera posible: en silencio. Si una clave de
configuracion no es la que el widget espera, TradingView no da ningun error —
monta el marco, no encuentra nada que pintar y deja un hueco en blanco. Desde
fuera es indistinguible de "la red bloquea TradingView", y eso convierte un
fallo de una linea en media hora de diagnostico equivocado.

No se puede comprobar aqui que TradingView devuelva datos (haria falta red y
un navegador). Lo que si se puede comprobar es que le mandamos las claves que
su documentacion pide, que es donde estuvo el fallo.
"""

from __future__ import annotations

import json

import pytest

from stocks_tracker.app.components import tv_widgets


@pytest.fixture
def captured(monkeypatch):
    """Sustituye el renderizado por una captura de lo que se le manda."""
    calls: list[dict] = []

    def fake_render(widget, config, height, key):
        calls.append({"widget": widget, "config": config, "height": height, "key": key})

    monkeypatch.setattr(tv_widgets, "enabled", lambda: True)
    monkeypatch.setattr(tv_widgets, "_render", fake_render)
    return calls


# ---------------------------------------------------------------------------
# Panel de mercado (precios en vivo de la portada)
# ---------------------------------------------------------------------------
def test_market_overview_uses_the_keys_this_widget_expects(captured):
    """El fallo real: se usaron `name` / `displayName`, que son las claves del
    widget market-quotes. market-overview quiere `title` / `originalTitle` en
    la pestana y `s` / `d` en el simbolo, y con las otras se queda vacio."""
    tv_widgets.market_overview()

    tabs = captured[0]["config"]["tabs"]
    assert tabs, "el panel se manda sin ninguna pestana"

    for tab in tabs:
        assert "title" in tab, f"pestana sin 'title': {tab.keys()}"
        assert "originalTitle" in tab, f"pestana sin 'originalTitle': {tab.keys()}"
        assert tab["symbols"], f"pestana '{tab['title']}' sin simbolos"
        for symbol in tab["symbols"]:
            assert set(symbol) == {"s", "d"}, (
                f"simbolo con claves incorrectas: {symbol}. "
                "market-overview solo entiende 's' (simbolo) y 'd' (etiqueta)."
            )


def test_market_overview_does_not_carry_the_other_widgets_keys(captured):
    """Guardarrail directo contra la reincidencia: `name` y `displayName` son
    las claves que parecen las obvias y son las equivocadas."""
    tv_widgets.market_overview()
    payload = json.dumps(captured[0]["config"])

    for wrong in ('"name"', '"displayName"', '"originalName"'):
        assert wrong not in payload, (
            f"{wrong} es de market-quotes; aqui deja el panel en blanco"
        )


def test_market_overview_fits_inside_its_container(captured):
    """`_render` reserva 32 px para el pie de atribucion. Si el widget se cree
    mas alto que el hueco que tiene, se come la ultima fila."""
    tv_widgets.market_overview(height=380)
    call = captured[0]
    assert call["config"]["height"] <= call["height"] - 32


def test_market_overview_accepts_custom_groups(captured):
    mine = [{"title": "Mio", "originalTitle": "Mio", "symbols": [{"s": "SP:SPX", "d": "X"}]}]
    tv_widgets.market_overview(groups=mine)
    assert captured[0]["config"]["tabs"] == mine


def test_market_overview_is_silent_when_widgets_are_off(monkeypatch, captured):
    monkeypatch.setattr(tv_widgets, "enabled", lambda: False)
    tv_widgets.market_overview()
    assert not captured, "se renderiza un widget con TradingView desactivado"


# ---------------------------------------------------------------------------
# Cinta de cotizaciones
# ---------------------------------------------------------------------------
def test_ticker_tape_uses_its_own_key_names(captured):
    """La cinta si usa `proName` / `title`. Mismo proveedor, esquema distinto
    por widget: por eso cada uno necesita su comprobacion."""
    tv_widgets.ticker_tape()

    symbols = captured[0]["config"]["symbols"]
    assert symbols
    for symbol in symbols:
        assert "proName" in symbol, f"simbolo de cinta sin 'proName': {symbol}"


# ---------------------------------------------------------------------------
# Reglas comunes a todos los widgets
# ---------------------------------------------------------------------------
def test_every_widget_sends_a_serialisable_config(captured):
    """Un objeto no serializable reventaria en `json.dumps` dentro del render,
    ya con la pagina a medio pintar."""
    tv_widgets.market_overview()
    tv_widgets.ticker_tape()
    tv_widgets.advanced_chart("NASDAQ:AAPL")
    tv_widgets.technical_analysis("NASDAQ:AAPL")
    tv_widgets.stock_heatmap()
    tv_widgets.economic_calendar()
    tv_widgets.screener()

    assert len(captured) == 7
    for call in captured:
        json.dumps(call["config"])  # no debe lanzar


def test_the_theme_is_part_of_the_key(captured):
    """Sin esto el iframe no se vuelve a montar al cambiar de tema y se queda
    con los colores anteriores."""
    tv_widgets.market_overview()
    assert captured[0]["key"].endswith(("light", "dark"))


def test_attribution_is_always_rendered():
    """Condicion de la licencia de uso gratuito. No depende de la red: va en
    nuestro HTML, no dentro del iframe de TradingView."""
    src = (tv_widgets.__file__)
    html = open(src, encoding="utf-8").read()
    assert "tradingview-widget-copyright" in html
    assert "TradingView" in html
