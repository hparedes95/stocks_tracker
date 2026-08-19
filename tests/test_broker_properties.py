"""Invariantes del simulador, comprobadas con secuencias generadas.

Por que aqui y no en otro sitio. Un test escrito a mano prueba la secuencia que
se le ocurrio a quien lo escribio, y los fallos de contabilidad de un broker no
viven ahi: viven en "compra, compra otra vez, vende la mitad, avanza sesion,
cancela, vende el resto en la misma sesion". Esa clase de secuencia no se
escribe a mano porque no se piensa. Hypothesis las genera y, cuando encuentra
una que rompe algo, la reduce al minimo caso que sigue fallando.

Que el sitio es el correcto no es una corazonada: el PR #2 arreglo dos fallos
justo aqui —ventas pendientes que no se reservaban, y ventas parciales que
contaban un day trade por cada fill—, y los dos eran de este tipo.

Una invariante NO es una comprobacion de que el resultado sea bueno. Es algo
que tiene que cumplirse SIEMPRE, salga la simulacion como salga:

  - el efectivo nunca es NaN ni infinito,
  - ninguna posicion tiene cantidad negativa,
  - la equity es siempre efectivo mas valor de mercado,
  - no se puede vender mas de lo que se tiene,
  - lo que sale de la caja al comprar es exactamente lo que costo,
  - el dinero no aparece de la nada.

La ultima es la importante, y es la que ningun test a mano cubre bien: en un
mundo sin comisiones ni deslizamiento y con precios constantes, comprar y
vender tiene que dejar la caja como estaba. Un signo cambiado en cualquier
rama de `_fill` rompe eso y no rompe nada mas.
"""

from __future__ import annotations

import math

import pandas as pd
import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, initialize, invariant, rule

from stocks_tracker.trading.brokers.base import (
    BrokerRejectedError,
    InsufficientFundsError,
    OrderRequest,
)
from stocks_tracker.trading.brokers.simulated import SimulatedBroker

TICKERS = ("AAA", "BBB", "CCC")
N_SESIONES = 12


def _precios(valores: dict[str, list[float]]) -> pd.DataFrame:
    """Barras diarias a partir de una lista de cierres por ticker.

    Todas las barras son planas (open = high = low = close). Con barras planas
    la ejecucion es predecible y las invariantes de contabilidad se pueden
    comprobar al centimo; el comportamiento con huecos y stops se prueba aparte
    en test_simulated_broker.py, que es donde toca.
    """
    fechas = pd.bdate_range("2024-01-02", periods=N_SESIONES)
    filas = []
    for ticker, cierres in valores.items():
        for fecha, cierre in zip(fechas, cierres, strict=True):
            filas.append({"date": fecha, "ticker": ticker, "open": cierre,
                          "high": cierre, "low": cierre, "close": cierre,
                          "volume": 1_000_000})
    return pd.DataFrame(filas)


precios_validos = st.lists(
    st.floats(min_value=1.0, max_value=500.0, allow_nan=False, allow_infinity=False),
    min_size=N_SESIONES, max_size=N_SESIONES,
)


# ---------------------------------------------------------------------------
# Propiedades de una sola operacion
# ---------------------------------------------------------------------------
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow], deadline=None)
@given(
    cierres=precios_validos,
    efectivo=st.floats(min_value=10.0, max_value=100_000.0, allow_nan=False),
    cantidad=st.floats(min_value=0.001, max_value=1000.0, allow_nan=False),
    deslizamiento=st.floats(min_value=0.0, max_value=200.0, allow_nan=False),
)
def test_a_buy_takes_exactly_what_it_costs_out_of_the_cash(
    cierres, efectivo, cantidad, deslizamiento
):
    """Lo que sale de la caja es cantidad x precio ejecutado, ni un centimo mas.

    El precio ejecutado incluye el deslizamiento, que en una compra va EN
    CONTRA. Si el signo estuviera al reves, comprar saldria mas barato que el
    precio de mercado y toda simulacion daria de mas.
    """
    broker = SimulatedBroker(_precios({"AAA": cierres}), initial_cash=efectivo,
                             slippage_bps=deslizamiento)
    caja_antes = broker.cash
    try:
        broker.submit_order(OrderRequest(symbol="AAA", side="buy", qty=cantidad,
                                         client_order_id="c1"))
        broker.advance()
    except InsufficientFundsError:
        assume(False)  # sin dinero para esta compra: no dice nada del invariante

    rellenos = [f for f in broker.fills if f.side == "buy"]
    assume(rellenos)
    relleno = rellenos[0]
    assert relleno.price >= relleno.extra["raw_price"] - 1e-12, \
        "el deslizamiento de una compra tiene que encarecer, no abaratar"
    assert broker.cash == pytest.approx(
        caja_antes - relleno.qty * relleno.price - relleno.commission, rel=1e-9, abs=1e-9
    )


