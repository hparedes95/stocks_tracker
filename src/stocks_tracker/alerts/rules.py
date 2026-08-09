"""Definicion y compilacion de las reglas de alerta.

Las condiciones vienen de `config/alerts.yaml`, que es texto que escribe el
usuario. Se evaluan con el evaluador seguro de `core/safe_eval.py`: recorre el
AST y solo permite comparaciones, booleanos y aritmetica. Nada de llamadas a
funciones ni acceso a atributos.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from ..core.config import _load_yaml

SEVERITIES = ("baja", "media", "alta", "critica")

# Ambitos reconocidos. `universe:` y `sector:` llevan sufijo.
SCOPE_WATCHLIST = "watchlist"
SCOPE_PORTFOLIO = "portfolio"
SCOPE_MARKET = "market"


@dataclass(frozen=True)
class Rule:
    id: str
    name: str
    scope: str
    when: str
    message: str
    severity: str = "media"
    cooldown_days: int = 10
    note: str = ""

    @property
    def is_market_scope(self) -> bool:
        """Las reglas de mercado no van por ticker: se evaluan una sola vez."""
        return self.scope == SCOPE_MARKET

    @property
    def universe(self) -> str | None:
        if self.scope.startswith("universe:"):
            return self.scope.split(":", 1)[1]
        return None

    @property
    def sector(self) -> str | None:
        if self.scope.startswith("sector:"):
            return self.scope.split(":", 1)[1]
        return None


@dataclass(frozen=True)
class ChannelConfig:
    name: str
    enabled: bool
    settings: dict[str, Any]


@lru_cache(maxsize=1)
def _raw() -> dict:
    return _load_yaml("alerts.yaml")


@lru_cache(maxsize=1)
def get_defaults() -> dict:
    return dict(_raw().get("defaults") or {})


@lru_cache(maxsize=1)
def get_rules() -> tuple[Rule, ...]:
    defaults = get_defaults()
    rules: list[Rule] = []

    for spec in _raw().get("rules") or []:
        if not spec.get("id") or not spec.get("when"):
            continue
        rules.append(
            Rule(
                id=str(spec["id"]),
                name=str(spec.get("name", spec["id"])),
                scope=str(spec.get("scope", SCOPE_WATCHLIST)),
                when=str(spec["when"]),
                message=str(spec.get("message", "{ticker}: {name}")),
                severity=str(spec.get("severity", defaults.get("severity", "media"))),
                cooldown_days=int(
                    spec.get("cooldown_days", defaults.get("cooldown_days", 10))
                ),
                note=str(spec.get("note", "")).strip(),
            )
        )
    return tuple(rules)


@lru_cache(maxsize=1)
def get_channels() -> tuple[ChannelConfig, ...]:
    out: list[ChannelConfig] = []
    for name, spec in (_raw().get("channels") or {}).items():
        spec = spec or {}
        out.append(
            ChannelConfig(
                name=str(name),
                enabled=bool(spec.get("enabled", False)),
                settings={k: v for k, v in spec.items() if k != "enabled"},
            )
        )
    return tuple(out)


def get_rule(rule_id: str) -> Rule | None:
    for rule in get_rules():
        if rule.id == rule_id:
            return rule
    return None


def severity_rank(severity: str) -> int:
    """Orden de gravedad, para poder ordenar y filtrar."""
    try:
        return SEVERITIES.index(severity)
    except ValueError:
        return 0


def reload() -> None:
    """Vacia las caches. Util tras editar el YAML sin reiniciar."""
    _raw.cache_clear()
    get_defaults.cache_clear()
    get_rules.cache_clear()
    get_channels.cache_clear()
