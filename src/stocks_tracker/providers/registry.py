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


def _build_stooq():
    from .stooq_provider import StooqProvider

    return StooqProvider()


def _build_twelve_data():
    from .twelve_data_provider import TwelveDataProvider

    return TwelveDataProvider()


def _build_kraken():
    from .kraken_provider import KrakenPriceProvider

    return KrakenPriceProvider()


_BUILDERS["yfinance"] = _build_yfinance
_BUILDERS["stooq"] = _build_stooq
_BUILDERS["synthetic"] = SyntheticProvider
# Kraken solo sirve cripto, asi que NO entra en la cadena por defecto de
# `settings.yaml`: en una cadena responderia "no lo tengo" a todas las
# acciones y solo anadiria latencia. Se pide por nombre desde la ingesta de
# cripto.
_BUILDERS["kraken"] = _build_kraken
# Twelve Data tampoco entra en la cadena por defecto, y por un motivo distinto:
# su cuota gratuita (800 peticiones al dia) se agota en la primera pasada del
# universo. Existe para ser el TERCERO en la auditoria cruzada, donde se piden
# decenas de valores y no cientos.
_BUILDERS["twelve_data"] = _build_twelve_data


def available_providers() -> list[str]:
    return sorted(_BUILDERS)


def build_provider(name: str):
    builder = _BUILDERS.get(name)
    if builder is None:
        raise ProviderError(f"Proveedor desconocido: {name}")
    return builder()


def get_price_provider(name: str | None = None):
    """Proveedor de precios: uno concreto, o la cadena de `settings.yaml`.

    Con la cadena, el relevo no ocurre solo si el primero no se puede
    construir, sino tambien con lo que el primero no consigue traer. Ver
    `chain.ChainPriceProvider`.
    """
    if name:
        return build_provider(name)

    built = []
    errors: list[str] = []
    for candidate in get_settings().price_providers:
        try:
            built.append(build_provider(candidate))
        except ProviderError as exc:
            errors.append(f"{candidate}: {exc}")

    if not built:
        raise ProviderError(
            "Ningun proveedor de precios disponible. Detalle: " + "; ".join(errors)
        )
    if len(built) == 1:
        return built[0]

    from .chain import ChainPriceProvider

    return ChainPriceProvider(built)


def get_fundamentals_provider(name: str | None = None):
    """Los proveedores actuales sirven precios y fundamentales a la vez."""
    return get_price_provider(name)
