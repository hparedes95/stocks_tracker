"""Tests del lector publico de Polymarket. Sin red, sin wallet y sin clave.

Lo que se prueba aqui no es "que la API funcione" —eso no se puede probar sin
red— sino que lo que devuelve se interpreta bien. Es donde estan los fallos
que no lanzan ninguna excepcion y salen como una conclusion equivocada.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from stocks_tracker.trading.brokers.polymarket_public import (
    PolymarketError,
    PolymarketPublic,
    _market_from_gamma,
)


class FakeSession:
    def __init__(self, responses: dict):
        self.responses = responses
        self.calls: list[tuple[str, dict]] = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params or {}))
        for fragmento, respuesta in self.responses.items():
            if fragmento in url:
                return _Response(respuesta)
        return _Response([])


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


def lector(responses: dict) -> PolymarketPublic:
    p = PolymarketPublic(session=FakeSession(responses))
    p._last_call = -1e9
    return p


def mercado_crudo(**cambios) -> dict:
    """Un mercado tal y como lo devuelve Gamma, con sus cadenas JSON."""
    base = {
        "id": "512",
        "question": "¿Subira el BCE los tipos en diciembre?",
        "slug": "bce-tipos-diciembre",
        "conditionId": "0xabc",
        "outcomes": '["Yes", "No"]',
        "outcomePrices": '["0.62", "0.38"]',
        "clobTokenIds": '["111", "222"]',
        "endDate": "2026-12-15T12:00:00Z",
        "liquidityNum": 25000,
        "volumeNum": 180000,
        "spread": 0.01,
        "closed": False,
        "active": True,
    }
    base.update(cambios)
    return base


# ---------------------------------------------------------------------------
# Los campos que llegan como cadena JSON
# ---------------------------------------------------------------------------
def test_json_string_fields_become_lists():
    """Gamma manda `'["Yes","No"]'`, una cadena, no una lista. Es el fallo mas
    silencioso de los tres: una cadena tambien se recorre, asi que iterarla no
    lanza nada —da catorce "outcomes" de un caracter—."""
    m = _market_from_gamma(mercado_crudo())
    assert m.outcomes == ("Yes", "No")
    assert m.prices == (0.62, 0.38)
    assert m.token_ids == ("111", "222")
    assert m.is_binary


def test_a_list_that_is_already_a_list_also_works():
    """Segun el endpoint el mismo campo viene ya deserializado."""
    m = _market_from_gamma(mercado_crudo(outcomes=["Yes", "No"],
                                         outcomePrices=[0.62, 0.38]))
    assert m.outcomes == ("Yes", "No")
    assert m.prices == (0.62, 0.38)


def test_a_broken_field_gives_nothing_instead_of_garbage():
    m = _market_from_gamma(mercado_crudo(outcomes="{esto no es json",
                                         outcomePrices=None))
    assert m.outcomes == ()
    assert m.prices == ()
    assert not m.is_binary


# ---------------------------------------------------------------------------
# El precio ES la probabilidad
# ---------------------------------------------------------------------------
def test_the_yes_price_is_found_by_name_not_by_position():
    """El orden no esta garantizado. Confundirlo invierte el estudio entero:
    los aciertos contarian como fallos y la conclusion saldria del reves."""
    m = _market_from_gamma(mercado_crudo(outcomes='["No", "Yes"]',
                                         outcomePrices='["0.38", "0.62"]'))
    assert m.yes_price == 0.62


# ---------------------------------------------------------------------------
# Cerrado no es resuelto
# ---------------------------------------------------------------------------
def test_a_resolved_market_reports_its_winner():
    m = _market_from_gamma(mercado_crudo(closed=True, active=False,
                                         outcomePrices='["1", "0"]'))
    assert m.resolved_outcome == "Yes"
    assert m.is_resolved
    assert not m.is_void


def test_a_void_market_is_not_counted_as_a_loss():
    """Un mercado anulado queda cerrado con los dos precios a 0,5. Contarlo
    como un "no" inventa una perdida que nunca ocurrio y sesga la calibracion
    hacia abajo: pareceria que el mercado exagera cuando no lo hace."""
    m = _market_from_gamma(mercado_crudo(closed=True, active=False,
                                         outcomePrices='["0.5", "0.5"]'))
    assert m.resolved_outcome == ""
    assert not m.is_resolved
    assert m.is_void


def test_an_open_market_is_never_treated_as_resolved():
    m = _market_from_gamma(mercado_crudo(closed=False, outcomePrices='["0.99", "0.01"]'))
    assert not m.is_resolved, "un precio alto no es una resolucion"


def test_resolved_markets_leave_out_the_void_ones():
    respuesta = [
        mercado_crudo(id="1", closed=True, outcomePrices='["1", "0"]'),
        mercado_crudo(id="2", closed=True, outcomePrices='["0.5", "0.5"]'),
        mercado_crudo(id="3", closed=True, outcomePrices='["0", "1"]'),
    ]
    p = lector({"/markets": respuesta})
    ids = [m.market_id for m in p.resolved_markets()]
    assert ids == ["1", "3"]


# ---------------------------------------------------------------------------
# El historico y el futuro
# ---------------------------------------------------------------------------
HISTORIA = {"history": [
    {"t": 1700000000, "p": 0.40},
    {"t": 1700086400, "p": 0.55},
    {"t": 1700172800, "p": 0.95},
]}


def test_the_price_history_is_read():
    p = lector({"prices-history": HISTORIA})
    puntos = p.price_history("111")
    assert len(puntos) == 3
    assert puntos[0][1] == 0.40
    assert puntos[0][0] == datetime.fromtimestamp(1700000000, UTC)


def test_the_price_at_a_moment_never_looks_ahead():
    """Coger el punto mas CERCANO en vez del ultimo anterior mete el futuro en
    la muestra. No se nota en las metricas —salen mejores— y es exactamente lo
    que hace creer que hay ventaja donde no la hay."""
    p = lector({"prices-history": HISTORIA})
    # Diez minutos ANTES del ultimo punto: el mas cercano en el tiempo es el
    # que viene despues (0,95, que ya lleva dentro el resultado) y el correcto
    # es el anterior (0,55). Es el momento en el que las dos formas de buscar
    # dan respuestas distintas, y por eso es el que hay que probar.
    momento = datetime.fromtimestamp(1700172800 - 600, UTC)
    assert p.price_at("111", momento) == 0.55, "ha cogido un precio posterior"


def test_there_is_no_price_before_the_first_point():
    """Devolver el primero conocido seria inventarse un precio anterior al
    mercado."""
    p = lector({"prices-history": HISTORIA})
    antes = datetime.fromtimestamp(1600000000, UTC)
    assert p.price_at("111", antes) is None


# ---------------------------------------------------------------------------
# Fallos de la API
# ---------------------------------------------------------------------------
def test_an_unexpected_shape_is_an_error_not_an_empty_list():
    """Devolver [] si la API cambia dejaria el estudio sin muestra y con
    aspecto de haber funcionado."""
    p = lector({"/markets": {"error": "vaya"}})
    with pytest.raises(PolymarketError, match="lista"):
        p.markets()


def test_reading_needs_no_credentials(monkeypatch):
    """Es lo que permite medir si Polymarket merece la pena ANTES de conectar
    ninguna wallet."""
    monkeypatch.delenv("POLYMARKET_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("POLYMARKET_FUNDER_ADDRESS", raising=False)
    p = lector({"/markets": [mercado_crudo()]})
    assert len(p.markets()) == 1


def test_only_binary_markets_by_default():
    """Los mercados de varias opciones no encajan en el modelo de riesgo y
    colarlos daria posiciones mal dimensionadas."""
    respuesta = [
        mercado_crudo(id="1"),
        mercado_crudo(id="2", outcomes='["A", "B", "C"]',
                      outcomePrices='["0.3", "0.3", "0.4"]'),
    ]
    p = lector({"/markets": respuesta})
    assert [m.market_id for m in p.markets()] == ["1"]
