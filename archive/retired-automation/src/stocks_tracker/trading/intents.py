"""Vocabulario del bot: intencion, veredicto y orden aprobada.

La pieza importante de este modulo es `ApprovedOrder`, y lo importante de
`ApprovedOrder` es que **no se puede construir fuera de `risk.py`**.

El razonamiento: `execution.py` solo acepta `ApprovedOrder`. Si cualquier
modulo pudiera fabricar una, bastaria un descuido —una estrategia nueva, un
script de pruebas que se queda en el repositorio, un atajo un viernes por la
tarde— para que una orden llegase al broker sin pasar por los limites. Con la
llave de acunacion, ese descuido no compila: falla en el acto y con un mensaje
que dice por que.

No es una barrera criptografica; en Python nada lo es. Es una barrera contra el
error, que es el riesgo real aqui.
"""

from __future__ import annotations

from dataclasses import InitVar, dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from ..core.ids import ulid


class BypassError(RuntimeError):
    """Alguien ha intentado crear una orden aprobada sin pasar por el riesgo."""


# Llave de acunacion. Solo `risk.py` la importa; hay un test que lo comprueba
# recorriendo el AST de todo `src/`.
_MINT = object()


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"


class IntentType(StrEnum):
    OPEN = "open"
    ADD = "add"
    TRIM = "trim"
    CLOSE = "close"
    STOP_EXIT = "stop_exit"
    REBALANCE = "rebalance"


class Decision(StrEnum):
    APPROVE = "APPROVE"
    RESIZE = "RESIZE"
    VETO = "VETO"


@dataclass(frozen=True)
class Intent:
    """Lo que una estrategia querria hacer, ignorando deliberadamente los limites.

    Separar "que quiero" de "que me puedo permitir" es lo que permite que el
    riesgo sea un unico punto de aplicacion. Una estrategia que ya se
    autocensura produce vetos invisibles que nadie puede auditar.
    """

    ticker: str
    side: Side
    intent_type: IntentType
    ref_price: float
    strategy_id: str
    intent_id: str = field(default_factory=ulid)
    created_at: datetime = field(default_factory=datetime.now)
    qty_requested: float | None = None
    notional_requested: float | None = None
    stop_price: float | None = None
    stop_atr_mult: float | None = None
    risk_amount: float | None = None
    score_pctile: float | None = None
    regime: str | None = None
    rationale: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.ref_price <= 0:
            raise ValueError(f"{self.ticker}: precio de referencia no positivo")
        if self.qty_requested is None and self.notional_requested is None:
            raise ValueError(f"{self.ticker}: la intencion no dice cuanto")
        for name in ("qty_requested", "notional_requested"):
            value = getattr(self, name)
            if value is not None and value < 0:
                # Una cantidad negativa seria una posicion corta escrita al
                # reves. El mandato prohibe ponerse corto, y prefiero que
                # reviente aqui a que `risk.py` tenga que adivinar la intencion.
                raise ValueError(f"{self.ticker}: {name} negativo ({value})")

    @property
    def is_exit(self) -> bool:
        return self.intent_type in (IntentType.CLOSE, IntentType.TRIM,
                                    IntentType.STOP_EXIT)

    @property
    def is_protective(self) -> bool:
        """Un cierre por stop se ejecuta incluso con el bot en HALT_NEW.

        Parar de abrir no puede implicar dejar de protegerse: seria empeorar la
        situacion justo cuando el sistema ha decidido que algo va mal.
        """
        return self.intent_type is IntentType.STOP_EXIT


@dataclass(frozen=True)
class RiskVerdict:
    """Respuesta del riesgo a una intencion, con el porque siempre relleno."""

    intent: Intent
    decision: Decision
    rule_id: str
    reason_code: str
    reason_text: str
    qty_approved: float | None = None
    notional_approved: float | None = None
    stop_price: float | None = None
    risk_amount: float | None = None
    observed: float | None = None
    limit_value: float | None = None
    notes: dict[str, Any] = field(default_factory=dict)

    @property
    def approved(self) -> bool:
        return self.decision in (Decision.APPROVE, Decision.RESIZE)

    def to_order(self) -> ApprovedOrder:
        """Solo tiene sentido llamarla desde `risk.py`; la llave lo garantiza."""
        raise BypassError(
            "RiskVerdict.to_order no acuna ordenes: la unica via es "
            "RiskManager, que es quien tiene la llave."
        )


@dataclass(frozen=True)
class ApprovedOrder:
    """Orden que ya ha pasado por los limites. Unico tipo que acepta la ejecucion.

    `mint` es la llave: sin ella el constructor lanza `BypassError`. Ver la
    explicacion de arriba.
    """

    mint: InitVar[object]
    intent_id: str
    ticker: str
    side: Side
    intent_type: IntentType
    ref_price: float
    rule_notes: dict[str, Any] = field(default_factory=dict)
    qty: float | None = None
    notional: float | None = None
    stop_price: float | None = None
    risk_amount: float | None = None
    client_order_id: str = ""

    def __post_init__(self, mint: object) -> None:
        if mint is not _MINT:
            raise BypassError(
                "ApprovedOrder solo puede crearla RiskManager. Si necesitas "
                "una orden en un test, pasala por el riesgo: saltarselo aqui "
                "es saltarselo tambien en produccion."
            )
        if self.qty is None and self.notional is None:
            raise ValueError(f"{self.ticker}: orden aprobada sin tamano")
        if not self.client_order_id:
            # Determinista a proposito: es lo que hace idempotente el reenvio.
            # Si tras enviar la orden el proceso muere antes de anotarla, al
            # reintentar el broker reconoce el mismo identificador y devuelve
            # la orden existente en lugar de crear una segunda.
            object.__setattr__(self, "client_order_id", f"st-{self.intent_id}")
