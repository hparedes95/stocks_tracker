"""Ejecuta la validacion y etiqueta las senales.

Uso tipico:

    python -m stocks_tracker.backtest.run_backtest --tag-signals

Escribe `signal_evidence` con la etiqueta de cada senal por ambito y horizonte.
La regla de riesgo del bot (fases posteriores) leera esa tabla: una senal sin
`evidence = 'validada'` en su propio ambito no puede operar.
"""

from __future__ import annotations

import argparse

import pandas as pd
from rich.console import Console
from rich.table import Table

from ..core.db import connect, migrate, upsert_df
from ..core.timeutils import utcnow
from . import engine as eng

console = Console()

# Coste por operacion asumido, en puntos basicos y por pata. Sin costes,
# cualquier estrategia de alta rotacion parece rentable.
DEFAULT_COST_BPS = 10.0

# Ambito segun el mercado del valor. Se separan porque una senal validada en
# EE.UU. no esta validada en Europa: distinta liquidez, distinto horario y
# distinta base de inversores.
SCOPE_BY_SUFFIX = {
    ".MC": eng.SCOPE_EQUITY_EU, ".DE": eng.SCOPE_EQUITY_EU,
    ".PA": eng.SCOPE_EQUITY_EU, ".AS": eng.SCOPE_EQUITY_EU,
    ".MI": eng.SCOPE_EQUITY_EU, ".BR": eng.SCOPE_EQUITY_EU,
    ".L": eng.SCOPE_EQUITY_EU, ".SW": eng.SCOPE_EQUITY_EU,
}


def scope_of(ticker: str) -> str:
    if ticker.endswith("-USD") or ticker.endswith("-EUR"):
        return eng.SCOPE_CRYPTO
    for suffix, scope in SCOPE_BY_SUFFIX.items():
        if ticker.endswith(suffix):
            return scope
    return eng.SCOPE_EQUITY_US


