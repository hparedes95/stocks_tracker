"""Puerta 1: el backtest con costes que decide si la estrategia se activa.

Lo importante de este modulo no son los nueve umbrales sino las dos negativas
que van antes de ellos, porque un aprobado falso aqui es lo que acaba llevando
dinero real a una estrategia que no funciona:

1. **No certifica sobre precios inventados.** El almacen puede contener series
   sinteticas del modo de prueba. Un backtest sobre datos generados por
   nosotros mide lo bien que el generador imita a la estrategia, y sale
   espectacular.
2. **No certifica sobre un ranking con fundamentales.** No hay serie historica
   de fundamentales: puntuar 2019 con los balances de hoy es mirar el futuro.

Superar la puerta NO significa que la estrategia vaya a ganar dinero. Significa
que no ha fallado ninguna de las comprobaciones que sabemos hacer, y que no hay
un error de programacion o de diseno evidente. Es una condicion necesaria, en
ningun caso suficiente.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..backtest import metrics as mx
from ..core.config import get_factor_config, get_trading_config
from ..core.db import connect
from ..core.scoring import weights_hash

SESSIONS_YEAR = 252


@dataclass(frozen=True)
class Check:
    """Una comprobacion, con el numero observado y el que se pedia."""

    name: str
    passed: bool
    observed: str
    required: str
    detail: str = ""


@dataclass
class GateReport:
    checks: list[Check] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """Aprueba solo si no hay bloqueos Y todas las comprobaciones pasan."""
        return not self.blockers and bool(self.checks) and all(c.passed for c in self.checks)

    def add(self, name: str, passed: bool, observed, required: str,
            detail: str = "") -> None:
        self.checks.append(
            Check(name, bool(passed), _fmt(observed), required, detail)
        )


def _miles(value: int) -> str:
    return f"{value:,}".replace(",", ".")


def _fmt(value) -> str:
    if isinstance(value, float):
        return f"{value:,.2f}".replace(",", " ")
    return str(value)


# ---------------------------------------------------------------------------
# Bloqueos previos
# ---------------------------------------------------------------------------
def find_blockers(preset: str = "bot_core") -> list[str]:
    """Motivos por los que el resultado del backtest no seria interpretable."""
    blockers: list[str] = []

    with connect(read_only=True) as conn:
        sources = conn.execute(
            "SELECT source, COUNT(*) AS n FROM prices_daily GROUP BY 1"
        ).fetchdf()
        n_scores = conn.execute(
            "SELECT COUNT(DISTINCT date) AS n FROM factor_scores WHERE weights_hash = ?",
            [weights_hash(get_factor_config().weights(preset))],
        ).fetchone()[0]

    if not sources.empty:
        total = int(sources["n"].sum())
        synthetic = int(sources.loc[sources["source"] == "synthetic", "n"].sum())
        if synthetic:
            # El separador de miles se aplica SOLO a los numeros. Un
            # `.replace(",", ".")` sobre la frase entera se comia las comas del
            # texto y dejaba "a la estrategia. no la estrategia".
            blockers.append(
                f"{_miles(synthetic)} de {_miles(total)} precios son SINTETICOS. "
                "Un backtest sobre series que hemos generado nosotros mide lo "
                "bien que el generador imita a la estrategia, no la estrategia. "
                "Descarga el universo real antes de certificar nada."
            )

    factores = {f for f, w in get_factor_config().weights(preset).items() if w}
    from ..compute.run_compute import PRICE_ONLY_FACTORS

    fuera = factores - PRICE_ONLY_FACTORS
    if fuera:
        blockers.append(
            f"El preset '{preset}' usa {sorted(fuera)}, que salen de los "
            "fundamentales. No hay serie historica de fundamentales, asi que su "
            "ranking pasado incorpora informacion del futuro."
        )

    if n_scores < 100:
        blockers.append(
            f"Solo hay {n_scores} sesiones con ranking del perfil '{preset}'. "
            "Calcula el historico primero:\n"
            "    python -m stocks_tracker.compute.run_compute --history 6"
        )

    return blockers


# ---------------------------------------------------------------------------
# Referencia
# ---------------------------------------------------------------------------
def benchmark_curve(start, end) -> pd.Series:
    """Indice equiponderado del universo, que es la referencia honesta.

    No se usa el S&P 500 a proposito: esta ponderado por capitalizacion, y una
    estrategia que elige entre 500 valores con el mismo peso cada uno tiene que
    batir a "comprar todos por igual", no a "comprar sobre todo las siete
    grandes". Compararse con el indice haria pasar por habilidad lo que solo es
    una apuesta de tamano.
    """
    with connect(read_only=True) as conn:
        rows = conn.execute(
            """
            SELECT p.date, AVG(i.ret_1d) AS ret
            FROM indicators_daily i
            JOIN instruments inst USING (ticker)
            JOIN prices_daily p ON p.ticker = i.ticker AND p.date = i.date
            WHERE inst.asset_class = 'equity' AND i.date BETWEEN ? AND ?
            GROUP BY p.date ORDER BY p.date
            """,
            [start, end],
        ).fetchdf()
    if rows.empty:
        return pd.Series(dtype=float)
    serie = rows.set_index("date")["ret"].fillna(0.0)
    return (1.0 + serie).cumprod()


# ---------------------------------------------------------------------------
# Evaluacion
# ---------------------------------------------------------------------------
def evaluate(summary: dict, robustness: dict | None = None,
             preset: str = "bot_core") -> GateReport:
    """Aplica los nueve umbrales de la adenda al resultado de un backtest."""
    report = GateReport(blockers=find_blockers(preset))

    curva = pd.DataFrame(summary.get("curva") or [], columns=["date", "equity"])
    if curva.empty:
        report.blockers.append("El backtest no ha producido curva de resultados.")
        return report

    curva["date"] = pd.to_datetime(curva["date"])
    equity = curva.set_index("date")["equity"]
    returns = equity.pct_change().dropna()
    years = len(equity) / SESSIONS_YEAR

    # 1. Periodo cubierto
    report.add("Periodo cubierto", years >= 5.0, f"{years:.1f} anos", ">= 5 anos",
               "Menos de cinco anos no cubre un ciclo completo de mercado.")

    # 2. Numero de operaciones
    trades = int(summary.get("operaciones", 0))
    report.add("Operaciones", trades >= 100, trades, ">= 100",
               "Con menos, cualquier resultado cabe dentro del azar.")

    # 3. Sharpe
    sharpe = mx.sharpe(returns)
    report.add("Sharpe", sharpe >= 0.50, sharpe, ">= 0,50")

    # 4. Caida maxima
    drawdown = abs(mx.max_drawdown(equity)) * 100.0
    report.add("Caida maxima", drawdown <= 20.0, f"{drawdown:.1f} %", "<= 20 %")

    # 5. Expectativa por operacion, ya con costes dentro del simulador
    total_return = equity.iloc[-1] / equity.iloc[0] - 1.0
    expectancy = (total_return / trades * 100.0) if trades else 0.0
    report.add("Expectativa por operacion", trades > 0 and expectancy > 0,
               f"{expectancy:+.3f} %", "> 0 %",
               "Despues de comisiones y deslizamiento.")

    # 6. Ventanas moviles de doce meses
    if len(equity) > SESSIONS_YEAR:
        rolling = equity.pct_change(SESSIONS_YEAR).dropna()
        positive = float((rolling > 0).mean()) * 100.0
    else:
        positive = 0.0
    report.add("Ventanas de 12 meses positivas", positive >= 55.0,
               f"{positive:.0f} %", ">= 55 %")

    # 7. Frente a la referencia equiponderada
    bench = benchmark_curve(equity.index.min().date(), equity.index.max().date())
    if bench.empty:
        report.add("Frente al equiponderado", False, "sin referencia", "comparable",
                   "No se ha podido construir el indice equiponderado.")
    else:
        bench_return = float(bench.iloc[-1] / bench.iloc[0] - 1.0)
        bench_dd = abs(mx.max_drawdown(bench)) * 100.0
        gana = total_return >= bench_return
        # Rendir menos se admite SOLO si a cambio se sufre mucho menos: una
        # estrategia mas tranquila puede ser preferible aunque gane menos.
        compensa = drawdown < bench_dd / 2.0
        report.add(
            "Frente al equiponderado", gana or compensa,
            f"{total_return * 100:+.1f} % con caida {drawdown:.1f} %",
            f"batir {bench_return * 100:+.1f} % o caer menos de {bench_dd / 2:.1f} %",
        )

    # 8. Estabilidad entre anos
    por_ano = equity.resample("YE").last().pct_change().dropna()
    if len(por_ano) >= 2 and por_ano.sum() != 0:
        peso_maximo = float((por_ano / por_ano.sum()).abs().max()) * 100.0
    else:
        peso_maximo = 100.0
    report.add("Estabilidad entre anos", peso_maximo <= 60.0,
               f"el mejor ano aporta {peso_maximo:.0f} %", "<= 60 %",
               "Un solo ano bueno no es una estrategia.")

    # 9. Robustez a los parametros
    if robustness:
        peor = min(robustness.values())
        report.add("Robustez a parametros", peor >= 0.35, peor, ">= 0,35 de Sharpe",
                   "Variando el stop y el numero de posiciones un 25 %.")
    else:
        report.add("Robustez a parametros", False, "sin medir",
                   ">= 0,35 de Sharpe",
                   "Ejecuta la puerta con --robustez para comprobarlo.")

    return report


def render(report: GateReport) -> str:
    """Informe legible. Los bloqueos van primero y en primera persona."""
    lines: list[str] = []

    if report.blockers:
        lines.append("NO SE PUEDE CERTIFICAR")
        lines.append("=" * 60)
        for blocker in report.blockers:
            lines.append(f"  - {blocker}")
        lines.append("")

    if report.checks:
        # Anchos calculados sobre el contenido: con anchos fijos, "Frente al
        # equiponderado" desbordaba su columna y descuadraba la tabla entera
        # justo en la fila mas dificil de leer.
        w_name = max(len("Comprobacion"), *(len(c.name) for c in report.checks))
        w_obs = max(len("Observado"), *(len(c.observed) for c in report.checks))
        w_req = max(len("Umbral"), *(len(c.required) for c in report.checks))

        lines.append(f"{'':5s} {'Comprobacion':{w_name}s}  {'Observado':>{w_obs}s}  "
                     f"{'Umbral':>{w_req}s}")
        lines.append("-" * (7 + w_name + w_obs + w_req))
        for check in report.checks:
            marca = "OK" if check.passed else "FALLA"
            lines.append(f"{marca:5s} {check.name:{w_name}s}  "
                         f"{check.observed:>{w_obs}s}  {check.required:>{w_req}s}")
        lines.append("")

    if report.blockers:
        lines.append("El resultado de arriba NO es valido mientras haya bloqueos.")
    elif report.passed:
        lines.append("PUERTA 1 SUPERADA.")
        lines.append(
            "Esto NO dice que la estrategia vaya a ganar dinero. Dice que no ha "
            "fallado ninguna comprobacion que sepamos hacer, y que no hay un "
            "error evidente de diseno. Es condicion necesaria, nunca suficiente."
        )
    else:
        fallan = [c.name for c in report.checks if not c.passed]
        lines.append(f"PUERTA 1 NO SUPERADA. Falla: {', '.join(fallan)}.")
        lines.append(
            "La estrategia no se activa. Se ajusta o se descarta; no se opera."
        )

    return "\n".join(lines)


def robustness_sharpes(run_backtest, start, base_params: dict) -> dict[str, float]:
    """Sharpe al variar los parametros clave un 25 % arriba y abajo.

    Si el resultado depende de que el stop sea 2,5 y no 2,0, lo que se ha
    encontrado es una casualidad del historico, no un comportamiento del
    mercado.
    """
    cfg = get_trading_config()
    out: dict[str, float] = {}
    variaciones = {
        "stop 1,9x": {"atr_stop_mult": cfg.limit("atr_stop_mult") * 0.75},
        "stop 3,1x": {"atr_stop_mult": cfg.limit("atr_stop_mult") * 1.25},
        "5 posiciones": {"max_positions": max(2, round(cfg.limit("max_positions") * 0.75))},
        "9 posiciones": {"max_positions": round(cfg.limit("max_positions") * 1.25)},
    }
    for nombre, cambio in variaciones.items():
        summary = run_backtest(start, overrides=cambio)
        curva = pd.DataFrame(summary.get("curva") or [], columns=["date", "equity"])
        if curva.empty:
            out[nombre] = float("nan")
            continue
        equity = curva.set_index("date")["equity"]
        out[nombre] = mx.sharpe(equity.pct_change().dropna())
    return {k: (0.0 if np.isnan(v) else v) for k, v in out.items()}
