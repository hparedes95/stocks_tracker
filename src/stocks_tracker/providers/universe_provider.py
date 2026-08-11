"""Constituyentes de los indices.

Wikipedia mantiene listas actualizadas del S&P 500 y del Nasdaq 100. Es
gratuito y fiable en la practica, pero es una pagina web: puede cambiar de
formato o no estar accesible. Por eso cada universo lleva una lista manual de
respaldo en `universe.yaml` y el sistema NUNCA se queda sin universo.

Aviso sobre el sesgo de supervivencia: esto devuelve los constituyentes de HOY,
no los de hace cinco anos. Los backtests que usen este universo sobreestiman
los resultados, porque las empresas que quebraron o salieron del indice no
aparecen. `universe_membership` guarda la composicion diaria para que, con el
tiempo, el sesgo desaparezca hacia adelante.
"""

from __future__ import annotations

import io

import pandas as pd
import requests
from rich.console import Console

from .base import ProviderError

console = Console()

_TIMEOUT = 30
# La politica de Wikimedia exige un User-Agent que identifique la herramienta y
# ofrezca un punto de contacto; los que no lo hacen reciben 403.
_HEADERS = {
    "User-Agent": (
        "stocks-tracker/0.1 (uso personal; "
        "https://github.com/hparedes95/stocks_tracker)"
    )
}

WIKIPEDIA_SOURCES: dict[str, dict] = {
    "SP500": {
        "url": "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
        "table_index": 0,
        "symbol_column": "Symbol",
        "name_column": "Security",
        "sector_column": "GICS Sector",
    },
    "NASDAQ100": {
        "url": "https://en.wikipedia.org/wiki/Nasdaq-100",
        "table_index": None,      # se busca la tabla que tenga columna Ticker
        "symbol_column": "Ticker",
        "name_column": "Company",
        "sector_column": "GICS Sector",
    },
    "ESTOXX50": {
        "url": "https://en.wikipedia.org/wiki/EURO_STOXX_50",
        "table_index": None,
        "symbol_column": "Ticker",
        "name_column": "Name",
        "sector_column": None,
    },
}


def _normalize_us_ticker(symbol: str) -> str:
    """Wikipedia usa BRK.B; yfinance usa BRK-B."""
    return str(symbol).strip().upper().replace(".", "-")


def _pick_table(tables: list[pd.DataFrame], spec: dict) -> pd.DataFrame | None:
    index = spec.get("table_index")
    if index is not None and index < len(tables):
        return tables[index]
    # Sin indice fijo, se busca la primera tabla que tenga la columna esperada.
    wanted = spec.get("symbol_column")
    for table in tables:
        if wanted in [str(c) for c in table.columns]:
            return table
    return None


class UniverseProvider:
    name = "wikipedia"

    def fetch_constituents(self, universe: str) -> pd.DataFrame:
        """Devuelve ticker, name, gics_sector para un universo conocido.

        Lanza ProviderError si no se puede leer: quien llama decide si cae a la
        lista manual.
        """
        spec = WIKIPEDIA_SOURCES.get(universe)
        if spec is None:
            raise ProviderError(f"Sin fuente configurada para el universo '{universe}'")

        try:
            response = requests.get(spec["url"], headers=_HEADERS, timeout=_TIMEOUT)
            response.raise_for_status()
            # io.StringIO y no la cadena pelada: pandas 3.0 elimino el paso de
            # HTML literal y ahora interpreta la cadena como una RUTA DE
            # FICHERO. El resultado era un FileNotFoundError en cada descarga,
            # en cualquier maquina, que el respaldo convertia en un silencioso
            # "manual (fallo la descarga)" y dejaba el universo en 50 valores.
            tables = pd.read_html(io.StringIO(response.text))
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(f"No se pudo leer {spec['url']}: {exc}") from exc

        table = _pick_table(tables, spec)
        if table is None or table.empty:
            raise ProviderError(f"No se encontro la tabla de constituyentes de {universe}")

        symbol_col = spec["symbol_column"]
        if symbol_col not in table.columns:
            raise ProviderError(
                f"La tabla de {universe} no tiene columna '{symbol_col}'. "
                "La pagina ha cambiado de formato."
            )

        out = pd.DataFrame({"ticker": table[symbol_col].map(_normalize_us_ticker)})

        name_col = spec.get("name_column")
        out["name"] = (
            table[name_col].astype(str) if name_col in table.columns else None
        )
        sector_col = spec.get("sector_column")
        out["gics_sector"] = (
            table[sector_col].astype(str)
            if sector_col and sector_col in table.columns
            else None
        )

        out = out[out["ticker"].str.len().between(1, 12)]
        return out.drop_duplicates(subset=["ticker"]).reset_index(drop=True)

    def supports(self, universe: str) -> bool:
        return universe in WIKIPEDIA_SOURCES


def resolve_universe(universe: str, manual_tickers: list[str],
                     source: str = "manual") -> tuple[list[str], str]:
    """Tickers de un universo y de donde han salido.

    Con `source: wikipedia` se intenta la descarga y, si falla, se cae a la
    lista manual. Preferimos un universo reducido pero funcionando a una pagina
    en blanco porque Wikipedia cambio una cabecera.
    """
    if source != "wikipedia":
        return manual_tickers, "manual"

    try:
        constituents = UniverseProvider().fetch_constituents(universe)
    except ProviderError as exc:
        # El motivo se imprime. Antes se descartaba, y "manual (fallo la
        # descarga)" era todo lo que se sabia: no distinguia un 403 de un
        # timeout, de un cambio de formato de la pagina o —lo que realmente
        # pasaba— de un fallo de programacion nuestro. Costo dias averiguarlo.
        console.print(f"[yellow]  {universe}: {exc}[/]")
        return manual_tickers, "manual (fallo la descarga)"

    tickers = constituents["ticker"].tolist()
    if len(tickers) < 20:
        return manual_tickers, "manual (descarga incompleta)"

    # Se unen ambas: la lista manual puede contener valores que interesan y que
    # el indice no incluye.
    merged = list(dict.fromkeys(tickers + manual_tickers))
    return merged, "wikipedia"
