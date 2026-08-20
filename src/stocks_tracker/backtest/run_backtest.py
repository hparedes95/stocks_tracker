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
from ..core.config import get_settings
from ..core.db import connect, migrate, upsert_df
from ..core.timeutils import utcnow
from . import engine as eng
from . import experiments as exp
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

    `pit` restringe los EVENTOS a los valores que pertenecian al universo ese
    dia, en vez de a los de hoy. Va apagado por defecto y no por comodidad: la
    tabla de composicion empieza el dia que se ejecuto la primera ingesta, asi
    que encenderlo hoy dejaria el backtest con unos pocos dias de datos. Ver
    `core/membership.py` para lo que esto corrige y lo que no.

    Filtra las SENALES y nunca los precios, y esa distincion no es cosmetica.
    `forward_returns` desplaza la serie por POSICION —`close.shift(-1-h)`—, asi
    que un hueco en los precios de un ticker no se salta: se cruza. Un valor
    que estuvo fuera del indice tres meses dejaria un agujero y el retorno "a
    21 sesiones" del dia anterior pasaria a medir tres meses largos, sin dar
    ningun error y con un numero perfectamente creible. Los precios se cargan
    completos y la pertenencia decide que eventos cuentan.
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
            f"""
            SELECT {'DISTINCT ' if pit else ''}s.ticker, s.date, s.signal_id,
                   s.direction, s.strength
            FROM signals s
            {membership.join_vigente("s") if pit else ''}
            """
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


def frontera() -> pd.Timestamp:
    """La fecha que separa descubrimiento de confirmacion.

    Sale de `settings.yaml` y es FIJA a proposito: con una fraccion del
    historico, la frontera se desplazaria sola segun entran datos y el tramo que
    hoy esta reservado formaria parte del descubrimiento el mes que viene, sin
    que nadie se entere.
    """
    valor = get_settings().backtest.get("confirmation_from")
    if not valor:
        raise ValueError(
            "Falta `backtest.confirmation_from` en settings.yaml. Sin frontera "
            "no hay tramo reservado, y sin tramo reservado no hay confirmacion "
            "posible: solo descubrimiento contandose a si mismo."
        )
    return pd.Timestamp(valor)


