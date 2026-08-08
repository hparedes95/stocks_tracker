"""Utilidades de tiempo.

Existe para tener un unico sitio donde se decide como se representa "ahora".
DuckDB guarda TIMESTAMP sin zona, asi que todo lo que se persiste es UTC naive:
mezclarlo con horas locales produce comparaciones silenciosamente erroneas
cuando cambia el horario de verano.
"""

from __future__ import annotations

import pandas as pd


def utcnow() -> pd.Timestamp:
    """Instante actual en UTC, sin informacion de zona."""
    return pd.Timestamp.now(tz="UTC").tz_convert(None)


def hours_since(ts) -> float | None:
    """Horas transcurridas desde una marca temporal. None si no es valida."""
    if ts is None or pd.isna(ts):
        return None
    stamp = pd.Timestamp(ts)
    if stamp.tzinfo is not None:
        stamp = stamp.tz_convert(None)
    return float((utcnow() - stamp).total_seconds() / 3600.0)
