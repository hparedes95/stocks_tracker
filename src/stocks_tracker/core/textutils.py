"""Normalizacion de texto que llega de pandas.

Existe por una trampa que ya ha causado dos averias distintas en produccion, y
que no es evidente al leer el codigo:

    >>> import pandas as pd
    >>> rec = pd.DataFrame({"a": [None]}).to_dict("records")[0]
    >>> rec["a"]
    nan
    >>> bool(rec["a"])
    True

Un hueco de un DataFrame no es `None`: es `float('nan')`, y **es verdadero**.
Asi que `if valor:` da por bueno el hueco y `if not valor:` no dispara el
respaldo. Las dos averias:

1. `to_tv_symbol` llamaba a `.upper()` sobre el hueco y tumbaba la ingesta
   entera despues de doscientos simbolos resueltos.
2. `ingest_universe` no aplicaba la clase de activo por defecto, guardaba
   `asset_class` vacio, y el ranking —que filtra por `equity` y `etf`— se
   quedaba sin un solo instrumento que puntuar.

La segunda es la peor de las dos: no dio ningun error. Solo un "Sin
instrumentos que puntuar" al final de una ingesta aparentemente correcta.
"""

from __future__ import annotations

from typing import Any


def is_missing(value: Any) -> bool:
    """True para None, NaN y cadenas vacias o de solo espacios."""
    if value is None:
        return True
    if isinstance(value, float) and value != value:  # NaN
        return True
    return isinstance(value, str) and not value.strip()


def as_text(value: Any) -> str:
    """Texto limpio, o cadena vacia si el valor falta.

    A diferencia de `str(value)`, no convierte un hueco en la cadena "nan",
    que es la otra forma habitual de que esto salga mal: un `"nan"` guardado
    en la base pasa todos los filtros de "tiene valor".
    """
    return "" if is_missing(value) else str(value).strip()


def first_text(*values: Any) -> str:
    """El primer valor con contenido. Util para cadenas de respaldo."""
    for value in values:
        text = as_text(value)
        if text:
            return text
    return ""
