"""Tests de la confirmacion de ordenes retenidas.

Sin esto, `guarded` seria peor que `auto`: lo que cruza un freno se quedaria
retenido y nadie podria ni aprobarlo ni rechazarlo. El freno solo tiene
sentido si hay forma de levantarlo, y esa forma tiene que ser segura: una
confirmacion tardia o a un precio que ya no existe es peor que no confirmar.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from stocks_tracker.core import db
from stocks_tracker.trading import confirm


@pytest.fixture
def warehouse(tmp_path, monkeypatch):
    class Stub:
        warehouse_path = tmp_path / "test.duckdb"

    monkeypatch.setattr(db, "get_settings", lambda: Stub())
    db.migrate()
    return Stub.warehouse_path


def retener(intent_id: str = "01ABCDEF", ticker: str = "BTC/EUR",
            horas: float = 18.0, precio: float = 100.0,
            side: str = "buy", notional: float = 6.0) -> str:
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO intents (intent_id, run_id, strategy_id, created_at, "
            "ticker, side, intent_type, notional_approved, ref_price, "
            "rationale, status, expires_at, decision_note) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [intent_id, "r1", "crypto_momentum_v1", datetime.now(), ticker,
             side, "open", notional, precio,
             '{"reasons": ["Entre las mejores por momentum."]}',
             "PENDING_CONFIRMATION", datetime.now() + timedelta(hours=horas),
             "La orden supera el tope que sale solo."],
        )
    return intent_id


class BrokerEspia:
    def __init__(self, falla: bool = False):
        self.enviadas = []
        self.falla = falla

    def submit_order(self, request):
        if self.falla:
            raise RuntimeError("fondos insuficientes")
        self.enviadas.append(request)
        return request


# ---------------------------------------------------------------------------
# Ver lo que espera
# ---------------------------------------------------------------------------
def test_a_held_order_shows_up(warehouse):
    retener()
    p = confirm.pending()
    assert len(p) == 1
    assert p[0].ticker == "BTC/EUR"
    assert "tope" in p[0].reason


def test_the_short_id_is_enough_to_find_it(warehouse):
    """Teclear un ULID de 26 caracteres a mano invita a equivocarse, y
    equivocarse aqui es aprobar una orden que no era."""
    retener("01JABCDEFGHIJKLMNOPQRSTUVW")
    assert confirm.find("STUVW") is not None


def test_the_reason_and_the_rationale_both_show(warehouse):
    """Por que espera y por que se propuso son dos preguntas distintas, y con
    una sola no se puede decidir."""
    retener()
    texto = confirm.render(confirm.pending())
    assert "tope" in texto
    assert "momentum" in texto


def test_nothing_pending_says_so(warehouse):
    assert "No hay ninguna" in confirm.render(confirm.pending())


# ---------------------------------------------------------------------------
# Caducidad
# ---------------------------------------------------------------------------
def test_an_expired_order_is_not_listed(warehouse):
    retener(horas=-1)
    assert confirm.pending() == []


def test_an_expired_order_is_not_sent_even_if_approved(warehouse):
    """Aprobar dos dias despues una orden calculada con el precio del lunes es
    mandar al mercado una decision que ya no corresponde a ningun precio."""
    retener(horas=-2)
    espia = BrokerEspia()
    enviada, motivo = confirm.approve("01ABCDEF", espia, current_price=100.0)
    assert not enviada
    assert espia.enviadas == []
    assert "caduco" in motivo


def test_expiring_marks_them_instead_of_leaving_them_pending(warehouse):
    """Dejarlas en PENDING para siempre llenaria la lista de ordenes muertas y
    la util quedaria enterrada."""
    retener(horas=-1)
    confirm.expire_stale()
    with db.connect(read_only=True) as conn:
        estado = conn.execute(
            "SELECT status FROM intents WHERE intent_id = '01ABCDEF'"
        ).fetchone()[0]
    assert estado == "EXPIRED"


def test_a_live_order_is_not_expired_by_mistake(warehouse):
    retener(horas=5)
    confirm.expire_stale()
    assert len(confirm.pending()) == 1


# ---------------------------------------------------------------------------
# Aprobar
# ---------------------------------------------------------------------------
def test_approving_sends_the_order(warehouse):
    retener()
    espia = BrokerEspia()
    enviada, _ = confirm.approve("01ABCDEF", espia, current_price=100.0)
    assert enviada
    assert espia.enviadas[0].symbol == "BTC/EUR"


def test_the_client_id_is_the_same_one_the_cycle_would_have_used(warehouse):
    """Es lo que hace idempotente el reenvio. Si al confirmar se generase otro,
    una orden ya enviada por el ciclo se duplicaria."""
    retener()
    espia = BrokerEspia()
    confirm.approve("01ABCDEF", espia, current_price=100.0)
    assert espia.enviadas[0].client_order_id == "st-01ABCDEF"


def test_an_approved_order_is_marked_so_it_cannot_be_sent_twice(warehouse):
    retener()
    espia = BrokerEspia()
    confirm.approve("01ABCDEF", espia, current_price=100.0)
    enviada, motivo = confirm.approve("01ABCDEF", espia, current_price=100.0)
    assert not enviada
    assert len(espia.enviadas) == 1, "la ha enviado dos veces"


def test_a_broker_rejection_does_not_mark_it_as_sent(warehouse):
    """Marcarla como enviada tras un rechazo la haria desaparecer de la lista
    sin haberse ejecutado: una orden perdida y sin rastro de por que."""
    retener()
    enviada, motivo = confirm.approve("01ABCDEF", BrokerEspia(falla=True),
                                      current_price=100.0)
    assert not enviada
    assert "fondos" in motivo
    assert len(confirm.pending()) == 1, "ha desaparecido de la lista"


# ---------------------------------------------------------------------------
# El precio se comprueba de nuevo
# ---------------------------------------------------------------------------
def test_a_moved_price_stops_the_order(warehouse):
    """Lo que aprobaste era una orden a otro precio. Ejecutarla igual le daria
    a tu confirmacion un significado que no tenia."""
    retener(precio=100.0)
    espia = BrokerEspia()
    enviada, motivo = confirm.approve("01ABCDEF", espia, current_price=110.0,
                                      max_drift_pct=2.0)
    assert not enviada
    assert espia.enviadas == []
    assert "10.0%" in motivo


def test_a_small_move_does_not_stop_it(warehouse):
    """Si cualquier centimo la parase, en cripto no se ejecutaria nunca nada."""
    retener(precio=100.0)
    espia = BrokerEspia()
    enviada, _ = confirm.approve("01ABCDEF", espia, current_price=101.0,
                                 max_drift_pct=2.0)
    assert enviada


def test_the_drift_is_measured_in_both_directions(warehouse):
    """Un precio que ha CAIDO un 10 % tampoco es el que se aprobo: para una
    venta es peor, no mejor."""
    assert confirm.price_drift_pct(
        confirm.Pending("x", datetime.now(), None, "BTC/EUR", "sell", "close",
                        None, 6.0, 100.0, None, "", {}), 90.0
    ) == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# Rechazar
# ---------------------------------------------------------------------------
def test_rejecting_takes_it_off_the_list(warehouse):
    retener()
    confirm.reject("01ABCDEF")
    assert confirm.pending() == []


def test_a_rejected_order_is_never_sent(warehouse):
    retener()
    confirm.reject("01ABCDEF")
    espia = BrokerEspia()
    enviada, _ = confirm.approve("01ABCDEF", espia, current_price=100.0)
    assert not enviada
    assert espia.enviadas == []
