"""Tests de `crypto_momentum_v1`.

La estrategia solo lee del contexto, asi que se prueba entera con uno de
mentira: sin base de datos, sin red y sin broker. Lo que se comprueba no es que
gane dinero —eso no lo dice ningun test— sino que decide lo que dice decidir.
Una estrategia que compra por un motivo distinto del que explica es peor que
una mala: la explicacion del dashboard seria falsa.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from stocks_tracker.trading.context import StrategyContext
from stocks_tracker.trading.intents import IntentType, Side
from stocks_tracker.trading.strategies.crypto_momentum import (
    REGIME_TICKER,
    CryptoMomentum,
)

PARES = ["BTC/EUR", "ETH/EUR", "SOL/EUR", "ADA/EUR", "DOT/EUR", "LINK/EUR"]


def contexto(indicadores: dict, posiciones: dict | None = None,
             bot_positions: dict | None = None) -> StrategyContext:
    """Contexto con los indicadores que se le den, por ticker."""
    df = pd.DataFrame.from_dict(indicadores, orient="index")
    return StrategyContext(
        as_of=date(2026, 6, 1), mode="simulated", equity=25.0, cash=25.0,
        positions=posiciones or {}, bot_positions=bot_positions or {},
        indicators=df, last_price_date=date(2026, 6, 1),
    )


def fila(close=100.0, roc_3m=0.10, roc_6m=0.20, atr14=5.0,
         above_sma50=True, above_sma200=True) -> dict:
    return {"close": close, "roc_3m": roc_3m, "roc_6m": roc_6m, "atr14": atr14,
            "above_sma50": above_sma50, "above_sma200": above_sma200}


def mercado(alcista: bool = True, **por_par) -> dict:
    """Los seis pares con datos normales, mas lo que se quiera cambiar."""
    base = {p: fila() for p in PARES}
    base[REGIME_TICKER] = fila(above_sma200=alcista)
    for par, cambios in por_par.items():
        base[par] = {**base.get(par, fila()), **cambios}
    return base


@pytest.fixture
def estrategia():
    return CryptoMomentum()


# ---------------------------------------------------------------------------
# Leer un booleano como booleano
# ---------------------------------------------------------------------------
def test_a_boolean_indicator_is_read_as_a_boolean():
    """`ctx.indicator` devuelve SIEMPRE float, tambien para los booleanos, asi
    que `is True` no se cumple nunca. Ese fallo no rompe nada: desactiva el
    filtro en silencio y la estrategia sigue diciendo en el dashboard que lo
    mira. Ya paso al escribir esto.

    Los tres estados se distinguen: sin dato no es lo mismo que por debajo.
    """
    from stocks_tracker.trading.strategies.crypto_momentum import flag

    ctx = contexto({
        "A/EUR": {"above_sma50": True},
        "B/EUR": {"above_sma50": False},
        "C/EUR": {"above_sma50": None},
    })
    assert flag(ctx, "A/EUR", "above_sma50") is True
    assert flag(ctx, "B/EUR", "above_sma50") is False
    assert flag(ctx, "C/EUR", "above_sma50") is None
    assert flag(ctx, "NOEXISTE/EUR", "above_sma50") is None


# ---------------------------------------------------------------------------
# El filtro de regimen
# ---------------------------------------------------------------------------
def test_no_new_positions_when_bitcoin_is_below_its_200_day(estrategia):
    """En cripto los desplomes son del 70-80 %. Este interruptor es casi lo
    unico que separa perder un 20 % de perderlo casi todo."""
    ctx = contexto(mercado(alcista=False))
    compras = [i for i in estrategia.propose(ctx) if i.side == Side.BUY]
    assert compras == []


def test_positions_open_when_bitcoin_is_above_its_200_day(estrategia):
    ctx = contexto(mercado(alcista=True))
    compras = [i for i in estrategia.propose(ctx) if i.side == Side.BUY]
    assert compras, "no abre nada con el regimen a favor"


def test_missing_regime_data_does_not_count_as_bullish(estrategia):
    """Sin dato de bitcoin no se puede afirmar que el mercado este bajista,
    pero tampoco alcista. Tratar la ausencia como alcista abriria posiciones
    justo cuando faltan datos, que es cuando peor idea es."""
    datos = mercado()
    datos[REGIME_TICKER] = {**fila(), "above_sma200": None}
    ctx = contexto(datos)
    assert estrategia.bullish_regime(ctx) is not True
    assert [i for i in estrategia.propose(ctx) if i.side == Side.BUY] == []


def test_a_bear_regime_does_not_liquidate_everything_at_once(estrategia):
    """Las posiciones salen por sus propias reglas. Liquidar de golpe al
    cruzar la media convierte un indicador que oscila en una venta total,
    y el cruce se cancela a los dos dias mas veces de las que se mantiene."""
    datos = mercado(alcista=False)
    posiciones = {"ETH/EUR": {"qty": 0.1, "avg_entry_price": 90.0}}
    ctx = contexto(datos, posiciones)
    ventas = [i for i in estrategia.propose(ctx) if i.side == Side.SELL]
    assert ventas == [], "ha liquidado por el regimen y no por sus reglas"


# ---------------------------------------------------------------------------
# El ranking
# ---------------------------------------------------------------------------
def test_the_ranking_averages_positions_not_returns(estrategia):
    """Promediar rentabilidades deja que un +400 % en un horizonte tape lo que
    diga el otro. Con puestos, los dos horizontes pesan igual."""
    datos = mercado(
        **{
            # Enorme a 3 meses, la peor a 6: no puede salir primera.
            "SOL/EUR": {"roc_3m": 4.00, "roc_6m": -0.50},
            # Segunda en los dos: deberia ganar al promediar puestos.
            "ETH/EUR": {"roc_3m": 0.50, "roc_6m": 0.50},
        }
    )
    orden = estrategia.ranking(contexto(datos))
    assert orden[0] == "ETH/EUR", f"gana la del pico aislado: {orden}"


def test_the_ranking_is_stable_between_runs(estrategia):
    """Dos ejecuciones con los mismos datos tienen que dar el mismo orden, o
    el bot compraria una moneda distinta cada vez sin que nada cambiara."""
    datos = mercado()  # todos iguales: empate total
    ctx = contexto(datos)
    assert estrategia.ranking(ctx) == estrategia.ranking(ctx)


def test_a_pair_missing_a_horizon_is_left_out(estrategia):
    """Puntuarla con el horizonte que tenga la dejaria dentro o fuera segun
    que dato le falte, no segun como se este comportando."""
    datos = mercado(**{"ADA/EUR": {"roc_6m": None}})
    assert "ADA/EUR" not in estrategia.ranking(contexto(datos))


def test_only_the_mandate_whitelist_is_ranked(estrategia):
    """El universo no se descubre. Si entrara cualquier cosa que haya en el
    almacen, el bot cripto acabaria comprando acciones."""
    datos = mercado()
    datos["AAPL"] = fila(roc_3m=9.0, roc_6m=9.0)
    orden = estrategia.ranking(contexto(datos))
    assert "AAPL" not in orden


# ---------------------------------------------------------------------------
# Entradas
# ---------------------------------------------------------------------------
def test_the_best_ranked_pair_below_its_50_day_is_not_bought(estrategia):
    """El ranking dice cual es la mejor del grupo; la media de 50 dice si sube
    o solo cae menos que las demas. Sin este filtro, en un mercado bajista se
    compra siempre la que menos baja."""
    datos = mercado(**{"SOL/EUR": {"roc_3m": 5.0, "roc_6m": 5.0,
                                   "above_sma50": False}})
    compras = [i.ticker for i in estrategia.propose(contexto(datos))
               if i.side == Side.BUY]
    assert "SOL/EUR" not in compras


def test_it_does_not_open_more_than_the_mandate_allows(estrategia):
    ctx = contexto(mercado())
    compras = [i for i in estrategia.propose(ctx) if i.side == Side.BUY]
    assert len(compras) <= int(estrategia.params["max_positions"])


def test_existing_positions_do_not_take_a_free_slot(estrategia):
    """Contar una posicion abierta como hueco libre abriria de mas."""
    datos = mercado()
    orden = estrategia.ranking(contexto(datos))
    tope = int(estrategia.params["max_positions"])
    posiciones = {t: {"qty": 0.1, "avg_entry_price": 50.0} for t in orden[:tope]}
    ctx = contexto(datos, posiciones)
    assert [i for i in estrategia.propose(ctx) if i.side == Side.BUY] == []


def test_it_does_not_rebuy_something_it_is_selling_today(estrategia):
    """Vender y recomprar el mismo dia es pagar dos veces la horquilla para
    quedarse igual.

    El caso que lo destapa es una posicion que sale por STOP estando aun
    arriba del ranking y sobre su media de 50: los filtros de entrada la
    aceptarian encantados. Si la salida fuera por perder la media de 50, el
    propio filtro de entrada la rechazaria y este test pasaria sin comprobar
    nada.
    """
    datos = mercado(**{"ETH/EUR": {
        "close": 50.0, "atr14": 1.0,      # muy por debajo del stop
        "above_sma50": True,               # pero sigue sobre su media
        "roc_3m": 9.0, "roc_6m": 9.0,      # y primera del ranking
    }})
    posiciones = {"ETH/EUR": {"qty": 0.1, "avg_entry_price": 100.0}}
    intents = estrategia.propose(contexto(datos, posiciones))

    vendidos = {i.ticker for i in intents if i.side == Side.SELL}
    comprados = {i.ticker for i in intents if i.side == Side.BUY}
    assert "ETH/EUR" in vendidos, "el escenario no dispara la venta que se prueba"
    assert not (vendidos & comprados), "recompra hoy lo que vende hoy"


def test_an_entry_explains_itself(estrategia):
    """El dashboard muestra estas razones. Si no coinciden con lo que hizo el
    codigo, la explicacion es falsa, que es peor que no explicar nada."""
    compras = [i for i in estrategia.propose(contexto(mercado()))
               if i.side == Side.BUY]
    razones = " ".join(compras[0].rationale["reasons"]).lower()
    assert "momentum" in razones
    assert "media de 50" in razones
    assert "media de 200" in razones


# ---------------------------------------------------------------------------
# Salidas
# ---------------------------------------------------------------------------
def test_losing_the_50_day_closes_the_position(estrategia):
    datos = mercado(**{"ETH/EUR": {"above_sma50": False}})
    posiciones = {"ETH/EUR": {"qty": 0.1, "avg_entry_price": 50.0}}
    ventas = [i for i in estrategia.propose(contexto(datos, posiciones))
              if i.side == Side.SELL]
    assert [v.ticker for v in ventas] == ["ETH/EUR"]


def test_there_is_a_band_between_entering_and_leaving(estrategia):
    """Se entra en el top N y solo se sale por debajo de N+1. Sin esa banda, la
    moneda que oscila alrededor del cuarto puesto se compra y se vende
    continuamente: con posiciones de seis euros, cada vuelta se come en
    horquilla mas de lo que puede ganar."""
    tope = int(estrategia.params["max_positions"])
    buffer = int(estrategia.params["exit_rank_buffer"])

    # Una moneda justo en el puesto N+1: fuera del top de entrada, dentro de
    # la banda de salida.
    datos = mercado()
    for i, par in enumerate(PARES):
        datos[par] = {**datos[par], "roc_3m": 1.0 - i * 0.1, "roc_6m": 1.0 - i * 0.1}
    datos[REGIME_TICKER] = {**datos[REGIME_TICKER], "above_sma200": True}

    orden = estrategia.ranking(contexto(datos))
    justo_fuera = orden[tope]           # puesto N+1, dentro de la banda
    posiciones = {justo_fuera: {"qty": 0.1, "avg_entry_price": 50.0}}
    ventas = [i.ticker for i in estrategia.propose(contexto(datos, posiciones))
              if i.side == Side.SELL]
    assert justo_fuera not in ventas, "vende sin banda de histeresis"

    # Y una que si ha caido por debajo de la banda: esa si sale.
    muy_fuera = orden[tope + buffer]
    posiciones = {muy_fuera: {"qty": 0.1, "avg_entry_price": 50.0}}
    ventas = [i.ticker for i in estrategia.propose(contexto(datos, posiciones))
              if i.side == Side.SELL]
    assert muy_fuera in ventas, "no vende ni cayendo fuera de la banda"


def test_a_touched_stop_is_a_protective_exit(estrategia):
    """El tipo importa: el riesgo no frena una salida protectora, y si saliera
    como venta normal podria quedar vetada por el limite de ordenes del dia."""
    datos = mercado(**{"ETH/EUR": {"close": 50.0, "atr14": 1.0}})
    posiciones = {"ETH/EUR": {"qty": 0.1, "avg_entry_price": 100.0}}
    ventas = [i for i in estrategia.propose(contexto(datos, posiciones))
              if i.ticker == "ETH/EUR"]
    assert ventas[0].intent_type == IntentType.STOP_EXIT


def test_the_stop_is_wider_than_in_equities(estrategia):
    """Con la volatilidad de cripto, un stop de 2,5x ATR lo toca cualquier
    martes: la posicion se cerraria por ruido y no porque la idea haya dejado
    de valer."""
    assert float(estrategia.params["stop_atr_mult"]) >= 4.0


def test_the_stop_trails_upwards_only(estrategia):
    """Un stop que baja cuando baja el precio no es un stop."""
    datos = mercado(**{"ETH/EUR": {"close": 100.0, "atr14": 5.0}})
    held = {"qty": 0.1, "avg_entry_price": 100.0}

    sin_subida = estrategia._current_stop(contexto(datos, {"ETH/EUR": held}),
                                          "ETH/EUR", held)
    con_subida = estrategia._current_stop(
        contexto(datos, {"ETH/EUR": held},
                 {"ETH/EUR": {"highest_close_since_entry": 200.0}}),
        "ETH/EUR", held,
    )
    assert con_subida > sin_subida


def test_a_position_without_indicators_is_not_touched(estrategia):
    """Vender por falta de datos convierte un fallo de descarga en una orden."""
    datos = mercado()
    posiciones = {"XRP/EUR": {"qty": 1.0, "avg_entry_price": 1.0}}
    ventas = [i for i in estrategia.propose(contexto(datos, posiciones))
              if i.ticker == "XRP/EUR"]
    assert ventas == []


# ---------------------------------------------------------------------------
# Cadencia
# ---------------------------------------------------------------------------
def test_it_runs_every_day_because_crypto_never_closes(estrategia):
    """Un fin de semana puede mover un 30 %; esperar al lunes es tan arbitrario
    como esperar al jueves. Lo que evita operar a diario son los limites del
    mandato, no el calendario."""
    for dia in range(1, 8):
        ctx = contexto(mercado())
        ctx = StrategyContext(**{**ctx.__dict__, "as_of": date(2026, 6, dia)})
        assert estrategia.should_run_today(ctx)


def test_no_parameter_was_tuned_on_the_data(estrategia):
    """La defensa principal contra el sobreajuste: si no se busca, no se puede
    sobreajustar. Este test existe para que cambiar uno de estos numeros
    porque "mejora el backtest" requiera tocar el test y leer esto."""
    from stocks_tracker.trading.strategies.crypto_momentum import MOMENTUM_FIELDS

    assert MOMENTUM_FIELDS == ("roc_3m", "roc_6m"), "horizontes de manual"
    assert estrategia.params["exit_rank_buffer"] == 1
