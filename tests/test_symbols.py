"""Tests del mapeo yfinance -> TradingView.

Sin este mapeo los widgets muestran "Invalid symbol" y la aplicacion parece
rota. El test de cobertura del universo hace fallar el CI si alguien anade un
mercado nuevo sin regla.
"""

from __future__ import annotations

import pytest

from stocks_tracker.core.config import (
    all_active_tickers,
    get_symbol_blacklist,
    get_symbol_overrides,
)
from stocks_tracker.core.symbols import from_tv_symbol, to_tv_symbol, tv_exchange_of


@pytest.mark.parametrize(
    ("ticker", "exchange", "asset_class", "expected"),
    [
        ("AAPL", "NMS", "equity", "NASDAQ:AAPL"),
        ("JPM", "NYQ", "equity", "NYSE:JPM"),
        ("SPY", "PCX", "etf", "AMEX:SPY"),
        ("XLK", "PCX", "etf", "AMEX:XLK"),
        ("SAN.MC", None, "equity", "BME:SAN"),
        ("ITX.MC", None, "equity", "BME:ITX"),
        ("SAP.DE", None, "equity", "XETR:SAP"),
        ("ASML.AS", None, "equity", "EURONEXT:ASML"),
        ("MC.PA", None, "equity", "EURONEXT:MC"),
        ("ENI.MI", None, "equity", "MIL:ENI"),
        ("SHEL.L", None, "equity", "LSE:SHEL"),
        ("NESN.SW", None, "equity", "SIX:NESN"),
        ("BTC-USD", None, "crypto", "CRYPTO:BTCUSD"),
        ("ETH-USD", None, "crypto", "CRYPTO:ETHUSD"),
    ],
)
def test_rule_based_mapping(ticker, exchange, asset_class, expected):
    assert to_tv_symbol(ticker, exchange, asset_class, overrides={}) == expected


def test_nordic_hyphen_becomes_underscore():
    """En los mercados nordicos el guion del ticker pasa a guion bajo."""
    assert to_tv_symbol("NOVO-B.CO", None, "equity", overrides={}) == "OMXCOP:NOVO_B"


def test_us_hyphen_becomes_dot():
    """yfinance usa BRK-B; TradingView usa BRK.B."""
    assert to_tv_symbol("BRK-B", "NYQ", "equity", overrides={}) == "NYSE:BRK.B"


@pytest.mark.parametrize("ticker", ["^GSPC", "^IBEX", "GC=F", "CL=F"])
def test_indices_and_futures_need_overrides(ticker):
    """No hay regla fiable: sin override, mejor None que un simbolo inventado."""
    assert to_tv_symbol(ticker, None, "index", overrides={}) is None


def test_overrides_take_precedence():
    overrides = {"AAPL": "MANUAL:AAPL"}
    assert to_tv_symbol("AAPL", "NMS", "equity", overrides) == "MANUAL:AAPL"


def test_blacklisted_ticker_returns_none():
    """La lista negra fuerza nuestro propio grafico."""
    blacklist = get_symbol_blacklist()
    if not blacklist:
        pytest.skip("Sin tickers en la lista negra")
    ticker = next(iter(blacklist))
    assert to_tv_symbol(ticker, "NMS", "equity") is None


def test_unknown_us_ticker_without_exchange_returns_none():
    """Sin codigo de bolsa no se adivina: un prefijo erroneo rompe el widget."""
    assert to_tv_symbol("ZZZZ", None, "equity", overrides={}) is None


def test_tv_exchange_extraction():
    assert tv_exchange_of("NASDAQ:AAPL") == "NASDAQ"
    assert tv_exchange_of(None) is None
    assert tv_exchange_of("sin_dos_puntos") is None


@pytest.mark.parametrize(
    "ticker",
    ["AAPL", "SAN.MC", "SAP.DE", "ENI.MI", "SHEL.L", "BTC-USD"],
)
def test_roundtrip_for_reversible_cases(ticker):
    exchange = "NMS" if "." not in ticker and "-" not in ticker else None
    tv = to_tv_symbol(ticker, exchange, "equity", overrides={})
    assert tv is not None
    assert from_tv_symbol(tv) == ticker


def test_universe_is_fully_mapped():
    """TODO ticker del universo configurado debe resolver o estar en la lista negra.

    Este es el test que evita que un mercado nuevo entre en el YAML y llene la
    interfaz de iframes rotos sin que nadie se entere.
    """
    overrides = get_symbol_overrides()
    blacklist = get_symbol_blacklist()

    unresolved = []
    for ticker in all_active_tickers():
        if ticker in blacklist:
            continue
        # Se prueban los codigos de bolsa plausibles para tickers estadounidenses.
        candidates = [
            to_tv_symbol(ticker, exchange, "equity", overrides)
            for exchange in (None, "NMS", "NYQ", "PCX")
        ]
        if not any(candidates):
            unresolved.append(ticker)

    assert not unresolved, (
        f"{len(unresolved)} tickers sin equivalencia en TradingView ni entrada "
        f"en symbol_overrides.yaml: {unresolved[:15]}"
    )


# ---------------------------------------------------------------------------
# Huecos que llegan desde pandas
# ---------------------------------------------------------------------------
def test_missing_exchange_does_not_crash_the_whole_ingest():
    """Fallo real en la instalacion del usuario: un instrumento sin bolsa
    declarada llegaba como float('nan'), `bool(nan)` es True, y la ingesta
    entera moria con "'float' object has no attribute 'upper'" tras haber
    resuelto ya doscientos simbolos."""
    from stocks_tracker.core.symbols import resolve_all, to_tv_symbol

    assert to_tv_symbol("AAPL", float("nan"), float("nan")) is None
    assert to_tv_symbol("CRIT.MC", float("nan"), float("nan")) == "BME:CRIT"
    # El sufijo europeo no depende de la bolsa declarada, y el ETF sin bolsa
    # sigue cayendo en su caso por defecto.
    assert to_tv_symbol("SPY", float("nan"), "etf") == "AMEX:SPY"

    resolved = resolve_all([
        {"ticker": "AAPL", "exchange": float("nan"), "asset_class": float("nan")},
        {"ticker": "MSFT", "exchange": "NMS", "asset_class": "equity"},
    ])
    assert len(resolved) == 2, "un hueco no puede tumbar la lista entera"
    assert resolved[1]["tv_symbol"] == "NASDAQ:MSFT"


def test_numeric_exchange_is_treated_as_text():
    """Algun proveedor devuelve codigos numericos. No deben reventar."""
    from stocks_tracker.core.symbols import to_tv_symbol

    assert to_tv_symbol("AAPL", 123, "equity") is None
