"""Tests del broker simulado.

Lo que se prueba aqui no es "que el codigo corra" sino que el modelado no
regale dinero. Un simulador optimista produce un backtest que se cree a si
mismo, y esa es la forma mas cara de equivocarse en este proyecto: acaba en
dinero real.
"""

from __future__ import annotations

import pandas as pd
import pytest

from stocks_tracker.trading.brokers.base import (
    BrokerRejectedError,
    InsufficientFundsError,
    OrderRequest,
)
from stocks_tracker.trading.brokers.simulated import SimulatedBroker, snapshot


def bars(rows: list[tuple]) -> pd.DataFrame:
    """rows: (fecha, ticker, open, high, low, close)"""
    return pd.DataFrame(
        rows, columns=["date", "ticker", "open", "high", "low", "close"]
    ).assign(volume=1_000_000)


@pytest.fixture
def broker() -> SimulatedBroker:
    return SimulatedBroker(
        prices=bars([
            ("2024-01-02", "AAA", 100, 101, 99, 100),
            ("2024-01-03", "AAA", 102, 103, 101, 102),
            ("2024-01-04", "AAA", 104, 105, 103, 104),
            ("2024-01-05", "AAA", 106, 107, 105, 106),
            ("2024-01-02", "BBB", 50, 51, 49, 50),
            ("2024-01-03", "BBB", 50, 51, 49, 50),
            ("2024-01-04", "BBB", 50, 51, 49, 50),
            ("2024-01-05", "BBB", 50, 51, 49, 50),
        ]),
        initial_cash=1000.0,
        slippage_bps=0.0,
    )


# ---------------------------------------------------------------------------
# Anti look-ahead
# ---------------------------------------------------------------------------
def test_orders_fill_at_the_next_session_open(broker):
    """Ejecutar al cierre que se acaba de leer es mirar el futuro: ese precio
    no existia cuando se tomo la decision."""
    broker.submit_order(OrderRequest(symbol="AAA", side="buy", notional=100.0,
                                     client_order_id="c1"))
    assert broker.get_positions() == [], "se ha ejecutado el mismo dia"

    broker.advance()  # 2024-01-03, abre a 102
    position = broker.get_position("AAA")
    assert position is not None
    assert position.avg_entry_price == pytest.approx(102.0)


def test_an_order_with_no_bar_stays_pending(broker):
    """Sin cotizacion ese dia no se inventa un precio: produciria operaciones
    que nunca ocurrieron."""
    thin = SimulatedBroker(
        prices=bars([
            ("2024-01-02", "AAA", 100, 101, 99, 100),
            ("2024-01-03", "BBB", 50, 51, 49, 50),
            ("2024-01-04", "AAA", 110, 111, 109, 110),
        ]),
        initial_cash=1000.0, slippage_bps=0.0,
    )
    thin.submit_order(OrderRequest(symbol="AAA", side="buy", notional=100.0,
                                   client_order_id="c1"))
    thin.advance()  # 03-ene: AAA no cotiza
    assert thin.get_position("AAA") is None
    thin.advance()  # 04-ene: ya si
    assert thin.get_position("AAA").avg_entry_price == pytest.approx(110.0)


# ---------------------------------------------------------------------------
# Stops
# ---------------------------------------------------------------------------
def test_a_gap_below_the_stop_fills_at_the_real_open():
    """Suponer que un stop se cumple siempre a su precio regala dinero que en
    el mercado no existe. Si abre con un salto en contra, se vende ahi."""
    gap = SimulatedBroker(
        prices=bars([
            ("2024-01-02", "AAA", 100, 101, 99, 100),
            ("2024-01-03", "AAA", 90, 92, 88, 91),   # hueco a la baja
        ]),
        initial_cash=1000.0, slippage_bps=0.0,
    )
    from stocks_tracker.trading.brokers.simulated import _Holding

    gap._holdings["AAA"] = _Holding(qty=1.0, avg_entry_price=100.0)

    gap.submit_order(
        OrderRequest(symbol="AAA", side="sell", qty=1.0, client_order_id="stop1"),
        stop_price=98.0,
    )
    gap.advance()
    fill = gap.fills[-1]
    assert fill.price == pytest.approx(90.0), (
        "se ha ejecutado al precio del stop en lugar de a la apertura real"
    )


def test_a_stop_that_is_not_touched_keeps_waiting():
    from stocks_tracker.trading.brokers.simulated import _Holding

    calm = SimulatedBroker(
        prices=bars([
            ("2024-01-02", "AAA", 100, 101, 99, 100),
            ("2024-01-03", "AAA", 100, 102, 99.5, 101),
        ]),
        initial_cash=1000.0, slippage_bps=0.0,
    )
    calm._holdings["AAA"] = _Holding(qty=1.0, avg_entry_price=100.0)
    calm.submit_order(
        OrderRequest(symbol="AAA", side="sell", qty=1.0, client_order_id="s"),
        stop_price=98.0,
    )
    calm.advance()
    assert calm.fills == []
    assert len(calm.get_orders("open")) == 1


# ---------------------------------------------------------------------------
# Costes
# ---------------------------------------------------------------------------
def test_slippage_moves_the_price_against_us_in_both_directions():
    costly = SimulatedBroker(
        prices=bars([
            ("2024-01-02", "AAA", 100, 101, 99, 100),
            ("2024-01-03", "AAA", 100, 101, 99, 100),
            ("2024-01-04", "AAA", 100, 101, 99, 100),
        ]),
        initial_cash=1000.0, slippage_bps=100.0,  # 1 %
    )
    costly.submit_order(OrderRequest(symbol="AAA", side="buy", notional=100.0,
                                     client_order_id="b"))
    costly.advance()
    assert costly.fills[-1].price == pytest.approx(101.0), "comprar deberia costar mas"

    qty = costly.get_position("AAA").qty
    costly.submit_order(OrderRequest(symbol="AAA", side="sell", qty=qty,
                                     client_order_id="s"))
    costly.advance()
    assert costly.fills[-1].price == pytest.approx(99.0), "vender deberia cobrar menos"