def load_data(scope: str | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Precios y senales del ambito indicado.

    No se carga ningun indice: la referencia de la validacion es el propio
    universo equiponderado, que se calcula despues a partir de estos precios.
    """
    with connect(read_only=True) as conn:
        prices = conn.execute(
            """
            SELECT p.ticker, p.date, p.adj_close
            FROM prices_daily p
            JOIN instruments i USING (ticker)
            WHERE i.asset_class IN ('equity', 'etf')
            ORDER BY p.ticker, p.date
            """
        ).fetchdf()
        signals = conn.execute(
            "SELECT ticker, date, signal_id, direction, strength FROM signals"
        ).fetchdf()

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


def run(
    scope: str = eng.SCOPE_EQUITY_US,
    horizons: tuple[int, ...] = eng.DEFAULT_HORIZONS,
    cost_bps: float = DEFAULT_COST_BPS,
    tag: bool = False,
) -> pd.DataFrame:
    """Valida todas las senales del ambito y devuelve la tabla de resultados."""
    migrate()
    prices, signals = load_data(scope)

    if prices.empty or signals.empty:
        console.print("[yellow]Sin datos suficientes. Ejecuta la ingesta y el calculo.[/]")
        return pd.DataFrame()

    console.print(
        f"[cyan]Validando[/] ambito '{scope}': {prices['ticker'].nunique()} valores, "
        f"{len(signals)} eventos, coste {cost_bps:.0f} pb por pata"
    )

    fwd = eng.forward_returns(prices, horizons)

    # Referencia: el propio universo equiponderado, no un indice externo.
    # Comparar contra el SPY mezclaria el aporte de la senal con la diferencia
    # estructural entre estas acciones y el indice, y ese error hace que
    # senales opuestas salgan ambas "ganadoras".
    bench_fwd = eng.universe_forward_returns(fwd, horizons)

    rows: list[dict] = []
    for signal_id in sorted(signals["signal_id"].unique()):
        for horizon in horizons:
            result = eng.validate_signal(
                signal_id, signals, fwd, horizon, scope, bench_fwd, cost_bps
            )
            rows.append(
                {
                    "signal_id": signal_id,
                    "scope": scope,
                    "horizon_days": horizon,
                    "evidence": result.evidence,
                    "ic_ir": result.ic_ir,
                    "hit_rate": result.event.hit_rate,
                    "avg_excess_ret": result.event.avg_excess,
                    "n_obs": result.event.n_obs,
                    "oos_from": result.oos_from.date() if result.oos_from is not None else None,
                    "oos_to": result.oos_to.date() if result.oos_to is not None else None,
                    "costs_bps_assumed": cost_bps,
                    "updated_at": utcnow(),
                    "_motivo": result.reason,
                    "_t_stat": result.event.t_stat,
                    "_ventanas_positivas": result.positive_folds,
                    "_ventanas": len(result.folds),
                }
            )

    table = pd.DataFrame(rows)
    if table.empty:
        return table

    _print_report(table, horizons)

    if tag:
        payload = table.drop(columns=[c for c in table.columns if c.startswith("_")])
        with connect() as conn:
            n = upsert_df(
                conn, "signal_evidence", payload,
                keys=["signal_id", "scope", "horizon_days"],
            )
        console.print(f"[green]Etiquetadas {n} combinaciones senal/horizonte.[/]")

    return table


def _print_report(table: pd.DataFrame, horizons: tuple[int, ...]) -> None:
    """Informe por consola, con el horizonte de referencia destacado."""
    reference = horizons[len(horizons) // 2]
    subset = table[table["horizon_days"] == reference].sort_values(
        "avg_excess_ret", ascending=False
    )

    report = Table(title=f"Validacion de senales · horizonte {reference} sesiones")
    report.add_column("Senal")
    report.add_column("Eventos", justify="right")
    report.add_column("Acierto", justify="right")
    report.add_column("Exceso medio", justify="right")
    report.add_column("t", justify="right")
    report.add_column("Ventanas +", justify="right")
    report.add_column("Evidencia")

    colors = {
        eng.VALIDATED: "green", eng.WEAK: "yellow",
        eng.NOT_VALIDATED: "red", eng.NO_DATA: "dim",
    }
    # Se recorre como diccionarios: los nombres que empiezan por guion bajo se
    # convierten en posicionales (_8, _9...) con itertuples, y eso se rompe en
    # cuanto alguien anade una columna.
    for row in subset.to_dict("records"):
        report.add_row(
            row["signal_id"],
            str(row["n_obs"]),
            f"{row['hit_rate']:.0%}" if pd.notna(row["hit_rate"]) else "—",
            f"{row['avg_excess_ret']:+.2%}" if pd.notna(row["avg_excess_ret"]) else "—",
            f"{row['_t_stat']:.1f}" if pd.notna(row["_t_stat"]) else "—",
            f"{row['_ventanas_positivas']}/{row['_ventanas']}",
            f"[{colors.get(row['evidence'], 'white')}]{row['evidence']}[/]",
        )

    console.print(report)

    validated = (subset["evidence"] == eng.VALIDATED).sum()
    console.print(
        f"\n[bold]{validated} de {len(subset)} senales superan la validacion[/] "
        f"a {reference} sesiones."
    )
    console.print(
        "[dim]Que una senal supere esto NO significa que vaya a funcionar: "
        "significa que en este historico no se comporto como el azar. El "
        "universo usado son los constituyentes de HOY, asi que los resultados "
        "estan sesgados al alza por supervivencia.[/]"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Validacion historica de senales")
    parser.add_argument(
        "--scope", default=eng.SCOPE_EQUITY_US,
        choices=[eng.SCOPE_EQUITY_US, eng.SCOPE_EQUITY_EU, eng.SCOPE_CRYPTO],
    )
    parser.add_argument("--tag-signals", action="store_true",
                        help="escribe las etiquetas en signal_evidence")
    parser.add_argument("--cost-bps", type=float, default=DEFAULT_COST_BPS)
    parser.add_argument("--horizons", type=str, default="5,10,21,63")
    args = parser.parse_args()

    horizons = tuple(int(h) for h in args.horizons.split(",") if h.strip())
    run(scope=args.scope, horizons=horizons, cost_bps=args.cost_bps, tag=args.tag_signals)


if __name__ == "__main__":
    main()
