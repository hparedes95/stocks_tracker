"""Lectura de `config/watch.yaml` y calendario de vigilancia."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time
from functools import lru_cache
from typing import Any
from zoneinfo import ZoneInfo

from ..core.config import _load_yaml, get_settings


@dataclass(frozen=True)
class Threshold:
    """Un escalon de aviso. `value` es el porcentaje o el nivel absoluto."""

    value: float
    severity: str
    label: str

    @property
    def is_drop(self) -> bool:
        return self.value < 0


@dataclass(frozen=True)
class WatchConfig:
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def enabled(self) -> bool:
        return bool(self.raw.get("enabled", True))

    @property
    def interval_seconds(self) -> int:
        # Por debajo de 15 s no se gana nada (la fuente va con retraso) y se
        # multiplica el riesgo de bloqueo.
        return max(15, int(self.raw.get("interval_seconds", 60)))

    @property
    def watch_portfolio(self) -> bool:
        return bool(self.raw.get("watch_portfolio", True))

    @property
    def escalation_only(self) -> bool:
        return bool(self.raw.get("escalation_only", True))

    @property
    def min_minutes_between(self) -> int:
        return int(self.raw.get("min_minutes_between", 10))

    @property
    def notify_recovery(self) -> bool:
        return bool(self.raw.get("notify_recovery", True))

    @property
    def weekdays_only(self) -> bool:
        return bool((self.raw.get("schedule") or {}).get("weekdays_only", True))

    @property
    def always_watch_crypto(self) -> bool:
        return bool((self.raw.get("schedule") or {}).get("always_watch_crypto", True))

    @property
    def windows(self) -> list[tuple[time, time]]:
        out: list[tuple[time, time]] = []
        for spec in (self.raw.get("schedule") or {}).get("windows", []) or []:
            try:
                start_txt, end_txt = str(spec).split("-", 1)
                out.append((_parse_time(start_txt), _parse_time(end_txt)))
            except (ValueError, TypeError):
                continue
        return out

    def symbols(self, group: str) -> list[str]:
        return list((self.raw.get("symbols") or {}).get(group, []) or [])

    @property
    def all_symbols(self) -> list[str]:
        groups = self.raw.get("symbols") or {}
        seen: list[str] = []
        for members in groups.values():
            for ticker in members or []:
                if ticker not in seen:
                    seen.append(ticker)
        return seen

    def group_of(self, ticker: str) -> str:
        for group, members in (self.raw.get("symbols") or {}).items():
            if ticker in (members or []):
                return str(group)
        return "portfolio"

    def thresholds(self, name: str) -> list[Threshold]:
        """Escalones ordenados de menos a mas grave.

        Para caidas eso significa de menos negativo a mas negativo; para
        niveles absolutos, de menor a mayor. Ordenarlos aqui evita que un YAML
        mal ordenado haga que un -20% dispare el aviso de -1.5%.
        """
        out = []
        for spec in (self.raw.get("thresholds") or {}).get(name, []) or []:
            value = spec.get("pct", spec.get("value"))
            if value is None:
                continue
            out.append(
                Threshold(
                    value=float(value),
                    severity=str(spec.get("severity", "media")),
                    label=str(spec.get("label", "")),
                )
            )
        if out and out[0].is_drop:
            return sorted(out, key=lambda t: -t.value)
        return sorted(out, key=lambda t: t.value)


def _parse_time(text: str) -> time:
    hour, _, minute = text.strip().partition(":")
    return time(int(hour), int(minute or 0))


@lru_cache(maxsize=1)
def get_watch_config() -> WatchConfig:
    return WatchConfig(raw=_load_yaml("watch.yaml"))


def reload() -> None:
    get_watch_config.cache_clear()


def local_timezone() -> ZoneInfo:
    return ZoneInfo(str(get_settings().raw.get("timezone", "Europe/Madrid")))


def is_watch_time(now: datetime | None = None) -> bool:
    """¿Toca vigilar la renta variable ahora mismo?

    Se evalua en hora LOCAL del usuario, no en UTC: las ventanas del YAML estan
    escritas como las lee una persona, y con el horario de verano la diferencia
    con Nueva York cambia dos veces al ano.
    """
    cfg = get_watch_config()
    moment = now or datetime.now(local_timezone())
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=local_timezone())

    if cfg.weekdays_only and moment.weekday() >= 5:
        return False

    windows = cfg.windows
    if not windows:
        return True

    current = moment.timetz().replace(tzinfo=None)
    for start, end in windows:
        if start <= end:
            if start <= current <= end:
                return True
        # Ventana que cruza medianoche.
        elif current >= start or current <= end:
            return True
    return False
