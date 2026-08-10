"""Registro de lo que hace el bot. Persistencia de decisiones, ordenes y estado.

La invariante que sostiene todo lo demas: **cualquier candidato de cualquier
ciclo deja al menos una fila en `decision_log`, incluidos los descartados**. Sin
eso, "por que no compraste X el martes" no tiene respuesta, y un bot cuyas
decisiones no se pueden reconstruir no se puede corregir ni confiar.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from ..core.db import connect
from ..core.ids import ulid
from .intents import Decision, RiskVerdict


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


@dataclass
class Journal:
    run_id: str
    mode: str
    strategy_id: str

    _decisions: list[tuple] = None  # type: ignore[assignment]
    _intents: list[tuple] = None    # type: ignore[assignment]
    _violations: list[tuple] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self._decisions = []
        self._intents = []
        self._violations = []

    # ------------------------------------------------------------------
    def decision(self, ticker: str, decision: str, reason_code: str,
                 reason_text: str, context: dict | None = None) -> None:
        self._decisions.append((
            ulid(), self.run_id, datetime.now(), self.mode, self.strategy_id,
            ticker, decision, reason_code, reason_text, _json(context or {}),
        ))

    def from_verdict(self, verdict: RiskVerdict) -> None:
        """Un veredicto produce a la vez la fila de auditoria y la de intencion."""
        intent = verdict.intent
        decision = {
            Decision.APPROVE: "APPROVED",
            Decision.RESIZE: "RESIZED",
            Decision.VETO: "VETOED",
        }[verdict.decision]

        self.decision(
            intent.ticker, decision, verdict.reason_code, verdict.reason_text,
            {
                "rule_id": verdict.rule_id,
                "observed": verdict.observed,
                "limit": verdict.limit_value,
                "intent_type": str(intent.intent_type),
                "ref_price": intent.ref_price,
                "score_pctile": intent.score_pctile,
                **verdict.notes,
            },
        )

        self._intents.append((
            intent.intent_id, self.run_id, self.strategy_id, intent.created_at,
            intent.ticker, None, str(intent.side), str(intent.intent_type),
            intent.qty_requested, intent.notional_requested,
            verdict.qty_approved, verdict.notional_approved,
            intent.ref_price, verdict.stop_price, intent.stop_atr_mult,
            verdict.risk_amount, _json(intent.rationale),
            intent.score_pctile, intent.regime,
            str(verdict.decision), _json({"rule_id": verdict.rule_id,
                                          **verdict.notes}),
            "VETOED" if verdict.decision is Decision.VETO else "APPROVED",
            None, "auto", datetime.now(), verdict.reason_text,
        ))

    def violation(self, entry: dict) -> None:
        self._violations.append((
            ulid(), datetime.now(), self.run_id, self.mode,
            entry.get("rule_id"), entry.get("severity"), entry.get("ticker"),
            entry.get("observed"), entry.get("limit_value"),
            entry.get("headroom"), entry.get("action_taken"),
            _json(entry),
        ))

    # ------------------------------------------------------------------
    def flush(self) -> dict[str, int]:
        """Vuelca todo de una vez, en una transaccion.

        De una vez y no fila a fila para que el registro no quede a medias si
        el proceso muere en mitad del ciclo: o esta la decision entera o no
        esta, pero nunca media.
        """
        counts = {"decision_log": 0, "intents": 0, "risk_violations": 0}
        if not (self._decisions or self._intents or self._violations):
            return counts

        with connect() as conn:
            conn.execute("BEGIN TRANSACTION")
            try:
                if self._decisions:
                    conn.executemany(
                        "INSERT INTO decision_log (decision_id, run_id, logged_at, "
                        "mode, strategy_id, ticker, decision, reason_code, "
                        "reason_text, context) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        self._decisions,
                    )
                    counts["decision_log"] = len(self._decisions)
                if self._intents:
                    conn.executemany(
                        "INSERT INTO intents (intent_id, run_id, strategy_id, "
                        "created_at, ticker, tv_symbol, side, intent_type, "
                        "qty_requested, notional_requested, qty_approved, "
                        "notional_approved, ref_price, stop_price, stop_atr_mult, "
                        "risk_amount, rationale, score_pctile, regime, "
                        "risk_verdict, risk_notes, status, expires_at, "
                        "decided_by, decided_at, decision_note) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        self._intents,
                    )
                    counts["intents"] = len(self._intents)
                if self._violations:
                    conn.executemany(
                        "INSERT INTO risk_violations (id, logged_at, run_id, mode, "
                        "rule_id, severity, ticker, observed, limit_value, "
                        "headroom, action_taken, detail) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        self._violations,
                    )
                    counts["risk_violations"] = len(self._violations)
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

        self._decisions, self._intents, self._violations = [], [], []
        return counts


def start_run(run_id: str, mode: str, strategy_id: str, phase: str,
              equity: float) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO bot_runs (run_id, strategy_id, mode, phase, started_at, "
            "status, equity_start) VALUES (?,?,?,?,?,?,?)",
            [run_id, strategy_id, mode, phase, datetime.now(), "RUNNING", equity],
        )


def finish_run(run_id: str, status: str, equity: float, counts: dict,
               error: str | None = None) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE bot_runs SET finished_at = ?, status = ?, equity_end = ?, "
            "n_intents = ?, n_approved = ?, n_submitted = ?, n_filled = ?, "
            "error = ? WHERE run_id = ?",
            [datetime.now(), status, equity, counts.get("intents", 0),
             counts.get("approved", 0), counts.get("submitted", 0),
             counts.get("filled", 0), error, run_id],
        )


def save_snapshot(state, mode: str, strategy_id: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM portfolio_snapshots WHERE snapshot_at = ? AND mode = ?",
                     [state.snapshot_at, mode])
        conn.execute(
            "INSERT INTO portfolio_snapshots (snapshot_at, mode, strategy_id, cash, "
            "equity, long_market_value, buying_power, n_positions, "
            "gross_exposure_pct, daytrade_count, peak_equity, drawdown_pct, "
            "positions) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [state.snapshot_at, mode, strategy_id, state.cash, state.equity,
             state.long_market_value, state.cash, state.n_positions,
             state.gross_exposure_pct, state.daytrade_count, state.peak_equity,
             state.drawdown_pct, _json(state.positions)],
        )
