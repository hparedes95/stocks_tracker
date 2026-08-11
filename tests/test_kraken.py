"""Tests del adaptador de Kraken. Sin red, sin cuenta y sin claves reales.

Es lo que permite tener el bot de cripto montado y comprobado ANTES de que
exista la cuenta: la firma es determinista y el resto se prueba con una sesion
HTTP falsa. Cuando el usuario pegue sus claves, lo unico que no se habra
ejercitado es la red.
"""

from __future__ import annotations

import base64

import pytest

from stocks_tracker.core import secrets
from stocks_tracker.trading.brokers import kraken_auth as ka
from stocks_tracker.trading.brokers.base import (
    BrokerAuthError,
    BrokerRateLimitError,
    BrokerRejectedError,
    InsufficientFundsError,
    OrderRequest,
)
from stocks_tracker.trading.brokers.kraken import KrakenBroker, _userref

SECRETO = base64.b64encode(b"un-secreto-de-prueba-de-32-bytes").decode()


class FakeSession:
    """Sesion HTTP falsa: devuelve lo que se le diga y anota lo que recibio."""

    def __init__(self, responses: dict):
        self.responses = responses
        self.calls: list[tuple[str, dict]] = []

    def _respond(self, url, payload):
        self.calls.append((url, payload))
        for fragmento, respuesta in self.responses.items():
            if fragmento in url:
                return _Response(respuesta)
        return _Response({"error": [], "result": {}})

    def get(self, url, params=None, timeout=None):
        return self._respond(url, params or {})

    def post(self, url, data=None, headers=None, timeout=None):
        return self._respond(url, {"data": data, "headers": headers})


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


@pytest.fixture
def con_claves(monkeypatch):
    monkeypatch.setenv("KRAKEN_API_KEY", "clave-de-prueba")
    monkeypatch.setenv("KRAKEN_API_SECRET", SECRETO)
    secrets.load_env.cache_clear()
    yield
    secrets.load_env.cache_clear()


@pytest.fixture
def sin_claves(monkeypatch, tmp_path):
    monkeypatch.delenv("KRAKEN_API_KEY", raising=False)
    monkeypatch.delenv("KRAKEN_API_SECRET", raising=False)
    monkeypatch.setattr(secrets, "project_root", lambda: tmp_path)
    secrets.load_env.cache_clear()
    yield
    secrets.load_env.cache_clear()


def broker(responses: dict) -> KrakenBroker:
    b = KrakenBroker(session=FakeSession(responses))
    # Sin espera entre llamadas: el limitador se prueba aparte.
    b._last_call = -1e9
    return b


# ---------------------------------------------------------------------------
# La firma
# ---------------------------------------------------------------------------
def test_the_signature_is_deterministic():
    datos = {"nonce": 1616492376594, "pair": "XBTEUR", "type": "buy"}
    assert ka.sign("/0/private/AddOrder", datos, SECRETO) == \
           ka.sign("/0/private/AddOrder", datos, SECRETO)


def test_the_signature_depends_on_path_and_nonce():
    """Si no dependiera de los dos, una peticion firmada valdria para otra."""
    datos = {"nonce": 1, "pair": "XBTEUR"}
    base = ka.sign("/0/private/AddOrder", datos, SECRETO)
    assert base != ka.sign("/0/private/Balance", datos, SECRETO)
    assert base != ka.sign("/0/private/AddOrder", {**datos, "nonce": 2}, SECRETO)


def test_signing_without_a_nonce_is_refused():
    """Kraken responderia "Invalid key", que suena a clave equivocada y manda
    a revisar lo que no es."""
    with pytest.raises(ValueError, match="nonce"):
        ka.sign("/0/private/Balance", {"pair": "XBTEUR"}, SECRETO)


def test_the_nonce_always_grows():
    """Kraken rechaza un nonce menor o igual al ultimo usado por esa clave. Dos
    llamadas en el mismo milisegundo darian el mismo numero y el fallo seria
    intermitente y solo bajo carga."""
    valores = [ka.nonce() for _ in range(5000)]
    assert all(b > a for a, b in zip(valores, valores[1:], strict=False))


def test_the_signed_body_is_the_body_that_is_sent(con_claves):
    """Serializar dos veces produce dos cadenas distintas si algun valor cambia
    de orden, y la firma deja de valer."""
    b = broker({"Balance": {"error": [], "result": {"ZEUR": "100.0"}}})
    b.get_account()
    _, payload = b.session.calls[0]
    enviado = payload["data"]
    assert "nonce=" in enviado
    assert payload["headers"]["API-Sign"]


# ---------------------------------------------------------------------------
# Sin credenciales
# ---------------------------------------------------------------------------
def test_it_builds_without_credentials(sin_claves):
    """Poder construirlo sin claves es lo que permite montar y probar el bot
    antes de que exista la cuenta."""
    b = KrakenBroker()
    assert b.name == "kraken"


