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

from ..core import lineage, membership
from ..core.db import connect, migrate, upsert_df
from ..core.timeutils import utcnow
from . import engine as eng
from . import multiple_testing as mt

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


def load_data(scope: str | None = None,
              pit: bool = False) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Precios y senales del ambito indicado.

    No se carga ningun indice: la referencia de la validacion es el propio
    universo equiponderado, que se calcula despues a partir de estos precios.

    `pit` restringe cada fecha a los valores que pertenecian al universo ESE
    DIA, en vez de a los de hoy. Va apagado por defecto y no por comodidad: la
    tabla de composicion empieza el dia que se ejecuto la primera ingesta, asi
    que encenderlo hoy dejaria el backtest con unos pocos dias de datos. Ver
    `core/membership.py` para lo que esto corrige y lo que no.
    """
    filtro_pit = """
            JOIN universe_membership m ON m.ticker = p.ticker
                 AND m.valid_from <= p.date
                 AND (m.valid_to IS NULL OR m.valid_to > p.date)
    """ if pit else ""
    with connect(read_only=True) as conn:
        prices = conn.execute(
            f"""
            SELECT DISTINCT p.ticker, p.date, p.adj_close
            FROM prices_daily p
            JOIN instruments i USING (ticker)
            {filtro_pit}
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
    pit: bool = False,
) -> pd.DataFrame:
    """Valida todas las senales del ambito y devuelve la tabla de resultados."""
    migrate()
    prices, signals = load_data(scope, pit=pit)

    if prices.empty or signals.empty:
        console.print("[yellow]Sin datos suficientes. Ejecuta la ingesta y el calculo.[/]")
        return pd.DataFrame()

    console.print(
        f"[cyan]Validando[/] ambito '{scope}': {prices['ticker'].nunique()} valores, "
        f"{len(signals)} eventos, coste {cost_bps:.0f} pb por pata"
    )

    _avisar_del_sesgo(prices, pit)

    fwd = eng.forward_returns(prices, horizons)

    # Referencia: el propio universo equiponderado, no un indice externo.
    # Comparar contra el SPY mezclaria el aporte de la senal con la diferencia
    # estructural entre estas acciones y el indice, y ese error hace que
    # senales opuestas salgan ambas "ganadoras".
    bench_fwd = eng.universe_forward_returns(fwd, horizons)

    results = [
        eng.validate_signal(signal_id, signals, fwd, horizon, scope, bench_fwd, cost_bps)
        for signal_id in sorted(signals["signal_id"].unique())
        for horizon in horizons
    ]

    # La correccion necesita la familia entera, asi que va despues del bucle y
    # no dentro: cuantos falsos positivos esperar depende de cuantas pruebas se
    # hicieron, no de la senal que se este mirando.
    results = eng.apply_multiple_testing(results)

    # El sello se construye AQUI y no al guardar: el rango de datos y el numero
    # de filas son los de esta ejecucion, y despues ya no se pueden reconstruir.
    sello = lineage.sellar(
        {"horizontes": list(horizons), "coste_bps": cost_bps, "ambito": scope,
         "fdr_q": mt.FDR_Q, "min_fechas": eng.mx.MIN_DATES,
         "min_eventos": eng.mx.MIN_OBSERVATIONS, "universo_pit": pit},
        data_from=prices["date"].min().date(),
        data_to=prices["date"].max().date(),
        n_rows=len(prices),
    )
    console.print(f"[dim]{lineage.describir(sello)}[/]")

    rows = [
        {
            "signal_id": r.signal_id,
            "scope": r.scope,
            "horizon_days": r.horizon_days,
            "evidence": r.evidence,
            "ic_ir": r.ic_ir,
            "hit_rate": r.event.hit_rate,
            "avg_excess_ret": r.event.avg_excess,
            "n_obs": r.event.n_obs,
            "n_dates": r.event.n_dates,
            "t_stat": r.event.t_stat,
            "p_value": r.event.p_value,
            "q_value": r.q_value,
            "n_tests": r.n_tests,
            "ci_low": r.event.ci_low,
            "ci_high": r.event.ci_high,
            "oos_from": r.oos_from.date() if r.oos_from is not None else None,
            "oos_to": r.oos_to.date() if r.oos_to is not None else None,
            "costs_bps_assumed": cost_bps,
            "updated_at": utcnow(),
            **sello.as_dict(),
            "_motivo": r.reason,
            "_ventanas_positivas": r.positive_folds,
            "_ventanas": len(r.folds),
        }
        for r in results
    ]

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


