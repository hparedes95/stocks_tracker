"""Kill switch: estados, disparo y rearme.

Asimetria deliberada: **parar es facil y automatico; volver a arrancar es
incomodo y solo lo hace una persona desde la consola.** Un sistema que se
rearma solo no tiene kill switch, tiene una pausa.

| Estado     | Que hace                                                        |
|------------|-----------------------------------------------------------------|
| `RUNNING`  | Operativa normal                                                |
| `HALT_NEW` | Deja de abrir y ampliar. **Sigue ejecutando cierres de stop.**   |
| `HALTED`   | Tras liquidar todo. No vuelve a operar hasta rearme manual.     |

Que `HALT_NEW` no bloquee los cierres de proteccion es intencionado: dejar de
protegerse justo cuando el sistema ha decidido que algo va mal seria empeorar
la situacion en el peor momento.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from ..core.config import get_trading_config
from ..core.db import connect


class State(StrEnum):
    RUNNING = "RUNNING"
    HALT_NEW = "HALT_NEW"
    FLATTEN_PENDING = "FLATTEN_PENDING"
    HALTED = "HALTED"


@dataclass(frozen=True)
class BotState:
    mode: str
    state: State
    autonomy: str = "semi"
    halted_at: datetime | None = None
    halt_rule: str = ""
    halt_detail: str = ""
    peak_equity: float = 0.0
    day_start_equity: float = 0.0
    day_start_date: object = None

    @property
    def can_open(self) -> bool:
        return self.state is State.RUNNING

    @property
    def can_protect(self) -> bool:
        """Cerrar por stop se permite en todo salvo la liquidacion ya hecha."""
        return self.state in (State.RUNNING, State.HALT_NEW, State.FLATTEN_PENDING)


def read_state(mode: str = "simulated") -> BotState:
    with connect(read_only=True) as conn:
        row = conn.execute(
            "SELECT mode, state, autonomy, halted_at, halt_rule, halt_detail, "
            "peak_equity, day_start_equity, day_start_date "
            "FROM bot_state WHERE mode = ?",
            [mode],
        ).fetchone()
    if row is None:
        return BotState(mode=mode, state=State.RUNNING)
    return BotState(
        mode=row[0], state=State(row[1] or "RUNNING"), autonomy=row[2] or "semi",
        halted_at=row[3], halt_rule=row[4] or "", halt_detail=row[5] or "",
        peak_equity=float(row[6] or 0.0), day_start_equity=float(row[7] or 0.0),
        day_start_date=row[8],
    )


def _write(mode: str, **fields) -> None:
    columns = ", ".join(fields)
    placeholders = ", ".join("?" for _ in fields)
    updates = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values())
    with connect() as conn:
        exists = conn.execute(
            "SELECT 1 FROM bot_state WHERE mode = ?", [mode]
        ).fetchone()
        if exists:
            conn.execute(
                f"UPDATE bot_state SET {updates}, updated_at = ? WHERE mode = ?",
                [*values, datetime.now(), mode],
            )
        else:
            conn.execute(
                f"INSERT INTO bot_state (mode, {columns}, updated_at) "
                f"VALUES (?, {placeholders}, ?)",
                [mode, *values, datetime.now()],
            )


def trip(mode: str, rule: str, detail: str, action: str) -> State:
    """Dispara el kill switch. `action` es 'halt_new' o 'flatten'."""
    new_state = State.FLATTEN_PENDING if action == "flatten" else State.HALT_NEW
    _write(mode, state=str(new_state), halted_at=datetime.now(),
           halt_rule=rule, halt_detail=detail)
    return new_state


def mark_flattened(mode: str) -> None:
    _write(mode, state=str(State.HALTED))


def clear_daily_halt(mode: str) -> bool:
    """Levanta SOLO la parada por perdida diaria, al cambiar el dia.

    Es la unica excepcion automatica del sistema, y se sostiene porque el
    limite es por definicion diario: mantenerlo al dia siguiente seria aplicar
    dos veces el mismo castigo. Cualquier otra parada exige rearme manual.
    """
    state = read_state(mode)
    if state.state is State.HALT_NEW and state.halt_rule == "daily_loss":
        _write(mode, state=str(State.RUNNING), halt_rule="", halt_detail="")
        return True
    return False


def start_day(mode: str, equity: float, day: object) -> None:
    _write(mode, day_start_equity=equity, day_start_date=day)


def update_peak(mode: str, equity: float) -> None:
    state = read_state(mode)
    if equity > state.peak_equity:
        _write(mode, peak_equity=equity)


def rearm(mode: str, confirm: str, note: str) -> None:
    """Rearme manual. Solo por CLI y con la frase exacta, que incluye el modo."""
    expected = f"REARMAR BOT {mode.upper()}"
    if confirm != expected:
        raise ValueError(
            f"La frase de confirmacion no coincide. Se esperaba exactamente: "
            f"{expected!r}"
        )

    state = read_state(mode)
    if state.state is State.RUNNING:
        raise ValueError("El bot no esta parado: no hay nada que rearmar.")

    cooldown = float(get_trading_config().kill_switch.get("cooldown_hours", 12))
    if state.halted_at is not None:
        elapsed = datetime.now() - state.halted_at
        if elapsed < timedelta(hours=cooldown):
            remaining = timedelta(hours=cooldown) - elapsed
            raise ValueError(
                f"Faltan {remaining} para poder rearmar. El tiempo de espera "
                "existe para que la decision no se tome en caliente."
            )

    if mode == "live" and not _live_confirmed():
        raise ValueError(
            "Rearmar en real exige la variable de entorno ALPACA_LIVE_CONFIRMED."
        )

    fields = {"state": str(State.RUNNING), "rearmed_at": datetime.now(),
              "rearmed_by": "cli", "rearm_note": note, "halt_rule": "",
              "halt_detail": ""}
    if state.halt_rule == "max_drawdown":
        # Tras liquidar, el maximo historico se reinicia a la equity actual. Si
        # no, el bot quedaria permanentemente en caida maxima y se volveria a
        # matar en el primer ciclo, sin haber operado.
        fields["peak_equity"] = 0.0
    _write(mode, **fields)


def _live_confirmed() -> bool:
    import os

    return bool(os.environ.get("ALPACA_LIVE_CONFIRMED"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Estado y rearme del kill switch del bot."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    show = sub.add_parser("status", help="Muestra el estado actual")
    show.add_argument("--mode", default="simulated")

    again = sub.add_parser("rearm", help="Vuelve a poner el bot en marcha")
    again.add_argument("--mode", default="simulated")
    again.add_argument("--confirm", required=True,
                       help="Frase exacta: 'REARMAR BOT <MODO>'")
    again.add_argument("--note", required=True,
                       help="Por que se rearma. Queda registrado.")

    args = parser.parse_args(argv)

    if args.command == "status":
        state = read_state(args.mode)
        print(f"Modo:   {state.mode}")
        print(f"Estado: {state.state}")
        if state.halt_rule:
            print(f"Motivo: {state.halt_rule} — {state.halt_detail}")
            print(f"Desde:  {state.halted_at}")
        return 0

    try:
        rearm(args.mode, args.confirm, args.note)
    except ValueError as exc:
        print(f"No se ha rearmado: {exc}", file=sys.stderr)
        return 1
    print(f"Bot rearmado en modo {args.mode}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
