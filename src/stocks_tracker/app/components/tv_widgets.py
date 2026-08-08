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

# Margen que evita la barra de scroll interna que provoca el div de atribucion.
_HEIGHT_PADDING = 14


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
    """Construye el contenedor del widget, con atribucion obligatoria.

    El `key` se incrusta como atributo del contenedor a proposito: hace que el
    HTML difiera entre temas, y con ello Streamlit vuelve a montar el iframe.
    Sin eso, al pasar de claro a oscuro el widget se queda con el tema anterior.
    """
    payload = json.dumps(config)
    html = f"""
    <div class="tradingview-widget-container" data-key="{key}" style="height:{height}px">
      <div class="tradingview-widget-container__widget" style="height:{height - 32}px"></div>
      <div class="tradingview-widget-copyright"
           style="font:11px system-ui,-apple-system,sans-serif;
                  color:#898781;text-align:right;padding-top:2px">
        <a href="https://es.tradingview.com/" rel="noopener nofollow" target="_blank"
           style="color:#898781;text-decoration:none">Datos y graficos por TradingView</a>
      </div>
      <script type="text/javascript" src="{_BASE}{widget}.js" async>
      {payload}
      </script>
    </div>
    """
    st.iframe(html, height=height + _HEIGHT_PADDING, width="stretch")


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
    symbols = symbols or [
        {"proName": "SP:SPX", "title": "S&P 500"},
        {"proName": "NASDAQ:NDX", "title": "Nasdaq 100"},
        {"proName": "BME:IBC", "title": "IBEX 35"},
        {"proName": "INDEX:SX5E", "title": "Euro Stoxx 50"},
        {"proName": "TVC:VIX", "title": "VIX"},
        {"proName": "FX:EURUSD", "title": "EUR/USD"},
        {"proName": "TVC:GOLD", "title": "Oro"},
        {"proName": "TVC:USOIL", "title": "Petroleo"},
        {"proName": "CRYPTO:BTCUSD", "title": "Bitcoin"},
    ]
    height = 52 if compact else 84
    config = {
        **_locale_defaults(),
        "symbols": symbols,
        "showSymbolLogo": True,
        "isTransparent": True,
        "displayMode": "compact" if compact else "regular",
    }
    _render("ticker-tape", config, height, _key("tape"))


def advanced_chart(
    tv_symbol: str | None, height: int = 620, interval: str = "D",
    studies: list[str] | None = None, fallback: Callable[[], None] | None = None,
) -> None:
    """Grafico principal de velas. Es la herramienta familiar del usuario."""
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
        "autosize": True,
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
        "height": height - 30,
    }
    _render("technical-analysis", config, height, _key("ta", tv_symbol))
    st.caption(
        "Opinion tecnica de TradingView. Contraste externo, "
        "no es nuestra senal ni una recomendacion."
    )


def symbol_info(tv_symbol: str | None, height: int = 180) -> None:
    if not enabled() or not tv_symbol:
        return
    config = {**_locale_defaults(), "symbol": tv_symbol, "width": "100%", "isTransparent": True}
    _render("symbol-info", config, height, _key("info", tv_symbol))


def fundamental_data(tv_symbol: str | None, height: int = 500) -> None:
    """Estados financieros. Cubre el hueco de yfinance, sobre todo en Europa."""
    if not enabled() or not tv_symbol:
        return
    config = {
        **_locale_defaults(), "symbol": tv_symbol, "displayMode": "regular",
        "isTransparent": True, "width": "100%", "height": height - 30,
    }
    _render("financials", config, height, _key("fin", tv_symbol))


def company_profile(tv_symbol: str | None, height: int = 400) -> None:
    if not enabled() or not tv_symbol:
        return
    config = {
        **_locale_defaults(), "symbol": tv_symbol, "isTransparent": True,
        "width": "100%", "height": height - 30,
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
        "height": height - 30,
    }
    _render("stock-heatmap", config, height, _key("heatmap", data_source))


def crypto_heatmap(height: int = 520) -> None:
    if not enabled():
        return
    config = {
        **_locale_defaults(), "dataSource": "Crypto", "blockSize": "market_cap_calc",
        "blockColor": "change", "isTransparent": True, "width": "100%",
        "height": height - 30,
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
        "height": height - 30,
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
        "width": "100%", "height": height - 30,
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
        "width": "100%", "height": height - 30,
    }
    _render("screener", config, height, _key("screener", market))


def mini_symbol_overview(tv_symbol: str | None, height: int = 200) -> None:
    if not enabled() or not tv_symbol:
        return
    config = {
        **_locale_defaults(), "symbol": tv_symbol, "dateRange": "6M",
        "isTransparent": True, "autosize": True, "chartOnly": False,
    }
    _render("mini-symbol-overview", config, height, _key("mini", tv_symbol))
