"""Lo que el bot ha hecho, en solo lectura, para la página del bot.

**Por que este modulo existe y por que es tan corto.** Habia una regla de
arquitectura que prohibia al dashboard tocar nada del bot. El motivo era
concreto y sigue siendo bueno: si una página mostrase las propuestas de una
estrategia sin validar, se leerian como recomendaciones y acabarian
ejecutandose a mano.

Eso sigue prohibido. Lo que se abre aqui es lo contrario de una propuesta: lo
que el bot YA hizo, lo que tiene abierto y lo que espera tu visto bueno. Es un
registro, no un consejo. Y con el freno de mano puesto hace falta verlo: una
orden retenida que no aparece en ninguna pantalla es una orden perdida.

La frontera queda asi, y los tests la fijan:

- El dashboard PUEDE leer el registro del bot: ordenes, posiciones, decisiones
  y pendientes. Todo por aqui, en solo lectura.
- El dashboard NO PUEDE importar lo que decide —estrategia, riesgo, ciclo— ni
  mostrar intenciones vetadas o simuladas como si fueran candidatas. Una
  propuesta que nadie ha validado no se enseña.
"""

from __future__ import annotations

import pandas as pd

from ..core.db import connect


def _empty(cols: list[str]) -> pd.DataFrame:
    return pd.DataFrame(columns=cols)


def modes() -> list[str]:
    """Carteras con actividad, tal cual estan guardadas (`live:kraken`)."""
    with connect(read_only=True) as conn:
        filas = conn.execute(
            "SELECT DISTINCT mode FROM decision_log WHERE mode IS NOT NULL "
            "ORDER BY mode"
        ).fetchall()
    return [f[0] for f in filas]


def positions(mode: str) -> pd.DataFrame:
    """Lo que el bot tiene abierto en esa cartera."""
    with connect(read_only=True) as conn:
        return conn.execute(
            "SELECT ticker, qty, avg_entry_price, stop_price, opened_at, "
            "highest_close_since_entry FROM bot_positions WHERE mode = ? "
            "ORDER BY ticker",
            [mode],
        ).fetchdf()


def orders(mode: str, limit: int = 50) -> pd.DataFrame:
    with connect(read_only=True) as conn:
        return conn.execute(
            "SELECT submitted_at, ticker, side, qty, notional, status "
            "FROM orders WHERE mode = ? ORDER BY submitted_at DESC LIMIT ?",
            [mode, limit],
        ).fetchdf()


def pending(mode: str | None = None) -> pd.DataFrame:
    """Ordenes retenidas por el freno de mano, sin las caducadas.

    La caducidad se filtra aqui y no se corrige: escribir en la base desde el
    dashboard convertiria una página de lectura en un segundo escritor, y
    DuckDB solo admite uno. Quien las marca es `trading.confirm`.
    """
    sql = (
        "SELECT intent_id, created_at, expires_at, ticker, side, "
        "notional_approved, qty_approved, ref_price, decision_note "
        "FROM intents WHERE status = 'PENDING_CONFIRMATION' "
        "AND (expires_at IS NULL OR expires_at > now()) "
    )
    params: list = []
    with connect(read_only=True) as conn:
        return conn.execute(sql + " ORDER BY created_at", params).fetchdf()


def recent_decisions(mode: str, limit: int = 60) -> pd.DataFrame:
    """Por que hizo —o no hizo— cada cosa.

    Es la respuesta a "por que no compro X el martes", que sin esto no la
    tiene nadie.
    """
    with connect(read_only=True) as conn:
        return conn.execute(
            "SELECT logged_at, ticker, decision, reason_code, reason_text "
            "FROM decision_log WHERE mode = ? ORDER BY logged_at DESC LIMIT ?",
            [mode, limit],
        ).fetchdf()


def last_run(mode: str) -> dict | None:
    with connect(read_only=True) as conn:
        filas = conn.execute(
            "SELECT run_id, started_at, finished_at, status, phase, "
            "equity_start, equity_end FROM bot_runs WHERE mode = ? "
            "ORDER BY started_at DESC LIMIT 1",
            [mode],
        ).fetchdf()
    return None if filas.empty else filas.iloc[0].to_dict()


def kill_switch(mode: str) -> dict | None:
    with connect(read_only=True) as conn:
        filas = conn.execute(
            "SELECT state, halted_at, halt_rule, halt_detail "
            "FROM bot_state WHERE mode = ?",
            [mode],
        ).fetchdf()
    return None if filas.empty else filas.iloc[0].to_dict()
