"""Broker simulado sobre `prices_daily`. Sin red, sin dinero, sin Alpaca.

Es la pieza que hace posible probar el bot entero —estrategia, riesgo,
ejecucion y contabilidad— en el CI y en el backtest. Todo lo que la fase 6
ejecuta pasa por aqui.

Tres decisiones de modelado que sesgan el resultado si se hacen mal:

1. **Las ordenes se ejecutan a la apertura de la sesion SIGUIENTE.** Proponer y
   ejecutar al mismo cierre que se acaba de leer es mirar el futuro: ese precio
   no estaba disponible cuando se tomo la decision. Es el mismo cuidado
   anti-look-ahead que el backtest de la fase 3.
2. **Un hueco por debajo del stop ejecuta a la apertura real, no al precio del
   stop.** Suponer que un stop se cumple siempre a su precio regala dinero que
   en el mercado no existe: si abre con un salto del 8 % en contra, se vende
   ahi. Es la diferencia entre un backtest honesto y uno que se cree a si mismo.
3. **El calendario sale de los datos.** Las fechas presentes en `prices_daily`
   SON las sesiones. Asi no hay que mantener un calendario de festivos que
   acabaria discrepando de los precios que realmente tenemos.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time

import pandas as pd

from ...core.ids import ulid
from .base import (
    Account,
    BrokerMode,
    BrokerRejectedError,
    Clock,
    Fill,
    InsufficientFundsError,
    Order,
    OrderRequest,
    Position,
)

_BPS = 1e-4


@dataclass
class _Holding:
    qty: float = 0.0
    avg_entry_price: float = 0.0


@dataclass
class _Pending:
    order: Order
    stop_price: float | None = None


class SimulatedBroker:
    """Implementa `BrokerAdapter` contra un historico de precios en memoria."""

    mode = BrokerMode.SIMULATED
    name = "simulated"

    def __init__(
        self,
        prices: pd.DataFrame,
        initial_cash: float = 55.0,
        slippage_bps: float = 15.0,
        commission_bps: float = 0.0,
        fractionable: set[str] | None = None,
    ) -> None:
        required = {"ticker", "date", "open", "high", "low", "close"}
        missing = required - set(prices.columns)
        if missing:
            raise ValueError(f"Faltan columnas en los precios: {sorted(missing)}")

        frame = prices.copy()
        frame["date"] = pd.to_datetime(frame["date"]).dt.date
        self._prices = frame.set_index(["date", "ticker"]).sort_index()

        self.sessions: list[date] = sorted(frame["date"].unique())
        if not self.sessions:
            raise ValueError("No hay ni una sesion de precios para simular")
        self._cursor = 0

        self.initial_cash = float(initial_cash)
        self.cash = float(initial_cash)
        self.slippage_bps = float(slippage_bps)
        self.commission_bps = float(commission_bps)
        self._fractionable = fractionable

        self._holdings: dict[str, _Holding] = {}
        self._pending: list[_Pending] = []
        self._orders: dict[str, Order] = {}
        self.fills: list[Fill] = []
        self.peak_equity = float(initial_cash)

        # Un day trade es abrir y cerrar el mismo valor en la misma sesion. Se
        # cuenta aqui porque la regla PDT de FINRA (3 en 5 dias habiles por
        # debajo de 25.000 $) es una restriccion dura con 50 EUR, y hay que
        # poder probarla en el backtest y no solo descubrirla en produccion.
        self._opened_today: dict[date, set[str]] = {}
        self._daytrades: list[date] = []

    # ------------------------------------------------------------------
    # Calendario
    # ------------------------------------------------------------------
    @property
    def current_date(self) -> date:
        return self.sessions[self._cursor]

    @property
    def has_next_session(self) -> bool:
        return self._cursor + 1 < len(self.sessions)

    def seek(self, when: date) -> None:
        """Situa el cursor en la primera sesion >= `when`."""
        for i, session in enumerate(self.sessions):
            if session >= when:
                self._cursor = i
                return
        self._cursor = len(self.sessions) - 1

    def advance(self) -> date:
        """Pasa a la sesion siguiente y ejecuta ahi lo que estuviera pendiente."""
        if not self.has_next_session:
            raise IndexError("No quedan sesiones en el historico simulado")
        self._cursor += 1
        self._process_pending()
        equity = self.get_account().equity
        self.peak_equity = max(self.peak_equity, equity)
        return self.current_date

    def get_clock(self) -> Clock:
        session = self.current_date
        open_at = datetime.combine(session, time(9, 30))
        close_at = datetime.combine(session, time(16, 0))
        return Clock(
            timestamp=close_at, is_open=False,
            next_open=open_at, next_close=close_at, session_date=session,
        )

    # ------------------------------------------------------------------
    # Precios
    # ------------------------------------------------------------------
    def _bar(self, ticker: str, when: date | None = None) -> pd.Series | None:
        try:
            return self._prices.loc[(when or self.current_date, ticker)]
        except KeyError:
            return None

    def get_latest_price(self, symbols: list[str]) -> dict[str, float]:
        out: dict[str, float] = {}
        for symbol in symbols:
            bar = self._bar(symbol)
            if bar is not None and pd.notna(bar["close"]):
                out[symbol] = float(bar["close"])
        return out

    def is_fractionable(self, symbol: str) -> bool:
        # Sin lista explicita se asume que si: en el universo del bot (S&P 500 y
        # Nasdaq 100) Alpaca ofrece fraccionadas en practicamente todo, y
        # asumir lo contrario apagaria la simulacion entera.
        if self._fractionable is None:
            return True
        return symbol in self._fractionable

    def supports(self, feature: str) -> bool:
        # Ni bracket ni OCO: Alpaca los rechaza en ordenes fraccionadas y
        # notional (error 42210000), asi que los stops los lleva nuestro bot.
        # El simulador miente si dice que si, y esa mentira solo se descubriria
        # al pasar a papel.
        return feature in {"fractional", "notional", "stop"}

    # ------------------------------------------------------------------
    # Cuenta y posiciones
    # ------------------------------------------------------------------
    def long_market_value(self) -> float:
        total = 0.0
        for ticker, holding in self._holdings.items():
            if holding.qty <= 0:
                continue
            bar = self._bar(ticker)
            price = float(bar["close"]) if bar is not None else holding.avg_entry_price
            total += holding.qty * price
        return total

    def get_account(self) -> Account:
        equity = self.cash + self.long_market_value()
        return Account(
            account_id="SIM", currency="USD", cash=self.cash, equity=equity,
            buying_power=self.cash, last_equity=equity,
            daytrade_count=self.daytrade_count(),
            pattern_day_trader=False, trading_blocked=False,
            account_blocked=False, shorting_enabled=False,
        )

    def daytrade_count(self, window: int = 5) -> int:
        """Day trades en las ultimas `window` sesiones."""
        recent = set(self.sessions[max(0, self._cursor - window + 1):self._cursor + 1])
        return sum(1 for d in self._daytrades if d in recent)

    def get_positions(self) -> list[Position]:
        out = []
        for ticker, holding in sorted(self._holdings.items()):
            if holding.qty <= 0:
                continue
            bar = self._bar(ticker)
            price = float(bar["close"]) if bar is not None else holding.avg_entry_price
            value = holding.qty * price
            cost = holding.qty * holding.avg_entry_price
            out.append(
                Position(
                    symbol=ticker, qty=holding.qty,
                    avg_entry_price=holding.avg_entry_price,
                    market_value=value, unrealized_pl=value - cost,
                    unrealized_plpc=(value / cost - 1.0) if cost else 0.0,
                    current_price=price,
                )
            )
        return out

    def get_position(self, symbol: str) -> Position | None:
        return next((p for p in self.get_positions() if p.symbol == symbol), None)

    # ------------------------------------------------------------------
    # Ordenes
    # ------------------------------------------------------------------
    def get_orders(self, status: str = "open") -> list[Order]:
        if status == "open":
            return [p.order for p in self._pending]
        return [o for o in self._orders.values() if status in ("all", o.status)]

    def get_order_by_client_id(self, client_order_id: str) -> Order | None:
        return self._orders.get(client_order_id)

    def submit_order(self, req: OrderRequest, stop_price: float | None = None) -> Order:
        """Encola la orden. Se ejecuta en la sesion siguiente, no en esta.

        Idempotente por `client_order_id`: reenviar la misma orden devuelve la
        que ya existe en lugar de duplicarla. Es lo que hace seguro reintentar
        tras una caida a medio camino.
        """
        existing = self._orders.get(req.client_order_id)
        if existing is not None:
            return existing

        if req.side not in ("buy", "sell"):
            raise BrokerRejectedError(f"Lado desconocido: {req.side}")
        if req.side == "sell":
            held = self._holdings.get(req.symbol, _Holding()).qty
            wanted = req.qty if req.qty is not None else 0.0
            if wanted - held > 1e-9:
                # Vender mas de lo que se tiene es ponerse corto, y el mandato
                # lo prohibe. Que el broker simulado lo permitiera dejaria
                # pasar en el backtest algo que el broker real rechazaria.
                raise BrokerRejectedError(
                    f"{req.symbol}: venta de {wanted} con {held} en cartera"
                )

        order = Order(
            broker_order_id=ulid(), client_order_id=req.client_order_id,
            symbol=req.symbol, side=req.side, order_type=req.order_type,
            tif=req.tif, status="accepted", submitted_at=self.get_clock().timestamp,
            qty=req.qty, notional=req.notional,
        )
        self._orders[order.client_order_id] = order
        self._pending.append(_Pending(order=order, stop_price=stop_price))
        return order

    def cancel_order(self, broker_order_id: str) -> None:
        for pending in list(self._pending):
            if pending.order.broker_order_id == broker_order_id:
                self._pending.remove(pending)
                self._replace(pending.order, status="canceled")

    def cancel_all_orders(self) -> int:
        count = len(self._pending)
        for pending in list(self._pending):
            self._pending.remove(pending)
            self._replace(pending.order, status="canceled")
        return count

    def close_position(self, symbol: str, qty: float | None = None) -> Order:
        holding = self._holdings.get(symbol)
        if holding is None or holding.qty <= 0:
            raise BrokerRejectedError(f"{symbol}: no hay posicion que cerrar")
        amount = holding.qty if qty is None else min(qty, holding.qty)
        return self.submit_order(
            OrderRequest(symbol=symbol, side="sell", qty=amount,
                         client_order_id=f"close-{symbol}-{ulid()}")
        )

    def close_all_positions(self, cancel_orders: bool = True) -> list[Order]:
        if cancel_orders:
            self.cancel_all_orders()
        return [self.close_position(p.symbol) for p in self.get_positions()]

    # ------------------------------------------------------------------
    # Ejecucion
    # ------------------------------------------------------------------
    def _replace(self, order: Order, **changes) -> Order:
        updated = Order(**{**order.__dict__, **changes})
        self._orders[order.client_order_id] = updated
        return updated

    def _process_pending(self) -> None:
        session = self.current_date
        for pending in list(self._pending):
            order = pending.order
            bar = self._bar(order.symbol, session)
            if bar is None or pd.isna(bar["open"]):
                # Sin cotizacion ese dia la orden sigue pendiente. Inventar un
                # precio seria peor: produciria operaciones que no ocurrieron.
                continue

            if pending.stop_price is not None:
                if float(bar["low"]) > pending.stop_price:
                    continue  # el stop no se ha tocado; sigue vigilando
                # Si abre por debajo del stop, se ejecuta a la apertura real.
                price = min(float(bar["open"]), pending.stop_price)
            else:
                price = float(bar["open"])

            self._pending.remove(pending)
            self._fill(order, price, session)

    def _fill(self, order: Order, raw_price: float, session: date) -> None:
        direction = 1 if order.side == "buy" else -1
        price = raw_price * (1 + direction * self.slippage_bps * _BPS)
        if price <= 0:
            self._replace(order, status="rejected", reject_reason="precio no positivo")
            return

        qty = order.qty
        if qty is None:
            qty = (order.notional or 0.0) / price
        gross = qty * price
        commission = gross * self.commission_bps * _BPS

        if order.side == "buy":
            if gross + commission > self.cash + 1e-9:
                self._replace(order, status="rejected",
                              reject_reason="efectivo insuficiente")
                raise InsufficientFundsError(
                    f"{order.symbol}: hacen falta {gross + commission:.2f} y hay "
                    f"{self.cash:.2f}"
                )
            self.cash -= gross + commission
            holding = self._holdings.setdefault(order.symbol, _Holding())
            total_cost = holding.qty * holding.avg_entry_price + gross
            holding.qty += qty
            holding.avg_entry_price = total_cost / holding.qty if holding.qty else 0.0
            self._opened_today.setdefault(session, set()).add(order.symbol)
        else:
            holding = self._holdings.get(order.symbol, _Holding())
            qty = min(qty, holding.qty)
            gross = qty * price
            commission = gross * self.commission_bps * _BPS
            self.cash += gross - commission
            holding.qty -= qty
            if holding.qty <= 1e-9:
                self._holdings.pop(order.symbol, None)
            if order.symbol in self._opened_today.get(session, set()):
                self._daytrades.append(session)

        filled_at = datetime.combine(session, time(9, 30))
        self._replace(order, status="filled", filled_qty=qty,
                      filled_avg_price=price, filled_at=filled_at)
        self.fills.append(
            Fill(
                fill_id=ulid(), client_order_id=order.client_order_id,
                ticker=order.symbol, side=order.side, qty=qty, price=price,
                filled_at=filled_at, commission=commission,
                slippage_bps=self.slippage_bps * direction,
                extra={"raw_price": raw_price, "session": str(session)},
            )
        )


@dataclass
class SimulatedState:
    """Foto de la cartera simulada, para volcarla a `portfolio_snapshots`."""

    snapshot_at: datetime
    cash: float
    equity: float
    long_market_value: float
    n_positions: int
    gross_exposure_pct: float
    daytrade_count: int
    peak_equity: float
    drawdown_pct: float
    positions: list[dict] = field(default_factory=list)


def snapshot(broker: SimulatedBroker) -> SimulatedState:
    account = broker.get_account()
    lmv = broker.long_market_value()
    peak = max(broker.peak_equity, account.equity)
    return SimulatedState(
        snapshot_at=broker.get_clock().timestamp,
        cash=account.cash, equity=account.equity, long_market_value=lmv,
        n_positions=len(broker.get_positions()),
        gross_exposure_pct=(lmv / account.equity * 100.0) if account.equity else 0.0,
        daytrade_count=account.daytrade_count,
        peak_equity=peak,
        drawdown_pct=((account.equity / peak - 1.0) * 100.0) if peak else 0.0,
        positions=[p.__dict__ for p in broker.get_positions()],
    )
