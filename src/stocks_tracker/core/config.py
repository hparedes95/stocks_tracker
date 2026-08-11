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


@lru_cache(maxsize=1)
def get_breadth_scope() -> str:
    """Universo de referencia para la amplitud y el semaforo de riesgo."""
    raw = _load_yaml("universe.yaml")
    return str(raw.get("breadth_scope") or "SP500")


@lru_cache(maxsize=1)
def get_macro_config() -> dict[str, Any]:
    return _load_yaml("macro.yaml")


@lru_cache(maxsize=1)
def get_fred_series() -> dict[str, dict]:
    return dict(get_macro_config().get("fred_series") or {})


class ConfigError(ValueError):
    """Configuracion invalida. Aborta el arranque en lugar de seguir a medias."""


# Limites que no son configurables. No estan en el YAML para poder cambiarlos,
# sino para que el mandato se lea completo en un solo sitio; si alguien los
# pone a `true`, el bot no arranca. Sin esto, relajar el mandato seria editar
# una linea de YAML, que es exactamente lo que no debe poder hacerse en
# caliente y sin pensarlo dos veces.
FORBIDDEN_ALWAYS = ("allow_shorting", "allow_leverage", "allow_options",
                    "allow_extended_hours")


@dataclass(frozen=True)
class TradingConfig:
    """Mandato del bot. Unica fuente de los limites de riesgo."""

    raw: dict[str, Any]

    def __post_init__(self) -> None:
        risk = self.raw.get("risk") or {}
        for key in FORBIDDEN_ALWAYS:
            if risk.get(key):
                raise ConfigError(
                    f"'{key}' no se puede activar: es una prohibicion absoluta "
                    "del mandato, no una opcion."
                )
        if self.mode not in ("simulated", "paper", "live"):
            raise ConfigError(f"Modo de trading desconocido: {self.mode}")
        if self.autonomy not in ("semi", "auto"):
            raise ConfigError(f"Nivel de autonomia desconocido: {self.autonomy}")
        if self.capital_cap <= 0:
            raise ConfigError("capital_cap tiene que ser positivo")

    @property
    def mode(self) -> str:
        return str(self.raw.get("mode", "simulated"))

    @property
    def autonomy(self) -> str:
        return str(self.raw.get("autonomy", "semi"))

    @property
    def capital_cap(self) -> float:
        return float(self.raw.get("capital_cap", 55.0))

    @property
    def initial_equity(self) -> float:
        return float(self.raw.get("initial_equity", self.capital_cap))

    @property
    def universe(self) -> dict[str, Any]:
        return dict(self.raw.get("universe") or {})

    @property
    def risk(self) -> dict[str, Any]:
        return dict(self.raw.get("risk") or {})

    @property
    def execution(self) -> dict[str, Any]:
        return dict(self.raw.get("execution") or {})

    @property
    def approval(self) -> dict[str, Any]:
        return dict(self.raw.get("approval") or {})

    @property
    def kill_switch(self) -> dict[str, Any]:
        return dict(self.raw.get("kill_switch") or {})

    def strategy(self, strategy_id: str) -> dict[str, Any]:
        return dict((self.raw.get("strategies") or {}).get(strategy_id) or {})

    @property
    def venues(self) -> dict[str, Any]:
        return dict(self.raw.get("venues") or {})

    def venue(self, key: str) -> VenueConfig:
        raw = self.venues.get(key)
        if raw is None:
            disponibles = ", ".join(sorted(self.venues)) or "ninguno"
            raise ConfigError(
                f"No hay ningun venue llamado '{key}'. Configurados: {disponibles}"
            )
        return VenueConfig(key=key, raw=raw)

    def enabled_venues(self) -> list[str]:
        return sorted(k for k, v in self.venues.items() if (v or {}).get("enabled"))

    def autonomy_for(self, mode: str) -> str:
        """Autonomia por modo. En real es 'semi' y no se negocia.

        Aprobar cuarenta propuestas de papel no ensena nada y produce fatiga de
        alertas: a la decima se pulsa "aprobar" sin leer, y una aprobacion que
        se sella sin mirar no es un control, es teatro. La friccion vuelve
        donde hay consecuencias.
        """
        politica = dict(self.raw.get("autonomy_policy") or {})
        if mode == "live":
            return "semi"
        return str(politica.get(mode, "semi"))

    def limit(self, name: str) -> float:
        """Limite numerico de riesgo, con error claro si falta.

        Devolver un valor por defecto silencioso seria peligroso: un limite
        ausente pasaria a ser un limite inventado por el codigo, y el usuario
        creeria estar operando bajo el mandato que leyo en el YAML.
        """
        risk = self.risk
        if name not in risk:
            raise ConfigError(f"Falta el limite de riesgo '{name}' en trading.yaml")
        return float(risk[name])


@dataclass(frozen=True)
class VenueConfig:
    """Un mercado donde operar, con su cartera y sus limites propios.

    Cartera separada, nunca un bote comun: una racha mala en cripto no puede
    consumir el presupuesto de Polymarket, y el kill switch de uno no para al
    otro. Compartir el saldo convertiria dos apuestas independientes en una
    sola, mas grande.
    """

    key: str
    raw: dict[str, Any]

    def __post_init__(self) -> None:
        for name in FORBIDDEN_ALWAYS:
            # `allow_extended_hours` si es legitimo en cripto y en mercados de
            # prediccion: funcionan 24/7 y "fuera de horario" no significa nada.
            # Los otros tres siguen prohibidos en todas partes.
            if name == "allow_extended_hours":
                continue
            if self.risk.get(name):
                raise ConfigError(
                    f"venue '{self.key}': '{name}' no se puede activar. Es una "
                    "prohibicion absoluta del mandato, no una opcion."
                )
        if self.capital_cap <= 0:
            raise ConfigError(f"venue '{self.key}': capital_cap tiene que ser positivo")

    @property
    def enabled(self) -> bool:
        return bool(self.raw.get("enabled", False))

    @property
    def label(self) -> str:
        return str(self.raw.get("label", self.key))

    @property
    def asset_class(self) -> str:
        return str(self.raw.get("asset_class", "equity"))

    @property
    def quote_currency(self) -> str:
        return str(self.raw.get("quote_currency", "EUR"))

    @property
    def capital_cap(self) -> float:
        return float(self.raw.get("capital_cap", 0.0))

    @property
    def initial_equity(self) -> float:
        return float(self.raw.get("initial_equity", self.capital_cap))

    @property
    def universe(self) -> dict[str, Any]:
        return dict(self.raw.get("universe") or {})

    @property
    def risk(self) -> dict[str, Any]:
        return dict(self.raw.get("risk") or {})

    @property
    def execution(self) -> dict[str, Any]:
        return dict(self.raw.get("execution") or {})

    def limit(self, name: str) -> float:
        """Limite numerico, con error claro si falta.

        Igual que en el mandato de acciones: un limite ausente que se rellena
        con un valor por defecto silencioso pasa a ser un limite inventado por
        el codigo, y el usuario creeria estar operando bajo lo que leyo.
        """
        if name not in self.risk:
            raise ConfigError(
                f"venue '{self.key}': falta el limite '{name}' en trading.yaml"
            )
        return float(self.risk[name])


@lru_cache(maxsize=1)
def get_trading_config() -> TradingConfig:
    return TradingConfig(raw=_load_yaml("trading.yaml"))


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
