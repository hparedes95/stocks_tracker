"""Seleccion del broker segun el modo.

Punto unico donde se decide con que se opera. En la fase 6 solo existe el
simulado: `paper` y `live` fallan con un mensaje que dice en que fase se
habilitan, en lugar de con un `ImportError` de un modulo que no existe.
"""

from __future__ import annotations

import pandas as pd

from ...core.config import ConfigError, get_trading_config
from .base import BrokerAdapter, BrokerMode
from .simulated import SimulatedBroker


def get_broker(mode: BrokerMode | str, prices: pd.DataFrame | None = None) -> BrokerAdapter:
    mode = BrokerMode(mode)
    cfg = get_trading_config()

    if mode is BrokerMode.SIMULATED:
        if prices is None:
            raise ValueError(
                "El broker simulado necesita el historico de precios: es su "
                "unica fuente de ejecucion."
            )
        execution = cfg.execution
        return SimulatedBroker(
            prices=prices,
            initial_cash=cfg.initial_equity,
            slippage_bps=float(execution.get("slippage_bps_assumed", 15.0)),
            commission_bps=float(execution.get("commission_bps", 0.0)),
        )

    raise ConfigError(
        f"El modo '{mode}' todavia no esta implementado. La fase 6 es solo "
        "simulacion: el broker real (Alpaca, en papel) llega en la fase 7, y "
        "no antes de que el backtest con costes supere la puerta 1."
    )
