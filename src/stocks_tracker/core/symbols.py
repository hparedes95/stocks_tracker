"""Traduccion de tickers yfinance a simbolos de TradingView.

yfinance dice `AAPL`, `SAN.MC`, `BTC-USD`, `^GSPC`.
TradingView dice `NASDAQ:AAPL`, `BME:SAN`, `CRYPTO:BTCUSD`, `SP:SPX`.

Sin esta traduccion los widgets muestran "Invalid symbol" y la app parece rota.
La resolucion es: overrides del YAML -> reglas por sufijo -> reglas por bolsa
-> None (y entonces se dibuja nuestro propio grafico, nunca un iframe roto).
"""

from __future__ import annotations

from .config import get_symbol_blacklist, get_symbol_overrides

# Sufijo de mercado de yfinance -> prefijo de bolsa en TradingView.
SUFFIX_TO_EXCHANGE: dict[str, str] = {
    ".MC": "BME",        # Madrid
    ".DE": "XETR",       # Xetra
    ".F": "FWB",         # Frankfurt
    ".PA": "EURONEXT",   # Paris
    ".AS": "EURONEXT",   # Amsterdam
    ".BR": "EURONEXT",   # Bruselas
    ".LS": "EURONEXT",   # Lisboa
    ".MI": "MIL",        # Milan
    ".L": "LSE",         # Londres
    ".SW": "SIX",        # Suiza
    ".VI": "VIE",        # Viena
    ".CO": "OMXCOP",     # Copenhague
    ".ST": "OMXSTO",     # Estocolmo
    ".HE": "OMXHEX",     # Helsinki
    ".OL": "OSL",        # Oslo
    ".IR": "EURONEXT",   # Dublin
}

# Codigo de bolsa que devuelve yfinance -> prefijo de TradingView.
EXCHANGE_TO_TV: dict[str, str] = {
    "NMS": "NASDAQ",
    "NGM": "NASDAQ",
    "NCM": "NASDAQ",
    "NAS": "NASDAQ",
    "NYQ": "NYSE",
    "PCX": "AMEX",  # NYSE Arca, donde cotizan casi todos los ETF
    "ASE": "AMEX",
    "BTS": "AMEX",
}

# Sufijos nordicos donde el guion del ticker pasa a guion bajo.
_UNDERSCORE_EXCHANGES = {"OMXCOP", "OMXSTO", "OMXHEX", "OSL"}


def _normalize_base(base: str, exchange: str) -> str:
    if exchange in _UNDERSCORE_EXCHANGES:
        return base.replace("-", "_")
    return base


def to_tv_symbol(
    ticker: str,
    exchange: str | None = None,
    asset_class: str | None = None,
    overrides: dict[str, str] | None = None,
) -> str | None:
    """Devuelve el simbolo de TradingView, o None si no hay equivalencia."""
    if not ticker:
        return None

    ticker = ticker.strip()
    overrides = get_symbol_overrides() if overrides is None else overrides

    if ticker in get_symbol_blacklist():
        return None
    if ticker in overrides:
        return overrides[ticker]

    # Indices y futuros no tienen regla fiable: solo via overrides.
    if ticker.startswith("^") or ticker.endswith("=F"):
        return None

    # Divisas
    if ticker.endswith("=X"):
        pair = ticker[:-2].upper()
        return f"FX:{pair}" if len(pair) == 6 else None

    # Cripto
    if ticker.endswith("-USD") or ticker.endswith("-EUR"):
        base, quote = ticker.rsplit("-", 1)
        return f"CRYPTO:{base.upper()}{quote.upper()}"

    # Sufijo de mercado europeo
    if "." in ticker:
        base, suffix = ticker.rsplit(".", 1)
        tv_exchange = SUFFIX_TO_EXCHANGE.get(f".{suffix}")
        if tv_exchange:
            return f"{tv_exchange}:{_normalize_base(base, tv_exchange).upper()}"
        return None

    # EE.UU.: depende del codigo de bolsa que reporte yfinance
    if exchange:
        tv_exchange = EXCHANGE_TO_TV.get(exchange.upper())
        if tv_exchange:
            base = ticker.replace("-", ".")  # BRK-B -> BRK.B
            return f"{tv_exchange}:{base.upper()}"

    # Sin codigo de bolsa: para ETF conocidos AMEX es lo habitual; para el
    # resto no adivinamos, porque un prefijo erroneo produce un iframe roto.
    if asset_class == "etf":
        return f"AMEX:{ticker.upper()}"
    return None


def tv_exchange_of(tv_symbol: str | None) -> str | None:
    if not tv_symbol or ":" not in tv_symbol:
        return None
    return tv_symbol.split(":", 1)[0]


def from_tv_symbol(tv_symbol: str) -> str | None:
    """Inversa aproximada, util para depurar. No es exacta para todos los casos."""
    if not tv_symbol or ":" not in tv_symbol:
        return None
    exchange, base = tv_symbol.split(":", 1)

    for suffix, tv_ex in SUFFIX_TO_EXCHANGE.items():
        if tv_ex == exchange:
            return f"{base.replace('_', '-')}{suffix}"
    if exchange in {"NASDAQ", "NYSE", "AMEX"}:
        return base.replace(".", "-")
    if exchange == "CRYPTO":
        for quote in ("USD", "EUR"):
            if base.endswith(quote):
                return f"{base[: -len(quote)]}-{quote}"
    if exchange == "FX":
        return f"{base}=X"
    return None


def resolve_all(instruments: list[dict]) -> list[dict]:
    """Resuelve `tv_symbol` para una lista de instrumentos.

    Se ejecuta en la ingesta, nunca en tiempo de render: la UI solo lee la
    columna ya calculada.
    """
    overrides = get_symbol_overrides()
    out = []
    for inst in instruments:
        tv = to_tv_symbol(
            inst.get("ticker", ""),
            inst.get("exchange"),
            inst.get("asset_class"),
            overrides,
        )
        enriched = dict(inst)
        enriched["tv_symbol"] = tv
        enriched["tv_exchange"] = tv_exchange_of(tv)
        enriched["tv_source"] = (
            "override" if inst.get("ticker") in overrides else ("rule" if tv else None)
        )
        enriched["tv_verified"] = False
        out.append(enriched)
    return out
