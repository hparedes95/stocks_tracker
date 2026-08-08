"""Carga y validacion de la configuracion YAML.

Todo lo configurable vive en `config/*.yaml`. Este modulo los lee una sola vez
y los expone como objetos tipados; ningun otro modulo abre esos ficheros.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


def project_root() -> Path:
    """Raiz del proyecto (contiene `config/` y `src/`)."""
    env = os.environ.get("STOCKS_TRACKER_ROOT")
    if env:
        return Path(env).resolve()
    return Path(__file__).resolve().parents[3]


def _load_yaml(name: str) -> dict[str, Any]:
    path = project_root() / "config" / name
    if not path.exists():
        raise FileNotFoundError(f"Falta el fichero de configuracion: {path}")
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _flatten_tickers(raw: Any) -> list[str]:
    """Acepta listas YAML normales y el estilo compacto `- AAPL, MSFT, NVDA`.

    El estilo compacto mantiene `universe.yaml` legible sin una linea por
    ticker; a cambio hay que separar por comas aqui.
    """
    out: list[str] = []
    if raw is None:
        return out
    for item in raw:
        if isinstance(item, str):
            out.extend(part.strip() for part in item.split(",") if part.strip())
        else:
            out.append(str(item).strip())
    # dedup preservando orden
    seen: set[str] = set()
    return [t for t in out if not (t in seen or seen.add(t))]


@dataclass(frozen=True)
class UniverseSpec:
    key: str
    name: str
    source: str
    benchmark: str
    currency: str
    asset_class: str
    tickers: list[str]


@dataclass(frozen=True)
class SubmetricSpec:
    field: str
    sign: int = 1
    min_valid: float | None = None
    max_valid: float | None = None


@dataclass(frozen=True)
class FactorSpec:
    name: str
    submetrics: list[SubmetricSpec]


@dataclass
class Settings:
    raw: dict[str, Any]

    @property
    def warehouse_path(self) -> Path:
        return project_root() / self.raw["paths"]["warehouse"]

    @property
    def raw_dir(self) -> Path:
        return project_root() / self.raw["paths"]["raw"]

    @property
    def logs_dir(self) -> Path:
        return project_root() / self.raw["paths"]["logs"]

    @property
    def ingest(self) -> dict[str, Any]:
        return self.raw.get("ingest", {})

    @property
    def compute(self) -> dict[str, Any]:
        return self.raw.get("compute", {})

    @property
    def ui(self) -> dict[str, Any]:
        return self.raw.get("ui", {})

    @property
    def price_providers(self) -> list[str]:
        return self.raw.get("providers", {}).get("price", ["yfinance"])

    @property
    def tradingview_enabled(self) -> bool:
        return bool(self.ui.get("tradingview", {}).get("enabled", True))


@dataclass
class FactorConfig:
    raw: dict[str, Any]
    factors: dict[str, FactorSpec] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name, spec in (self.raw.get("factors") or {}).items():
            subs = [SubmetricSpec(**s) for s in spec.get("submetrics", [])]
            self.factors[name] = FactorSpec(name=name, submetrics=subs)

    @property
    def peer_group(self) -> str:
        return self.raw.get("peer_group", "gics_sector")

    @property
    def min_group_size(self) -> int:
        return int(self.raw.get("min_group_size", 8))

    @property
    def winsorize(self) -> tuple[float, float]:
        lo, hi = self.raw.get("winsorize", [0.02, 0.98])
        return float(lo), float(hi)

    @property
    def robust_zscore(self) -> bool:
        return bool(self.raw.get("robust_zscore", True))

    @property
    def coverage_floor(self) -> float:
        return float(self.raw.get("coverage_floor", 0.4))

    @property
    def presets(self) -> dict[str, dict[str, float]]:
        return self.raw.get("presets", {})

    @property
    def guards(self) -> dict[str, Any]:
        return self.raw.get("guards", {})

    def regime_multipliers(self, regime: str) -> dict[str, float]:
        return (self.raw.get("regime_multipliers") or {}).get(regime, {}) or {}

    def weights(self, preset: str = "balanced") -> dict[str, float]:
        w = self.presets.get(preset)
        if w is None:
            raise KeyError(f"Preset de pesos desconocido: {preset}")
        return dict(w)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(raw=_load_yaml("settings.yaml"))


@lru_cache(maxsize=1)
def get_factor_config() -> FactorConfig:
    return FactorConfig(raw=_load_yaml("factors.yaml"))


@lru_cache(maxsize=1)
def get_explanations() -> dict[str, Any]:
    return _load_yaml("explanations.yaml")


@lru_cache(maxsize=1)
def get_symbol_overrides() -> dict[str, str]:
    """Aplana el YAML de overrides en un unico mapa ticker -> simbolo TV."""
    raw = _load_yaml("symbol_overrides.yaml")
    flat: dict[str, str] = {}
    for section, values in raw.items():
        if section == "blacklist":
            continue
        if isinstance(values, dict):
            flat.update({str(k): str(v) for k, v in values.items()})
    return flat


@lru_cache(maxsize=1)
def get_symbol_blacklist() -> frozenset[str]:
    raw = _load_yaml("symbol_overrides.yaml")
    return frozenset(raw.get("blacklist") or [])


@lru_cache(maxsize=1)
def get_universes() -> dict[str, UniverseSpec]:
    raw = _load_yaml("universe.yaml")
    out: dict[str, UniverseSpec] = {}
    for key, spec in (raw.get("universes") or {}).items():
        out[key] = UniverseSpec(
            key=key,
            name=spec.get("name", key),
            source=spec.get("source", "manual"),
            benchmark=spec.get("benchmark", "^GSPC"),
            currency=spec.get("currency", "USD"),
            asset_class=spec.get("asset_class", "equity"),
            tickers=_flatten_tickers(spec.get("tickers")),
        )
    return out


@lru_cache(maxsize=1)
def get_active_universes() -> list[str]:
    raw = _load_yaml("universe.yaml")
    return list(raw.get("active") or list(get_universes()))


@lru_cache(maxsize=1)
def get_sector_etfs() -> dict[str, str]:
    raw = _load_yaml("universe.yaml")
    return dict(raw.get("sector_etfs") or {})


def all_active_tickers() -> list[str]:
    """Todos los tickers de los universos activos, sin duplicados."""
    universes = get_universes()
    seen: set[str] = set()
    out: list[str] = []
    for key in get_active_universes():
        spec = universes.get(key)
        if spec is None:
            continue
        for t in spec.tickers:
            if t not in seen:
                seen.add(t)
                out.append(t)
    return out
