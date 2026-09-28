"""Modo papel: precios reales, ejecucion simulada, cero dinero.

Kraken spot **no tiene entorno de pruebas**. Eso deja dos opciones honestas y
ninguna intermedia: o se opera con dinero real, o se simula la ejecucion por
nuestra cuenta. Esto es lo segundo, y conviene tener claro que prueba y que no.

**Lo que SI prueba, que es casi todo lo que puede fallar:** que la descarga
funcione con la red de verdad, que la estrategia decida sobre precios reales de
ahora y no de un backtest, que el riesgo aplique el mandato correcto, que el
freno de mano retenga lo que debe, que la contabilidad cuadre entre ciclos, y
que el kill switch salte cuando toca. Es exactamente donde han aparecido todos
los fallos de este programa.

**Lo que NO prueba:** que Kraken acepte la orden. La firma, los minimos por
par, el redondeo de decimales y el rechazo por fondos solo se ejercitan
mandando de verdad. Al pasar a real queda ese tramo sin estrenar, y por eso el
freno de mano retiene la primera orden con dinero.

**Lo que tampoco es:** una estimacion fiable del resultado. Se ejecuta al
precio del momento con el deslizamiento que dice el mandato, pero un libro
real tiene profundidad y el nuestro no. En posiciones de seis euros la
diferencia es pequena; en cualquier otra cosa, no.

El estado vive en `orders` y `fills` con el modo `paper:...`, o sea en las
mismas tablas que lo real. No es casualidad: es lo que permite comparar las dos
contabilidades con la misma consulta, y lo que hace que la reconciliacion del
dia que se pase a real ejercite el mismo codigo que ya se ha usado un mes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ...core.db import connect
from ...core.ids import ulid
from .base import (
    Account,
    BrokerMode,
    BrokerRejectedError,
    Clock,
    InsufficientFundsError,
    Order,
    OrderRequest,
    Position,
)


@dataclass
class PaperBroker:
    """Ejecuta contra precios reales sin mandar nada a ningun sitio."""

    prices: object                      # algo con `get_latest_price(symbols)`
    mode_key: str                       # 'paper:kraken'
    initial_cash: float = 25.0
    slippage_bps: float = 25.0
    commission_bps: float = 26.0
    name: str = "paper"
    mode: BrokerMode = BrokerMode.PAPER
    _pairs: dict = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Precios: los de verdad
    # ------------------------------------------------------------------
    def get_latest_price(self, symbols: list[str]) -> dict[str, float]:
        return self.prices.get_latest_price(symbols)

    def _price(self, symbol: str) -> float:
        precio = self.get_latest_price([symbol]).get(symbol)
        if not precio or precio <= 0:
            raise BrokerRejectedError(
                f"{symbol}: sin precio actual. No se simula la ejecucion a "
                "ciegas: inventar un precio produciria una contabilidad que no "
                "corresponde a ningun mercado."
            )
        return float(precio)

    def get_clock(self) -> Clock:
        ahora = datetime.now()
        return Clock(timestamp=ahora, is_open=True, next_open=ahora,
                     next_close=ahora + timedelta(days=365),
                     session_date=ahora.date())

    # ------------------------------------------------------------------
    # Contabilidad, derivada de los fills
    # ------------------------------------------------------------------
    def _fills(self) -> list[tuple]:
        with connect(read_only=True) as conn:
            return conn.execute(
                "SELECT ticker, side, qty, price, commission FROM fills "
                "WHERE mode = ? ORDER BY filled_at", [self.mode_key],
            ).fetchall()

    def _book(self) -> tuple[dict[str, dict], float]:
        """Posiciones y caja, reconstruidas desde los fills.

        Se derivan y no se guardan aparte a proposito: un saldo guardado puede
        quedar desincronizado de sus operaciones y entonces hay dos verdades.
        Con esto solo hay una, y es la lista de lo que se ejecuto.
        """
        posiciones: dict[str, dict] = {}
        caja = self.initial_cash
        for ticker, side, qty, price, comision in self._fills():
            qty = float(qty or 0.0)
            price = float(price or 0.0)
            comision = float(comision or 0.0)
            pos = posiciones.setdefault(ticker, {"qty": 0.0, "coste": 0.0})
            if side == "buy":
                caja -= qty * price + comision
                pos["coste"] += qty * price
                pos["qty"] += qty
            else:
                caja += qty * price - comision
                if pos["qty"] > 0:
                    pos["coste"] *= max(0.0, 1.0 - qty / pos["qty"])
                pos["qty"] -= qty
        return ({t: p for t, p in posiciones.items() if p["qty"] > 1e-12}, caja)

    def get_positions(self) -> list[Position]:
        posiciones, _ = self._book()
        if not posiciones:
            return []
        precios = self.get_latest_price(list(posiciones))
        out = []
        for ticker, p in posiciones.items():
            actual = float(precios.get(ticker) or 0.0)
            entrada = p["coste"] / p["qty"] if p["qty"] else 0.0
            valor = p["qty"] * actual
            out.append(Position(
                symbol=ticker, qty=p["qty"], avg_entry_price=entrada,
                market_value=valor,
                unrealized_pl=valor - p["coste"],
                unrealized_plpc=((valor / p["coste"] - 1.0) if p["coste"] else 0.0),
                current_price=actual,
            ))
        return out

    def get_position(self, symbol: str) -> Position | None:
        return next((p for p in self.get_positions() if p.symbol == symbol), None)

    def get_account(self) -> Account:
        _, caja = self._book()
        valor = sum(p.market_value for p in self.get_positions())
        return Account(
            account_id=self.mode_key, currency="EUR", cash=caja,
            equity=caja + valor, buying_power=caja, last_equity=caja + valor,
            daytrade_count=0, pattern_day_trader=False,
            trading_blocked=False, account_blocked=False, shorting_enabled=False,
        )

    # ------------------------------------------------------------------
    # Ordenes
    # ------------------------------------------------------------------
    def get_orders(self, status: str = "open") -> list[Order]:
        # Se ejecuta todo al momento, asi que nunca hay ordenes abiertas. Es
        # una simplificacion y esta declarada: en real, una orden limitada
        # puede quedarse en el libro y aqui eso no se reproduce.
        return []

    def get_order_by_client_id(self, client_order_id: str) -> Order | None:
        with connect(read_only=True) as conn:
            fila = conn.execute(
                "SELECT client_order_id, ticker, side, qty, notional, status, "
                "submitted_at FROM orders WHERE mode = ? AND client_order_id = ?",
                [self.mode_key, client_order_id],
            ).fetchone()
        if not fila:
            return None
        return Order(
            broker_order_id=fila[0], client_order_id=fila[0], symbol=fila[1],
            side=fila[2], order_type="market", tif="gtc", status=fila[5] or "filled",
            submitted_at=fila[6] or datetime.now(),
            qty=float(fila[3] or 0.0), filled_qty=float(fila[3] or 0.0),
        )

    def submit_order(self, req: OrderRequest) -> Order:
        """Simula la ejecucion al precio actual y la anota como si fuera real."""
        # Misma idempotencia que en real, y por el mismo motivo: si el proceso
        # muere despues de anotar y el ciclo reintenta, no puede contarse dos
        # veces. Que aqui no haya dinero de por medio es justo lo que haria
        # tentador saltarselo, y entonces el mes de pruebas no probaria esto.
        existente = self.get_order_by_client_id(req.client_order_id)
        if existente is not None:
            return existente

        precio = self._price(req.symbol)
        # El deslizamiento siempre en contra: comprar sale mas caro y vender
        # mas barato. A favor seria inventarse una ventaja.
        signo = 1.0 if req.side == "buy" else -1.0
        ejecucion = precio * (1.0 + signo * self.slippage_bps / 10_000.0)

        qty = req.qty
        if qty is None:
            if req.notional is None:
                raise BrokerRejectedError(f"{req.symbol}: orden sin tamano")
            qty = float(req.notional) / ejecucion
        qty = float(qty)

        importe = qty * ejecucion
        comision = importe * self.commission_bps / 10_000.0

        _, caja = self._book()
        if req.side == "buy" and importe + comision > caja + 1e-9:
            raise InsufficientFundsError(
                f"{req.symbol}: hacen falta {importe + comision:.2f} EUR y hay "
                f"{caja:.2f}."
            )
        if req.side == "sell":
            abierta = self.get_position(req.symbol)
            if abierta is None or abierta.qty + 1e-12 < qty:
                raise BrokerRejectedError(
                    f"{req.symbol}: se intenta vender {qty:.8f} y hay "
                    f"{(abierta.qty if abierta else 0.0):.8f}. En papel esto se "
                    "rechaza igual que en real: permitirlo seria simular un "
                    "corto que el mandato prohibe."
                )

        ahora = datetime.now()
        with connect() as conn:
            conn.execute(
                "INSERT INTO orders (client_order_id, broker_order_id, ticker, "
                "side, qty, notional, status, submitted_at, mode) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                [req.client_order_id, req.client_order_id, req.symbol, req.side,
                 qty, importe, "filled", ahora, self.mode_key],
            )
            conn.execute(
                "INSERT INTO fills (fill_id, client_order_id, broker_order_id, "
                "ticker, side, qty, price, filled_at, commission, slippage_bps, "
                "mode) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                [ulid(), req.client_order_id, req.client_order_id, req.symbol,
                 req.side, qty, ejecucion, ahora, comision, self.slippage_bps,
                 self.mode_key],
            )

        return Order(
            broker_order_id=req.client_order_id,
            client_order_id=req.client_order_id, symbol=req.symbol,
            side=req.side, order_type="market", tif="gtc", status="filled",
            submitted_at=ahora, qty=qty, filled_qty=qty,
            filled_avg_price=ejecucion,
        )

    def cancel_order(self, broker_order_id: str) -> None:
        return None

    def cancel_all_orders(self) -> int:
        return 0

    def close_position(self, symbol: str, qty: float | None = None) -> Order:
        abierta = self.get_position(symbol)
        if abierta is None or abierta.qty <= 0:
            raise BrokerRejectedError(f"{symbol}: no hay posicion que cerrar")
        cantidad = abierta.qty if qty is None else min(qty, abierta.qty)
        return self.submit_order(OrderRequest(
            symbol=symbol, side="sell", qty=cantidad,
            client_order_id=f"close-{ulid()}",
        ))

    def close_all_positions(self, cancel_orders: bool = True) -> list[Order]:
        return [self.close_position(p.symbol) for p in self.get_positions()]

    # ------------------------------------------------------------------
    def is_fractionable(self, symbol: str) -> bool:
        return True

    def minimum_order(self, symbol: str) -> float:
        minimo = getattr(self.prices, "minimum_order", None)
        return float(minimo(symbol)) if callable(minimo) else 0.0

    def supports(self, feature: str) -> bool:
        return feature in {"fractional", "limit", "stop", "market"}
