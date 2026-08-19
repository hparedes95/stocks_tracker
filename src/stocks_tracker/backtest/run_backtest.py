"""Ejecuta la validacion y etiqueta las senales."""

from __future__ import annotations

import argparse
import hashlib
import os

import pandas as pd
from rich.console import Console
from rich.table import Table

from ..core.db import connect, migrate, upsert_df
from ..core.timeutils import utcnow
from . import engine as eng
from . import metrics as mx

console = Console()
DEFAULT_COST_BPS = 10.0
SCOPE_BY_SUFFIX = {".MC": eng.SCOPE_EQUITY_EU, ".DE": eng.SCOPE_EQUITY_EU, ".PA": eng.SCOPE_EQUITY_EU,
                   ".AS": eng.SCOPE_EQUITY_EU, ".MI": eng.SCOPE_EQUITY_EU, ".BR": eng.SCOPE_EQUITY_EU,
                   ".L": eng.SCOPE_EQUITY_EU, ".SW": eng.SCOPE_EQUITY_EU}


def scope_of(ticker: str) -> str:
    if ticker.endswith("-USD") or ticker.endswith("-EUR"):
        return eng.SCOPE_CRYPTO
    for suffix, scope in SCOPE_BY_SUFFIX.items():
        if ticker.endswith(suffix):
            return scope
    return eng.SCOPE_EQUITY_US


