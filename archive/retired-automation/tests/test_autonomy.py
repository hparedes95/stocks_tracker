"""Tests del freno de mano.

Esto decide si una orden sale sin que nadie la mire, asi que un fallo aqui no
se nota hasta que ya se ha ejecutado algo que no debia. Se prueba entero sin
base de datos: `brakes_for` recibe primitivos justo para eso.
"""

from __future__ import annotations

import pytest

from stocks_tracker.core.config import ConfigError, TradingConfig
from stocks_tracker.trading.autonomy import (
    DEFAULT_BRAKES,
    Autonomy,
    brake_settings,
    brakes_for,
    explain,
    parse,
    requires_confirmation,
)

AJUSTES = {"confirm_above_eur": 10.0, "confirm_first_live_order": True,
           "confirm_when_drawdown_over_pct": 8.0}


def frenos(**cambios):
    base = {"notional": 6.0, "is_opening": True, "drawdown_pct": 0.0,
            "live_orders_so_far": 5, "settings": AJUSTES}
    return brakes_for(**{**base, **cambios})


def codigos(lista):
    return sorted(b.code for b in lista)


# ---------------------------------------------------------------------------
# Los tres niveles
# ---------------------------------------------------------------------------
def test_auto_never_stops():
    assert requires_confirmation(Autonomy.AUTO, notional=10_000.0, is_opening=True,
                                 drawdown_pct=50.0, live_orders_so_far=0) == []


def test_semi_always_stops():
    """Aunque no cruce ningun freno: en semi no sale nada solo."""
    parado = requires_confirmation(Autonomy.SEMI, notional=0.5, is_opening=False)
    assert codigos(parado) == ["semi"]


def test_guarded_lets_a_normal_order_through():
    """Si parara tambien lo normal, seria `semi` con otro nombre y a la decima
    confirmacion se pulsa sin leer."""
    assert requires_confirmation("guarded", notional=6.0, is_opening=True,
                                 drawdown_pct=0.0, live_orders_so_far=5,
                                 settings=AJUSTES) == []


def test_an_unknown_level_is_refused():
    """'automatico' o 'full' no pueden caer en el lado permisivo: seria un bot
    operando solo por una errata."""
    with pytest.raises(ConfigError, match="automatico"):
        parse("automatico")


# ---------------------------------------------------------------------------
# Freno 1: el importe
# ---------------------------------------------------------------------------
def test_an_oversized_order_waits():
    """Un error de calculo del tamano se manifiesta asi: una orden mucho mayor
    de lo normal. Es el sintoma mas fiable y el mas caro."""
    assert codigos(frenos(notional=25.0)) == ["importe"]


def test_the_threshold_is_exclusive():
    """Justo en el tope no frena; un centimo por encima si. Sin fijarlo, el
    comportamiento en el borde depende de como se escribio la comparacion."""
    assert frenos(notional=10.0) == []
    assert codigos(frenos(notional=10.01)) == ["importe"]


def test_a_zero_threshold_disables_the_brake():
    """Poner 0 tiene que significar "no frenes por importe", no "frena
    siempre": lo segundo convertiria un ajuste en una trampa."""
    assert frenos(notional=1000.0,
                  settings={**AJUSTES, "confirm_above_eur": 0}) == []


# ---------------------------------------------------------------------------
# Freno 2: la primera orden real
# ---------------------------------------------------------------------------
def test_the_very_first_live_order_waits():
    """Es el unico momento en que el programa toca dinero por primera vez, y
    ahi sale todo lo que las pruebas no vieron."""
    assert "primera" in codigos(frenos(live_orders_so_far=0))


def test_only_the_first_one_waits():
    assert "primera" not in codigos(frenos(live_orders_so_far=1))


# ---------------------------------------------------------------------------
# Freno 3: abrir estando en perdidas
# ---------------------------------------------------------------------------
def test_opening_while_down_waits():
    assert "perdidas" in codigos(frenos(drawdown_pct=12.0))


def test_closing_while_down_does_not_wait():
    """Cerrar reduce el riesgo. Frenar una salida mientras la cartera cae es
    justo lo contrario de lo que hay que hacer, y ademas una salida protectora
    llega tarde por definicion si espera a que alguien la lea."""
    assert "perdidas" not in codigos(frenos(is_opening=False, drawdown_pct=30.0))


def test_a_small_drawdown_does_not_wait():
    assert "perdidas" not in codigos(frenos(drawdown_pct=3.0))