@settings(max_examples=200, deadline=None)
@given(
    cierres=precios_validos,
    cantidad=st.floats(min_value=0.001, max_value=100.0, allow_nan=False),
)
def test_buying_and_selling_the_same_thing_leaves_the_cash_untouched(cierres, cantidad):
    """Sin comisiones, sin deslizamiento y a precio constante, ida y vuelta es
    neutra. Es la invariante que caza un signo cambiado en cualquier rama de
    `_fill`, porque cualquier signo mal deja un residuo."""
    plano = [cierres[0]] * N_SESIONES
    broker = SimulatedBroker(_precios({"AAA": plano}), initial_cash=1_000_000.0,
                             slippage_bps=0.0, commission_bps=0.0)
    caja_inicial = broker.cash

    broker.submit_order(OrderRequest(symbol="AAA", side="buy", qty=cantidad,
                                     client_order_id="c1"))
    broker.advance()
    broker.submit_order(OrderRequest(symbol="AAA", side="sell", qty=cantidad,
                                     client_order_id="v1"))
    broker.advance()

    assert broker.cash == pytest.approx(caja_inicial, rel=1e-9)
    assert broker.get_positions() == []


@settings(max_examples=150, deadline=None)
@given(
    cierres=precios_validos,
    tiene=st.floats(min_value=1.0, max_value=100.0, allow_nan=False),
    pide=st.floats(min_value=0.001, max_value=500.0, allow_nan=False),
)
def test_you_can_never_sell_more_than_you_hold(cierres, tiene, pide):
    """Vender lo que no se tiene seria abrir un corto sin decirlo, y el mandato
    no permite cortos. Tiene que rechazarse al enviar, no al ejecutar: en
    mercado real el rechazo llega antes."""
    plano = [cierres[0]] * N_SESIONES
    broker = SimulatedBroker(_precios({"AAA": plano}), initial_cash=1_000_000.0,
                             slippage_bps=0.0)
    broker.submit_order(OrderRequest(symbol="AAA", side="buy", qty=tiene,
                                     client_order_id="c1"))
    broker.advance()

    if pide <= tiene + 1e-9:
        broker.submit_order(OrderRequest(symbol="AAA", side="sell", qty=pide,
                                         client_order_id="v1"))
    else:
        with pytest.raises(BrokerRejectedError):
            broker.submit_order(OrderRequest(symbol="AAA", side="sell", qty=pide,
                                             client_order_id="v1"))


@settings(max_examples=150, deadline=None)
@given(cierres=precios_validos,
       efectivo=st.floats(min_value=1.0, max_value=1000.0, allow_nan=False))
def test_you_can_never_spend_more_cash_than_you_have(cierres, efectivo):
    """Comprar mas de lo que se puede pagar no puede dejar la caja en negativo:
    en un broker real la orden se rechaza, y una caja negativa aqui seria
    apalancamiento invisible."""
    plano = [cierres[0]] * N_SESIONES
    broker = SimulatedBroker(_precios({"AAA": plano}), initial_cash=efectivo,
                             slippage_bps=0.0)
    enorme = efectivo / plano[0] * 10.0
    broker.submit_order(OrderRequest(symbol="AAA", side="buy", qty=enorme,
                                     client_order_id="c1"))
    try:
        broker.advance()
    except InsufficientFundsError:
        pass
    assert broker.cash >= -1e-9
    assert broker.cash == pytest.approx(efectivo)