def test_commission_is_charged_on_top(broker):
    charged = SimulatedBroker(
        prices=bars([
            ("2024-01-02", "AAA", 100, 101, 99, 100),
            ("2024-01-03", "AAA", 100, 101, 99, 100),
        ]),
        initial_cash=1000.0, slippage_bps=0.0, commission_bps=50.0,
    )
    charged.submit_order(OrderRequest(symbol="AAA", side="buy", notional=100.0,
                                      client_order_id="b"))
    charged.advance()
    assert charged.cash == pytest.approx(1000.0 - 100.0 - 0.5)


# ---------------------------------------------------------------------------
# Reglas duras del broker
# ---------------------------------------------------------------------------
def test_selling_more_than_held_is_rejected(broker):
    """Vender lo que no se tiene es ponerse corto, y el mandato lo prohibe.
    Que el simulador lo permitiera dejaria pasar en el backtest algo que el
    broker real rechaza."""
    with pytest.raises(BrokerRejectedError):
        broker.submit_order(OrderRequest(symbol="AAA", side="sell", qty=1.0,
                                         client_order_id="s"))


def test_buying_without_cash_is_rejected(broker):
    broker.submit_order(OrderRequest(symbol="AAA", side="buy", notional=5000.0,
                                     client_order_id="b"))
    with pytest.raises(InsufficientFundsError):
        broker.advance()


def test_resubmitting_the_same_order_does_not_duplicate_it(broker):
    """Idempotencia: es lo que hace seguro reintentar tras una caida."""
    first = broker.submit_order(OrderRequest(symbol="AAA", side="buy",
                                             notional=100.0, client_order_id="c1"))
    second = broker.submit_order(OrderRequest(symbol="AAA", side="buy",
                                              notional=100.0, client_order_id="c1"))
    assert first.broker_order_id == second.broker_order_id
    broker.advance()
    assert len(broker.fills) == 1


def test_qty_and_notional_together_are_refused():
    with pytest.raises(ValueError, match="excluyentes"):
        OrderRequest(symbol="AAA", side="buy", qty=1.0, notional=100.0,
                     client_order_id="x")


# ---------------------------------------------------------------------------
# Regla PDT
# ---------------------------------------------------------------------------
def test_day_trades_are_counted():
    """Con menos de 25.000 $ FINRA permite 3 en 5 dias habiles. Con 50 EUR es
    una restriccion dura, y hay que poder probarla en el backtest en lugar de
    descubrirla operando."""
    from stocks_tracker.trading.brokers.simulated import _Holding

    pdt = SimulatedBroker(
        prices=bars([
            ("2024-01-02", "AAA", 100, 101, 99, 100),
            ("2024-01-03", "AAA", 100, 101, 99, 100),
        ]),
        initial_cash=1000.0, slippage_bps=0.0,
    )
    pdt.submit_order(OrderRequest(symbol="AAA", side="buy", notional=100.0,
                                  client_order_id="b"))
    pdt.advance()
    assert pdt.daytrade_count() == 0

    qty = pdt.get_position("AAA").qty
    pdt._pending.clear()
    pdt.submit_order(OrderRequest(symbol="AAA", side="sell", qty=qty,
                                  client_order_id="s"))
    # La venta se ejecuta en la MISMA sesion en que se compro: es un day trade.
    pdt._process_pending()
    assert pdt.daytrade_count() == 1
    assert isinstance(pdt._holdings.get("AAA", _Holding()), _Holding)


# ---------------------------------------------------------------------------
# Contabilidad
# ---------------------------------------------------------------------------
def test_equity_tracks_cash_plus_market_value(broker):
    broker.submit_order(OrderRequest(symbol="AAA", side="buy", notional=200.0,
                                     client_order_id="b"))
    broker.advance()   # compra a 102
    account = broker.get_account()
    assert account.equity == pytest.approx(account.cash + broker.long_market_value())

    broker.advance()   # AAA cierra a 104: la posicion vale mas
    assert broker.get_account().equity > account.equity


def test_the_snapshot_reports_drawdown_from_the_peak(broker):
    falling = SimulatedBroker(
        prices=bars([
            ("2024-01-02", "AAA", 100, 101, 99, 100),
            ("2024-01-03", "AAA", 100, 101, 99, 100),
            ("2024-01-04", "AAA", 60, 61, 59, 60),
        ]),
        initial_cash=1000.0, slippage_bps=0.0,
    )
    falling.submit_order(OrderRequest(symbol="AAA", side="buy", notional=500.0,
                                      client_order_id="b"))
    falling.advance()
    falling.advance()
    state = snapshot(falling)
    assert state.drawdown_pct < 0
    assert state.n_positions == 1
    assert state.peak_equity >= state.equity


def test_the_calendar_comes_from_the_data(broker):
    """No hay calendario de festivos que mantener: las fechas presentes en los
    precios SON las sesiones."""
    assert broker.sessions[0].isoformat() == "2024-01-02"
    assert len(broker.sessions) == 4
    broker.seek(broker.sessions[2])
    assert broker.current_date == broker.sessions[2]
