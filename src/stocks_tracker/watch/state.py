"""Memoria del vigilante entre iteraciones.

Se guarda en un JSON, NO en DuckDB, y es una decision deliberada: el almacen
tiene un unico escritor y la ingesta nocturna lo toma en exclusiva. Un
vigilante escribiendo cada minuto chocaria con ella justo la noche en que se
actualizan los datos. Un fichero suelto no compite con nadie y sobrevive a un
reinicio, que es todo lo que hace falta aqui.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from ..core.config import get_settings


def state_path() -> Path:
    return get_settings().logs_dir / "watch_state.json"


@dataclass
class WatchState:
    """Nivel de aviso alcanzado hoy por cada simbolo."""

    day: str = ""
    levels: dict[str, float] = field(default_factory=dict)
    last_notified: dict[str, str] = field(default_factory=dict)

    # ------------------------------------------------------------------
    def roll_over(self, today: date | None = None) -> bool:
        """Empieza un dia nuevo si toca. Devuelve True si ha habido cambio.

        Los niveles se miden contra el cierre anterior, asi que al cambiar de
        sesion dejan de significar nada y hay que rearmar el sistema.
        """
        stamp = (today or date.today()).isoformat()
        if self.day == stamp:
            return False
        self.day = stamp
        self.levels.clear()
        self.last_notified.clear()
        return True

    def level_of(self, key: str) -> float | None:
        return self.levels.get(key)

    def record(self, key: str, level: float, when: datetime) -> None:
        self.levels[key] = float(level)
        self.last_notified[key] = when.isoformat()

    def clear(self, key: str) -> None:
        self.levels.pop(key, None)
        self.last_notified.pop(key, None)

    def minutes_since(self, key: str, when: datetime) -> float | None:
        stamp = self.last_notified.get(key)
        if not stamp:
            return None
        try:
            previous = datetime.fromisoformat(stamp)
        except ValueError:
            return None
        if (previous.tzinfo is None) != (when.tzinfo is None):
            previous = previous.replace(tzinfo=when.tzinfo)
        return (when - previous).total_seconds() / 60.0

    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        return {"day": self.day, "levels": self.levels,
                "last_notified": self.last_notified}


def load(path: Path | None = None) -> WatchState:
    """Lee el estado. Un fichero corrupto no puede impedir vigilar."""
    target = path or state_path()
    if not target.exists():
        return WatchState()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return WatchState()
    if not isinstance(raw, dict):
        return WatchState()
    return WatchState(
        day=str(raw.get("day", "")),
        levels={str(k): float(v) for k, v in (raw.get("levels") or {}).items()
                if isinstance(v, (int, float))},
        last_notified={str(k): str(v)
                       for k, v in (raw.get("last_notified") or {}).items()},
    )


def save(state: WatchState, path: Path | None = None) -> None:
    """Escribe el estado de forma atomica.

    Si el proceso muere a mitad de la escritura, un fichero truncado haria que
    el vigilante perdiera la memoria del dia y volviera a avisar de todo. Se
    escribe a un temporal y se renombra, que en el mismo sistema de ficheros es
    atomico.
    """
    target = path or state_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(state.to_dict(), indent=2), encoding="utf-8")
    tmp.replace(target)