def recortar(prices: pd.DataFrame, signals: pd.DataFrame, fase: str,
             corte: pd.Timestamp) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Deja solo el tramo que le corresponde a la fase.

    Los PRECIOS se recortan igual que las senales y no se dejan enteros: si en
    descubrimiento se dejaran los precios completos, `forward_returns` mediria
    el resultado de una senal de diciembre de 2022 con los precios de 2023, o
    sea con el tramo reservado. El recorte tiene que llegar hasta el final del
    horizonte, no solo hasta la senal.
    """
    if fase == exp.CONFIRMACION:
        return (prices[prices["date"] >= corte],
                signals[signals["date"] >= corte] if not signals.empty else signals)
    return (prices[prices["date"] < corte],
            signals[signals["date"] < corte] if not signals.empty else signals)


def run(
    scope: str = eng.SCOPE_EQUITY_US,
    horizons: tuple[int, ...] = eng.DEFAULT_HORIZONS,
    cost_bps: float = DEFAULT_COST_BPS,
    tag: bool = False,
    pit: bool = False,
    fase: str = exp.DESCUBRIMIENTO,
    congelar: bool = False,
) -> pd.DataFrame:
    """Valida todas las senales del ambito y devuelve la tabla de resultados."""
    migrate()
    prices, signals = load_data(scope, pit=pit)

    if prices.empty or signals.empty:
        console.print("[yellow]Sin datos suficientes. Ejecuta la ingesta y el calculo.[/]")
        return pd.DataFrame()

    corte = frontera()
    prices, signals = recortar(prices, signals, fase, corte)
    if prices.empty or signals.empty:
        console.print(
            f"[yellow]No hay datos en el tramo de {fase} (frontera "
            f"{corte:%d/%m/%Y}).[/]"
        )
        return pd.DataFrame()

    console.print(
        f"[cyan]Validando[/] ambito '{scope}' · fase [bold]{fase}[/]: "
        f"{prices['ticker'].nunique()} valores, {len(signals)} eventos, "
        f"coste {cost_bps:.0f} pb por pata"
    )
    console.print(
        f"[dim]Frontera {corte:%d/%m/%Y}: "
        + ("se usa SOLO lo anterior; lo posterior queda reservado y sin mirar."
           if fase == exp.DESCUBRIMIENTO
           else "se usa SOLO lo posterior, que no intervino en el descubrimiento.")
        + "[/]"
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

    # La confirmacion solo se deja correr si la especificacion estaba congelada
    # antes, y si no habia fallado ya. Se comprueba TODA la familia de golpe y
    # antes de tocar nada: dejar pasar la mitad seria dejar pasar la mitad de la
    # muestra reservada.
    especificaciones = {
        (r.signal_id, r.horizon_days): exp.Spec(
            signal_id=r.signal_id, scope=scope, horizon_days=r.horizon_days,
            cost_bps=cost_bps, universe="pit" if pit else "todos",
        )
        for r in results
    }
    if fase == exp.CONFIRMACION:
        results, especificaciones = _solo_las_candidatas(
            results, especificaciones, scope)
        if not results:
            console.print(
                "[yellow]Ninguna senal llego a `estable` en descubrimiento, asi "
                "que no hay nada que confirmar. El tramo reservado sigue "
                "intacto.[/]"
            )
            return pd.DataFrame()
        _exigir_congelacion(especificaciones.values())
    elif congelar:
        _congelar_todas(results, especificaciones)

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

    registro = _registrar_experimentos(results, especificaciones, fase=fase,
                                       corte=corte, prices=prices, scope=scope)

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
            "estado": registro["estados"][(r.signal_id, r.horizon_days)],
            "fase": fase,
            "spec_hash": especificaciones[(r.signal_id, r.horizon_days)].spec_hash,
            "intentos": registro["intentos"][r.signal_id],
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


def _congelar_todas(results, especificaciones) -> None:
    """Fija SOLO las que llegaron a `estable`.

    Congelar todo seria ruido: una especificacion congelada es una candidata a
    gastar muestra reservada, y las que no fueron ni significativas no lo son.
    """
    candidatas = [
        especificaciones[(r.signal_id, r.horizon_days)]
        for r in results
        if len(r.folds) >= 2 and r.positive_folds >= 2
        and r.event.is_significant and r.survives_fdr is not False
        and r.event.avg_excess > 0
    ]
    if not candidatas:
        console.print(
            "[yellow]Ninguna senal llega a `estable`, asi que no hay nada que "
            "congelar. El tramo reservado sigue sin tocarse.[/]"
        )
        return
    with connect() as conn:
        for s in candidatas:
            exp.congelar(conn, s, "congelado tras el descubrimiento")
    console.print(
        f"[green]Congeladas {len(candidatas)} especificaciones.[/] A partir de "
        "ahora, cambiar la senal, el horizonte, el universo, la referencia o "
        "el coste produce un experimento DISTINTO, y quedara anotado como tal."
    )


def _solo_las_candidatas(results, especificaciones, scope):
    """Deja pasar a confirmacion solo lo que llego a `estable`.

    Llevar al tramo reservado una senal que ni siquiera fue significativa
    gastaria muestra —que no se repone— para contestar algo que ya tenia
    respuesta.
    """
    with connect(read_only=True) as conn:
        elegibles = exp.candidatas(conn, scope)
    filtrados = [
        r for r in results
        if especificaciones[(r.signal_id, r.horizon_days)].spec_hash in elegibles
    ]
    descartados = len(results) - len(filtrados)
    if descartados:
        console.print(
            f"[dim]{descartados} combinaciones no llegaron a `estable` en "
            "descubrimiento y no entran en la confirmacion.[/]"
        )
    return filtrados, {
        (r.signal_id, r.horizon_days): especificaciones[(r.signal_id, r.horizon_days)]
        for r in filtrados
    }


def _exigir_congelacion(specs) -> None:
    """Las dos negativas que hacen que el tramo reservado siga siendo reservado.

    Se comprueban todas antes de seguir. Parar a la mitad habria gastado media
    muestra de confirmacion, y esa no se recupera.
    """
    problemas: list[str] = []
    with connect(read_only=True) as conn:
        for s in specs:
            try:
                exp.comprobar_confirmacion(conn, s)
            except exp.ContaminacionError as fallo:
                problemas.append(str(fallo))
    if problemas:
        for p in problemas[:5]:
            console.print(f"[red]  {p}[/]")
        raise exp.ContaminacionError(
            f"{len(problemas)} especificaciones no pueden confirmarse. "
            "No se ha mirado el tramo reservado."
        )


def _registrar_experimentos(results, especificaciones, *, fase, corte,
                            prices, scope) -> dict:
    """Anota TODOS los experimentos y devuelve el estado de cada uno.

    Tambien los que no llegan a ninguna parte. Un registro que solo guarda los
    que funcionaron es un album de aciertos, y para lo unico que existe es para
    contar cuantas veces se miro.
    """
    estados: dict = {}
    with connect() as conn:
        for r in results:
            spec = especificaciones[(r.signal_id, r.horizon_days)]
            # En confirmacion, "repite" es la misma exigencia que en
            # descubrimiento pero sobre datos que no intervinieron en elegir la
            # senal: exceso positivo y significativo. Si aqui se relajara el
            # criterio, la confirmacion aprobaria cosas que el descubrimiento
            # habria rechazado.
            repite = (r.event.avg_excess > 0 and r.event.is_significant
                      if fase == exp.CONFIRMACION else None)
            estado = exp.peldano(
                hay_datos=r.event.n_obs > 0 and r.event.n_dates > 0,
                significativa=(r.event.is_significant
                               and r.survives_fdr is not False
                               and r.event.avg_excess > 0),
                estable=len(r.folds) >= 2 and r.positive_folds >= 2,
                fase=fase, repite_fuera_de_muestra=repite,
            )
            exp.registrar(
                conn, spec, fase=fase, estado=estado, split_at=corte.date(),
                data_from=prices["date"].min().date(),
                data_to=prices["date"].max().date(),
                n_obs=r.event.n_obs, n_dates=r.event.n_dates,
                avg_excess=r.event.avg_excess, t_stat=r.event.t_stat,
                p_value=r.event.p_value, q_value=r.q_value, motivo=r.reason,
            )
            estados[(r.signal_id, r.horizon_days)] = estado
        intentos = {
            r.signal_id: exp.intentos(conn, r.signal_id, scope) for r in results
        }
    return {"estados": estados, "intentos": intentos}


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
    report.add_column("Estado")
    report.add_column("Intentos", justify="right")

    colores_estado = {
        exp.CONFIRMADA: "bold green", exp.ESTABLE: "green",
        exp.SIGNIFICATIVA: "yellow", exp.DESCUBIERTA: "dim",
        exp.REFUTADA: "red", exp.SIN_DATOS: "dim",
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
            f"[{colores_estado.get(row['estado'], 'white')}]{row['estado']}[/]",
            str(row.get("intentos", 0)),
        )

    console.print(report)

    confirmadas = (subset["estado"] == exp.CONFIRMADA).sum()
    estables = (subset["estado"] == exp.ESTABLE).sum()
    console.print(
        f"\n[bold]{confirmadas} confirmadas y {estables} estables[/] de "
        f"{len(subset)} senales a {reference} sesiones."
    )
    if estables and not confirmadas:
        console.print(
            "[dim]`estable` es el techo del descubrimiento: por bueno que salga "
            "el numero, sale sobre los mismos datos con los que se eligio la "
            "senal. Para llegar a `confirmada` hace falta congelar y ejecutar "
            "`--fase confirmacion`.[/]"
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
        "--fase", default=exp.DESCUBRIMIENTO,
        choices=[exp.DESCUBRIMIENTO, exp.CONFIRMACION],
        help="descubrimiento usa el tramo anterior a la frontera; confirmacion "
             "usa el reservado, y solo si la especificacion estaba congelada.",
    )
    parser.add_argument(
        "--congelar", action="store_true",
        help="Fija las especificaciones tras el descubrimiento. A partir de ahi, "
             "cambiar cualquier cosa produce un experimento distinto.",
    )
    parser.add_argument(
        "--universo-historico", action="store_true", dest="pit",
        help="Restringe cada fecha a los valores que pertenecian al universo "
             "ESE DIA. Reduce el sesgo de supervivencia, pero solo funciona "
             "para el periodo del que hay composicion guardada.",
    )
    args = parser.parse_args()

    horizons = tuple(int(h) for h in args.horizons.split(",") if h.strip())
    run(scope=args.scope, horizons=horizons, cost_bps=args.cost_bps,
        tag=args.tag_signals, pit=args.pit, fase=args.fase,
        congelar=args.congelar)


if __name__ == "__main__":
    main()
