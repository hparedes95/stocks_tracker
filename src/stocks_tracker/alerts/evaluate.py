"""Evaluacion de las reglas de alerta.

Dos cosas hacen que este modulo sea util en lugar de molesto:

1. **Periodo de espera.** Sin el, la misma alerta se dispararia cada dia
   mientras la condicion siga siendo cierta. En una semana dejarias de leerlas,
   y entonces el sistema deja de servir para nada.
2. **Solo senales con evidencia.** Si una senal no ha demostrado nada en la
   validacion historica, no merece interrumpirte.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd

from ..core.db import connect
from ..core.safe_eval import evaluate as safe_evaluate
from ..core.safe_eval import safe_format
from ..core.scoring import preset_hash
from ..core.timeutils import utcnow
from .rules import Rule, get_defaults, get_rules


@dataclass
class Alert:
    id: str
    rule_id: str
    ticker: str | None
    triggered_at: pd.Timestamp
    message: str
    severity: str
    payload: dict


def _row_variables(row: pd.Series) -> dict:
    """Variables disponibles para la condicion, saneadas."""
    out: dict = {}
    for key, value in row.items():
        if isinstance(value, (np.integer, np.floating, float, int)):
            fval = float(value)
            if not np.isfinite(fval):
                continue
            out[str(key)] = fval
        elif isinstance(value, (np.bool_, bool)):
            out[str(key)] = bool(value)
        elif value is not None and not (isinstance(value, float) and value != value):
            out[str(key)] = value
    return out


def _alert_preset() -> str:
    """Perfil con el que se evaluan las reglas.

    Las alertas usan siempre el perfil por defecto, no el que tengas
    seleccionado en la pantalla: un aviso no puede depender de en que pestana
    dejaste el navegador.
    """
    from ..core.config import get_settings

    return str(get_settings().compute.get("weights_preset", "balanced"))


def _load_context() -> dict[str, pd.DataFrame]:
    """Foto del ultimo dia: indicadores, scores, watchlist, cartera y regimen."""
    with connect(read_only=True) as conn:
        snapshot = conn.execute(
            """
            SELECT i.*, inst.name, inst.gics_sector, inst.currency,
                   f.composite, f.composite_pctile, f.coverage,
                   fu.dividend_yield, fu.payout_ratio, fu.trailing_pe,
                   fu.net_debt_to_ebitda
            FROM indicators_daily i
            JOIN instruments inst ON inst.ticker = i.ticker
            LEFT JOIN factor_scores f
                   ON f.ticker = i.ticker AND f.date = i.date
                  AND f.weights_hash = ?
            LEFT JOIN fundamentals_snapshot fu
                   ON fu.ticker = i.ticker
                  AND fu.as_of = (SELECT MAX(as_of) FROM fundamentals_snapshot
                                  WHERE ticker = i.ticker)
            WHERE i.date = (SELECT MAX(date) FROM indicators_daily)
            """,
            # Sin filtrar por perfil, un valor apareceria una vez por perfil
            # calculado y la misma regla dispararia varias alertas identicas.
            [preset_hash(_alert_preset())],
        ).fetchdf()

        watchlist = conn.execute(
            "SELECT ticker, target_price, note FROM watchlist WHERE list_name = 'default'"
        ).fetchdf()

        positions = conn.execute(
            """
            SELECT ticker, qty, avg_cost FROM positions
            WHERE closed_at IS NULL AND qty > 0
            """
        ).fetchdf()

        membership = conn.execute(
            "SELECT universe, ticker FROM universe_membership WHERE valid_to IS NULL"
        ).fetchdf()

        regime = conn.execute(
            "SELECT * FROM regime_daily ORDER BY date DESC LIMIT 2"
        ).fetchdf()

        breadth = conn.execute(
            """
            SELECT * FROM breadth_daily
            WHERE scope = (SELECT scope FROM breadth_daily
                           GROUP BY scope ORDER BY COUNT(*) DESC LIMIT 1)
            ORDER BY date DESC LIMIT 1
            """
        ).fetchdf()

        evidence = conn.execute(
            """
            SELECT signal_id, evidence FROM signal_evidence
            WHERE scope = 'equity_us' AND horizon_days = 21
            """
        ).fetchdf()

        signals = conn.execute(
            "SELECT ticker, signal_id FROM signals WHERE date = (SELECT MAX(date) FROM signals)"
        ).fetchdf()

    return {
        "snapshot": snapshot, "watchlist": watchlist, "positions": positions,
        "membership": membership, "regime": regime, "breadth": breadth,
        "evidence": evidence, "signals": signals,
    }


def _scope_tickers(rule: Rule, ctx: dict) -> set[str] | None:
    """Tickers a los que aplica una regla. None = no aplica a ninguno."""
    if rule.scope == "watchlist":
        return set(ctx["watchlist"]["ticker"]) if not ctx["watchlist"].empty else set()
    if rule.scope == "portfolio":
        return set(ctx["positions"]["ticker"]) if not ctx["positions"].empty else set()

    universe = rule.universe
    if universe:
        membership = ctx["membership"]
        return set(membership[membership["universe"] == universe]["ticker"])

    sector = rule.sector
    if sector:
        snapshot = ctx["snapshot"]
        return set(snapshot[snapshot["gics_sector"] == sector]["ticker"])

    return None


def _recent_alerts(rule_id: str, cooldown_days: int) -> set[str]:
    """Tickers ya avisados por esta regla dentro del periodo de espera."""
    if cooldown_days <= 0:
        return set()
    cutoff = utcnow() - timedelta(days=cooldown_days)
    with connect(read_only=True) as conn:
        df = conn.execute(
            "SELECT DISTINCT ticker FROM alerts WHERE rule_id = ? AND triggered_at >= ?",
            [rule_id, cutoff],
        ).fetchdf()
    if df.empty:
        return set()
    return {t for t in df["ticker"] if t is not None}


def _market_variables(ctx: dict) -> dict:
    """Variables de las reglas de mercado (regimen y amplitud)."""
    variables: dict = {}

    regime = ctx["regime"]
    if not regime.empty:
        latest = regime.iloc[0]
        variables["regime"] = str(latest.get("regime", ""))
        variables["risk_score"] = float(latest.get("risk_score", float("nan")))
        variables["vix"] = float(latest.get("vix", float("nan")))
        # El cambio de regimen es lo informativo; el nivel se ve en el dashboard.
        previous = str(regime.iloc[1]["regime"]) if len(regime) > 1 else variables["regime"]
        variables["regime_changed"] = bool(previous != variables["regime"])
        variables["previous_regime"] = previous

    breadth = ctx["breadth"]
    if not breadth.empty:
        latest = breadth.iloc[0]
        for field in ("pct_above_sma200", "pct_above_sma50", "avg_pairwise_corr"):
            value = latest.get(field)
            if value is not None and pd.notna(value):
                variables[field] = float(value)

    return {k: v for k, v in variables.items()
            if not (isinstance(v, float) and not np.isfinite(v))}


def evaluate_rules(rules: tuple[Rule, ...] | None = None) -> list[Alert]:
    """Evalua todas las reglas y devuelve las alertas nuevas."""
    rules = rules if rules is not None else get_rules()
    ctx = _load_context()
    defaults = get_defaults()
    require_validated = bool(defaults.get("require_validated_signals", True))

    if ctx["snapshot"].empty:
        return []

    validated: set[str] = set()
    if not ctx["evidence"].empty:
        validated = set(
            ctx["evidence"][ctx["evidence"]["evidence"] == "validada"]["signal_id"]
        )

    signals_by_ticker: dict[str, list[str]] = {}
    if not ctx["signals"].empty:
        signals_by_ticker = (
            ctx["signals"].groupby("ticker")["signal_id"].apply(list).to_dict()
        )

    snapshot = ctx["snapshot"].set_index("ticker", drop=False)
    watchlist = (
        ctx["watchlist"].set_index("ticker") if not ctx["watchlist"].empty
        else pd.DataFrame()
    )
    positions = (
        ctx["positions"].set_index("ticker") if not ctx["positions"].empty
        else pd.DataFrame()
    )

    alerts: list[Alert] = []
    now = utcnow()

    for rule in rules:
        recent = _recent_alerts(rule.id, rule.cooldown_days)

        if rule.is_market_scope:
            variables = _market_variables(ctx)
            if not variables or "market" in recent:
                continue
            if not safe_evaluate(rule.when, variables):
                continue
            message = safe_format(rule.message, variables) or rule.name
            alerts.append(
                Alert(
                    id=str(uuid.uuid4()), rule_id=rule.id, ticker=None,
                    triggered_at=now, message=message, severity=rule.severity,
                    payload=variables,
                )
            )
            continue

        tickers = _scope_tickers(rule, ctx)
        if not tickers:
            continue

        for ticker in sorted(tickers & set(snapshot.index)):
            if ticker in recent:
                continue

            row = snapshot.loc[ticker]
            if isinstance(row, pd.DataFrame):
                row = row.iloc[0]
            variables = _row_variables(row)

            # Datos de la watchlist y de la cartera, para reglas como el
            # precio objetivo o el coste medio.
            if not watchlist.empty and ticker in watchlist.index:
                wl = watchlist.loc[ticker]
                target = wl.get("target_price")
                variables["target_price"] = (
                    float(target) if target is not None and pd.notna(target) else 0.0
                )
            else:
                variables.setdefault("target_price", 0.0)

            if not positions.empty and ticker in positions.index:
                pos = positions.loc[ticker]
                variables["avg_cost"] = float(pos["avg_cost"])
                variables["qty"] = float(pos["qty"])
                if variables["avg_cost"] > 0:
                    variables["pnl_pct"] = (
                        float(row.get("close", 0.0)) / variables["avg_cost"] - 1.0
                    )

            if not safe_evaluate(rule.when, variables):
                continue

            # Si la regla se apoya en una senal sin evidencia historica, no se
            # avisa: interrumpir por algo que no ha demostrado nada es ruido.
            if require_validated:
                active = signals_by_ticker.get(ticker, [])
                if active and validated and not (set(active) & validated):
                    continue

            variables["ticker"] = ticker
            variables["name"] = str(row.get("name") or ticker)
            message = safe_format(rule.message, variables) or f"{ticker}: {rule.name}"

            alerts.append(
                Alert(
                    id=str(uuid.uuid4()), rule_id=rule.id, ticker=ticker,
                    triggered_at=now, message=message, severity=rule.severity,
                    payload={
                        k: v for k, v in variables.items()
                        if k in ("close", "rsi14", "ret_1d", "composite_pctile",
                                 "drawdown", "rel_volume_20", "target_price")
                    },
                )
            )

    return alerts


def persist(alerts: list[Alert]) -> int:
    """Guarda las alertas nuevas. Devuelve cuantas se han escrito."""
    if not alerts:
        return 0

    import json

    rows = [
        {
            "id": a.id, "rule_id": a.rule_id, "ticker": a.ticker,
            "triggered_at": a.triggered_at, "message": a.message,
            "payload": json.dumps({**a.payload, "severity": a.severity}),
            "delivered": False, "channel": None, "acknowledged": False,
        }
        for a in alerts
    ]
    from ..core.db import upsert_df

    with connect() as conn:
        return upsert_df(conn, "alerts", pd.DataFrame(rows), keys=["id"])


def mark_delivered(alert_ids: list[str], channel: str) -> None:
    if not alert_ids:
        return
    placeholders = ", ".join("?" for _ in alert_ids)
    with connect() as conn:
        conn.execute(
            f"UPDATE alerts SET delivered = TRUE, channel = ? WHERE id IN ({placeholders})",
            [channel, *alert_ids],
        )


def acknowledge(alert_ids: list[str]) -> None:
    """Marca alertas como vistas, para que dejen de destacar en la interfaz."""
    if not alert_ids:
        return
    placeholders = ", ".join("?" for _ in alert_ids)
    with connect() as conn:
        conn.execute(
            f"UPDATE alerts SET acknowledged = TRUE WHERE id IN ({placeholders})",
            list(alert_ids),
        )


def recent(days: int = 30, only_unacknowledged: bool = False) -> pd.DataFrame:
    cutoff = utcnow() - timedelta(days=days)
    clause = " AND NOT acknowledged" if only_unacknowledged else ""
    with connect(read_only=True) as conn:
        return conn.execute(
            f"""
            SELECT * FROM alerts
            WHERE triggered_at >= ? {clause}
            ORDER BY triggered_at DESC
            """,
            [cutoff],
        ).fetchdf()


def purge_older_than(days: int = 365) -> int:
    """Limpia el historico antiguo para que la tabla no crezca sin limite."""
    cutoff = date.today() - timedelta(days=days)
    with connect() as conn:
        before = conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
        conn.execute("DELETE FROM alerts WHERE triggered_at < ?", [cutoff])
        after = conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
    return int(before - after)
