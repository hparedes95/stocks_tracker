"""Seleccion de proveedor con cadena de fallback.

Si el primero falla o no esta disponible, se pasa al siguiente. Es lo que hace
que una ruptura de la API de Yahoo no deje el sistema muerto.
"""

from __future__ import annotations

from ..core.config import get_settings
from .base import ProviderError
from .synthetic_provider import SyntheticProvider

_BUILDERS: dict[str, callable] = {}


def _build_yfinance():
    from .yfinance_provider import YFinanceProvider

    return YFinanceProvider()


_BUILDERS["yfinance"] = _build_yfinance
_BUILDERS["synthetic"] = SyntheticProvider


def available_providers() -> list[str]:
    return sorted(_BUILDERS)


def get_price_provider(name: str | None = None):
    """Devuelve el primer proveedor de precios utilizable.

    Con `name` se fuerza uno concreto; sin el, se recorre la cadena de
    `settings.yaml`.
    """
    if name:
        builder = _BUILDERS.get(name)
        if builder is None:
            raise ProviderError(f"Proveedor desconocido: {name}")
        return builder()

    errors: list[str] = []
    for candidate in get_settings().price_providers:
        builder = _BUILDERS.get(candidate)
        if builder is None:
            errors.append(f"{candidate}: no registrado")
            continue
        try:
            return builder()
        except ProviderError as exc:
            errors.append(f"{candidate}: {exc}")

    raise ProviderError(
        "Ningun proveedor de precios disponible. Detalle: " + "; ".join(errors)
    )


def get_fundamentals_provider(name: str | None = None):
    """Los proveedores actuales sirven precios y fundamentales a la vez."""
    return get_price_provider(name)
