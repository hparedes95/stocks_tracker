"""Widgets embebidos de TradingView.

Unica puerta a los widgets: ninguna pagina escribe HTML de iframe directamente.

Tres reglas que no se pueden saltar:

1. La atribucion a TradingView es condicion de la licencia de uso gratuito. Se
   inyecta siempre y ninguna pagina puede desactivarla.
2. El `key` incluye el tema y se incrusta en el HTML. Sin esa diferencia el
   iframe no se vuelve a montar y el widget se queda con el tema anterior al
   pasar de claro a oscuro.
3. Sin simbolo equivalente NO se renderiza el widget. Se llama al `fallback`, que
   dibuja nuestro propio grafico. Un iframe con simbolo invalido muestra
   "Invalid symbol" y hace parecer que la aplicacion esta rota.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import streamlit as st

from ...core.config import get_settings
from .theme import is_dark

_BASE = "https://s3.tradingview.com/external-embedding/embed-widget-"

# Reserva del pie de atribucion. Son 32 y no los 18 que parecen bastar mirando
# nuestro HTML: el script de TradingView inyecta su propia hoja de estilos con
# `.tradingview-widget-copyright { line-height:32px !important }`, y un
# `!important` de autor gana a nuestro estilo en linea. O sea que el pie mide
# 32 aunque nosotros pidamos 16.
#
# Por eso el fragmento que publica TradingView usa `calc(100% - 32px)`: ese 32
# no era un margen a ojo, era su medida. Bajarlo a 18 recortaba el enlace de
# atribucion —que es condicion de la licencia, ver regla 1 de arriba— y ademas
# en silencio, porque `overflow:hidden` quito la barra de scroll que antes lo
# delataba.
#
# El fallo que se arreglo no era este numero, sino de donde se restaba: antes
# salia del alto que pedia la pagina, asi que en la cinta el widget se quedaba
# con 20 px. Ahora se anade por fuera y cada widget recibe lo que pidio.
_COPYRIGHT_PX = 32


def enabled() -> bool:
    """Los widgets se pueden apagar globalmente (modo sin conexion)."""
    return get_settings().tradingview_enabled


def _color_theme() -> str:
    return "dark" if is_dark() else "light"


def _locale_defaults() -> dict:
    tv_cfg = get_settings().ui.get("tradingview", {})
    return {
        "locale": get_settings().ui.get("locale", "es"),
        "timezone": tv_cfg.get("timezone", "Europe/Madrid"),
        "colorTheme": _color_theme(),
    }


def _render(widget: str, config: dict, height: int, key: str) -> None:
    """Monta el widget con su atribucion, dandole EXACTAMENTE `height` px.

    `height` es el alto del widget, no el del contenedor. Antes era lo segundo
    y cada widget restaba por su cuenta 30 px en su configuracion mientras el
    div reservaba 32: el widget se dibujaba dos pixeles mas alto que su caja y
    aparecia una barra de scroll interna en todos.

    Dos cosas mas que salieron al medirlo en el navegador:

    - El documento del iframe trae `body { margin: 8px }` por defecto. Son 16 px
      verticales que nadie habia contado, asi que el contenedor no cabia en el
      iframe y se recortaba por abajo. Se pone el margen a cero.
    - `overflow:hidden` en el body evita la barra de scroll que asomaba por el
      pixel de diferencia al redondear.

    El `key` se incrusta como atributo del contenedor a proposito: hace que el
    HTML difiera entre temas, y con ello Streamlit vuelve a montar el iframe.
    Sin eso, al pasar de claro a oscuro el widget se queda con el tema anterior.
    """
    payload = json.dumps(config)
    total = height + _COPYRIGHT_PX
    html = f"""
    <style>
      html, body {{ margin:0; padding:0; overflow:hidden; }}
      .tradingview-widget-container {{ width:100%; }}
      .tradingview-widget-container__widget {{ width:100%; }}
    </style>
    <div class="tradingview-widget-container" data-key="{key}" style="height:{total}px">
      <div class="tradingview-widget-container__widget" style="height:{height}px"></div>
      <div class="tradingview-widget-copyright"
           style="font:11px system-ui,-apple-system,sans-serif;
                  color:#898781;text-align:right">
        <a href="https://es.tradingview.com/" rel="noopener nofollow" target="_blank"
           style="color:#898781;text-decoration:none">Datos y graficos por TradingView</a>
      </div>
      <script type="text/javascript" src="{_BASE}{widget}.js" async>
      {payload}
      </script>
    </div>
    """
    st.iframe(html, height=total, width="stretch")


def _key(name: str, *parts: str) -> str:
    """El tema forma parte de la clave: si no, el iframe no se vuelve a montar."""
    return "_".join(["tv", name, *[str(p) for p in parts], _color_theme()])


def _unavailable(fallback: Callable[[], None] | None, message: str) -> None:
    if fallback is not None:
        fallback()
        st.caption(message)
    else:
        st.info(message)


# --------------------------------------------------------------------------
# Widgets
# --------------------------------------------------------------------------
def ticker_tape(symbols: list[dict] | None = None, compact: bool = True) -> None:
    """Cinta de cotizaciones. Va en la cabecera global, fuera de la navegacion."""
    if not enabled():
        return
    # Mismo criterio de simbolos que `market_overview`: los indices de EE. UU.
    # y el VIX van por contrato replicante porque los oficiales estan bajo
    # licencia de bolsa y el widget gratuito los omite en silencio.
    symbols = symbols or [
        {"proName": "FOREXCOM:SPXUSD", "title": "S&P 500"},
        {"proName": "FOREXCOM:NSXUSD", "title": "Nasdaq 100"},
        {"proName": "BME:IBC", "title": "IBEX 35"},
        {"proName": "INDEX:SX5E", "title": "Euro Stoxx 50"},
        {"proName": "CAPITALCOM:VIX", "title": "VIX"},
        {"proName": "FX:EURUSD", "title": "EUR/USD"},
        {"proName": "TVC:GOLD", "title": "Oro"},
        {"proName": "TVC:USOIL", "title": "Petroleo"},
        {"proName": "CRYPTO:BTCUSD", "title": "Bitcoin"},
    ]
    # OJO con el nombre: el `compact` de TradingView NO es el modo pequeno.
    # Es el que APILA descripcion sobre precio en dos lineas —pensado para
    # anchos estrechos— y ocupa mas alto, no menos. El de una sola linea es
    # `regular`.
    #
    # Estaban al reves: `compact=True` (que es lo que usa la cabecera en todas
    # las paginas) pedia el modo de dos lineas y le daba el alto de una. Con
    # `overflow:hidden` la segunda linea se recorta sin barra de scroll que lo
    # avise, que es justo el sintoma que se reporto.
    #
    # Nuestro `compact` significa "tira de cabecera, baja": eso es `regular`.
    display_mode = "regular" if compact else "compact"
    # Una linea con logo son ~46 px; dos, ~72. Se anaden 10 de holgura porque
    # el alto exacto depende de la fuente del sistema y del zoom del navegador,
    # y pasarse diez pixeles no cuesta nada mientras que quedarse corto recorta.
    height = 56 if compact else 82
    config = {
        **_locale_defaults(),
        "symbols": symbols,
        "showSymbolLogo": True,
        "isTransparent": True,
        "displayMode": display_mode,
    }
    _render("ticker-tape", config, height, _key("tape"))


def market_overview(height: int = 400, groups: list[dict] | None = None) -> None:
    """Panel de indices y activos macro, con precio EN VIVO.

    Complementa a las tarjetas propias, no las sustituye. Las nuestras salen de
    los cierres del almacen y llevan el contexto que calculamos —distancia a
    maximos, percentil del VIX, amplitud—; este widget no sabe nada de eso,
    pero se mueve mientras miras.

    Los datos viven dentro del iframe: no se pueden leer desde aqui (politica
    de mismo origen del navegador) ni alimentan ningun calculo. Es una ventana,
    no una fuente.
    """
    if not enabled():
        return

    # OJO con los nombres de las claves. Este widget espera `title` /
    # `originalTitle` en cada pestana y `s` / `d` en cada simbolo. Las claves
    # `name` / `displayName`, que parecen las obvias, son las de otro widget
    # (market-quotes) y aqui no dan ningun error: TradingView monta el marco,
    # no encuentra simbolos y lo deja vacio. Eso es justo lo que se vio en
    # pantalla la primera vez, y por eso hay un test que vigila las claves.
    groups = groups or [
        {
            "title": "Indices",
            "originalTitle": "Indices",
            "symbols": [
                # Los indices de EE. UU. van por su contrato replicante, no por
                # el indice al contado. `SP:SPX` y `NASDAQ:NDX` son datos bajo
                # licencia de bolsa: el widget gratuito no los sirve y omite la
                # fila sin decir nada, que es como desaparecieron de la pantalla.
                # Los CFD cotizan casi 24 h, asi que fuera del horario de Wall
                # Street marcan algo distinto del cierre oficial. La diferencia
                # se explica en el pie de la pestana.
                {"s": "FOREXCOM:SPXUSD", "d": "S&P 500"},
                {"s": "FOREXCOM:NSXUSD", "d": "Nasdaq 100"},
                # Los europeos si son libres en el widget.
                {"s": "BME:IBC", "d": "IBEX 35"},
                {"s": "INDEX:SX5E", "d": "Euro Stoxx 50"},
                {"s": "XETR:DAX", "d": "DAX"},
            ],
        },
        {
            "title": "Riesgo y materias primas",
            "originalTitle": "Riesgo",
            "symbols": [
                # Mismo motivo que arriba: el VIX del CBOE tampoco entra en el
                # widget gratuito.
                {"s": "CAPITALCOM:VIX", "d": "VIX"},
                {"s": "TVC:GOLD", "d": "Oro"},
                {"s": "TVC:USOIL", "d": "Petroleo"},
                {"s": "FX:EURUSD", "d": "EUR/USD"},
                {"s": "TVC:DXY", "d": "Dolar"},
            ],
        },
        {
            "title": "Cripto",
            "originalTitle": "Cripto",
            "symbols": [
                {"s": "CRYPTO:BTCUSD", "d": "Bitcoin"},
                {"s": "CRYPTO:ETHUSD", "d": "Ethereum"},
            ],
        },
    ]

    config = {
        **_locale_defaults(),
        "tabs": groups,
        "dateRange": "1D",
        "showChart": True,
        "isTransparent": True,
        "showSymbolLogo": True,
        "showFloatingTooltip": True,
        "largeChartUrl": "",
        "width": "100%",
        "height": height,
        "plotLineColorGrowing": "rgba(41, 98, 255, 1)",
        "plotLineColorFalling": "rgba(41, 98, 255, 1)",
        "belowLineFillColorGrowing": "rgba(41, 98, 255, 0.12)",
        "belowLineFillColorFalling": "rgba(41, 98, 255, 0.12)",
        "belowLineFillColorGrowingBottom": "rgba(41, 98, 255, 0)",
        "belowLineFillColorFallingBottom": "rgba(41, 98, 255, 0)",
        "symbolActiveColor": "rgba(41, 98, 255, 0.12)",
        "gridLineColor": "rgba(240, 243, 250, 0)",
        "scaleFontColor": "rgba(120, 123, 134, 1)",
    }
    _render("market-overview", config, height, _key("overview"))


def advanced_chart(
    tv_symbol: str | None, height: int = 720, interval: str = "D",
    studies: list[str] | None = None, fallback: Callable[[], None] | None = None,
) -> None:
    """Grafico principal de velas. Es la herramienta familiar del usuario.

    Alto generoso a proposito: con la barra lateral de dibujo, la de tiempos y
    dos indicadores debajo, por debajo de unos 700 px el area de velas queda en
    una franja donde no se distingue nada.
    """
    if not enabled() or not tv_symbol:
        _unavailable(
            fallback,
            "Sin equivalencia en TradingView; se muestra nuestro grafico."
            if not tv_symbol
            else "Widgets de TradingView desactivados; se muestra nuestro grafico.",
        )
        return
    config = {
        **_locale_defaults(),
        "symbol": tv_symbol,
        "interval": interval,
        "theme": _color_theme(),
        "style": "1",
        # Alto explicito y NO `autosize`. Este grafico vive en una pestana que
        # arranca oculta, y una pestana oculta en Streamlit deja el iframe en
        # 0x0: `autosize` mide su contenedor al arrancar, se encuentra cero y
        # dibuja un grafico diminuto que ya no vuelve a crecer al abrir la
        # pestana. Medido en el navegador: los iframes de las pestanas
        # inactivas daban 0x0 mientras el visible daba 980x574.
        "width": "100%",
        "height": height,
        "hide_side_toolbar": False,
        "allow_symbol_change": False,
        "studies": studies or ["STD;SMA", "STD;RSI"],
        "backgroundColor": "rgba(0,0,0,0)",
        "support_host": "https://www.tradingview.com",
    }
    _render("advanced-chart", config, height, _key("chart", tv_symbol))


def technical_analysis(tv_symbol: str | None, height: int = 400,
                       interval: str = "1D") -> None:
    """Medidor tecnico de TradingView.

    Lleva rotulo obligatorio: es la opinion de un tercero, no nuestra senal. Sin
    esa aclaracion, un "STRONG BUY" en pantalla se lee como recomendacion de
    esta herramienta, que es justo lo que no queremos.
    """
    if not enabled() or not tv_symbol:
        return
    config = {
        **_locale_defaults(),
        "symbol": tv_symbol,
        "interval": interval,
        "showIntervalTabs": True,
        "displayMode": "single",
        "isTransparent": True,
        "width": "100%",
        "height": height,
    }
    _render("technical-analysis", config, height, _key("ta", tv_symbol))
    st.caption(
        "Opinion tecnica de TradingView. Contraste externo, "
        "no es nuestra senal ni una recomendacion."
    )


def symbol_info(tv_symbol: str | None, height: int = 180) -> None:
    if not enabled() or not tv_symbol:
        return
    config = {**_locale_defaults(), "symbol": tv_symbol, "width": "100%",
              "isTransparent": True}
    _render("symbol-info", config, height, _key("info", tv_symbol))


def fundamental_data(tv_symbol: str | None, height: int = 500) -> None:
    """Estados financieros. Cubre el hueco de yfinance, sobre todo en Europa."""
    if not enabled() or not tv_symbol:
        return
    config = {
        **_locale_defaults(), "symbol": tv_symbol, "displayMode": "regular",
        "isTransparent": True, "width": "100%", "height": height,
    }
    _render("financials", config, height, _key("fin", tv_symbol))


def company_profile(tv_symbol: str | None, height: int = 400) -> None:
    if not enabled() or not tv_symbol:
        return
    config = {
        **_locale_defaults(), "symbol": tv_symbol, "isTransparent": True,
        "width": "100%", "height": height,
    }
    _render("symbol-profile", config, height, _key("profile", tv_symbol))


def stock_heatmap(data_source: str = "SPX500", height: int = 560) -> None:
    """Mapa de calor por sectores. Confirmacion visual de que sector tira hoy."""
    if not enabled():
        return
    config = {
        **_locale_defaults(),
        "dataSource": data_source,
        "grouping": "sector",
        "blockSize": "market_cap_basic",
        "blockColor": "change",
        "hasTopBar": False,
        "isDataSetEnabled": False,
        "isZoomEnabled": True,
        "hasSymbolTooltip": True,
        "isTransparent": True,
        "width": "100%",
        "height": height,
    }
    _render("stock-heatmap", config, height, _key("heatmap", data_source))


def crypto_heatmap(height: int = 520) -> None:
    if not enabled():
        return
    config = {
        **_locale_defaults(), "dataSource": "Crypto", "blockSize": "market_cap_calc",
        "blockColor": "change", "isTransparent": True, "width": "100%",
        "height": height,
    }
    _render("crypto-coins-heatmap", config, height, _key("cheatmap"))


def top_stories(tv_symbol: str | None = None, height: int = 520) -> None:
    if not enabled():
        return
    config = {
        **_locale_defaults(),
        "feedMode": "symbol" if tv_symbol else "all_symbols",
        "isTransparent": True,
        "displayMode": "regular",
        "width": "100%",
        "height": height,
    }
    if tv_symbol:
        config["symbol"] = tv_symbol
    _render("timeline", config, height, _key("news", tv_symbol or "all"))


def economic_calendar(height: int = 520) -> None:
    if not enabled():
        return
    config = {
        **_locale_defaults(), "importanceFilter": "0,1",
        "countryFilter": "es,eu,us", "isTransparent": True,
        "width": "100%", "height": height,
    }
    _render("events", config, height, _key("calendar"))


def screener(market: str = "america", height: int = 520) -> None:
    """Screener de TradingView.

    Complemento, nunca sustituto del nuestro: el nuestro explica por que aparece
    cada valor, este no. Por eso va siempre en una pestana secundaria.
    """
    if not enabled():
        return
    config = {
        **_locale_defaults(), "market": market, "defaultColumn": "overview",
        "showToolbar": True, "isTransparent": True,
        "width": "100%", "height": height,
    }
    _render("screener", config, height, _key("screener", market))


def mini_symbol_overview(tv_symbol: str | None, height: int = 200) -> None:
    if not enabled() or not tv_symbol:
        return
    config = {
        # Alto explicito por el mismo motivo que el grafico grande: esto vive
        # dentro de columnas y desplegables que pueden empezar cerrados, y
        # `autosize` mediria cero al arrancar y ya no creceria.
        **_locale_defaults(), "symbol": tv_symbol, "dateRange": "6M",
        "isTransparent": True, "chartOnly": False,
        "width": "100%", "height": height,
    }
    _render("mini-symbol-overview", config, height, _key("mini", tv_symbol))