@settings(max_examples=150, deadline=None)
@given(
    precio_a=st.floats(min_value=1.0, max_value=300.0, allow_nan=False),
    precio_b=st.floats(min_value=1.0, max_value=300.0, allow_nan=False),
    cantidad_a=st.floats(min_value=0.1, max_value=50.0, allow_nan=False),
    cantidad_b=st.floats(min_value=0.1, max_value=50.0, allow_nan=False),
)
def test_the_average_cost_is_weighted_and_not_the_last_price_paid(
    precio_a, precio_b, cantidad_a, cantidad_b
):
    """Dos compras a distinto precio: el coste medio es la media PONDERADA.

    Con `avg_entry_price = price` —el ultimo precio pagado— la suite entera del
    repositorio sigue en verde, y sin embargo de ese numero salen el resultado
    no realizado que se ve en pantalla, el tamano de la siguiente compra y la
    distancia al stop.
    """
    assume(abs(precio_a - precio_b) > 0.5)
    cierres = [precio_a, precio_a] + [precio_b] * (N_SESIONES - 2)
    broker = SimulatedBroker(_precios({"AAA": cierres}), initial_cash=1_000_000.0,
                             slippage_bps=0.0, commission_bps=0.0)
    broker.submit_order(OrderRequest(symbol="AAA", side="buy", qty=cantidad_a,
                                     client_order_id="c1"))
    broker.advance()          # ejecuta a precio_a
    broker.submit_order(OrderRequest(symbol="AAA", side="buy", qty=cantidad_b,
                                     client_order_id="c2"))
    broker.advance()          # ejecuta a precio_b

    esperado = ((cantidad_a * precio_a + cantidad_b * precio_b)
                / (cantidad_a + cantidad_b))
    assert broker.get_position("AAA").avg_entry_price == pytest.approx(esperado, rel=1e-9)


@settings(max_examples=100, deadline=None)
@given(
    precio=st.floats(min_value=5.0, max_value=300.0, allow_nan=False),
    cantidad=st.floats(min_value=1.0, max_value=50.0, allow_nan=False),
    fraccion=st.floats(min_value=0.05, max_value=0.95, allow_nan=False),
)
def test_selling_part_of_a_position_does_not_change_its_average_cost(
    precio, cantidad, fraccion
):
    """Vender la mitad no cambia lo que costo la otra mitad. Si cambiara, el
    resultado no realizado del resto se reescribiria solo al vender."""
    cierres = [precio] * 2 + [precio * 2] * (N_SESIONES - 2)
    broker = SimulatedBroker(_precios({"AAA": cierres}), initial_cash=1_000_000.0,
                             slippage_bps=0.0, commission_bps=0.0)
    broker.submit_order(OrderRequest(symbol="AAA", side="buy", qty=cantidad,
                                     client_order_id="c1"))
    broker.advance()
    medio_antes = broker.get_position("AAA").avg_entry_price

    broker.submit_order(OrderRequest(symbol="AAA", side="sell",
                                     qty=cantidad * fraccion, client_order_id="v1"))
    broker.advance()
    assert broker.get_position("AAA").avg_entry_price == pytest.approx(medio_antes)


