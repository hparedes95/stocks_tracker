"""Lector publico de Polymarket. Sin wallet, sin clave, sin firmar nada.

Esta es la mitad del venue que se puede tener terminada y comprobada antes de
tocar dinero, y la que decide si merece la pena tocarlo. Leer mercados,
precios e historico no exige credenciales; solo enviar ordenes las exige.

**Por que este fichero va primero.** En un mercado de prediccion el precio ES
la probabilidad: un contrato a 0,30 no esta "barato", es que el mercado cree
que pasa un 30 % de las veces. Si esa cifra esta bien calibrada —de cada cien
contratos a 0,30, treinta acaban valiendo 1— comprar barato no tiene ninguna
ventaja: se gana el 30 % de las veces y se pierde el 70 %, y sale a cero antes
de restar la horquilla. La unica pregunta que importa es si el precio se
desvia de la frecuencia real, y para responderla hacen falta mercados ya
resueltos. Eso es lo que lee este modulo, y por eso no necesita cuenta.

Tres cosas de la API que rompen el estudio en silencio si no se tratan:

1. **`outcomes`, `outcomePrices` y `clobTokenIds` llegan como cadenas JSON**,
   no como listas: `'["Yes", "No"]'`. Usarlas tal cual da una cadena de 14
   caracteres donde se esperaba una lista de dos.
2. **Cerrado no es resuelto.** Un mercado anulado queda `closed` con los dos
   precios a 0,5. Contarlo como un "no" seria inventar una perdida que nunca
   ocurrio y sesgar la calibracion hacia abajo.
3. **Los numeros vienen a veces como texto.** `"0.51"` y `0.51` conviven en el
   mismo campo segun el endpoint.

No se ha podido contrastar contra la API real desde aqui: la politica de red
de este entorno bloquea polymarket.com igual que bloquea Kraken. Por eso el
mapeo de campos esta aislado en `_market_from_gamma` y hay un diagnostico
—`python -m stocks_tracker.trading.brokers.polymarket_public`— que se ejecuta
en el equipo del usuario y dice que campo no cuadra, en vez de fallar con un
KeyError a mitad de un ciclo.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

import requests

from ...core.textutils import as_float, as_text

_GAMMA = "https://gamma-api.polymarket.com"
_CLOB = "https://clob.polymarket.com"
_TIMEOUT = 30
_MIN_SECONDS_BETWEEN_CALLS = 0.5

# Gamma pagina de 100 en 100 y no avisa de que hay mas: devuelve una lista
# corta y ya. El tope existe para que un filtro mal puesto no se traiga el
# mercado entero en un bucle infinito.
_PAGE = 100
_MAX_PAGES = 50


class PolymarketError(RuntimeError):
    """Fallo leyendo Polymarket. Nunca se traga: sin datos no hay estudio."""


@dataclass(frozen=True)
class PredictionMarket:
    """Un mercado binario, ya normalizado.

    Solo se construye desde `_market_from_gamma`, que es el unico sitio que
    conoce los nombres de campo de la API. Si Polymarket los cambia, falla ahi
    y no en veinte sitios distintos.
    """

    market_id: str
    question: str
    slug: str
    condition_id: str
    outcomes: tuple[str, ...]
    prices: tuple[float, ...]
    token_ids: tuple[str, ...]
    end_date: datetime | None
    liquidity: float
    volume: float
    spread: float
    closed: bool
    active: bool

    @property
    def is_binary(self) -> bool:
        return len(self.outcomes) == 2 and len(self.prices) == 2

    @property
    def yes_price(self) -> float:
        """Precio del "si", que es la probabilidad implicita.

        Se busca por nombre y no por posicion: el orden no esta garantizado, y
        confundirlo invierte el estudio entero —los aciertos pasarian a contar
        como fallos y la conclusion saldria del reves—.
        """
        for nombre, precio in zip(self.outcomes, self.prices, strict=False):
            if nombre.strip().lower() in ("yes", "si", "sí", "true"):
                return precio
        return self.prices[0] if self.prices else 0.0

    @property
    def resolved_outcome(self) -> str:
        """Que gano, o cadena vacia si no se puede afirmar.

        Un mercado resuelto deja sus precios en 1 y 0. Cualquier otra cosa
        —anulado, en disputa, aun abierto— devuelve vacio a proposito: es
        preferible descartar un caso a meter uno inventado en la muestra.
        """
        if not self.closed or not self.is_binary:
            return ""
        for nombre, precio in zip(self.outcomes, self.prices, strict=False):
            if precio >= 0.99:
                return nombre
        return ""

    @property
    def is_resolved(self) -> bool:
        return bool(self.resolved_outcome)

    @property
    def is_void(self) -> bool:
        """Cerrado pero sin ganador: anulado o en disputa.

        Se distingue de "no resuelto todavia" porque en el estudio hay que
        contarlo aparte. Meterlo como perdida sesga la calibracion hacia abajo
        e inventa un mal resultado que no ocurrio.
        """
        return self.closed and not self.is_resolved

    def days_to_resolution(self, now: datetime | None = None) -> float:
        if self.end_date is None:
            return float("inf")
        ahora = now or datetime.now(UTC)
        if self.end_date.tzinfo is None:
            ahora = ahora.replace(tzinfo=None)
        return (self.end_date - ahora).total_seconds() / 86400.0


def _json_list(value: object) -> tuple[str, ...]:
    """Campo que llega como cadena JSON: `'["Yes","No"]'` -> `("Yes","No")`.

    Es el detalle que mas silenciosamente rompe: una cadena tambien se puede
    recorrer, asi que `for o in market["outcomes"]` no lanza nada — itera
    caracteres. El estudio saldria con catorce "outcomes" de un caracter.
    """
    if isinstance(value, (list, tuple)):
        return tuple(as_text(v) for v in value)
    texto = as_text(value)
    if not texto:
        return ()
    try:
        cargado = json.loads(texto)
    except (ValueError, TypeError):
        return ()
    if isinstance(cargado, list):
        return tuple(as_text(v) for v in cargado)
    return ()


def _json_floats(value: object) -> tuple[float, ...]:
    return tuple(as_float(v) for v in _json_list(value))


def _parse_date(value: object) -> datetime | None:
    texto = as_text(value)
    if not texto:
        return None
    try:
        return datetime.fromisoformat(texto.replace("Z", "+00:00"))
    except ValueError:
        return None


def _market_from_gamma(raw: dict) -> PredictionMarket:
    """Unico sitio que conoce los nombres de campo de Gamma."""
    return PredictionMarket(
        market_id=as_text(raw.get("id")),
        question=as_text(raw.get("question")),
        slug=as_text(raw.get("slug")),
        condition_id=as_text(raw.get("conditionId")),
        outcomes=_json_list(raw.get("outcomes")),
        prices=_json_floats(raw.get("outcomePrices")),
        token_ids=_json_list(raw.get("clobTokenIds")),
        end_date=_parse_date(raw.get("endDate")),
        liquidity=as_float(raw.get("liquidityNum", raw.get("liquidity"))),
        volume=as_float(raw.get("volumeNum", raw.get("volume"))),
        spread=as_float(raw.get("spread")),
        closed=bool(raw.get("closed")),
        active=bool(raw.get("active")),
    )


@dataclass
class PolymarketPublic:
    """Lectura publica de Polymarket. No firma nada y no puede gastar."""

    session: requests.Session = field(default_factory=requests.Session)
    _last_call: float = 0.0

    # ------------------------------------------------------------------
    def _throttle(self) -> None:
        espera = _MIN_SECONDS_BETWEEN_CALLS - (time.monotonic() - self._last_call)
        if espera > 0:
            time.sleep(espera)
        self._last_call = time.monotonic()

    def _get(self, url: str, params: dict | None = None) -> object:
        self._throttle()
        try:
            response = self.session.get(url, params=params or {}, timeout=_TIMEOUT)
        except requests.RequestException as exc:
            raise PolymarketError(f"Polymarket no responde: {exc}") from exc
        try:
            return response.json()
        except ValueError as exc:
            raise PolymarketError(
                f"Polymarket ha devuelto algo que no es JSON ({url})"
            ) from exc

    # ------------------------------------------------------------------
    # Mercados
    # ------------------------------------------------------------------
    def markets(
        self,
        *,
        closed: bool | None = None,
        min_liquidity: float = 0.0,
        min_volume: float = 0.0,
        limit: int = _PAGE,
        only_binary: bool = True,
    ) -> list[PredictionMarket]:
        """Lista mercados, paginando hasta juntar `limit`.

        `closed=None` no filtra; `False` son los abiertos y `True` el
        historico, que es lo que alimenta el estudio de calibracion.
        """
        params: dict[str, object] = {"limit": _PAGE}
        if closed is not None:
            params["closed"] = str(bool(closed)).lower()
        if min_liquidity > 0:
            params["liquidity_num_min"] = min_liquidity
        if min_volume > 0:
            params["volume_num_min"] = min_volume

        out: list[PredictionMarket] = []
        for pagina in range(_MAX_PAGES):
            datos = self._get(f"{_GAMMA}/markets", {**params, "offset": pagina * _PAGE})
            if not isinstance(datos, list):
                raise PolymarketError(
                    "La lista de mercados no ha venido como lista. Puede que "
                    "la API haya cambiado: revisa `_market_from_gamma`."
                )
            if not datos:
                break
            for raw in datos:
                if not isinstance(raw, dict):
                    continue
                mercado = _market_from_gamma(raw)
                if only_binary and not mercado.is_binary:
                    continue
                out.append(mercado)
                if len(out) >= limit:
                    return out
            if len(datos) < _PAGE:
                break
        return out

    def resolved_markets(
        self, *, min_volume: float = 1000.0, limit: int = 500
    ) -> list[PredictionMarket]:
        """Mercados ya resueltos, que son la muestra del estudio.

        Se descartan los anulados: `resolved_outcome` solo devuelve algo
        cuando hay ganador claro. Se filtra por volumen porque un mercado sin
        apenas dinero no tiene un precio que signifique nada, y meterlo en la
        muestra mide ruido.
        """
        candidatos = self.markets(closed=True, min_volume=min_volume, limit=limit * 2)
        return [m for m in candidatos if m.is_resolved][:limit]

    def market(self, market_id: str) -> PredictionMarket:
        datos = self._get(f"{_GAMMA}/markets/{market_id}")
        if not isinstance(datos, dict):
            raise PolymarketError(f"Mercado {market_id} no encontrado")
        return _market_from_gamma(datos)

    # ------------------------------------------------------------------
    # Precios
    # ------------------------------------------------------------------
    def price_history(
        self, token_id: str, *, interval: str = "max", fidelity_minutes: int = 60
    ) -> list[tuple[datetime, float]]:
        """Historico de precio de un contrato.

        Es lo que permite preguntar "que decia el mercado una semana antes",
        que es la unica forma honesta de medir calibracion: el precio del
        ultimo dia ya incorpora el resultado y mediria una obviedad.
        """
        datos = self._get(
            f"{_CLOB}/prices-history",
            {"market": token_id, "interval": interval, "fidelity": fidelity_minutes},
        )
        historia = datos.get("history") if isinstance(datos, dict) else None
        if not isinstance(historia, list):
            return []
        out = []
        for punto in historia:
            if not isinstance(punto, dict):
                continue
            marca = as_float(punto.get("t"))
            precio = as_float(punto.get("p"))
            if marca > 0:
                out.append((datetime.fromtimestamp(marca, UTC), precio))
        return out

    def price_at(
        self, token_id: str, when: datetime, *, history: list | None = None
    ) -> float | None:
        """Precio vigente en un momento dado. `None` si no hay dato anterior.

        Coge el ultimo punto ANTERIOR a `when`, nunca el mas cercano: el mas
        cercano puede ser posterior, y eso es mirar el futuro. En un mercado
        de prediccion ese error no se nota en las metricas —salen mejores— y
        es exactamente lo que hace creer que hay ventaja donde no la hay.
        """
        puntos = history if history is not None else self.price_history(token_id)
        anteriores = [p for t, p in puntos if t <= when]
        return anteriores[-1] if anteriores else None

    def book(self, token_id: str) -> dict:
        """Libro de ordenes. Sin libro no hay salida, y en Polymarket la
        horquilla se come la ventaja antes que cualquier otra cosa."""
        datos = self._get(f"{_CLOB}/book", {"token_id": token_id})
        return datos if isinstance(datos, dict) else {}


# ---------------------------------------------------------------------------
def diagnose(reader: PolymarketPublic | None = None) -> tuple[bool, list[str]]:
    """Comprueba contra la API real que el mapeo de campos sigue valiendo.

    Existe porque este modulo se escribio sin poder llamar a Polymarket: la
    red de desarrollo lo bloquea. Esto se ejecuta en el equipo del usuario y
    dice que campo falta, en vez de reventar a mitad de un ciclo con un
    mercado a precio cero que parecia una ganga.
    """
    reader = reader or PolymarketPublic()
    problemas: list[str] = []

    try:
        abiertos = reader.markets(closed=False, limit=5)
    except PolymarketError as exc:
        return False, [f"No se ha podido leer la lista de mercados: {exc}"]

    if not abiertos:
        problemas.append("La API no ha devuelto ningun mercado abierto")

    for m in abiertos:
        if not m.question:
            problemas.append(f"Mercado {m.market_id}: sin pregunta (campo 'question')")
        if len(m.outcomes) != 2:
            problemas.append(
                f"Mercado {m.market_id}: 'outcomes' ha dado {len(m.outcomes)} "
                "valores en vez de 2 (¿ha dejado de venir como cadena JSON?)"
            )
        if not m.token_ids:
            problemas.append(
                f"Mercado {m.market_id}: sin 'clobTokenIds', no se puede pedir "
                "el historico de precio"
            )
        if not 0.0 < m.yes_price < 1.0:
            problemas.append(
                f"Mercado {m.market_id}: precio {m.yes_price} fuera de (0,1); "
                "en un mercado abierto eso no deberia pasar"
            )

    try:
        resueltos = reader.resolved_markets(limit=5)
        if not resueltos:
            problemas.append(
                "No ha salido ningun mercado resuelto: sin ellos no se puede "
                "medir la calibracion"
            )
        elif resueltos[0].token_ids:
            historia = reader.price_history(resueltos[0].token_ids[0])
            if not historia:
                problemas.append(
                    "El historico de precios ha venido vacio: sin el no se "
                    "puede saber que decia el mercado antes de resolverse"
                )
    except PolymarketError as exc:
        problemas.append(f"Historico no disponible: {exc}")

    return not problemas, problemas


def main() -> int:
    """`python -m stocks_tracker.trading.brokers.polymarket_public`"""
    print()
    print("  Comprobacion de la lectura publica de Polymarket")
    print("  " + "=" * 62)
    print("  No hace falta wallet ni clave: esto solo lee.")
    print()

    ok, problemas = diagnose()
    if ok:
        print("  Todo cuadra: los campos de la API son los esperados.")
    else:
        print("  Hay que revisar esto:")
        for p in problemas:
            print(f"    - {p}")
    print()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