def test_public_calls_need_no_credentials(sin_claves):
    b = broker({"Ticker": {"error": [], "result": {"XXBTZEUR": {"c": ["50000.0", "1"]}}}})
    assert b.get_latest_price(["BTC/EUR"]) == {"BTC/EUR": 50000.0}


def test_a_private_call_says_which_key_is_missing(sin_claves):
    b = broker({})
    with pytest.raises(secrets.MissingCredential, match="KRAKEN_API_KEY"):
        b.get_account()


# ---------------------------------------------------------------------------
# Errores de Kraken
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("error,esperado", [
    ("EAPI:Invalid key", BrokerAuthError),
    ("EGeneral:Permission denied", BrokerAuthError),
    ("EAPI:Rate limit exceeded", BrokerRateLimitError),
    ("EOrder:Insufficient funds", InsufficientFundsError),
])
def test_kraken_errors_map_to_our_types(con_claves, error, esperado):
    """Kraken devuelve 200 con el error DENTRO del cuerpo. Mirar solo el codigo
    HTTP daria por buena una orden rechazada."""
    b = broker({"Balance": {"error": [error], "result": {}}})
    with pytest.raises(esperado):
        b.get_account()


def test_a_permission_error_is_not_confused_with_a_bad_key(con_claves):
    """"Permission denied" casi siempre significa que falta un permiso en la
    clave, no que la clave este mal. Si el bot pide saldo y sale esto, lo que
    falta es marcar 'Query Funds'."""
    b = broker({"Balance": {"error": ["EGeneral:Permission denied"], "result": {}}})
    with pytest.raises(BrokerAuthError, match="Permission denied"):
        b.get_account()


# ---------------------------------------------------------------------------
# Ordenes
# ---------------------------------------------------------------------------
def test_resending_the_same_order_does_not_duplicate_it(con_claves):
    """Idempotencia: es lo que hace seguro reintentar cuando la primera orden
    llego al broker y la respuesta no volvio."""
    ya_existe = {
        "error": [],
        "result": {"open": {"TX123": {
            "userref": _userref("st-ABC"), "descr": {"pair": "XBTEUR", "type": "buy",
                                                     "ordertype": "market"},
            "status": "open", "opentm": 0, "vol": "0.001", "vol_exec": "0",
        }}},
    }
    b = broker({"OpenOrders": ya_existe, "ClosedOrders": {"error": [], "result": {"closed": {}}}})
    orden = b.submit_order(OrderRequest(symbol="BTC/EUR", side="buy", qty=0.001,
                                        client_order_id="st-ABC"))
    assert orden.broker_order_id == "TX123"
    assert not any("AddOrder" in url for url, _ in b.session.calls), (
        "ha enviado una orden nueva teniendo ya la misma"
    )


def test_the_returned_order_keeps_our_identifier(con_claves):
    """Kraken solo guarda el hash numerico. Si al reenviar devolvieramos ese
    numero, arriba no cuadraria con `bot_orders`, que lleva el ULID, y la
    conciliacion daria por huerfana una orden que si es nuestra."""
    ya_existe = {
        "error": [],
        "result": {"open": {"TX123": {
            "userref": _userref("st-ABC"), "descr": {"pair": "XXBTZEUR", "type": "buy",
                                                     "ordertype": "market"},
            "status": "open", "opentm": 0, "vol": "0.001", "vol_exec": "0",
        }}},
    }
    b = broker({"OpenOrders": ya_existe, "ClosedOrders": {"error": [], "result": {"closed": {}}}})
    orden = b.submit_order(OrderRequest(symbol="BTC/EUR", side="buy", qty=0.001,
                                        client_order_id="st-ABC"))
    assert orden.client_order_id == "st-ABC"
    assert orden.symbol == "BTC/EUR", "devuelve el nombre interno de Kraken"


def test_the_client_id_survives_a_restart():
    """No se usa `hash()`: Python lo aleatoriza por proceso, asi que tras un
    reinicio no coincidiria y la orden se duplicaria. Es justo el fallo que la
    idempotencia debe evitar."""
    assert _userref("st-01JABCDEF") == _userref("st-01JABCDEF")
    assert _userref("st-01JABCDEF") != _userref("st-01JOTRO")
    assert 0 <= _userref("st-01JABCDEF") <= 0x7FFFFFFF, (
        "userref de Kraken solo admite enteros de 32 bits con signo"
    )


def test_an_order_by_amount_is_refused(con_claves):
    """Kraken opera por volumen, no por importe. Enviarlo mal se rechaza al
    otro lado con un mensaje que no explica nada."""
    b = broker({"OpenOrders": {"error": [], "result": {"open": {}}},
                "ClosedOrders": {"error": [], "result": {"closed": {}}}})
    with pytest.raises(BrokerRejectedError, match="importe"):
        b.submit_order(OrderRequest(symbol="BTC/EUR", side="buy", notional=10.0,
                                    client_order_id="st-X"))


