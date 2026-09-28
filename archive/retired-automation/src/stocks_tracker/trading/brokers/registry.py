"""Seleccion del broker segun el mercado y el modo.

Punto unico donde se decide con que se opera. Falla con un mensaje que dice
que falta, no con un `ImportError` de un modulo que no existe.

El broker real de un venue NO se entrega sin mas: pasa antes por
`venues.require_tradeable`, que comprueba credenciales, que el venue este
activado y que su estrategia haya superado la puerta. Construirlo aqui sin esa
comprobacion seria un camino que llega al dinero saltandose la validacion, y
en ese caso el resto de las barreras dan igual.
"""

from __future__ import annotations

import pandas as pd

from ...core.config import ConfigError, get_trading_config
from .base import BrokerAdapter, BrokerMode
from .simulated import SimulatedBroker

# Que adaptador lleva cada mercado. Solo cripto por ahora: en Polymarket cada
# orden se firma con la clave privada de la wallet y esa parte no esta escrita.
_LIVE_BROKERS = {"kraken"}


def get_broker(mode: BrokerMode | str, prices: pd.DataFrame | None = None,
               venue: str | None = None) -> BrokerAdapter:
    mode = BrokerMode(mode)
    cfg = get_trading_config()

    if mode is BrokerMode.SIMULATED:
        if prices is None:
            raise ValueError(
                "El broker simulado necesita el historico de precios: es su "
                "unica fuente de ejecucion."
            )
        execution = cfg.venue(venue).execution if venue else cfg.execution
        equity = cfg.venue(venue).initial_equity if venue else cfg.initial_equity
        return SimulatedBroker(
            prices=prices,
            initial_cash=equity,
            slippage_bps=float(execution.get("slippage_bps_assumed", 15.0)),
            commission_bps=float(execution.get("commission_bps", 0.0)),
        )

    if venue is None:
        raise ConfigError(
            f"Para operar en '{mode}' hay que decir en que mercado. "
            f"Con adaptador: {', '.join(sorted(_LIVE_BROKERS)) or 'ninguno'}."
        )
    if venue not in _LIVE_BROKERS:
        raise ConfigError(
            f"'{venue}' no tiene adaptador de ejecucion todavia. "
            + ("En Polymarket cada orden se firma con la clave privada de la "
               "wallet, y esa parte no esta escrita: por ahora solo se puede "
               "leer y estudiar." if venue == "polymarket" else "")
        )

    return build_broker(venue, mode=mode)


def build_broker(venue: str, mode: BrokerMode | str = BrokerMode.LIVE) -> BrokerAdapter:
    """El adaptador real de un venue, previa comprobacion de que se puede usar.

    La comprobacion no es opcional ni se puede saltar pasando un argumento:
    este es el unico sitio del programa donde se construye algo capaz de
    gastar dinero, asi que es donde tiene que estar.
    """
    from ..venues import require_tradeable

    require_tradeable(venue, str(mode))

    if venue != "kraken":
        raise ConfigError(f"'{venue}' no tiene adaptador de ejecucion.")

    from .kraken import KrakenBroker

    if BrokerMode(mode) is BrokerMode.LIVE:
        return KrakenBroker(mode=BrokerMode.LIVE)

    # Kraken spot no tiene entorno de pruebas. El modo papel se hace con
    # precios reales de Kraken y ejecucion simulada por nuestra cuenta;
    # devolver el adaptador real habria mandado ordenes con dinero mientras
    # el usuario creia estar probando.
    from ..context import scope
    from .paper import PaperBroker

    cfg = get_trading_config()
    vcfg = cfg.venue(venue)
    return PaperBroker(
        prices=KrakenBroker(mode=BrokerMode.LIVE),   # solo para leer precios
        mode_key=scope(str(mode), venue),
        initial_cash=vcfg.initial_equity,
        slippage_bps=float(vcfg.execution.get("slippage_bps_assumed", 25.0)),
        commission_bps=float(vcfg.execution.get("commission_bps", 26.0)),
    )
