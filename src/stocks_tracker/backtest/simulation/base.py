"""Contrato del broker.

Existe por dos motivos que no son intercambiables:

1. **Aislar Alpaca.** El SDK del broker se importa en un unico fichero
   (`alpaca.py`, fase 7). Un test recorre el AST de `src/` para comprobarlo.
   Sin esa frontera, cambiar de broker —o probar sin broker— obliga a tocar
   media docena de modulos.
2. **Poder probarlo todo sin red ni dinero.** `SimulatedBroker` implementa el
   mismo contrato contra `prices_daily`. Es lo que usa la fase 6 entera y la
   suite de tests: cero llamadas de red en el CI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Protocol


class BrokerMode(StrEnum):
    SIMULATED = "simulated"
    PAPER = "paper"
    LIVE = "live"


class BrokerError(RuntimeError):
    """Fallo generico del broker."""


class BrokerAuthError(BrokerError):
    """Credenciales invalidas o ausentes."""


class BrokerRejectedError(BrokerError):
    """El broker ha rechazado la orden."""


class BrokerRateLimitError(BrokerError):
    """Limite de peticiones alcanzado."""


class BrokerUnavailableError(BrokerError):
    """El broker no responde."""


class InsufficientFundsError(BrokerError):
    """No hay efectivo para la orden."""


@dataclass(frozen=True)
class Account:
    account_id: str
    currency: str
    cash: float
    equity: float
    buying_power: float
    last_equity: float
    pattern_day_trader: bool = False
    daytrade_count: int = 0
    trading_blocked: bool = False
    account_blocked: bool = False
    shorting_enabled: bool = False


@dataclass(frozen=True)
class Position:
    symbol: str
    qty: float
    avg_entry_price: float
    market_value: float
    unrealized_pl: float
    unrealized_plpc: float
    current_price: float


@dataclass(frozen=True)
class Order:
    broker_order_id: str
    client_order_id: str
    symbol: str
    side: str
    order_type: str
    tif: str
    status: str
    submitted_at: datetime
    qty: float | None = None
    notional: float | None = None
    filled_qty: float = 0.0
    filled_avg_price: float | None = None
    filled_at: datetime | None = None
    reject_reason: str | None = None


@dataclass(frozen=True)
class OrderRequest:
    symbol: str
    side: str
    client_order_id: str
    order_type: str = "market"
    tif: str = "day"
    qty: float | None = None
    notional: float | None = None
    limit_price: float | None = None

    def __post_init__(self) -> None:
        if self.qty is None and self.notional is None:
            raise ValueError(f"{self.symbol}: orden sin cantidad ni importe")
        if self.qty is not None and self.notional is not None:
            # Alpaca rechaza las dos a la vez, y mandarlas seria ambiguo:
            # no sabriamos cual de las dos manda.
            raise ValueError(f"{self.symbol}: qty y notional son excluyentes")


@dataclass(frozen=True)
class Clock:
    timestamp: datetime
    is_open: bool
    next_open: datetime
    next_close: datetime
    session_date: date | None = None


@dataclass(frozen=True)
class Fill:
    """Ejecucion concreta. El slippage se mide contra el precio de referencia
    del intent, no contra el de la orden: lo que interesa saber es cuanto se
    ha desviado la realidad de la propuesta que se aprobo."""

    fill_id: str
    client_order_id: str
    ticker: str
    side: str
    qty: float
    price: float
    filled_at: datetime
    commission: float = 0.0
    slippage_bps: float | None = None
    extra: dict = field(default_factory=dict)


class BrokerAdapter(Protocol):
    mode: BrokerMode
    name: str

    def get_account(self) -> Account: ...
    def get_positions(self) -> list[Position]: ...
    def get_position(self, symbol: str) -> Position | None: ...
    def get_orders(self, status: str = "open") -> list[Order]: ...
    def get_order_by_client_id(self, client_order_id: str) -> Order | None: ...
    def submit_order(self, req: OrderRequest) -> Order: ...
    def cancel_order(self, broker_order_id: str) -> None: ...
    def cancel_all_orders(self) -> int: ...
    def close_position(self, symbol: str, qty: float | None = None) -> Order: ...
    def close_all_positions(self, cancel_orders: bool = True) -> list[Order]: ...
    def get_clock(self) -> Clock: ...
    def get_latest_price(self, symbols: list[str]) -> dict[str, float]: ...
    def is_fractionable(self, symbol: str) -> bool: ...
    def supports(self, feature: str) -> bool: ...
