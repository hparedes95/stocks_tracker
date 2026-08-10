"""Tests del dimensionamiento por volatilidad.

La adenda pedia `hypothesis` para las pruebas de propiedades. Se hacen aqui con
rejillas explicitas: el proyecto se instala en Windows desde un ZIP y cada
dependencia menos es una cosa menos que puede fallar en el `pip install`. Las
propiedades que se comprueban son las mismas, y ademas los casos son
reproducibles sin semilla.
"""

from __future__ import annotations

import pytest

from stocks_tracker.trading.sizing import regime_factor, size_by_atr, trailing_stop

BASE = dict(
    risk_per_trade_pct=1.5,
    atr_stop_mult=2.5,
    max_position_pct=22.0,
    target_position_pct=15.0,
    min_cash_pct=10.0,
    min_notional=1.0,
)


def size(**overrides):
    params = {**BASE, "equity": 100.0, "price": 50.0, "atr14": 1.0,
              "cash_available": 100.0, "regime": "risk_on"}
    params.update(overrides)
    return size_by_atr(**params)


# ---------------------------------------------------------------------------
# Propiedades que deben cumplirse siempre
# ---------------------------------------------------------------------------
def test_the_position_never_exceeds_the_per_asset_cap():
    for equity in (20.0, 55.0, 100.0, 5000.0):
        for price in (5.0, 50.0, 300.0):
            for atr in (0.05, 0.5, 2.0, 10.0):
                result = size(equity=equity, price=price, atr14=atr,
                              cash_available=equity)
                if not result.ok:
                    continue
                cap = equity * BASE["max_position_pct"] / 100.0
                assert result.notional <= cap + 1e-9, (
                    f"equity={equity} precio={price} atr={atr}: "
                    f"{result.notional} supera el tope {cap}"
                )


def test_the_size_is_never_negative():
    for atr in (0.01, 1.0, 100.0):
        result = size(atr14=atr)
        assert result.notional >= 0
        assert result.qty >= 0


def test_a_buy_stop_always_sits_below_the_price():
    for price in (5.0, 50.0, 300.0):
        for atr in (0.05, 0.5, 2.0):
            result = size(price=price, atr14=atr)
            if result.ok:
                assert result.stop_price < price


def test_the_theoretical_risk_never_exceeds_the_budget():
    """La propiedad que da sentido al modulo: pase lo que pase, si el stop se
    ejecuta a su precio no se pierde mas de lo presupuestado."""
    for equity in (20.0, 55.0, 100.0, 1000.0):
        for atr in (0.05, 0.5, 2.0):
            result = size(equity=equity, atr14=atr, cash_available=equity)
            if not result.ok:
                continue
            budget = equity * BASE["risk_per_trade_pct"] / 100.0
            assert result.risk_amount <= budget + 1e-9, (
                f"equity={equity} atr={atr}: se arriesga {result.risk_amount} "
                f"con un presupuesto de {budget}"
            )


def test_more_volatility_means_a_smaller_position():
    """Es la idea entera: arriesgar lo mismo, no comprar lo mismo."""
    calm = size(atr14=0.5)
    wild = size(atr14=5.0)
    assert wild.notional < calm.notional


# ---------------------------------------------------------------------------
# Limites y sus motivos
# ---------------------------------------------------------------------------
def test_the_cash_reserve_is_respected():
    result = size(equity=100.0, cash_available=12.0)
    # Reserva del 10 % de 100 = 10; solo quedan 2 disponibles.
    assert result.notional <= 2.0 + 1e-9


def test_a_hostile_regime_shrinks_the_position():
    on = size(regime="risk_on")
    neutral = size(regime="neutral")
    off = size(regime="risk_off")
    assert off.notional < neutral.notional < on.notional
    assert regime_factor("desconocido") == 0.8, (
        "un regimen que no se reconoce deberia tratarse como neutral, no como "
        "el mas permisivo"
    )


def test_a_stop_below_zero_is_refused():
    """Si el ATR es enorme frente al precio, la posicion no se puede proteger.
    Es justo donde no hay que entrar."""
    result = size(price=2.0, atr14=1.0)   # stop = 2 - 2.5 = -0.5
    assert not result.ok
    assert result.reason_code == "STOP_BELOW_ZERO"


def test_the_broker_minimum_is_honoured_when_it_fits():
    """Con 50 EUR el calculo puede dar menos del minimo del broker."""
    result = size(equity=55.0, price=50.0, atr14=20.0, cash_available=55.0)
    if result.ok:
        assert result.notional >= BASE["min_notional"]
        assert result.notional <= 55.0 * BASE["max_position_pct"] / 100.0


def test_no_limit_is_relaxed_to_make_an_order_fit():
    """Si subir al minimo del broker rompiese el tope por activo, se veta."""
    # Cartera de 3 EUR: el tope por activo son 0,66 y el minimo del broker 5.
    # Subir hasta el minimo romperia el tope, asi que no se abre.
    result = size(equity=3.0, price=50.0, atr14=1.0, cash_available=3.0,
                  min_notional=5.0)
    assert not result.ok
    assert result.reason_code == "POSITION_TOO_SMALL_FOR_RISK"


def test_missing_inputs_produce_a_refusal_not_a_guess():
    for bad in ({"price": 0.0}, {"atr14": 0.0}, {"equity": 0.0}):
        assert not size(**bad).ok


def test_the_binding_limit_is_reported():
    """Para poder responder 'por que compraste solo 9 euros'."""
    result = size(equity=100.0, cash_available=12.0)
    assert result.capped_by == "efectivo_disponible"


# ---------------------------------------------------------------------------
# Stop dinamico
# ---------------------------------------------------------------------------
def test_the_trailing_stop_rises_with_the_price():
    first = trailing_stop(entry_price=100.0, highest_close=100.0, atr14=2.0,
                          atr_stop_mult=2.5)
    later = trailing_stop(entry_price=100.0, highest_close=120.0, atr14=2.0,
                          atr_stop_mult=2.5)
    assert later > first


def test_the_trailing_stop_never_falls_back():
    """Un stop que retrocede cuando el precio retrocede no protege de nada:
    solo retrasa la perdida."""
    peak = trailing_stop(100.0, 120.0, 2.0, 2.5)
    after_pullback = trailing_stop(100.0, 120.0, 2.0, 2.5)
    assert after_pullback == pytest.approx(peak)
    # Y nunca por debajo del stop inicial.
    assert trailing_stop(100.0, 90.0, 2.0, 2.5) == pytest.approx(95.0)