# ---------------------------------------------------------------------------
# Varios a la vez
# ---------------------------------------------------------------------------
def test_every_reason_is_reported_not_just_the_first():
    """Confirmar viendo solo un motivo de tres es decidir con menos
    informacion de la que hay."""
    todos = frenos(notional=50.0, live_orders_so_far=0, drawdown_pct=20.0)
    assert codigos(todos) == ["importe", "perdidas", "primera"]


def test_the_reasons_are_readable():
    """Este texto va al aviso del movil. "brake_code=importe" no le dice nada
    a quien tiene que decidir en diez segundos."""
    texto = explain(frenos(notional=50.0))
    assert "50.00 EUR" in texto
    assert "10.00 EUR" in texto


# ---------------------------------------------------------------------------
# De donde salen los ajustes
# ---------------------------------------------------------------------------
def test_the_defaults_are_conservative():
    """Lo que no se ha decidido no se decide solo, y menos hacia el lado que
    gasta dinero sin preguntar."""
    assert DEFAULT_BRAKES["confirm_first_live_order"] is True
    assert DEFAULT_BRAKES["confirm_above_eur"] > 0


def venue_cfg(brakes: dict) -> TradingConfig:
    return TradingConfig(raw={
        "brakes": {"confirm_above_eur": 10.0, "confirm_first_live_order": True,
                   "confirm_when_drawdown_over_pct": 8.0},
        "venues": {"kraken": {"enabled": True, "capital_cap": 25.0,
                              "brakes": brakes}},
    })


def test_a_venue_can_tighten_its_own_brakes():
    cfg = venue_cfg({"confirm_above_eur": 5.0})
    assert brake_settings(cfg, "kraken")["confirm_above_eur"] == 5.0
    assert brake_settings(cfg)["confirm_above_eur"] == 10.0


def test_a_venue_cannot_loosen_a_brake():
    """Sin esta regla, un venue subiria su tope a 1000 EUR y se saltaria el
    freno del importe entero. Los frenos cazan fallos del PROGRAMA, y el
    programa es el mismo en todos los venues: uno puede necesitar mas cuidado,
    nunca menos."""
    cfg = venue_cfg({"confirm_above_eur": 1000.0,
                     "confirm_when_drawdown_over_pct": 90.0})
    ajustes = brake_settings(cfg, "kraken")
    assert ajustes["confirm_above_eur"] == 10.0
    assert ajustes["confirm_when_drawdown_over_pct"] == 8.0


def test_a_venue_cannot_switch_a_brake_off():
    """Poner 0 en el venue apagaria el freno. Apagar es aflojar."""
    cfg = venue_cfg({"confirm_above_eur": 0, "confirm_first_live_order": False})
    ajustes = brake_settings(cfg, "kraken")
    assert ajustes["confirm_above_eur"] == 10.0
    assert ajustes["confirm_first_live_order"] is True


def test_a_venue_can_switch_a_brake_on_that_was_off():
    """Apretar si vale en los dos sentidos."""
    cfg = TradingConfig(raw={
        "brakes": {"confirm_above_eur": 0, "confirm_first_live_order": False},
        "venues": {"kraken": {"enabled": True, "capital_cap": 25.0,
                              "brakes": {"confirm_above_eur": 5.0,
                                         "confirm_first_live_order": True}}},
    })
    ajustes = brake_settings(cfg, "kraken")
    assert ajustes["confirm_above_eur"] == 5.0
    assert ajustes["confirm_first_live_order"] is True


def test_a_broken_venue_config_does_not_remove_the_brakes():
    """Un venue mal configurado no puede dejar una orden sin frenos."""
    cfg = TradingConfig(raw={
        "brakes": {"confirm_above_eur": 10.0},
        "venues": {"kraken": {"enabled": True}},   # sin capital_cap: invalido
    })
    assert brake_settings(cfg, "kraken")["confirm_above_eur"] == 10.0


def test_an_unknown_venue_falls_back_to_the_general_ones():
    """Sin esto, un nombre de venue mal escrito dejaria la orden sin frenos."""
    cfg = TradingConfig(raw={"brakes": {"confirm_above_eur": 10.0}})
    assert brake_settings(cfg, "inventado")["confirm_above_eur"] == 10.0


def test_the_mandate_puts_real_money_in_guarded():
    """Lo que se acordo: automatico, con freno de mano."""
    from stocks_tracker.core.config import get_trading_config

    assert get_trading_config().autonomy_for("live") == "guarded"
