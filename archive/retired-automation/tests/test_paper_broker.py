"""Tests del modo papel.

El primero de este fichero cubre el peor fallo que ha tenido el programa:
`KrakenBroker` tenia un campo `mode` que no miraba nunca, asi que pedir modo
papel devolvia el adaptador real y las ordenes habrian salido con dinero
mientras el usuario creia estar probando.
"""

from __future__ import annotations

import pytest

from stocks_tracker.core import db
from stocks_tracker.trading.brokers.base import (
    BrokerMode,
    BrokerRejectedError,
    InsufficientFundsError,
    OrderRequest,
)
from stocks_tracker.trading.brokers.kraken import KrakenBroker
from stocks_tracker.trading.brokers.paper import PaperBroker


@pytest.fixture
def warehouse(tmp_path, monkeypatch):
    class Stub:
        warehouse_path = tmp_path / "test.duckdb"

    monkeypatch.setattr(db, "get_settings", lambda: Stub())
    db.migrate()
    return Stub.warehouse_path


class Precios:
    """Fuente de precios de mentira, con el mismo contrato que el adaptador."""

    def __init__(self, precios: dict):
        self.precios = precios
        self.consultas = 0

    def get_latest_price(self, symbols):
        self.consultas += 1
        return {s: self.precios[s] for s in symbols if s in self.precios}


_POR_DEFECTO = {"BTC/EUR": 100.0, "ETH/EUR": 50.0}


def broker(precios=None, caja: float = 25.0, slippage: float = 0.0,
           comision: float = 0.0) -> PaperBroker:
    # `precios or _POR_DEFECTO` estaria mal: un diccionario vacio es falso, y
    # el test de "sin precios" recibiria los de por defecto y pasaria sin
    # comprobar nada.
    return PaperBroker(
        prices=Precios(_POR_DEFECTO if precios is None else precios),
        mode_key="paper:kraken", initial_cash=caja,
        slippage_bps=slippage, commission_bps=comision,
    )


def comprar(b: PaperBroker, symbol="BTC/EUR", notional=10.0, cid=None):
    return b.submit_order(OrderRequest(
        symbol=symbol, side="buy", notional=notional,
        client_order_id=cid or f"st-{symbol}-{notional}",
    ))


# ---------------------------------------------------------------------------
# El fallo que motivo todo esto
# ---------------------------------------------------------------------------
def test_the_real_adapter_refuses_to_trade_outside_live_mode():
    """`KrakenBroker` tenia `mode` y no lo miraba: en modo papel devolvia este
    mismo adaptador y las ordenes salian con dinero real. Kraken spot no tiene
    entorno de pruebas, asi que no hay ninguna configuracion en la que mandar
    desde aqui sea inocuo.

    La comprobacion va en el metodo que gasta, no en quien lo construye: es el
    unico sitio por el que tiene que pasar cualquier camino.
    """
    for modo in (BrokerMode.PAPER, BrokerMode.SIMULATED):
        b = KrakenBroker(mode=modo)
        with pytest.raises(BrokerRejectedError, match="dinero real"):
            b.submit_order(OrderRequest(symbol="BTC/EUR", side="buy", qty=0.1,
                                        client_order_id="st-X"))


def test_the_registry_hands_out_a_paper_broker_for_paper_mode(monkeypatch):
    """Y no el real con otro nombre."""
    from stocks_tracker.trading.brokers import registry

    monkeypatch.setattr(registry, "require_tradeable", lambda *a, **k: None,
                        raising=False)
    monkeypatch.setattr("stocks_tracker.trading.venues.require_tradeable",
                        lambda *a, **k: None)
    b = registry.build_broker("kraken", mode="paper")
    assert isinstance(b, PaperBroker)


# ---------------------------------------------------------------------------
# Precios reales
# ---------------------------------------------------------------------------
def test_it_executes_at_the_real_price(warehouse):
    b = broker({"BTC/EUR": 200.0})
    orden = comprar(b, notional=20.0)
    assert orden.filled_avg_price == 200.0
    assert orden.filled_qty == pytest.approx(0.1)


def test_without_a_price_it_refuses_instead_of_inventing_one(warehouse):
    """Inventar un precio produciria una contabilidad que no corresponde a
    ningun mercado, y el mes de pruebas no diria nada."""
    b = broker({})
    with pytest.raises(BrokerRejectedError, match="sin precio"):
        comprar(b)


def test_slippage_always_goes_against_you(warehouse):
    """A favor seria inventarse una ventaja que en real no existe."""
    b = broker({"BTC/EUR": 100.0}, slippage=100.0)   # 1 %
    compra = comprar(b, notional=10.0)
    assert compra.filled_avg_price > 100.0

    venta = b.submit_order(OrderRequest(
        symbol="BTC/EUR", side="sell", qty=compra.filled_qty,
        client_order_id="st-venta"))
    assert venta.filled_avg_price < 100.0