def _avisar_del_sesgo(prices: pd.DataFrame, pit: bool) -> None:
    """Dice cuanta composicion real hay, con numeros de esta instalacion.

    El aviso de siempre —"los resultados estan sesgados por supervivencia"— es
    verdad y no sirve de nada: no se puede actuar sobre el ni saber si mejora.
    Decir "hay 0,03 anos de composicion real y el periodo son 10" es la misma
    advertencia convertida en algo comprobable, y que ademas se ve crecer con
    cada ingesta.
    """
    anos_datos = (prices["date"].max() - prices["date"].min()).days / 365.25
    with connect(read_only=True) as conn:
        anos_composicion = membership.anos_de_composicion(conn)
    console.print(
        f"[dim]{membership.aviso_de_supervivencia(anos_composicion, anos_datos)}[/]"
    )
    if pit and anos_composicion < anos_datos:
        console.print(
            "[yellow]--universo-historico solo tiene efecto en el tramo con "
            "composicion guardada; antes de esa fecha no hay filas y esos "
            "valores quedan FUERA del universo, que no es lo mismo que "
            "corregir el sesgo.[/]"
        )


def _print_report(table: pd.DataFrame, horizons: tuple[int, ...]) -> None:
    """Informe por consola, con el horizonte de referencia destacado."""
    reference = horizons[len(horizons) // 2]
    subset = table[table["horizon_days"] == reference].sort_values(
        "avg_excess_ret", ascending=False
    )

    report = Table(title=f"Validacion de senales · horizonte {reference} sesiones")
    report.add_column("Senal")
    report.add_column("Eventos", justify="right")
    report.add_column("Fechas", justify="right")
    report.add_column("Acierto", justify="right")
    report.add_column("Exceso medio", justify="right")
    report.add_column("IC 95 %", justify="right")
    report.add_column("t", justify="right")
    report.add_column("q", justify="right")
    report.add_column("Ventanas +", justify="right")
    report.add_column("Evidencia")

    colors = {
        eng.VALIDATED: "green", eng.WEAK: "yellow",
        eng.NOT_VALIDATED: "red", eng.NO_DATA: "dim",
    }
    def intervalo(row) -> str:
        if pd.isna(row["ci_low"]) or pd.isna(row["ci_high"]):
            return "—"
        return f"{row['ci_low']:+.2%} a {row['ci_high']:+.2%}"

    # Se recorre como diccionarios: los nombres que empiezan por guion bajo se
    # convierten en posicionales (_8, _9...) con itertuples, y eso se rompe en
    # cuanto alguien anade una columna.
    for row in subset.to_dict("records"):
        report.add_row(
            row["signal_id"],
            str(row["n_obs"]),
            str(row["n_dates"]),
            f"{row['hit_rate']:.0%}" if pd.notna(row["hit_rate"]) else "—",
            f"{row['avg_excess_ret']:+.2%}" if pd.notna(row["avg_excess_ret"]) else "—",
            intervalo(row),
            f"{row['t_stat']:.1f}" if pd.notna(row["t_stat"]) else "—",
            f"{row['q_value']:.3f}" if pd.notna(row["q_value"]) else "—",
            f"{row['_ventanas_positivas']}/{row['_ventanas']}",
            f"[{colors.get(row['evidence'], 'white')}]{row['evidence']}[/]",
        )

    console.print(report)

    validated = (subset["evidence"] == eng.VALIDATED).sum()
    console.print(
        f"\n[bold]{validated} de {len(subset)} senales superan la validacion[/] "
        f"a {reference} sesiones."
    )

    pruebas = int(table["n_tests"].max()) if "n_tests" in table else 0
    if pruebas:
        azar = mt.expected_false_positives(pruebas)
        console.print(
            f"[dim]Se han hecho {pruebas} pruebas en total (senales x horizontes). "
            f"Si ninguna senal sirviera, unas {azar:.0f} pasarian igualmente por "
            f"azar, por eso la etiqueta exige tambien un q por debajo de "
            f"{mt.FDR_Q:.2f}.[/]"
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
    parser.add_argument(
        "--universo-historico", action="store_true", dest="pit",
        help="Restringe cada fecha a los valores que pertenecian al universo "
             "ESE DIA. Reduce el sesgo de supervivencia, pero solo funciona "
             "para el periodo del que hay composicion guardada.",
    )
    args = parser.parse_args()

    horizons = tuple(int(h) for h in args.horizons.split(",") if h.strip())
    run(scope=args.scope, horizons=horizons, cost_bps=args.cost_bps,
        tag=args.tag_signals, pit=args.pit)


if __name__ == "__main__":
    main()
