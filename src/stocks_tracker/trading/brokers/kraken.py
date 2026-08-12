"""Adaptador de Kraken. Mismo contrato que el simulador, otro mundo.

Se construye sin credenciales a proposito: `KrakenBroker()` funciona siempre y
las llamadas publicas —pares, precios, historico— tampoco las necesitan. Solo
al pedir saldo o mandar una orden se exige la clave, y entonces el error dice
que falta y donde ponerla. Asi el bot entero se puede montar y probar antes de
que exista la cuenta.

Diferencias con una cuenta de acciones que no son detalles:

- **24/7.** No hay sesiones, ni cierre, ni regla PDT. El `get_clock()` siempre
  dice abierto, y eso es la verdad, no un atajo.
- **El minimo de orden manda.** Kraken exige un volumen minimo por par que con
  25 EUR limita fisicamente el numero de posiciones. No es un tope que hayamos
  elegido: es aritmetica.
- **Comisiones reales.** ~0,26 % por operacion en el tramo mas bajo. Con
  posiciones de 6 EUR, ida y vuelta se come el 0,5 %: por eso el mandato
  cripto pide 21 dias minimos de permanencia y como mucho dos ordenes al dia.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

import requests

from ...core import secrets
from ...core.ids import ulid
from ...core.kraken_symbols import CASH_ASSETS, canonical_asset, canonical_pair
from .base import (
    Account,
    BrokerAuthError,
    BrokerError,
    BrokerMode,
    BrokerRateLimitError,
    BrokerRejectedError,
    BrokerUnavailableError,
    Clock,
    InsufficientFundsError,
    Order,
    OrderRequest,
    Position,
)
from .kraken_auth import body, nonce, sign

_BASE = "https://api.kraken.com"
_TIMEOUT = 30

# Kraken puntua cada llamada y descuenta el contador con el tiempo. Se va muy
# por debajo del limite a proposito: este bot hace un punado de operaciones al
# mes y no gana nada apurando, mientras que un bloqueo temporal en mitad de un
# ciclo deja ordenes a medio conciliar.
_MIN_SECONDS_BETWEEN_CALLS = 1.0

# Errores de Kraken que significan algo concreto para nosotros. El resto se
# envuelve en BrokerError con su texto tal cual.
_ERROR_MAP = {
    "EAPI:Invalid key": BrokerAuthError,
    "EAPI:Invalid signature": BrokerAuthError,
    "EAPI:Invalid nonce": BrokerAuthError,
    "EGeneral:Permission denied": BrokerAuthError,
    "EAPI:Rate limit exceeded": BrokerRateLimitError,
    "EOrder:Rate limit exceeded": BrokerRateLimitError,
    "EService:Unavailable": BrokerUnavailableError,
    "EService:Busy": BrokerUnavailableError,
    "EOrder:Insufficient funds": InsufficientFundsError,
}

# La tabla de nombres de Kraken vive en `core.kraken_symbols`: la necesitan
# tambien la descarga del historico y el proveedor de precios, y una segunda
# copia seria la forma de que este fallo —ya corregido una vez— volviera por
# un lado mientras sigue arreglado por el otro.
_canonical_asset = canonical_asset
_canonical_pair = canonical_pair
_CASH_ASSETS = CASH_ASSETS


@dataclass
class KrakenBroker:
    """Implementa `BrokerAdapter` contra la API REST de Kraken."""

    mode: BrokerMode = BrokerMode.LIVE
    name: str = "kraken"
    session: requests.Session = field(default_factory=requests.Session)
    _last_call: float = 0.0
    _pairs: dict | None = None

    # ------------------------------------------------------------------
    # Transporte
    # ------------------------------------------------------------------
    def _throttle(self) -> None:
        espera = _MIN_SECONDS_BETWEEN_CALLS - (time.monotonic() - self._last_call)
        if espera > 0:
            time.sleep(espera)
        self._last_call = time.monotonic()

    def _check(self, payload: dict) -> dict:
        """Kraken devuelve 200 con los errores dentro del cuerpo.

        Mirar solo el codigo HTTP daria por buena una orden rechazada, que es
        de los pocos fallos capaces de descuadrar la contabilidad sin avisar.
        """
        errores = payload.get("error") or []
        if errores:
            primero = str(errores[0])
            for prefijo, excepcion in _ERROR_MAP.items():
                if primero.startswith(prefijo):
                    raise excepcion(primero)
            raise BrokerError("; ".join(str(e) for e in errores))
        return payload.get("result") or {}

    def _public(self, method: str, params: dict | None = None) -> dict:
        self._throttle()
        try:
            response = self.session.get(
                f"{_BASE}/0/public/{method}", params=params or {}, timeout=_TIMEOUT
            )
        except requests.RequestException as exc:
            raise BrokerUnavailableError(f"Kraken no responde: {exc}") from exc
        return self._check(response.json())

    def _private(self, method: str, data: dict | None = None) -> dict:
        key = secrets.get("KRAKEN_API_KEY")
        secret = secrets.get("KRAKEN_API_SECRET")

        path = f"/0/private/{method}"
        payload = {"nonce": nonce(), **(data or {})}
        headers = {
            "API-Key": key,
            "API-Sign": sign(path, payload, secret),
            "Content-Type": "application/x-www-form-urlencoded",
        }

        self._throttle()
        try:
            response = self.session.post(
                f"{_BASE}{path}", data=body(payload), headers=headers,
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise BrokerUnavailableError(f"Kraken no responde: {exc}") from exc
        return self._check(response.json())

    # ------------------------------------------------------------------
    # Mercado (publico: no necesita credenciales)
    # ------------------------------------------------------------------
    def pairs(self) -> dict:
        if self._pairs is None:
            self._pairs = self._public("AssetPairs")
        return self._pairs

    def pair_spec(self, symbol: str) -> dict:
        """Minimos y decimales de un par. Sin esto, una orden se rechaza por
        redondeo y el mensaje no dice cual de los dos limites fallo."""
        wanted = symbol.replace("/", "")
        for key, spec in self.pairs().items():
            if key == wanted or spec.get("altname") == wanted or spec.get("wsname") == symbol:
                return spec
        raise BrokerRejectedError(f"Kraken no conoce el par {symbol}")

    def get_latest_price(self, symbols: list[str]) -> dict[str, float]:
        if not symbols:
            return {}
        nombres = ",".join(s.replace("/", "") for s in symbols)
        result = self._public("Ticker", {"pair": nombres})

        # Kraken responde con SUS nombres ("XXBTZEUR"), no con los que se
        # pidieron, asi que se cruzan los dos por su forma canonica.
        por_par = {}
        for key, datos in result.items():
            canonico = _canonical_pair(key)
            if canonico:
                por_par[canonico] = datos

        out: dict[str, float] = {}
        for symbol in symbols:
            datos = por_par.get(_canonical_pair(symbol))
            if datos:
                out[symbol] = float(datos["c"][0])   # 'c' = ultimo cierre
        return out

    def ohlc(self, symbol: str, interval_minutes: int = 1440) -> list[dict]:
        """Velas para calcular indicadores. Publico y gratuito: es lo que
        permite validar la estrategia sin tener cuenta."""
        result = self._public(
            "OHLC", {"pair": symbol.replace("/", ""), "interval": interval_minutes}
        )
        for key, filas in result.items():
            if key == "last":
                continue
            return [
                {"date": datetime.fromtimestamp(int(f[0])).date(),
                 "open": float(f[1]), "high": float(f[2]), "low": float(f[3]),
                 "close": float(f[4]), "volume": float(f[6])}
                for f in filas
            ]
        return []

    def get_clock(self) -> Clock:
        """Siempre abierto. No es un atajo: cripto no cierra."""
        ahora = datetime.now()
        return Clock(timestamp=ahora, is_open=True,
                     next_open=ahora, next_close=ahora + timedelta(days=365),
                     session_date=ahora.date())

    # ------------------------------------------------------------------
    # Cuenta (privado: exige credenciales)
    # ------------------------------------------------------------------
    def get_account(self) -> Account:
        balance = self._private("Balance")
        moneda = "ZEUR"
        cash = float(balance.get(moneda, 0.0))
        equity = cash + sum(
            valor for clave, valor in self._market_values().items() if clave
        )
        return Account(
            account_id="kraken", currency="EUR", cash=cash, equity=equity,
            buying_power=cash, last_equity=equity,
            # La regla PDT es de la bolsa de EE. UU.: aqui no existe.
            daytrade_count=0, pattern_day_trader=False,
            trading_blocked=False, account_blocked=False, shorting_enabled=False,
        )

    def _market_values(self) -> dict[str, float]:
        posiciones = self.get_positions()
        return {p.symbol: p.market_value for p in posiciones}

    def get_positions(self) -> list[Position]:
        """En spot no hay 'posiciones': hay saldos. Se traducen al mismo tipo
        para que el resto del bot no tenga que saber en que venue esta."""
        balance = self._private("Balance")

        activos: dict[str, float] = {}
        for clave, valor in balance.items():
            # Los saldos con sufijo (".F", ".S") estan en earn o staking: no se
            # pueden vender sin desbloquearlos antes, asi que contarlos como
            # posicion haria creer al bot que puede cerrarlas.
            if "." in clave:
                continue
            cantidad = float(valor)
            if cantidad <= 0:
                continue
            activo = _canonical_asset(clave)
            if activo in _CASH_ASSETS:
                continue
            activos[activo] = activos.get(activo, 0.0) + cantidad

        if not activos:
            return []

        simbolos = [f"{a}/EUR" for a in activos]
        precios = self.get_latest_price(simbolos)

        out = []
        for activo, cantidad in activos.items():
            symbol = f"{activo}/EUR"
            precio = precios.get(symbol, 0.0)
            valor = cantidad * precio
            out.append(
                Position(
                    symbol=symbol, qty=cantidad,
                    # Kraken no guarda el precio medio de entrada: se lleva en
                    # `bot_positions`, que es nuestro. Aqui se deja el actual y
                    # el P&L real lo calcula la capa de arriba.
                    avg_entry_price=precio, market_value=valor,
                    unrealized_pl=0.0, unrealized_plpc=0.0, current_price=precio,
                )
            )
        return out

    def get_position(self, symbol: str) -> Position | None:
        return next((p for p in self.get_positions() if p.symbol == symbol), None)

    # ------------------------------------------------------------------
    # Ordenes
    # ------------------------------------------------------------------
    def get_orders(self, status: str = "open") -> list[Order]:
        if status == "open":
            result = self._private("OpenOrders")
            crudas = result.get("open") or {}
        else:
            result = self._private("ClosedOrders")
            crudas = result.get("closed") or {}
        return [self._to_order(txid, datos) for txid, datos in crudas.items()]

    def get_order_by_client_id(self, client_order_id: str) -> Order | None:
        """Kraken no busca por identificador propio, asi que se recorre.

        La comparacion se hace sobre `_userref(...)`, no sobre el identificador
        tal cual: Kraken nunca devuelve el ULID porque nunca lo recibio —solo
        guarda el entero de 32 bits que se le mando—. Comparar las cadenas
        directamente no coincide jamas, y eso convierte la idempotencia en
        adorno: cada reintento tras un corte de red mandaria una orden mas.
        """
        referencia = str(_userref(client_order_id))
        for order in self.get_orders("open") + self.get_orders("closed"):
            if order.client_order_id == referencia:
                return order
        return None

    def _to_order(self, txid: str, datos: dict) -> Order:
        descr = datos.get("descr") or {}
        par = str(descr.get("pair", ""))
        return Order(
            broker_order_id=txid,
            # Kraken solo guarda el entero, no el ULID. Se conserva tal cual y
            # `get_order_by_client_id` compara contra el mismo hash.
            client_order_id=str(datos.get("userref") or ""),
            symbol=_canonical_pair(par) or par,
            side=str(descr.get("type", "")),
            order_type=str(descr.get("ordertype", "")),
            tif="gtc",
            status=str(datos.get("status", "")),
            submitted_at=datetime.fromtimestamp(float(datos.get("opentm", 0))),
            qty=float(datos.get("vol", 0) or 0),
            filled_qty=float(datos.get("vol_exec", 0) or 0),
            filled_avg_price=float(datos.get("price", 0) or 0) or None,
            reject_reason=datos.get("reason"),
        )

    def submit_order(self, req: OrderRequest) -> Order:
        """Envia una orden REAL. Comprueba antes por identificador propio.

        Reintentar sin comprobar es como se duplica una orden despues de un
        corte de red: la primera llego al broker y la respuesta no volvio.
        """
        # Kraken spot NO tiene entorno de pruebas: esta clase habla siempre con
        # el mercado de verdad. El campo `mode` existia y no se miraba, asi que
        # pedir modo papel devolvia este mismo adaptador y las ordenes salian
        # con dinero real mientras el usuario creia estar probando. Es el peor
        # fallo posible de todo el programa, y por eso la comprobacion esta en
        # el metodo que gasta y no en quien lo construye.
        if self.mode is not BrokerMode.LIVE:
            raise BrokerRejectedError(
                f"Este adaptador opera con dinero real y se ha pedido en modo "
                f"'{self.mode}'. Kraken spot no tiene entorno de pruebas: para "
                "modo papel se usa `PaperBroker`, que lee precios reales de "
                "Kraken y simula la ejecucion sin mandar nada."
            )

        existente = self.get_order_by_client_id(req.client_order_id)
        if existente is not None:
            # Se devuelve con el identificador original: Kraken solo guarda el
            # hash, y arriba se concilia contra `bot_orders`, que lleva el ULID.
            return replace(existente, client_order_id=req.client_order_id)

        if req.side not in ("buy", "sell"):
            raise BrokerRejectedError(f"Lado desconocido: {req.side}")
        if req.notional is not None and req.qty is None:
            raise BrokerRejectedError(
                "Kraken no acepta ordenes por importe: hay que convertir a "
                "volumen con el precio actual antes de enviar."
            )

        datos = {
            "pair": req.symbol.replace("/", ""),
            "type": req.side,
            "ordertype": req.order_type,
            "volume": str(req.qty),
            # `userref` de Kraken solo admite enteros de 32 bits con signo y
            # nuestro identificador es un ULID de 26 caracteres. Se manda un
            # hash estable: sirve para reconocer la orden al reintentar, que es
            # para lo unico que se usa.
            "userref": _userref(req.client_order_id),
        }
        if req.order_type == "limit" and req.limit_price is not None:
            datos["price"] = str(req.limit_price)

        result = self._private("AddOrder", datos)
        txids = result.get("txid") or []
        return Order(
            broker_order_id=txids[0] if txids else ulid(),
            client_order_id=req.client_order_id, symbol=req.symbol,
            side=req.side, order_type=req.order_type, tif=req.tif,
            status="accepted", submitted_at=datetime.now(), qty=req.qty,
        )

    def cancel_order(self, broker_order_id: str) -> None:
        self._private("CancelOrder", {"txid": broker_order_id})

    def cancel_all_orders(self) -> int:
        result = self._private("CancelAll")
        return int(result.get("count", 0))

    def close_position(self, symbol: str, qty: float | None = None) -> Order:
        posicion = self.get_position(symbol)
        if posicion is None or posicion.qty <= 0:
            raise BrokerRejectedError(f"{symbol}: no hay posicion que cerrar")
        cantidad = posicion.qty if qty is None else min(qty, posicion.qty)
        return self.submit_order(
            OrderRequest(symbol=symbol, side="sell", qty=cantidad,
                         client_order_id=f"close-{ulid()}")
        )

    def close_all_positions(self, cancel_orders: bool = True) -> list[Order]:
        if cancel_orders:
            self.cancel_all_orders()
        return [self.close_position(p.symbol) for p in self.get_positions()]

    # ------------------------------------------------------------------
    def is_fractionable(self, symbol: str) -> bool:
        """En cripto todo es fraccionable; lo que manda es el minimo del par."""
        return True

    def minimum_order(self, symbol: str) -> float:
        spec = self.pair_spec(symbol)
        return float(spec.get("ordermin", 0.0) or 0.0)

    def supports(self, feature: str) -> bool:
        # Ni margen ni derivados: el mandato los prohibe y Kraken los ofrece,
        # asi que decir que no aqui es una barrera mas, no una descripcion.
        return feature in {"fractional", "limit", "stop", "market"}


def _userref(client_order_id: str) -> int:
    """ULID -> entero de 32 bits con signo, estable entre ejecuciones.

    No se usa `hash()`: Python lo aleatoriza por proceso, asi que al reintentar
    tras un reinicio no coincidiria y la orden se duplicaria. Ese es
    exactamente el fallo que la idempotencia tiene que evitar.
    """
    digest = hashlib.blake2s(client_order_id.encode(), digest_size=4).digest()
    return int.from_bytes(digest, "big") & 0x7FFFFFFF