# ---------------------------------------------------------------------------
# La contabilidad
# ---------------------------------------------------------------------------
def test_positions_survive_between_runs(warehouse):
    """La tarea programada arranca un proceso nuevo cada seis horas. Si el
    estado viviera en memoria, el bot creeria empezar de cero cada vez y
    compraria lo mismo indefinidamente."""
    comprar(broker(), notional=10.0)
    otro_proceso = broker()
    assert [p.symbol for p in otro_proceso.get_positions()] == ["BTC/EUR"]


def test_cash_goes_down_when_buying(warehouse):
    b = broker(caja=25.0)
    comprar(b, notional=10.0)
    assert b.get_account().cash == pytest.approx(15.0)


def test_the_commission_is_charged(warehouse):
    """0,26 % es lo que cobra Kraken. Simular sin comisiones daria un resultado
    mejor que el real por construccion."""
    b = broker(caja=25.0, comision=26.0)
    comprar(b, notional=10.0)
    assert b.get_account().cash < 15.0


def test_selling_returns_the_money(warehouse):
    b = broker(caja=25.0)
    compra = comprar(b, notional=10.0)
    b.submit_order(OrderRequest(symbol="BTC/EUR", side="sell",
                                qty=compra.filled_qty,
                                client_order_id="st-venta"))
    assert b.get_account().cash == pytest.approx(25.0)
    assert b.get_positions() == []


def test_you_cannot_spend_money_you_do_not_have(warehouse):
    b = broker(caja=25.0)
    with pytest.raises(InsufficientFundsError):
        comprar(b, notional=100.0)


def test_you_cannot_sell_what_you_do_not_hold(warehouse):
    """Permitirlo simularia un corto, que el mandato prohibe. Y el mes de
    pruebas daria por bueno un comportamiento imposible en real."""
    b = broker()
    with pytest.raises(BrokerRejectedError, match="vender"):
        b.submit_order(OrderRequest(symbol="BTC/EUR", side="sell", qty=1.0,
                                    client_order_id="st-corto"))


def test_the_average_entry_price_is_right_after_two_buys(warehouse):
    b = broker({"BTC/EUR": 100.0}, caja=100.0)
    comprar(b, notional=10.0, cid="st-1")
    b.prices.precios["BTC/EUR"] = 200.0
    comprar(b, notional=20.0, cid="st-2")
    pos = b.get_position("BTC/EUR")
    # 0,1 a 100 y 0,1 a 200 -> media 150.
    assert pos.qty == pytest.approx(0.2)
    assert pos.avg_entry_price == pytest.approx(150.0)


# ---------------------------------------------------------------------------
# Idempotencia, igual que en real
# ---------------------------------------------------------------------------
def test_resending_the_same_order_does_not_duplicate_it(warehouse):
    """Que aqui no haya dinero es justo lo que haria tentador saltarse esto, y
    entonces el mes de pruebas no probaria la idempotencia: el unico sitio
    donde se descubriria el fallo seria en real."""
    b = broker(caja=100.0)
    comprar(b, notional=10.0, cid="st-MISMO")
    comprar(b, notional=10.0, cid="st-MISMO")
    assert b.get_position("BTC/EUR").qty == pytest.approx(0.1)


def test_the_paper_books_are_kept_apart_from_the_real_ones(warehouse):
    """Misma tabla, distinto modo. Es lo que permite comparar las dos
    contabilidades con la misma consulta sin mezclarlas."""
    comprar(broker(), notional=10.0)
    with db.connect(read_only=True) as conn:
        modos = conn.execute("SELECT DISTINCT mode FROM fills").fetchall()
    assert modos == [("paper:kraken",)]


def test_a_partial_sale_keeps_the_average_entry_price(warehouse):
    """Al vender la mitad, el coste de lo que queda tiene que bajar a la mitad
    tambien. Si no, el precio medio de entrada se duplica y con el el stop y el
    P&L: el bot creeria estar perdiendo cuando no lo esta, y venderia.
    """
    b = broker({"BTC/EUR": 100.0}, caja=100.0)
    comprar(b, notional=10.0, cid="st-1")      # 0,1 a 100
    b.prices.precios["BTC/EUR"] = 200.0
    comprar(b, notional=20.0, cid="st-2")      # 0,1 a 200 -> media 150

    b.submit_order(OrderRequest(symbol="BTC/EUR", side="sell", qty=0.1,
                                client_order_id="st-mitad"))
    pos = b.get_position("BTC/EUR")
    assert pos.qty == pytest.approx(0.1)
    assert pos.avg_entry_price == pytest.approx(150.0), (
        "vender la mitad ha cambiado el precio medio de entrada"
    )
