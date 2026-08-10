"""CLI del bot. Fase 6: solo simulacion.

    python -m stocks_tracker.trading.run_bot --mode simulated --phase propose
    python -m stocks_tracker.trading.run_bot --backtest --from 2019-01-01

`propose` hace un ciclo sobre la ultima fecha del almacen y deja las
intenciones registradas, sin ejecutar nada. `--backtest` recorre el historico
sesion a sesion contra el broker simulado, que es la unica forma de saber si la
estrategia vale algo antes de arriesgar un euro.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import date

import pandas as pd

from ..core.config import get_trading_config
from ..core.db import connect
from ..core.ids import ulid
from ..core.locking import AlreadyRunning, single_writer
from . import journal, killswitch
from .brokers.base import OrderRequest
from .brokers.simulated import SimulatedBroker
from .context import StrategyContext, build_context
from .intents import Side
from .risk import RiskManager
from .strategies.momentum_multifactor import MomentumMultifactor


@dataclass
class CycleResult:
    run_id: str
    n_intents: int = 0
    n_approved: int = 0
    n_submitted: int = 0
    equity: float = 0.0


def load_prices(start: date | None = None, tickers: list[str] | None = None
                ) -> pd.DataFrame:
    where, params = ["close IS NOT NULL"], []
    if start is not None:
        where.append("date >= ?")
        params.append(start)
    if tickers:
        where.append(f"ticker IN ({', '.join('?' for _ in tickers)})")
        params.extend(tickers)
    with connect(read_only=True) as conn:
        return conn.execute(
            f"SELECT ticker, date, open, high, low, close, volume FROM prices_daily "
            f"WHERE {' AND '.join(where)} ORDER BY date, ticker",
            params,
        ).fetchdf()


# ---------------------------------------------------------------------------
def run_cycle(ctx: StrategyContext, strategy, risk: RiskManager,
              log: journal.Journal, broker=None) -> CycleResult:
    """Un ciclo: proponer, evaluar el riesgo, registrar y (si hay broker) enviar."""
    result = CycleResult(run_id=log.run_id, equity=ctx.equity)

    if not strategy.should_run_today(ctx):
        log.decision("", "SKIPPED_NO_SIGNAL", "NOT_A_REBALANCE_DAY",
                     "Hoy no toca rebalanceo y no faltan posiciones.")
        return result

    intents = strategy.propose(ctx)
    result.n_intents = len(intents)
    if not intents:
        log.decision("", "SKIPPED_NO_SIGNAL", "NO_CANDIDATES",
                     "Ningun valor cumple las condiciones de entrada ni de salida.")
        return result

    verdicts = risk.evaluate(intents, ctx)
    for verdict in verdicts:
        log.from_verdict(verdict)
    for violation in risk.violations:
        log.violation(violation)

    orders = risk.approve(verdicts)
    result.n_approved = len(orders)

    if broker is None:
        return result

    for order in orders:
        request = OrderRequest(
            symbol=order.ticker, side=str(order.side),
            client_order_id=order.client_order_id,
            qty=order.qty if order.side is Side.SELL else None,
            notional=order.notional if order.side is Side.BUY else None,
        )
        try:
            broker.submit_order(request)
        except Exception as exc:  # noqa: BLE001
            log.decision(order.ticker, "FAILED", "BROKER_REJECTED", str(exc))
            continue
        log.decision(order.ticker, "SUBMITTED", "SUBMITTED",
                     f"Orden enviada al broker ({order.client_order_id}).",
                     {"notional": order.notional, "qty": order.qty})
        result.n_submitted += 1

    return result


# ---------------------------------------------------------------------------
def run_backtest(start: date | None, mode: str = "simulated") -> dict:
    """Recorre el historico sesion a sesion. Sin red y sin mirar el futuro."""
    cfg = get_trading_config()
    prices = load_prices(start)
    if prices.empty:
        raise SystemExit(
            "No hay precios en el almacen. Descarga el universo primero:\n"
            "    python -m stocks_tracker.ingest.run_ingest --what all"
        )

    broker = SimulatedBroker(
        prices=prices,
        initial_cash=cfg.initial_equity,
        slippage_bps=float(cfg.execution.get("slippage_bps_assumed", 15.0)),
        commission_bps=float(cfg.execution.get("commission_bps", 0.0)),
    )
    strategy = MomentumMultifactor()
    risk = RiskManager(cfg=cfg, mode=mode)

    equity_curve: list[tuple[date, float]] = []
    sessions = 0
    while broker.has_next_session:
        session = broker.current_date
        ctx = build_context(as_of=session, mode=mode, broker=broker)
        # El estado del kill switch en el backtest vive en memoria: escribirlo
        # en la base por cada sesion simulada ensuciaria el estado real del bot.
        ctx.peak_equity = broker.peak_equity
        log = journal.Journal(run_id=ulid(), mode=mode,
                              strategy_id=strategy.strategy_id)
        run_cycle(ctx, strategy, risk, log, broker=broker)
        broker.advance()
        equity_curve.append((broker.current_date, broker.get_account().equity))
        sessions += 1

    final = broker.get_account()
    return {
        "sessions": sessions,
        "equity_final": final.equity,
        "equity_inicial": cfg.initial_equity,
        "retorno_pct": (final.equity / cfg.initial_equity - 1.0) * 100.0,
        "operaciones": len(broker.fills),
        "curva": equity_curve,
    }


# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Bot de trading. En la fase 6 solo simula: no toca dinero.",
    )
    parser.add_argument("--mode", default="simulated",
                        choices=["simulated", "paper", "live"])
    parser.add_argument("--phase", default="propose",
                        choices=["propose", "eod"])
    parser.add_argument("--backtest", action="store_true",
                        help="Recorre el historico en lugar de un solo dia")
    parser.add_argument("--from", dest="start", default=None,
                        help="Fecha inicial del backtest (AAAA-MM-DD)")
    args = parser.parse_args(argv)

    if args.mode != "simulated":
        print(
            f"El modo '{args.mode}' no esta disponible. La fase 6 es solo "
            "simulacion; operar en papel llega en la fase 7, y no antes de que "
            "el backtest con costes supere la puerta 1.",
            file=sys.stderr,
        )
        return 2

    start = date.fromisoformat(args.start) if args.start else None

    try:
        with single_writer("bot"):
            if args.backtest:
                summary = run_backtest(start, mode=args.mode)
                print(f"Sesiones simuladas: {summary['sessions']}")
                print(f"Operaciones:        {summary['operaciones']}")
                print(f"Equity inicial:     {summary['equity_inicial']:.2f}")
                print(f"Equity final:       {summary['equity_final']:.2f}")
                print(f"Retorno:            {summary['retorno_pct']:+.2f} %")
                print()
                print("Esto es una simulacion sobre datos pasados. No dice nada "
                      "sobre el futuro.")
                return 0

            return _propose_once(args.mode)
    except AlreadyRunning as exc:
        print(f"No se ha ejecutado: {exc}", file=sys.stderr)
        return 1


def _propose_once(mode: str) -> int:
    cfg = get_trading_config()
    strategy = MomentumMultifactor()
    run_id = ulid()

    ctx = build_context(mode=mode, strategy_id=strategy.strategy_id)
    killswitch.clear_daily_halt(mode)
    ctx.state = str(killswitch.read_state(mode).state)

    log = journal.Journal(run_id=run_id, mode=mode, strategy_id=strategy.strategy_id)
    journal.start_run(run_id, mode, strategy.strategy_id, "propose", ctx.equity)

    risk = RiskManager(cfg=cfg, mode=mode)
    try:
        result = run_cycle(ctx, strategy, risk, log)
        counts = log.flush()
        journal.finish_run(run_id, "OK", ctx.equity,
                           {"intents": result.n_intents,
                            "approved": result.n_approved})
    except Exception as exc:  # noqa: BLE001
        log.flush()
        journal.finish_run(run_id, "FAILED", ctx.equity, {}, str(exc))
        print(f"El ciclo ha fallado: {exc}", file=sys.stderr)
        return 1

    print(f"Fecha:        {ctx.as_of}")
    print(f"Regimen:      {ctx.regime} ({ctx.risk_score:+.0f})")
    print(f"Intenciones:  {result.n_intents}")
    print(f"Aprobadas:    {result.n_approved}")
    print(f"Registradas:  {counts['decision_log']} decisiones")
    print()
    print("Nada se ha ejecutado: la fase 6 solo propone y registra.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
