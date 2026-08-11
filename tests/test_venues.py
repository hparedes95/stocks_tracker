"""Tests del estado de los mercados.

Existen por una peticion concreta del usuario: "que solo tenga que poner las
claves y usarlo". Eso se rompe si al primer intento sale una traza en mitad de
un adaptador, porque no dice que falta, ni donde ponerlo, ni si el problema es
suyo o del programa.

Lo que se prueba aqui es que la respuesta a "¿ya puedo usarlo?" sea siempre una
frase accionable.
"""

from __future__ import annotations

import pytest

from stocks_tracker.core import secrets
from stocks_tracker.core.config import ConfigError
from stocks_tracker.trading import venues


@pytest.fixture(autouse=True)
def sin_credenciales(monkeypatch, tmp_path):
    for cred in secrets.CREDENTIALS:
        monkeypatch.delenv(cred.env, raising=False)
    monkeypatch.setattr(secrets, "project_root", lambda: tmp_path)
    secrets.load_env.cache_clear()
    monkeypatch.setattr(venues, "_is_validated", lambda venue: False)
    yield
    secrets.load_env.cache_clear()


def con_claves(monkeypatch, venue: str) -> None:
    for cred in secrets.CREDENTIALS:
        if cred.venue == venue and cred.required_for_trading:
            monkeypatch.setenv(cred.env, "una-credencial-larga-de-prueba")
    secrets.load_env.cache_clear()


# ---------------------------------------------------------------------------
# Los dos mercados existen y estan separados
# ---------------------------------------------------------------------------
def test_both_venues_are_configured():
    claves = {st.key for st in venues.all_status()}
    assert claves == {"kraken", "polymarket"}


def test_each_venue_has_its_own_purse():
    """Cartera separada, nunca un bote comun: compartir el saldo convertiria
    dos apuestas independientes en una sola, mas grande."""
    kraken = venues.status("kraken")
    poly = venues.status("polymarket")

    assert kraken.capital_cap > 0 and poly.capital_cap > 0
    assert kraken.currency == "EUR"
    assert poly.currency == "USDC"


def test_neither_venue_is_tradeable_out_of_the_box():
    """Se instala apagado. Encenderlo es una decision, no un descuido."""
    for st in venues.all_status():
        assert not st.can_trade


# ---------------------------------------------------------------------------
# La cadena de "que falta"
# ---------------------------------------------------------------------------
def test_the_first_thing_missing_is_the_credentials():
    st = venues.status("kraken")
    assert "KRAKEN_API_KEY" in st.why_not()
    assert "stocks.ps1 claves" in st.why_not(), "no dice donde mirar"


def test_with_credentials_it_asks_to_be_enabled(monkeypatch):
    con_claves(monkeypatch, "kraken")
    st = venues.status("kraken")
    assert "enabled: true" in st.why_not()


def test_with_credentials_and_enabled_it_still_needs_validation(monkeypatch):
    """El paso que la gente se salta. Sin validacion se puede simular, pero no
    poner dinero."""
    con_claves(monkeypatch, "kraken")
    monkeypatch.setattr(
        venues, "status",
        lambda v: venues.VenueStatus(
            key=v, label=v, configured=True, enabled=True, credentials_ok=True,
            missing_credentials=(), validated=False,
        ),
    )
    st = venues.status("kraken")
    assert not st.can_trade
    assert "validacion" in st.why_not()


def test_everything_in_place_means_tradeable():
    st = venues.VenueStatus(
        key="kraken", label="Kraken", configured=True, enabled=True,
        credentials_ok=True, missing_credentials=(), validated=True,
    )
    assert st.can_trade
    assert st.why_not() == ""


# ---------------------------------------------------------------------------
# Simular no necesita nada
# ---------------------------------------------------------------------------
def test_simulating_needs_neither_keys_nor_validation():
    """Es lo que permite construir y probar el bot entero sin cuenta."""
    for venue in ("kraken", "polymarket"):
        st = venues.require_tradeable(venue, "simulated")
        assert st.can_simulate


def test_trading_without_credentials_is_refused_with_a_reason():
    with pytest.raises(ConfigError) as exc:
        venues.require_tradeable("kraken", "live")
    mensaje = str(exc.value)
    assert "KRAKEN_API_KEY" in mensaje
    assert "No se puede operar" in mensaje


def test_polymarket_has_no_paper_mode():
    """No existe entorno de pruebas: o se simula contra historico, o es dinero
    real. Conviene saberlo antes de disenar la progresion, no despues."""
    with pytest.raises(ConfigError, match="no tiene entorno de pruebas"):
        venues.require_tradeable("polymarket", "paper")


def test_an_unknown_mode_lists_the_valid_ones():
    with pytest.raises(ConfigError, match="Modos: simulated, paper, live"):
        venues.require_tradeable("kraken", "inventado")


# ---------------------------------------------------------------------------
# Las prohibiciones valen en los dos mercados
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("venue", ["kraken", "polymarket"])
@pytest.mark.parametrize("prohibido", ["allow_shorting", "allow_leverage",
                                       "allow_options"])
def test_the_mandate_holds_in_every_venue(venue, prohibido):
    from stocks_tracker.core.config import VenueConfig

    with pytest.raises(ConfigError, match=prohibido):
        VenueConfig(key=venue, raw={"capital_cap": 10.0,
                                    "risk": {prohibido: True}})


def test_round_the_clock_markets_may_trade_outside_office_hours():
    """`allow_extended_hours` si es legitimo aqui: cripto y los mercados de
    prediccion funcionan 24/7 y "fuera de horario" no significa nada."""
    from stocks_tracker.core.config import VenueConfig

    v = VenueConfig(key="kraken", raw={"capital_cap": 10.0,
                                       "risk": {"allow_extended_hours": True}})
    assert v.risk["allow_extended_hours"]


def test_a_missing_limit_is_an_error_not_a_default():
    """Un limite ausente rellenado en silencio pasa a ser un limite inventado
    por el codigo, y el usuario creeria operar bajo lo que leyo en el YAML."""
    from stocks_tracker.core.config import VenueConfig

    v = VenueConfig(key="x", raw={"capital_cap": 10.0, "risk": {}})
    with pytest.raises(ConfigError, match="falta el limite"):
        v.limit("max_positions")


# ---------------------------------------------------------------------------
# Autonomia
# ---------------------------------------------------------------------------
def test_real_money_always_starts_semi_automatic():
    """No configurable. La friccion tiene valor donde hay consecuencias, y el
    primer dia en real es cuando aparecen los fallos que el papel no revela."""
    from stocks_tracker.core.config import TradingConfig

    cfg = TradingConfig(raw={"autonomy_policy": {"live": "auto"}})
    assert cfg.autonomy_for("live") == "semi", (
        "se ha podido poner el dinero real en automatico desde el YAML"
    )


def test_paper_runs_alone():
    """Aprobar cuarenta propuestas de papel no ensena nada y produce fatiga: a
    la decima se pulsa 'aprobar' sin leer."""
    from stocks_tracker.core.config import get_trading_config

    assert get_trading_config().autonomy_for("paper") == "auto"
