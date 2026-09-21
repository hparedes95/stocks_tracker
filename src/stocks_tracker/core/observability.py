"""Registro operativo estructurado, pequeno y seguro.

Los registros funcionales viven en DuckDB. Este fichero cubre justo el caso en
que DuckDB o un proveedor fallan: una linea JSON por evento en ``data/logs``.
Nunca contiene valores de credenciales y escribirlo es *best effort*; un disco
de logs lleno no puede convertir una descarga correcta en fallida.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .config import get_settings
from .secrets import redact
from .timeutils import utcnow


def _safe(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, (date, datetime, Path)):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_safe(item) for item in value]
    return redact(str(value))


def emit(event: str, *, level: str = "info", **fields: Any) -> bool:
    """Anade un evento JSONL y devuelve si pudo persistirlo.

    Los nombres que parecen secretos se omiten aunque el llamador se equivoque.
    Los textos restantes pasan por la redaccion central de credenciales.
    """
    now = utcnow()
    clean = {
        key: _safe(value)
        for key, value in fields.items()
        if not any(mark in key.lower() for mark in ("secret", "token", "password", "api_key"))
    }
    record = {
        "timestamp": now.isoformat(),
        "level": level,
        "event": event,
        "pid": os.getpid(),
        **clean,
    }
    try:
        folder = get_settings().logs_dir
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"events-{now:%Y-%m-%d}.jsonl"
        with target.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    except (OSError, KeyError, TypeError, ValueError):
        return False
    return True