def load_data(scope: str | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    with connect(read_only=True) as conn:
        prices = conn.execute("""SELECT p.ticker, p.date, p.adj_close FROM prices_daily p
            JOIN instruments i USING (ticker) WHERE i.asset_class IN ('equity', 'etf')
            ORDER BY p.ticker, p.date""").fetchdf()
        signals = conn.execute("SELECT ticker, date, signal_id, direction, strength FROM signals").fetchdf()
    if prices.empty:
        return prices, signals
    prices["date"] = pd.to_datetime(prices["date"])
    if not signals.empty:
        signals["date"] = pd.to_datetime(signals["date"])
    if scope:
        keep = {t for t in prices["ticker"].unique() if scope_of(t) == scope}
        prices = prices[prices["ticker"].isin(keep)]
        if not signals.empty:
            signals = signals[signals["ticker"].isin(keep)]
    return prices, signals


def run(scope: str = eng.SCOPE_EQUITY_US, horizons: tuple[int, ...] = eng.DEFAULT_HORIZONS,
        cost_bps: float = DEFAULT_COST_BPS, tag: bool = False) -> pd.DataFrame:
    migrate()
    prices, signals = load_data(scope)
    if prices.empty or signals.empty:
        console.print("[yellow]Sin datos suficientes. Ejecuta la ingesta y el calculo.[/]")
        return pd.DataFrame()
    console.print(f"[cyan]Validando[/] ambito '{scope}': {prices['ticker'].nunique()} valores, "
                  f"{len(signals)} eventos, coste {cost_bps:.0f} pb por pata")
    fwd = eng.forward_returns(prices, horizons)
    bench_fwd = eng.universe_forward_returns(fwd, horizons)

    rows: list[dict] = []
    for signal_id in sorted(signals["signal_id"].unique()):
        for horizon in horizons:
            result = eng.validate_signal(signal_id, signals, fwd, horizon, scope, bench_fwd, cost_bps)
            rows.append({
                "signal_id": signal_id, "scope": scope, "horizon_days": horizon,
                "evidence": result.evidence, "ic_ir": result.ic_ir,
                "hit_rate": result.event.hit_rate, "avg_excess_ret": result.event.avg_excess,
                "n_obs": result.event.n_obs,
                "oos_from": result.oos_from.date() if result.oos_from is not None else None,
                "oos_to": result.oos_to.date() if result.oos_to is not None else None,
                "costs_bps_assumed": cost_bps, "updated_at": utcnow(),
                "_motivo": result.reason, "_t_stat": result.event.t_stat,
                "_p_value": result.event.p_value,
                "_ventanas_positivas": result.positive_folds, "_ventanas": len(result.folds),
            })
    table = pd.DataFrame(rows)
    if table.empty:
        return table

    table["adjusted_p_value"] = mx.benjamini_hochberg(table["_p_value"].to_numpy())
    table["multiple_testing_method"] = "Benjamini-Hochberg FDR"
    table["data_quality_status"] = "technical_only;survivorship_bias_present"
    table["fundamentals_point_in_time"] = False

    # El estudio actual usa senales discretas: no hay IC-IR que pueda sustituir
    # la significancia. Solo sobrevive "validada" si el q-value es < 5%.
    for idx, row in table.iterrows():
        q = row["adjusted_p_value"]
        if row["evidence"] == eng.VALIDATED and (pd.isna(q) or q >= 0.05):
            table.at[idx, "evidence"] = eng.WEAK
            table.at[idx, "_motivo"] = f"No supera FDR: q={q:.3f}."

    config_material = f"scope={scope}|horizons={horizons}|cost_bps={cost_bps}|min_obs={mx.MIN_OBSERVATIONS}"
    config_hash = hashlib.sha256(config_material.encode()).hexdigest()
    table["config_hash"] = config_hash
    table["git_commit"] = os.getenv("GITHUB_SHA", os.getenv("GIT_COMMIT", "unknown"))
    table["data_from"] = prices["date"].min().date()
    table["data_to"] = prices["date"].max().date()

    _print_report(table, horizons)
    if tag:
        payload = table.drop(columns=[c for c in table.columns if c.startswith("_")])
        payload["p_value"] = table["_p_value"]
        with connect() as conn:
            n = upsert_df(conn, "signal_evidence", payload,
                          keys=["signal_id", "scope", "horizon_days"])
        console.print(f"[green]Etiquetadas {n} combinaciones senal/horizonte.[/]")
    return table


def _print_report(table: pd.DataFrame, horizons: tuple[int, ...]) -> None:
    reference = horizons[len(horizons) // 2]
    subset = table[table["horizon_days"] == reference].sort_values("avg_excess_ret", ascending=False)
    report = Table(title=f"Validacion de senales · horizonte {reference} sesiones")
    for name in ("Senal", "Eventos", "Acierto", "Exceso medio", "t", "p", "q(FDR)", "Evidencia"):
        report.add_column(name, justify="right" if name != "Senal" else "left")
    colors = {eng.VALIDATED: "green", eng.WEAK: "yellow", eng.NOT_VALIDATED: "red", eng.NO_DATA: "dim"}
    for row in subset.to_dict("records"):
        report.add_row(row["signal_id"], str(row["n_obs"]),
                       f"{row['hit_rate']:.0%}" if pd.notna(row["hit_rate"]) else "—",
                       f"{row['avg_excess_ret']:+.2%}" if pd.notna(row["avg_excess_ret"]) else "—",
                       f"{row['_t_stat']:.1f}" if pd.notna(row["_t_stat"]) else "—",
                       f"{row['_p_value']:.3g}" if pd.notna(row["_p_value"]) else "—",
                       f"{row['adjusted_p_value']:.3g}" if pd.notna(row["adjusted_p_value"]) else "—",
                       f"[{colors.get(row['evidence'], 'white')}]{row['evidence']}[/]")
    console.print(report)
    validated = (subset["evidence"] == eng.VALIDATED).sum()
    console.print(f"\n[bold]{validated} de {len(subset)} senales superan la validacion[/] a {reference} sesiones.")
    console.print("[dim]Significancia: HAC + Benjamini-Hochberg FDR. "
                  "Fundamentales: no point-in-time. Survivorship: limitacion conocida y explicita.[/]")


def main() -> None:
    parser = argparse.ArgumentParser(description="Validacion historica de senales")
    parser.add_argument("--scope", default=eng.SCOPE_EQUITY_US,
                        choices=[eng.SCOPE_EQUITY_US, eng.SCOPE_EQUITY_EU, eng.SCOPE_CRYPTO])
    parser.add_argument("--tag-signals", action="store_true")
    parser.add_argument("--cost-bps", type=float, default=DEFAULT_COST_BPS)
    parser.add_argument("--horizons", type=str, default="5,10,21,63")
    args = parser.parse_args()
    horizons = tuple(int(h) for h in args.horizons.split(",") if h.strip())
    run(scope=args.scope, horizons=horizons, cost_bps=args.cost_bps, tag=args.tag_signals)


if __name__ == "__main__":
    main()
