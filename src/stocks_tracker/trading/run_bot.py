"""CLI del bot.

    python -m stocks_tracker.trading.run_bot --mode simulated --phase propose
    python -m stocks_tracker.trading.run_bot --venue kraken --backtest
    python -m stocks_tracker.trading.run_bot --venue kraken --gate

`propose` hace un ciclo sobre la ultima fecha del almacen y deja las
intenciones registradas. `--backtest` recorre el historico sesion a sesion
contra el broker simulado, que es la unica forma de saber si la estrategia
vale algo antes de arriesgar un euro.

`--venue` elige el mercado y con el la estrategia, el universo, los limites y
—esto es lo que importa— la CARTERA. Cada venue lleva su contabilidad aparte
bajo la clave `modo:venue`: sin eso, una racha mala en cripto consumiria la
cuota de ordenes de Polymarket y el kill switch de uno pararia al otro.

Nada sale al broker sin pasar por dos puertas independientes: `risk.py` decide
si la orden respeta el mandato, y `autonomy.py` decide si ademas hace falta
que un humano la mire antes. La segunda no vigila a la estrategia sino al
programa; ver el modulo.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import pandas as pd

from ..core.config import TradingConfig, get_trading_config
from ..core.db import connect
from ..core.ids import ulid
from ..core.locking import AlreadyRunning, single_writer
from . import autonomy, journal, killswitch
from .brokers.base import OrderRequest
from .brokers.simulated import SimulatedBroker
from .context import StrategyContext, build_context
from .intents import Side
from .risk import RiskManager
from .strategies.crypto_momentum import CryptoMomentum
from .strategies.momentum_multifactor import MomentumMultifactor

# Que estrategia lleva cada mercado. Sin venue, la de acciones.
STRATEGY_BY_VENUE = {
    "kraken": CryptoMomentum,
}


def strategy_for(venue: str | None):
    """La estrategia del venue, o la de acciones si no se dice cual."""
    if venue is None:
        return MomentumMultifactor()
    builder = STRATEGY_BY_VENUE.get(venue)
    if builder is None:
        conocidos = ", ".join(sorted(STRATEGY_BY_VENUE)) or "ninguno"
        raise ValueError(
            f"El venue '{venue}' no tiene estrategia. Con estrategia: {conocidos}."
        )
    return builder(venue=venue)


def config_for(venue: str | None = None) -> TradingConfig:
    """El mandato que aplica a este mercado.

    Los limites del venue suben al nivel raiz porque es de donde los lee
    `RiskManager`. Es un mandato COMPLETO y no un parche sobre el de acciones:
    sin esto, el minimo de liquidez de bolsa —20 millones al dia— se aplicaria
    a cripto y vetaria todas las operaciones con un motivo que suena razonable
    y no tiene nada que ver. Paso exactamente eso al montarlo.
    """
    cfg = get_trading_config()
    if venue is None:
        return cfg
    vcfg = cfg.venue(venue)
    return TradingConfig(venue_key=venue,
                         raw={**cfg.raw,
                              "risk": vcfg.risk,
                              "execution": vcfg.execution,
                              "universe": vcfg.universe,
                              "capital_cap": vcfg.capital_cap,
                              "initial_equity": vcfg.initial_equity})


@dataclass
class CycleResult:
    run_id: str
    n_intents: int = 0
    n_approved: int = 0
    n_submitted: int = 0
    # Aprobadas por el riesgo pero esperando confirmacion humana. Se cuentan
    # aparte de las enviadas a proposito: si se sumaran, un dia en que todo se
    # quedo esperando pareceria un dia en que todo se ejecuto.
    n_pending: int = 0
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
              log: journal.Journal, broker=None,
              level: str = "auto", brake_settings: dict | None = None,
              live_orders_so_far: int = 1, ttl_hours: float = 18.0) -> CycleResult:
    """Un ciclo: proponer, evaluar el riesgo, registrar y (si hay broker) enviar.

    `level` por defecto es 'auto' porque quien llama sin decir nada es el
    backtest, y ahi no hay nadie a quien preguntar: una confirmacion pendiente
    en una simulacion es una orden que nunca se ejecuta y un resultado que no
    describe lo que haria el bot de verdad. En real lo pasa `_propose_once`
    leyendolo del mandato.
    """
    result = CycleResult(run_id=log.run_id, equity=ctx.equity)
    ordenes_previas = live_orders_so_far
    # Una confirmacion sin caducidad es una orden que se ejecuta al precio de
    # hace tres dias porque alguien la aprobo tarde.
    caduca = datetime.now() + timedelta(hours=float(ttl_hours))
    drawdown_pct = (
        (ctx.peak_equity - ctx.equity) / ctx.peak_equity * 100.0
        if ctx.peak_equity > 0 else 0.0
    )

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
        # Segunda puerta, independiente del riesgo. El riesgo ya dijo que la
        # orden respeta el mandato; esto decide si ademas hace falta que un
        # humano la mire. Va aqui y no dentro del riesgo porque la respuesta
        # depende del modo, y un limite que cambia entre simulacion y real
        # haria que el backtest dejase de describir lo que va a pasar.
        frenos = autonomy.requires_confirmation(
            level, notional=abs(float(order.notional or 0.0)),
            is_opening=order.side is Side.BUY,
            drawdown_pct=drawdown_pct, live_orders_so_far=ordenes_previas,
            settings=brake_settings,
        )
        if frenos:
            motivo = autonomy.explain(frenos)
            log.decision(
                order.ticker, "PENDING_CONFIRMATION",
                "_".join(f.code for f in frenos).upper(), motivo,
                {"notional": order.notional, "qty": order.qty,
                 "client_order_id": order.client_order_id},
            )
            # Y en la tabla de intenciones, que es de donde las lee quien
            # confirma. Dejarlo solo en el registro de auditoria daria una
            # orden retenida que nadie puede aprobar ni rechazar: en la
            # practica, una orden perdida.
            log.hold(order.intent_id, motivo, caduca)
            result.n_pending += 1
            continue

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
def run_backtest(start: date | None, mode: str = "simulated",
                 overrides: dict | None = None, venue: str | None = None) -> dict:
    """Recorre el historico sesion a sesion. Sin red y sin mirar el futuro.

    `overrides` cambia limites de riesgo solo para esta ejecucion. Lo usa la
    prueba de robustez de la puerta 1, que necesita repetir el backtest con el
    stop y el numero de posiciones movidos un 25 %.

    Con `venue`, se simula ESA cartera: su universo, su capital, sus limites y
    sus costes. Los de acciones no valen aqui —un 0,26 % de comision por
    operacion cambia por completo cuantas se puede permitir la estrategia— y
    usarlos daria un resultado que no describe lo que va a pasar.
    """
    cfg = config_for(venue)
    if overrides:
        cfg = TradingConfig(raw={**cfg.raw, "risk": {**cfg.risk, **overrides}})
    tickers = list((cfg.universe or {}).get("allowed") or []) if venue else None
    prices = load_prices(start, tickers=tickers)
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
    strategy = strategy_for(venue)
    risk = RiskManager(cfg=cfg, mode=mode)

    equity_curve: list[tuple[date, float]] = []
    sessions = 0
    while broker.has_next_session:
        session = broker.current_date
        ctx = build_context(as_of=session, mode=mode, broker=broker, venue=venue)
        # El estado del kill switch en el backtest vive en memoria: escribirlo
        # en la base por cada sesion simulada ensuciaria el estado real del bot.
        ctx.peak_equity = broker.peak_equity
        log = journal.Journal(run_id=ulid(), mode=ctx.mode,
                              strategy_id=strategy.strategy_id)
        # Sin frenos: en una simulacion no hay nadie a quien preguntar, y una
        # confirmacion pendiente seria una orden que nunca se ejecuta y un
        # resultado que no describe lo que haria el bot de verdad.
        run_cycle(ctx, strategy, risk, log, broker=broker, level="auto")
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
    parser.add_argument("--gate", action="store_true",
                        help="Ejecuta la puerta 1: el backtest con costes "
                             "frente a los umbrales que dan paso a la fase 7")
    parser.add_argument("--robustez", action="store_true",
                        help="Repite el backtest variando los parametros. "
                             "Tarda cinco veces mas.")
    parser.add_argument("--backtest", action="store_true",
                        help="Recorre el historico en lugar de un solo dia")
    parser.add_argument("--from", dest="start", default=None,
                        help="Fecha inicial del backtest (AAAA-MM-DD)")
    parser.add_argument("--venue", default=None,
                        help="Mercado: elige estrategia, universo, limites y "
                             "CARTERA. Sin esto, el bot de acciones.")
    args = parser.parse_args(argv)

    if args.mode != "simulated":
        # Operar con dinero exige que la puerta del venue haya dado el visto
        # bueno. La comprobacion vive en `venues.require_tradeable` y falla con
        # una frase que dice que falta, no con una traza.
        from .venues import require_tradeable

        if args.venue is None:
            print("Con --mode distinto de 'simulated' hay que decir --venue.",
                  file=sys.stderr)
            return 2
        try:
            require_tradeable(args.venue, args.mode)
        except Exception as exc:  # noqa: BLE001 — el motivo es lo unico que importa
            print(f"No se puede operar: {exc}", file=sys.stderr)
            return 2

    start = date.fromisoformat(args.start) if args.start else None

    try:
        with single_writer(f"bot-{args.venue or 'equity'}"):
            if args.gate:
                return _run_gate(start, robustez=args.robustez, venue=args.venue)

            if args.backtest:
                summary = run_backtest(start, mode=args.mode, venue=args.venue)
                print(f"Sesiones simuladas: {summary['sessions']}")
                print(f"Operaciones:        {summary['operaciones']}")
                print(f"Equity inicial:     {summary['equity_inicial']:.2f}")
                print(f"Equity final:       {summary['equity_final']:.2f}")
                print(f"Retorno:            {summary['retorno_pct']:+.2f} %")
                print()
                print("Esto es una simulacion sobre datos pasados. No dice nada "
                      "sobre el futuro.")
                return 0

            return _propose_once(args.mode, venue=args.venue)
    except AlreadyRunning as exc:
        print(f"No se ha ejecutado: {exc}", file=sys.stderr)
        return 1


def _run_gate(start: date | None, robustez: bool = False,
              venue: str | None = None) -> int:
    """Puerta 1. Devuelve 0 solo si la estrategia queda certificada."""
    from . import gate

    strategy_id = strategy_for(venue).strategy_id
    blockers = gate.find_blockers(venue=venue)
    if blockers:
        # Se comprueba ANTES de gastar minutos en un backtest cuyo resultado no
        # se podria interpretar de todas formas.
        report = gate.GateReport(blockers=blockers)
        print(gate.render(report))
        gate.save_report(report, {}, strategy_id=strategy_id)
        return 1

    summary = run_backtest(start, venue=venue)
    print(f"Sesiones simuladas: {summary['sessions']}")
    print(f"Operaciones:        {summary['operaciones']}")
    print()

    sharpes = None
    if robustez:
        print("Repitiendo el backtest con los parametros movidos un 25 %...")
        sharpes = gate.robustness_sharpes(
            lambda s, overrides: run_backtest(s, overrides=overrides, venue=venue),
            start, {}, venue=venue)
        for nombre, valor in sharpes.items():
            print(f"  {nombre:16s} Sharpe {valor:.2f}")
        print()

    report = gate.evaluate(summary, robustness=sharpes, venue=venue)
    print(gate.render(report))
    # Se guarda pase o no pase. Un suspenso es informacion tan util como un
    # aprobado, y a los dos meses nadie recuerda que umbral fallo.
    gate.save_report(report, summary, strategy_id=strategy_id)
    return 0 if report.passed else 1


def _propose_once(mode: str, venue: str | None = None,
                  broker=None) -> int:
    cfg = config_for(venue)
    strategy = strategy_for(venue)
    run_id = ulid()

    ctx = build_context(mode=mode, strategy_id=strategy.strategy_id, venue=venue,
                        broker=broker)
    clave = ctx.mode
    killswitch.clear_daily_halt(clave)
    ctx.state = str(killswitch.read_state(clave).state)

    log = journal.Journal(run_id=run_id, mode=clave, strategy_id=strategy.strategy_id)
    journal.start_run(run_id, clave, strategy.strategy_id, "propose", ctx.equity)

    nivel = cfg.autonomy_for(mode)
    risk = RiskManager(cfg=cfg, mode=clave)
    try:
        result = run_cycle(
            ctx, strategy, risk, log, broker=broker, level=nivel,
            brake_settings=autonomy.brake_settings(cfg, venue),
            live_orders_so_far=autonomy.live_orders_so_far(clave),
        )
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
    print(f"Cartera:      {clave}")
    print(f"Regimen:      {ctx.regime} ({ctx.risk_score:+.0f})")
    print(f"Autonomia:    {nivel}")
    print(f"Intenciones:  {result.n_intents}")
    print(f"Aprobadas:    {result.n_approved}")
    print(f"Enviadas:     {result.n_submitted}")
    print(f"Esperando:    {result.n_pending}")
    print(f"Registradas:  {counts['decision_log']} decisiones")
    print()
    if broker is None:
        print("Nada se ha ejecutado: sin broker, este ciclo solo propone.")
    elif result.n_pending:
        print(f"{result.n_pending} orden(es) esperan confirmacion. Para verlas:")
        print("    python -m stocks_tracker.trading.confirm")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
