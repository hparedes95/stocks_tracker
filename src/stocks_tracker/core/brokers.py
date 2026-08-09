"""Importacion de carteras exportadas desde un broker.

Por que se importa un fichero en lugar de conectarse:

- **eToro** no ofrece API de lectura de cartera a clientes particulares. Solo
  descarga del extracto.
- **Trade Republic** no tiene API publica de ningun tipo. Existen clientes no
  oficiales que inician sesion con tu telefono y tu PIN, pero eso significa
  entregar tus credenciales a un script de terceros y saltarse sus condiciones
  de uso. No se implementa aqui, y esa decision es deliberada.

Asi que el camino soportado es exportar del broker e importar el fichero. Es
manual y periodico, pero no pide credenciales de nada.

El problema real no es leer el CSV: es que **cada broker nombra las columnas a
su manera y Trade Republic identifica los valores por ISIN, no por ticker**.
Todo lo interesante de este modulo esta en resolver esas dos cosas.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from .config import _load_yaml, project_root

# Campos canonicos a los que se traduce cualquier extracto.
CANONICAL = ["ticker", "isin", "name", "qty", "avg_cost", "currency"]

# Alias conocidos, en minusculas y sin acentos. Se comparan por coincidencia
# exacta primero y por subcadena despues, asi que el orden importa: lo mas
# especifico va antes.
_ALIASES: dict[str, list[str]] = {
    "ticker": [
        "ticker", "symbol", "simbolo", "instrument symbol", "market",
        "ticker symbol", "codigo",
    ],
    "isin": ["isin", "isin code", "codigo isin"],
    "name": [
        "name", "nombre", "instrument", "instrumento", "descripcion",
        "description", "security", "valor", "producto", "asset name",
    ],
    "qty": [
        "quantity", "cantidad", "shares", "units", "unidades", "titulos",
        "amount of shares", "nominal", "participaciones", "position size",
    ],
    "avg_cost": [
        "average open rate", "avg open rate", "open rate", "average price",
        "precio medio", "avg price", "average cost", "coste medio",
        "buy price", "precio de compra", "purchase price", "entry price",
        "precio", "price",
    ],
    "currency": ["currency", "divisa", "moneda", "ccy"],
}

_ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")


@dataclass
class ImportResult:
    """Resultado de interpretar un extracto."""

    positions: pd.DataFrame = field(default_factory=pd.DataFrame)
    broker: str = "desconocido"
    unresolved: pd.DataFrame = field(default_factory=pd.DataFrame)
    warnings: list[str] = field(default_factory=list)
    column_map: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.positions.empty


def _normalize(text: Any) -> str:
    """Minusculas, sin acentos y sin puntuacion, para comparar cabeceras."""
    raw = str(text or "").strip().lower()
    for accented, plain in (("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"),
                            ("ú", "u"), ("ñ", "n")):
        raw = raw.replace(accented, plain)
    return re.sub(r"[^a-z0-9 ]+", " ", raw).strip()


def looks_like_isin(value: Any) -> bool:
    return bool(_ISIN_RE.match(str(value or "").strip().upper()))


def detect_broker(columns: list[str]) -> str:
    """Adivina el broker por sus cabeceras caracteristicas."""
    normalized = {_normalize(c) for c in columns}
    if any("open rate" in c for c in normalized) or "copy trader" in normalized:
        return "eToro"
    if "isin" in normalized and not any("open rate" in c for c in normalized):
        return "Trade Republic"
    return "generico"


def guess_column_map(columns: list[str]) -> dict[str, str]:
    """Empareja columnas del fichero con los campos canonicos.

    Se prueba coincidencia exacta antes que parcial: "price" es subcadena de
    "average price", y sin esa prioridad una columna de precio actual podria
    ganarle a la de precio medio de compra, que es la que importa.
    """
    normalized = {col: _normalize(col) for col in columns}
    mapping: dict[str, str] = {}
    taken: set[str] = set()

    for field_name, aliases in _ALIASES.items():
        for alias in aliases:
            for col, norm in normalized.items():
                if col in taken:
                    continue
                if norm == alias:
                    mapping[field_name] = col
                    taken.add(col)
                    break
            if field_name in mapping:
                break

    for field_name, aliases in _ALIASES.items():
        if field_name in mapping:
            continue
        for alias in aliases:
            for col, norm in normalized.items():
                if col in taken:
                    continue
                if alias in norm:
                    mapping[field_name] = col
                    taken.add(col)
                    break
            if field_name in mapping:
                break

    return mapping


def read_table(data: bytes, filename: str = "") -> pd.DataFrame:
    """Lee CSV o XLSX sin saber de antemano separador ni codificacion.

    Los extractos europeos llegan con punto y coma y en latin-1 mas veces de lo
    que seria razonable, y un fichero mal leido produce una sola columna con
    todo dentro en lugar de un error.
    """
    lower = filename.lower()
    if lower.endswith((".xlsx", ".xlsm", ".xls")):
        sheets = pd.read_excel(io.BytesIO(data), sheet_name=None)
        # eToro reparte el extracto en varias hojas: interesa la que trae
        # posiciones, no la de dividendos ni la de operaciones cerradas.
        best, best_score = None, -1
        for name, frame in sheets.items():
            score = len(guess_column_map(list(frame.columns)))
            if "clos" in _normalize(name):
                score -= 2  # posiciones cerradas: no son la cartera de hoy
            if score > best_score:
                best, best_score = frame, score
        return best if best is not None else pd.DataFrame()

    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        for sep in (None, ";", ",", "\t"):
            try:
                frame = pd.read_csv(
                    io.BytesIO(data), sep=sep, encoding=encoding,
                    engine="python", skip_blank_lines=True,
                )
            except (UnicodeDecodeError, pd.errors.ParserError, ValueError):
                continue
            if frame.shape[1] > 1:
                return frame
    return pd.DataFrame()


def isin_map() -> dict[str, str]:
    """ISIN -> ticker, desde `config/isin_map.yaml`."""
    path = project_root() / "config" / "isin_map.yaml"
    if not path.exists():
        return {}
    raw = _load_yaml("isin_map.yaml") or {}
    return {str(k).strip().upper(): str(v).strip()
            for k, v in (raw.get("isin_to_ticker") or {}).items()}


def _to_number(value: Any) -> float | None:
    """Convierte importes con formato europeo o anglosajon.

    "1.234,56" y "1,234.56" son el mismo numero escrito por dos personas
    distintas, y un extracto espanol trae el primero.
    """
    if value is None or (isinstance(value, float) and value != value):
        return None
    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip()
    if not text:
        return None
    text = re.sub(r"[^\d,.\-]", "", text)
    if not text or text in {"-", ".", ","}:
        return None

    if "," in text and "." in text:
        # Con los dos separadores no hay duda: el decimal es el ultimo.
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        # La coma no se usa como separador de miles en ningun extracto que
        # tenga tambien punto decimal, asi que aqui es decimal.
        text = text.replace(",", ".")

    try:
        return float(text)
    except ValueError:
        return None


def is_ambiguous_number(value: Any) -> bool:
    """¿Podria este texto significar dos cosas mil veces distintas?

    "1.234" es 1234 para un europeo y 1,234 para un anglosajon, y mirando solo
    ese valor no hay forma de saberlo. Se interpreta como decimal (que es lo
    que hacen `float()` y la mayoria de exportaciones automaticas), pero
    equivocarse multiplica la cartera por mil, asi que se avisa y se ensena la
    vista previa antes de guardar nada.
    """
    text = str(value or "").strip()
    if not re.fullmatch(r"-?\d{1,3}([.,])\d{3}", text):
        return False
    return True


def parse_positions(frame: pd.DataFrame, column_map: dict[str, str] | None = None,
                    default_currency: str = "EUR",
                    known_tickers: set[str] | None = None) -> ImportResult:
    """Traduce un extracto a posiciones canonicas.

    Agrupa las filas del mismo valor en una sola posicion con **coste medio
    ponderado**: los brokers listan cada compra por separado, y sumarlas sin
    ponderar daria un precio medio que no es el tuyo.
    """
    result = ImportResult()
    if frame is None or frame.empty:
        result.warnings.append("El fichero no contiene filas legibles.")
        return result

    result.broker = detect_broker(list(frame.columns))
    mapping = column_map or guess_column_map(list(frame.columns))
    result.column_map = mapping

    missing = [f for f in ("qty", "avg_cost") if f not in mapping]
    if missing:
        result.warnings.append(
            "No se han encontrado las columnas de "
            + " y ".join({"qty": "cantidad", "avg_cost": "precio medio"}[m]
                          for m in missing)
            + ". Asignalas a mano."
        )
        return result
    if "ticker" not in mapping and "isin" not in mapping:
        result.warnings.append(
            "El fichero no trae ni ticker ni ISIN: no hay forma de saber que "
            "valores son."
        )
        return result

    rows = []
    ambiguous = 0
    for record in frame.to_dict("records"):
        raw_cost = record.get(mapping.get("avg_cost"))
        if is_ambiguous_number(raw_cost):
            ambiguous += 1

        qty = _to_number(record.get(mapping.get("qty")))
        cost = _to_number(raw_cost)
        if not qty or qty <= 0 or cost is None or cost <= 0:
            continue

        ticker = str(record.get(mapping.get("ticker"), "") or "").strip().upper()
        isin = str(record.get(mapping.get("isin"), "") or "").strip().upper()
        # Algunos extractos meten el ISIN en la columna del simbolo.
        if looks_like_isin(ticker) and not isin:
            isin, ticker = ticker, ""

        rows.append(
            {
                "ticker": ticker,
                "isin": isin if looks_like_isin(isin) else "",
                "name": str(record.get(mapping.get("name"), "") or "").strip(),
                "qty": qty,
                "avg_cost": cost,
                "currency": (
                    str(record.get(mapping.get("currency"), "") or "").strip().upper()
                    or default_currency
                ),
            }
        )

    if not rows:
        result.warnings.append(
            "Ninguna fila tiene cantidad y precio validos. Puede que hayas "
            "exportado el historial de operaciones en vez de las posiciones "
            "abiertas."
        )
        return result

    if ambiguous:
        result.warnings.append(
            f"{ambiguous} precios del tipo «1.234» son ambiguos: pueden ser mil "
            "doscientos treinta y cuatro o uno coma dos tres cuatro. Se han "
            "leido como decimales. **Comprueba la vista previa** antes de "
            "guardar."
        )

    parsed = pd.DataFrame(rows)
    parsed = _resolve_tickers(parsed, known_tickers or set(), result)

    resolved = parsed[parsed["ticker"] != ""].copy()
    result.unresolved = parsed[parsed["ticker"] == ""].copy()

    if resolved.empty:
        return result

    result.positions = _aggregate(resolved)
    return result


def _resolve_tickers(parsed: pd.DataFrame, known: set[str],
                     result: ImportResult) -> pd.DataFrame:
    """Rellena el ticker a partir del ISIN cuando falta."""
    mapping = isin_map()
    out = parsed.copy()

    for i, row in out.iterrows():
        ticker = row["ticker"]
        if ticker and (not known or ticker in known):
            continue
        if ticker and known and ticker not in known:
            # El simbolo del broker no existe en nuestro universo: puede ser un
            # CFD, una fraccion o un nombre propio del broker.
            out.at[i, "ticker"] = mapping.get(row["isin"], "") if row["isin"] else ""
            continue
        if row["isin"]:
            out.at[i, "ticker"] = mapping.get(row["isin"], "")

    pending = int((out["ticker"] == "").sum())
    if pending:
        result.warnings.append(
            f"{pending} posiciones sin equivalencia. Anade su ISIN en "
            "`config/isin_map.yaml` o asignales el ticker a mano."
        )
    return out


def _aggregate(frame: pd.DataFrame) -> pd.DataFrame:
    """Une lotes del mismo valor con coste medio ponderado."""
    frame = frame.copy()
    frame["coste_total"] = frame["qty"] * frame["avg_cost"]

    grouped = frame.groupby("ticker", as_index=False).agg(
        qty=("qty", "sum"),
        coste_total=("coste_total", "sum"),
        currency=("currency", "first"),
        name=("name", "first"),
        isin=("isin", "first"),
    )
    grouped["avg_cost"] = grouped["coste_total"] / grouped["qty"]
    return grouped.drop(columns=["coste_total"])[
        ["ticker", "name", "isin", "qty", "avg_cost", "currency"]
    ]