# ---------------------------------------------------------------------------
# La nomenclatura antigua de Kraken
# ---------------------------------------------------------------------------
def test_kraken_asset_names_become_the_ones_we_use(con_claves):
    """Bitcoin es "XXBT" y el euro "ZEUR" en Kraken. Quitar la X y la Z a
    ciegas deja "BT", que no es ninguna moneda, y le come la inicial a LINK.
    No lanza error: el precio no aparece, la posicion se valora a cero y la
    equity sale mal en silencio."""
    b = broker({
        "Balance": {"error": [], "result": {
            "XXBT": "0.5",      # bitcoin, con el nombre antiguo
            "SOL": "3.0",       # moneda moderna, sin prefijo
            "ZEUR": "10.0",     # caja, no posicion
        }},
        "Ticker": {"error": [], "result": {
            "XXBTZEUR": {"c": ["50000.0", "1"]},
            "SOLEUR": {"c": ["100.0", "1"]},
        }},
    })
    posiciones = {p.symbol: p for p in b.get_positions()}
    assert set(posiciones) == {"BTC/EUR", "SOL/EUR"}, "el euro no es una posicion"
    assert posiciones["BTC/EUR"].market_value == pytest.approx(25000.0)
    assert posiciones["SOL/EUR"].market_value == pytest.approx(300.0)


def test_staked_balances_are_not_tradeable_positions(con_claves):
    """Los saldos con sufijo (".F", ".S") estan en earn o staking y no se
    pueden vender sin desbloquearlos. Contarlos como posicion haria creer al
    bot que puede cerrarlas, y el kill switch fallaria justo cuando importa."""
    b = broker({
        "Balance": {"error": [], "result": {"XXBT.F": "0.5", "ETH.S": "2.0"}},
        "Ticker": {"error": [], "result": {}},
    })
    assert b.get_positions() == []


def test_a_price_is_matched_by_pair_not_by_letters(sin_claves):
    """LINK no lleva prefijo: es la prueba de que el cruce se hace por par y no
    borrando caracteres."""
    b = broker({"Ticker": {"error": [], "result": {
        "LINKEUR": {"c": ["12.5", "1"]},
        "XXBTZEUR": {"c": ["50000.0", "1"]},
    }}})
    assert b.get_latest_price(["LINK/EUR", "BTC/EUR"]) == {
        "LINK/EUR": 12.5, "BTC/EUR": 50000.0,
    }


def test_an_unknown_pair_has_no_price_instead_of_a_wrong_one(sin_claves):
    """Kraken tambien cotiza pares cripto-cripto ("ETHXBT"), que no llevan
    divisa y no sabemos leer. Si todos los que no reconocemos cayeran en el
    mismo cajon, pedir uno devolveria el precio de otro: 0,05 en vez de 50.000.
    Devolver nada es peor de cara, pero el dimensionamiento usaria el numero
    equivocado como si fuera bueno."""
    b = broker({"Ticker": {"error": [], "result": {
        "ETHXBT": {"c": ["0.05", "1"]},
        "XXBTZEUR": {"c": ["50000.0", "1"]},
    }}})
    assert b.get_latest_price(["BTC/XBT"]) == {}
    assert b.get_latest_price(["INVENTADO/EUR"]) == {}
    # Los que si se reconocen siguen saliendo.
    assert b.get_latest_price(["BTC/EUR"]) == {"BTC/EUR": 50000.0}


# ---------------------------------------------------------------------------
# Cripto no es bolsa
# ---------------------------------------------------------------------------
def test_the_market_is_always_open():
    """No es un atajo: cripto no cierra. Un calendario de sesiones aqui seria
    una mentira que rompe los stops los fines de semana."""
    assert KrakenBroker().get_clock().is_open


def test_there_is_no_day_trade_limit(con_claves):
    """La regla PDT es de la bolsa de EE. UU. Aplicarla aqui bloquearia
    operaciones legitimas por un motivo que no existe."""
    b = broker({"Balance": {"error": [], "result": {"ZEUR": "100.0"}}})
    assert b.get_account().daytrade_count == 0


def test_margin_and_derivatives_are_refused():
    """Kraken los ofrece, y el mandato los prohibe: decir que no aqui es una
    barrera mas, no una descripcion."""
    b = KrakenBroker()
    assert not b.supports("margin")
    assert not b.supports("futures")
    assert b.supports("fractional")


def test_an_unknown_pair_is_refused():
    b = broker({"AssetPairs": {"error": [], "result": {
        "XXBTZEUR": {"altname": "XBTEUR", "wsname": "XBT/EUR", "ordermin": "0.0001"}}}})
    assert b.minimum_order("XBT/EUR") == 0.0001
    with pytest.raises(BrokerRejectedError, match="no conoce el par"):
        b.pair_spec("INVENTADO/EUR")
