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
import pathlib

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


# Simbolos que el widget gratuito NO sirve: son datos bajo licencia de bolsa.
# No dan error — la fila desaparece y el resto del panel se pinta como si nada,
# asi que el fallo solo se ve si sabes que esos valores tenian que estar.
_LICENSED = ("SP:SPX", "NASDAQ:NDX", "TVC:VIX", "CBOE:VIX", "INDEX:NDX")


def test_market_overview_avoids_licensed_symbols(captured):
    tv_widgets.market_overview()
    payload = json.dumps(captured[0]["config"])

    for symbol in _LICENSED:
        assert symbol not in payload, (
            f"{symbol} necesita licencia de bolsa: el widget gratuito lo omite "
            "sin avisar y esa fila no aparece"
        )


def test_ticker_tape_avoids_licensed_symbols(captured):
    """La cinta va en la cabecera de las nueve paginas, asi que un simbolo que
    no carga se nota mas aqui que en ningun otro sitio."""
    tv_widgets.ticker_tape()
    payload = json.dumps(captured[0]["config"])

    for symbol in _LICENSED:
        assert symbol not in payload, f"{symbol} no carga en el widget gratuito"


def test_the_two_headers_show_the_same_indices(captured):
    """La cinta de arriba y el panel en vivo miran los mismos mercados. Si se
    corrige un simbolo en uno y no en el otro, el usuario ve dos precios
    distintos para el mismo indice en la misma pantalla."""
    tv_widgets.ticker_tape()
    tv_widgets.market_overview()

    tape = {s["proName"] for s in captured[0]["config"]["symbols"]}
    panel = {s["s"] for tab in captured[1]["config"]["tabs"] for s in tab["symbols"]}

    assert {"FOREXCOM:SPXUSD", "FOREXCOM:NSXUSD", "BME:IBC"} <= tape & panel, (
        "la cinta y el panel usan simbolos distintos para el mismo indice"
    )


def test_market_overview_asks_for_exactly_the_height_it_gets(captured):
    """`height` es el alto del WIDGET, no el del contenedor: `_render` anade el
    pie de atribucion por fuera. Antes cada widget restaba 30 por su cuenta
    mientras el div reservaba 32, y el widget se dibujaba dos pixeles mas alto
    que su caja: barra de scroll interna en todos."""
    tv_widgets.market_overview(height=380)
    call = captured[0]
    assert call["config"]["height"] == call["height"]


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


# ---------------------------------------------------------------------------
# Tamanos: que ningun widget salga achatado ni cortado
# ---------------------------------------------------------------------------
def html_of(widget="advanced-chart", config=None, height=400, key="k"):
    """El HTML que `_render` manda al iframe, sin pasar por Streamlit."""
    capturado = {}

    class FakeSt:
        @staticmethod
        def iframe(html, height, width):
            capturado["html"] = html
            capturado["height"] = height

    import stocks_tracker.app.components.tv_widgets as m

    real = m.st
    m.st = FakeSt
    try:
        m._render(widget, config or {}, height, key)
    finally:
        m.st = real
    return capturado


def test_the_container_is_the_widget_plus_the_attribution():
    """Y nada mas: lo que pide la pagina es lo que recibe el widget."""
    cap = html_of(height=400)
    assert f"height:{400 + tv_widgets._COPYRIGHT_PX}px" in cap["html"]
    assert "height:400px" in cap["html"]
    assert cap["height"] == 400 + tv_widgets._COPYRIGHT_PX


def test_the_attribution_gets_the_space_tradingview_gives_it():
    """El script de TradingView inyecta `.tradingview-widget-copyright` con
    `line-height:32px !important`, y un `!important` de autor gana a nuestro
    estilo en linea: el pie mide 32 aunque pidamos 16.

    Por eso su propio fragmento usa `calc(100% - 32px)`. Reservar menos recorta
    el enlace de atribucion —condicion de la licencia— y ademas en silencio,
    porque `overflow:hidden` quito la barra de scroll que lo delataba.
    """
    assert tv_widgets._COPYRIGHT_PX >= 32