# ---------------------------------------------------------------------------
# Secuencias completas
# ---------------------------------------------------------------------------
class CarteraSimulada(RuleBasedStateMachine):
    """Genera secuencias de operaciones y comprueba las invariantes tras cada una.

    Hypothesis elige que regla ejecutar y con que argumentos, encadena decenas
    de pasos y, al encontrar un fallo, reduce la secuencia al minimo que sigue
    fallando. Ahi esta la diferencia con un test a mano: no hay que adivinar la
    secuencia mala, solo hay que saber decir que tiene que cumplirse siempre.
    """

    def __init__(self) -> None:
        super().__init__()
        self.broker = None

    @initialize(
        precios=st.lists(
            st.floats(min_value=5.0, max_value=200.0, allow_nan=False),
            min_size=N_SESIONES, max_size=N_SESIONES,
        ),
        efectivo=st.floats(min_value=100.0, max_value=50_000.0, allow_nan=False),
        deslizamiento=st.floats(min_value=0.0, max_value=100.0, allow_nan=False),
        comision=st.floats(min_value=0.0, max_value=100.0, allow_nan=False),
    )
    def arrancar(self, precios, efectivo, deslizamiento, comision):
        self.broker = SimulatedBroker(
            _precios({t: precios for t in TICKERS}),
            initial_cash=efectivo, slippage_bps=deslizamiento,
            commission_bps=comision,
        )
        self.efectivo_inicial = efectivo

    @rule(ticker=st.sampled_from(TICKERS),
          cantidad=st.floats(min_value=0.01, max_value=50.0, allow_nan=False))
    def comprar(self, ticker, cantidad):
        if self.broker is None:
            return
        try:
            self.broker.submit_order(OrderRequest(
                symbol=ticker, side="buy", qty=cantidad,
                client_order_id=f"c-{ticker}-{len(self.broker._orders)}"))
        except BrokerRejectedError:
            pass

    @rule(ticker=st.sampled_from(TICKERS), fraccion=st.floats(min_value=0.05, max_value=1.0))
    def vender(self, ticker, fraccion):
        if self.broker is None:
            return
        posicion = self.broker.get_position(ticker)
        if posicion is None:
            return
        # Se descuenta lo ya reservado por ventas pendientes: pedir mas seria
        # provocar un rechazo esperado, no probar nada.
        reservado = sum(
            (p.order.qty or 0.0) for p in self.broker._pending
            if p.order.symbol == ticker and p.order.side == "sell"
        )
        disponible = posicion.qty - reservado
        if disponible <= 1e-6:
            return
        try:
            self.broker.submit_order(OrderRequest(
                symbol=ticker, side="sell", qty=disponible * fraccion,
                client_order_id=f"v-{ticker}-{len(self.broker._orders)}"))
        except BrokerRejectedError:
            pass

    @rule(ticker=st.sampled_from(TICKERS))
    def vender_todo_lo_que_creo_tener(self, ticker):
        """Pide vender la posicion ENTERA sin descontar lo ya reservado.

        Es la regla maleducada, y es la que importa. `vender` resta antes las
        ventas pendientes, asi que nunca provoca el caso que hundio al broker
        antes del PR #2: dos ordenes del 60 % de la misma posicion aceptadas
        por separado que entre las dos venden el 120 %. Si el broker esta bien,
        la segunda se rechaza; si no, la invariante lo caza.
        """
        if self.broker is None:
            return
        posicion = self.broker.get_position(ticker)
        if posicion is None:
            return
        try:
            self.broker.submit_order(OrderRequest(
                symbol=ticker, side="sell", qty=posicion.qty,
                client_order_id=f"todo-{ticker}-{len(self.broker._orders)}"))
        except BrokerRejectedError:
            pass

    @rule()
    def avanzar_sesion(self):
        if self.broker is None or not self.broker.has_next_session:
            return
        try:
            self.broker.advance()
        except InsufficientFundsError:
            pass

    @rule()
    def cancelar_todo(self):
        if self.broker is not None:
            self.broker.cancel_all_orders()

    # -- Invariantes --------------------------------------------------------
    @invariant()
    def el_efectivo_es_un_numero(self):
        if self.broker is None:
            return
        assert math.isfinite(self.broker.cash), "el efectivo se ha vuelto NaN o infinito"

    @invariant()
    def el_efectivo_nunca_es_negativo(self):
        if self.broker is None:
            return
        assert self.broker.cash >= -1e-6, f"caja en negativo: {self.broker.cash}"

    @invariant()
    def ninguna_posicion_es_negativa(self):
        if self.broker is None:
            return
        for posicion in self.broker.get_positions():
            assert posicion.qty > 0, f"{posicion.symbol} con cantidad {posicion.qty}"

    @invariant()
    def la_equity_es_caja_mas_mercado(self):
        """La identidad contable. Si deja de cumplirse, cualquier limite de
        riesgo expresado en porcentaje de la equity esta midiendo otra cosa."""
        if self.broker is None:
            return
        cuenta = self.broker.get_account()
        assert math.isfinite(cuenta.equity)
        assert cuenta.equity == pytest.approx(
            self.broker.cash + self.broker.long_market_value(), rel=1e-9, abs=1e-6
        )

    @invariant()
    def las_ventas_pendientes_caben_en_lo_que_hay(self):
        """Lo que arreglo el PR #2: dos ventas del 60 % de la misma posicion se
        aceptaban por separado y entre las dos vendian el 120 %."""
        if self.broker is None:
            return
        for ticker in TICKERS:
            tenido = self.broker._holdings.get(ticker)
            reservado = sum(
                (p.order.qty or 0.0) for p in self.broker._pending
                if p.order.symbol == ticker and p.order.side == "sell"
            )
            disponible = tenido.qty if tenido else 0.0
            assert reservado <= disponible + 1e-6, (
                f"{ticker}: {reservado} reservadas para vender y solo {disponible}"
            )

    @invariant()
    def el_precio_medio_de_entrada_cuadra_con_los_fills(self):
        """El coste medio se reconstruye desde los fills, no se cree al broker.

        Ninguna otra prueba del repositorio cubria esto: se puede poner
        `avg_entry_price = price` —el ultimo precio pagado en vez de la media
        ponderada— y la suite entera pasa en verde. Y de ese numero salen el
        resultado no realizado que aparece en pantalla, el tamano de la
        siguiente compra y la distancia al stop. Estaria mal en los tres sitios
        a la vez y en ninguno daria error.

        Metodo del coste medio ponderado: una compra anade coste, una venta
        reduce cantidad y coste en la misma proporcion, de modo que el precio
        medio NO cambia al vender. Vender parte de una posicion no cambia lo
        que costo el resto.
        """
        if self.broker is None:
            return
        for ticker in TICKERS:
            tenido = self.broker._holdings.get(ticker)
            if tenido is None or tenido.qty <= 1e-9:
                continue
            cantidad, coste = 0.0, 0.0
            for f in self.broker.fills:
                if f.ticker != ticker:
                    continue
                if f.side == "buy":
                    coste += f.qty * f.price
                    cantidad += f.qty
                else:
                    medio = coste / cantidad if cantidad > 1e-12 else 0.0
                    coste -= f.qty * medio
                    cantidad -= f.qty
            if cantidad <= 1e-9:
                continue
            assert tenido.avg_entry_price == pytest.approx(
                coste / cantidad, rel=1e-6, abs=1e-9
            ), f"{ticker}: coste medio {tenido.avg_entry_price}, esperado {coste / cantidad}"

    @invariant()
    def la_caja_es_exactamente_lo_que_dicen_los_fills(self):
        """La identidad completa de la caja:

            efectivo = inicial + ventas - compras - comisiones

        Es la invariante mas fuerte del fichero. Cualquier signo cambiado en
        cualquier rama de `_fill` la rompe: la comision que suma en vez de
        restar, la venta que descuenta en vez de ingresar, el bruto calculado
        con la cantidad equivocada. Comprobar solo "la caja no es negativa" deja
        pasar todos esos: el dinero cuadra por casualidad o se pierde despacio,
        y ninguna de las dos cosas se nota.
        """
        if self.broker is None:
            return
        entra = sum(f.qty * f.price for f in self.broker.fills if f.side == "sell")
        sale = sum(f.qty * f.price for f in self.broker.fills if f.side == "buy")
        comisiones = sum(f.commission for f in self.broker.fills)
        assert self.broker.cash == pytest.approx(
            self.efectivo_inicial + entra - sale - comisiones, rel=1e-9, abs=1e-6
        )

    @invariant()
    def las_comisiones_solo_pueden_restar(self):
        """Una comision es un coste. Si en alguna rama sumase, operar mas daria
        mas dinero, y la simulacion premiaria justo lo que hay que evitar."""
        if self.broker is None:
            return
        assert all(f.commission >= 0 for f in self.broker.fills)

    @invariant()
    def el_deslizamiento_siempre_va_en_contra(self):
        """Comprar por encima del precio de la barra y vender por debajo. Con
        el signo al reves, el deslizamiento REGALA dinero en cada operacion y
        cuanto mas opera el bot, mejor sale el backtest."""
        if self.broker is None:
            return
        for f in self.broker.fills:
            crudo = f.extra["raw_price"]
            if f.side == "buy":
                assert f.price >= crudo - 1e-12, f"compra a {f.price} bajo {crudo}"
            else:
                assert f.price <= crudo + 1e-12, f"venta a {f.price} sobre {crudo}"

    @invariant()
    def los_precios_ejecutados_son_positivos(self):
        if self.broker is None:
            return
        for relleno in self.broker.fills:
            assert relleno.price > 0 and math.isfinite(relleno.price)
            assert relleno.qty > 0 and math.isfinite(relleno.qty)


TestCarteraSimulada = CarteraSimulada.TestCase
TestCarteraSimulada.settings = settings(
    max_examples=100, stateful_step_count=30, deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
