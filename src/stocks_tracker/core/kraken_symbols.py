"""Como se llaman las cosas en Kraken, en un solo sitio.

Kraken arrastra la nomenclatura de su primera version: bitcoin es "XXBT" y el
euro "ZEUR". La X y la Z iniciales marcaban "cripto" y "divisa", y hoy solo
las llevan los activos antiguos: SOL, ADA, LINK y DOT van tal cual.

Quitar esas letras a ciegas es un fallo silencioso: "XXBT" sin X queda en "BT"
—que no es ninguna moneda— y "LINK" perderia su propia inicial. El precio
simplemente no aparece, la posicion se valora a cero y la equity sale mal sin
que nada lance un error. Ya ocurrio una vez.

Esto vive en `core` y no en el adaptador porque lo necesitan los dos lados:
el que envia ordenes y el que descarga el historico. Con una copia en cada
uno, arreglar el fallo en una dejaria el otro roto, que es precisamente como
vuelve un fallo ya corregido.
"""

from __future__ import annotations

ASSET_ALIASES = {
    "XXBT": "BTC", "XBT": "BTC",
    "XETH": "ETH", "XXRP": "XRP", "XLTC": "LTC", "XXDG": "DOGE", "XDG": "DOGE",
    "XXLM": "XLM", "XXMR": "XMR", "XZEC": "ZEC", "XETC": "ETC", "XMLN": "MLN",
    "XREP": "REP", "XICN": "ICN",
    "ZEUR": "EUR", "ZUSD": "USD", "ZGBP": "GBP", "ZJPY": "JPY",
    "ZCAD": "CAD", "ZAUD": "AUD", "ZCHF": "CHF",
}

# Divisas de cotizacion, de mas larga a mas corta: "XXBTZEUR" termina tanto en
# "ZEUR" como en "EUR", y partir por la corta dejaria la base en "XXBTZ".
QUOTE_CODES = tuple(sorted(
    {"ZEUR", "ZUSD", "ZGBP", "ZJPY", "ZCAD", "ZAUD", "ZCHF",
     "EUR", "USD", "GBP", "JPY", "CAD", "AUD", "CHF", "USDT", "USDC", "DAI"},
    key=len, reverse=True,
))

# Lo que es dinero y no posicion. El saldo en euros es caja; el de una
# stablecoin tambien, porque el mandato cripto solo opera pares contra EUR.
CASH_ASSETS = frozenset({"EUR", "USD", "GBP", "JPY", "CAD", "AUD", "CHF",
                         "USDT", "USDC", "DAI"})


def canonical_asset(code: str) -> str:
    """'XXBT' -> 'BTC'. Lo que no este en la tabla se deja igual."""
    limpio = code.upper().split(".")[0]
    return ASSET_ALIASES.get(limpio, limpio)


def canonical_pair(name: str) -> str:
    """'XXBTZEUR' y 'BTC/EUR' -> 'BTC/EUR'. Cadena vacia si no se reconoce.

    Es lo que permite cruzar lo que se pidio con lo que Kraken responde, que
    no usa el mismo nombre casi nunca.
    """
    plano = name.upper().replace("/", "")
    for quote in QUOTE_CODES:
        if plano.endswith(quote) and len(plano) > len(quote):
            return f"{canonical_asset(plano[: -len(quote)])}/{canonical_asset(quote)}"
    return ""


def kraken_pair(symbol: str) -> str:
    """'BTC/EUR' -> 'XBTEUR', que es como hay que pedirlo.

    Kraken no reconoce "BTC" en la peticion aunque devuelva "XXBT" en la
    respuesta: para el, bitcoin sigue siendo XBT.
    """
    plano = symbol.upper().replace("/", "")
    return plano.replace("BTC", "XBT") if plano.startswith("BTC") else plano