def test_the_iframe_body_margin_is_reset():
    """El documento del iframe trae `body { margin: 8px }` por defecto: 16 px
    verticales que nadie contaba, con lo que el contenedor no cabia dentro del
    iframe y se recortaba por abajo. Medido en el navegador."""
    html = html_of()["html"]
    assert "margin:0" in html.replace(" ", "")


def test_no_widget_measures_its_own_container():
    """`autosize` mide el contenedor al arrancar. Estos widgets viven en
    pestanas y desplegables que empiezan cerrados, y en Streamlit un iframe
    oculto es de 0x0: el widget se dibuja diminuto y ya no vuelve a crecer al
    abrir la pestana. Es exactamente el sintoma que se reporto.

    Medido: los iframes de las pestanas inactivas daban 0x0 mientras el
    visible daba 980x574.
    """
    fuente = pathlib.Path(tv_widgets.__file__).read_text(encoding="utf-8")
    codigo = [linea for linea in fuente.splitlines()
              if "autosize" in linea and not linea.strip().startswith("#")]
    assert codigo == [], f"algun widget sigue midiendo su contenedor: {codigo}"


# Alto de las dos maquetaciones de la cinta, con la holgura ya dentro. El test
# de abajo se apoya en esto en vez de repetir el numero de produccion: una
# asercion `>= 46` contra un valor de exactamente 46 esta pegada a su propio
# limite y solo puede fallar si el alto BAJA, que es lo contrario de la
# regresion que dice vigilar.
_UNA_LINEA_PX = 46
_DOS_LINEAS_PX = 72


def test_the_header_tape_asks_for_the_single_line_layout(captured):
    """El `compact` de TradingView no es el modo pequeno: es el que APILA
    descripcion sobre precio en dos lineas y ocupa MAS alto. El de una sola
    linea es `regular`.

    Estaban al reves, y como la cabecera usa `compact=True` en todas las
    paginas, pedia el modo de dos lineas con el alto de una. Con
    `overflow:hidden` la segunda linea se recorta sin barra que lo avise.
    """
    tv_widgets.ticker_tape(compact=True)
    assert captured[0]["config"]["displayMode"] == "regular"
    assert captured[0]["height"] > _UNA_LINEA_PX, (
        "sin holgura: el alto exacto depende de la fuente del sistema y del "
        "zoom, y quedarse corto recorta"
    )


def test_the_tall_tape_has_room_for_two_lines(captured):
    tv_widgets.ticker_tape(compact=False)
    assert captured[0]["config"]["displayMode"] == "compact"
    assert captured[0]["height"] > _DOS_LINEAS_PX


def test_the_main_chart_is_big_enough_to_read(captured):
    """Con la barra lateral de dibujo, la de tiempos y dos indicadores debajo,
    por debajo de unos 700 px el area de velas queda en una franja donde no se
    distingue nada."""
    tv_widgets.advanced_chart("NASDAQ:AAPL")
    assert captured[0]["height"] >= 700


def test_every_sized_widget_agrees_with_its_box(captured):
    """Barrido: el alto que cada widget pide en su configuracion tiene que ser
    el mismo que el de la caja que `_render` le da. Un desajuste de un pixel
    saca una barra de scroll dentro del iframe."""
    tv_widgets.market_overview(height=380)
    tv_widgets.advanced_chart("NASDAQ:AAPL", height=700)
    tv_widgets.technical_analysis("NASDAQ:AAPL", height=420)
    tv_widgets.fundamental_data("NASDAQ:AAPL", height=560)
    tv_widgets.company_profile("NASDAQ:AAPL", height=400)
    tv_widgets.stock_heatmap(height=560)
    tv_widgets.crypto_heatmap(height=520)
    tv_widgets.top_stories("NASDAQ:AAPL", height=560)
    tv_widgets.economic_calendar(height=520)
    tv_widgets.screener(height=460)
    tv_widgets.mini_symbol_overview("NASDAQ:AAPL", height=220)

    revisados = 0
    for call in captured:
        alto = call["config"].get("height")
        if alto is None:
            continue          # widgets que se ajustan a su contenido
        assert alto == call["height"], (
            f"{call['widget']}: pide {alto} px dentro de una caja de "
            f"{call['height']} px"
        )
        revisados += 1
    assert revisados >= 10, "el barrido no ha comprobado casi nada"
