"""El que veta. Unico punto donde se aplican los limites.

Por que el riesgo veta en lugar de vivir dentro de cada estrategia:

1. **Punto unico.** Si los limites viven en la estrategia, anadir una cuarta
   significa reimplementar —y poder olvidar— el tope por activo. Con el veto,
   una estrategia nueva no puede saltarse un limite ni por descuido: no tiene
   acceso al camino de ejecucion.
2. **Falla cerrado.** Ante cualquier excepcion, dato que falta o estado raro,
   la respuesta es `VETO`. Un generador de senales que ademas controla el
   riesgo tiende a fallar abierto: si el calculo del limite peta, la orden pasa.
3. **Auditable.** Cada veto lleva regla, numeros y frase. "Por que no compro X
   el dia Y" se responde con SQL, sin leer codigo.
4. **`RESIZE` en vez de `VETO` cuando procede.** Recortar de 15 a 9 EUR para
   respetar un tope conserva la idea y deja constancia del recorte.

Sobre el orden de evaluacion: la adenda lista las reglas numeradas. Las de
nivel cuenta van primero, igual que alli. Dentro de cada intencion, en cambio,
se comprueba primero la elegibilidad (universo, evidencia, resultados,
duplicados) y despues el dimensionamiento. La adenda las listaba al reves, pero
dimensionar un ticker que no es elegible y anotar el veto como
'position_sizing_atr' cuando el motivo real era 'universe_gate' hace ilegible
el registro de auditoria, que es justo para lo que existe.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from ..core.config import TradingConfig, get_trading_config
from .context import StrategyContext
from .intents import (
    _MINT,
    ApprovedOrder,
    Decision,
    Intent,
    IntentType,
    RiskVerdict,
    Side,
)
from .sizing import size_by_atr


@dataclass(frozen=True)
class AccountGate:
    """Resultado de las reglas de nivel cuenta."""

    rule_id: str = ""
    reason_code: str = ""
    reason_text: str = ""
    action: str = ""          # veto | halt_new | flatten
    observed: float | None = None
    limit_value: float | None = None

    @property
    def blocks_everything(self) -> bool:
        return bool(self.rule_id)


@dataclass
class RiskManager:
    """Evalua intenciones contra el mandato. Nada llega al broker sin pasar aqui."""

    cfg: TradingConfig = field(default_factory=get_trading_config)
    mode: str = "simulated"
    violations: list[dict] = field(default_factory=list)
    account_gate: AccountGate = field(default_factory=AccountGate)

    # ------------------------------------------------------------------
    def evaluate(self, intents: list[Intent], ctx: StrategyContext) -> list[RiskVerdict]:
        """Un veredicto por intencion, siempre. Nunca lanza."""
        try:
            return self._evaluate(list(intents), ctx)
        except Exception as exc:  # noqa: BLE001 — fallar cerrado es el objetivo
            # Si el riesgo revienta, TODO se veta. Dejar pasar ordenes porque
            # el modulo que decide si son seguras ha fallado es la peor de las
            # combinaciones posibles.
            return [
                self._veto(
                    intent, "risk_internal_error", "RISK_INTERNAL_ERROR",
                    f"El control de riesgo ha fallado ({type(exc).__name__}); "
                    "por seguridad no se aprueba nada.",
                )
                for intent in intents
            ]

    # ------------------------------------------------------------------
    def _evaluate(self, intents: list[Intent], ctx: StrategyContext) -> list[RiskVerdict]:
        self.violations = []
        self.account_gate = self._account_rules(ctx)

        verdicts: list[RiskVerdict] = []

        if self.account_gate.blocks_everything:
            gate = self.account_gate
            for intent in intents:
                # Un cierre de proteccion sigue pasando mientras el estado lo
                # permita: dejar de protegerse cuando algo va mal es empeorarlo.
                if intent.is_protective and gate.action != "veto_all_including_exits":
                    verdicts.append(self._approve_exit(intent, ctx))
                    continue
                verdicts.append(
                    self._veto(intent, gate.rule_id, gate.reason_code,
                               gate.reason_text, gate.observed, gate.limit_value)
                )
            return verdicts

        # Prioridad por percentil cuando haya que repartir cupo escaso: si solo
        # caben tres ordenes, que sean las tres mejores y no las tres primeras.
        ordered = sorted(
            intents,
            key=lambda i: (not i.is_exit, -(i.score_pctile or 0.0)),
        )

        planned = _PlannedState.from_context(ctx)
        for intent in ordered:
            verdict = self._evaluate_one(intent, ctx, planned)
            verdicts.append(verdict)
            if verdict.approved:
                planned.apply(verdict, ctx)

        by_id = {v.intent.intent_id: v for v in verdicts}
        return [by_id[i.intent_id] for i in intents]

    # ------------------------------------------------------------------
    # Reglas de nivel cuenta (1-5). Un fallo aqui corta el ciclo entero.
    # ------------------------------------------------------------------
    def _account_rules(self, ctx: StrategyContext) -> AccountGate:
        # 1. kill_switch_active
        if ctx.state == "HALTED":
            return AccountGate(
                "kill_switch_active", "KILL_SWITCH_HALTED",
                "El bot esta parado tras liquidar. Solo se reactiva a mano.",
                "veto_all_including_exits",
            )
        if ctx.state in ("HALT_NEW", "FLATTEN_PENDING"):
            return AccountGate(
                "kill_switch_active", "KILL_SWITCH_HALT_NEW",
                "El bot no abre posiciones nuevas; los cierres de proteccion "
                "siguen activos.",
                "veto",
            )

        # 2. stale_data
        max_age = self.cfg.limit("max_data_staleness_hours")
        if ctx.data_age_hours > max_age:
            self._record("stale_data", "block", None, ctx.data_age_hours, max_age,
                         "veto")
            return AccountGate(
                "stale_data", "STALE_DATA",
                f"Los precios tienen {ctx.data_age_hours:.0f} h y el limite son "
                f"{max_age:.0f}. Operar sobre datos viejos es operar a ciegas.",
                "veto", ctx.data_age_hours, max_age,
            )

        # 3. max_drawdown -> kill switch
        max_dd = self.cfg.limit("max_drawdown_pct")
        if ctx.peak_equity > 0:
            drawdown = (1.0 - ctx.equity / ctx.peak_equity) * 100.0
            if drawdown >= max_dd:
                self._record("max_drawdown", "kill", None, drawdown, max_dd,
                             "flatten")
                return AccountGate(
                    "max_drawdown", "MAX_DRAWDOWN",
                    f"Caida del {drawdown:.1f} % desde el maximo, con el limite "
                    f"en {max_dd:.0f} %. Se liquida y se para.",
                    "flatten", drawdown, max_dd,
                )

        # 4. daily_loss
        max_daily = self.cfg.limit("max_daily_loss_pct")
        if ctx.day_start_equity > 0:
            loss = (1.0 - ctx.equity / ctx.day_start_equity) * 100.0
            if loss >= max_daily:
                self._record("daily_loss", "block", None, loss, max_daily,
                             "halt_new")
                return AccountGate(
                    "daily_loss", "DAILY_LOSS",
                    f"Perdida del {loss:.1f} % en el dia, con el limite en "
                    f"{max_daily:.0f} %. No se abre nada mas hoy.",
                    "halt_new", loss, max_daily,
                )

        # 5. broker_blocked
        if ctx.positions and getattr(ctx, "trading_blocked", False):
            return AccountGate(
                "broker_blocked", "BROKER_BLOCKED",
                "El broker tiene la cuenta bloqueada.", "veto",
            )

        return AccountGate()

    # ------------------------------------------------------------------
    def _evaluate_one(
        self, intent: Intent, ctx: StrategyContext, planned: _PlannedState
    ) -> RiskVerdict:
        if intent.is_exit:
            return self._evaluate_exit(intent, ctx, planned)
        return self._evaluate_entry(intent, ctx, planned)

    # --- salidas -------------------------------------------------------
    def _evaluate_exit(
        self, intent: Intent, ctx: StrategyContext, planned: _PlannedState
    ) -> RiskVerdict:
        held = ctx.positions.get(intent.ticker, {})
        qty_held = float(held.get("qty", 0.0))
        if qty_held <= 0:
            return self._veto(intent, "no_position", "NO_POSITION",
                              f"No hay posicion en {intent.ticker} que cerrar.")

        # 6. pdt_limit — un cierre que crearia un day trade se veta, salvo que
        #    sea un stop: protegerse tiene prioridad sobre la cuota de FINRA.
        max_daytrades = self.cfg.limit("max_day_trades_5d")
        if (not intent.is_protective
                and planned.daytrade_count >= max_daytrades
                and intent.ticker in planned.opened_today):
            self._record("pdt_limit", "block", intent.ticker,
                         planned.daytrade_count, max_daytrades, "veto")
            return self._veto(
                intent, "pdt_limit", "PDT_LIMIT",
                f"Cerrar hoy {intent.ticker} seria el day trade numero "
                f"{planned.daytrade_count + 1}, y el limite propio son "
                f"{max_daytrades:.0f} en cinco sesiones.",
                planned.daytrade_count, max_daytrades,
            )

        # 16. min_holding_days — nunca frena un stop.
        if not intent.is_protective:
            min_days = self.cfg.limit("min_holding_days")
            opened = self._opened_at(ctx, intent.ticker)
            if opened is not None and (ctx.as_of - opened).days < min_days:
                return self._veto(
                    intent, "min_holding_days", "MIN_HOLDING_DAYS",
                    f"{intent.ticker} se abrio hace "
                    f"{(ctx.as_of - opened).days} sesiones y el minimo son "
                    f"{min_days:.0f}. Cerrar antes crea day trades por descuido.",
                )

        qty = min(intent.qty_requested or qty_held, qty_held)
        return RiskVerdict(
            intent=intent, decision=Decision.APPROVE, rule_id="exit_allowed",
            reason_code="EXIT_OK",
            reason_text=("Cierre por stop." if intent.is_protective
                         else "Cierre aprobado."),
            qty_approved=qty, stop_price=intent.stop_price,
        )

    def _approve_exit(self, intent: Intent, ctx: StrategyContext) -> RiskVerdict:
        held = ctx.positions.get(intent.ticker, {})
        qty = min(intent.qty_requested or float(held.get("qty", 0.0)),
                  float(held.get("qty", 0.0)))
        if qty <= 0:
            return self._veto(intent, "no_position", "NO_POSITION",
                              f"No hay posicion en {intent.ticker} que cerrar.")
        return RiskVerdict(
            intent=intent, decision=Decision.APPROVE, rule_id="protective_exit",
            reason_code="PROTECTIVE_EXIT",
            reason_text="Cierre de proteccion: se ejecuta aunque el bot este parado.",
            qty_approved=qty,
        )

    # --- entradas ------------------------------------------------------
    def _evaluate_entry(
        self, intent: Intent, ctx: StrategyContext, planned: _PlannedState
    ) -> RiskVerdict:
        ticker = intent.ticker

        # 20. duplicate_intent
        max_per_ticker = self.cfg.limit("max_orders_per_ticker_per_day")
        if ticker in planned.tickers_ordered_today:
            return self._veto(
                intent, "duplicate_intent", "DUPLICATE_INTENT",
                f"Ya hay una orden de {ticker} hoy y el limite es "
                f"{max_per_ticker:.0f} por valor y dia.",
            )

        # 18. universe_gate
        veto = self._universe_gate(intent, ctx)
        if veto is not None:
            return veto

        # 17. evidence_gate
        if self.cfg.risk.get("block_if_evidence_not_validated", True):
            veto = self._evidence_gate(intent, ctx)
            if veto is not None:
                return veto

        # 15. earnings_blackout
        before = int(self.cfg.limit("block_days_before_earnings"))
        after = int(self.cfg.limit("block_days_after_earnings"))
        if ctx.has_earnings_near(ticker, before, after):
            return self._veto(
                intent, "earnings_blackout", "EARNINGS_BLACKOUT",
                f"{ticker} presenta resultados dentro de la ventana de "
                f"{before} dias antes y {after} despues. Es un evento binario "
                "que ninguna de nuestras senales predice.",
            )

        # 7. max_orders_per_day
        max_orders = self.cfg.limit("max_orders_per_day")
        if planned.orders_today >= max_orders:
            self._record("max_orders_per_day", "block", ticker,
                         planned.orders_today, max_orders, "veto")
            return self._veto(
                intent, "max_orders_per_day", "MAX_ORDERS_PER_DAY",
                f"Ya hay {planned.orders_today:.0f} ordenes hoy y el limite es "
                f"{max_orders:.0f}.",
                planned.orders_today, max_orders,
            )

        # 10. max_positions
        max_positions = self.cfg.limit("max_positions")
        is_new = ticker not in planned.positions
        if is_new and len(planned.positions) >= max_positions:
            self._record("max_positions", "block", ticker,
                         len(planned.positions), max_positions, "veto")
            return self._veto(
                intent, "max_positions", "MAX_POSITIONS",
                f"Ya hay {len(planned.positions)} posiciones y el mandato "
                f"permite {max_positions:.0f}.",
                float(len(planned.positions)), max_positions,
            )

        max_new = self.cfg.limit("max_new_positions_per_day")
        if is_new and planned.new_positions_today >= max_new:
            return self._veto(
                intent, "max_new_positions_per_day", "MAX_NEW_POSITIONS",
                f"Ya se han abierto {planned.new_positions_today:.0f} posiciones "
                f"hoy y el limite diario es {max_new:.0f}.",
            )

        # 8/9/11/12. Los topes se comprueban ANTES de dimensionar cuando ya no
        # queda ni un euro de margen. Si se hiciera despues, el veto quedaria
        # registrado como 'position_sizing_atr' —"la posicion es demasiado
        # pequena para el riesgo"— cuando el motivo real es que no hay
        # efectivo, y el registro de auditoria contaria una historia falsa.
        # Los recortes (RESIZE) si van despues: para recortar hay que saber
        # antes cuanto se queria.
        max_pos_pct = self.cfg.limit("max_position_pct")
        current = planned.positions.get(ticker, 0.0)
        position_room = ctx.equity * max_pos_pct / 100.0 - current
        if position_room <= 0:
            self._record("max_position_pct", "block", ticker, current,
                         ctx.equity * max_pos_pct / 100.0, "veto")
            return self._veto(
                intent, "max_position_pct", "MAX_POSITION_PCT",
                f"{ticker} ya ocupa el tope del {max_pos_pct:.0f} % de la cartera.",
                current, ctx.equity * max_pos_pct / 100.0,
            )

        max_sector = self.cfg.limit("max_sector_pct")
        sector = ctx.sector(ticker)
        sector_now = planned.sectors.get(sector, 0.0)
        sector_room = ctx.equity * max_sector / 100.0 - sector_now
        if sector_room <= 0:
            self._record("max_sector_pct", "block", ticker, sector_now,
                         ctx.equity * max_sector / 100.0, "veto")
            return self._veto(
                intent, "max_sector_pct", "MAX_SECTOR_PCT",
                f"El sector {sector} ya esta en el tope del {max_sector:.0f} %.",
                sector_now, ctx.equity * max_sector / 100.0,
            )

        max_gross = self.cfg.limit("max_gross_exposure_pct")
        gross_room = ctx.equity * max_gross / 100.0 - planned.gross_exposure
        if gross_room <= 0:
            self._record("max_gross_exposure", "block", ticker,
                         planned.gross_exposure, ctx.equity * max_gross / 100.0,
                         "veto")
            return self._veto(
                intent, "max_gross_exposure", "MAX_GROSS_EXPOSURE",
                f"La exposicion ya esta en el tope del {max_gross:.0f} %.",
                planned.gross_exposure, ctx.equity * max_gross / 100.0,
            )

        min_cash = self.cfg.limit("min_cash_pct")
        reserve = ctx.equity * min_cash / 100.0
        cash_room = planned.cash - reserve
        if cash_room <= 0:
            self._record("min_cash", "block", ticker, planned.cash, reserve, "veto")
            return self._veto(
                intent, "min_cash", "MIN_CASH",
                f"Quedan {planned.cash:.2f} y la reserva minima es "
                f"{reserve:.2f}: no hay efectivo disponible sin tocarla.",
                planned.cash, reserve,
            )

        # 13. position_sizing_atr
        atr14 = ctx.indicator(ticker, "atr14")
        if atr14 is None:
            return self._veto(
                intent, "position_sizing_atr", "NO_ATR",
                f"Sin ATR de {ticker} no se puede colocar el stop, y sin stop "
                "no se abre.",
            )

        sized = size_by_atr(
            equity=ctx.equity,
            price=intent.ref_price,
            atr14=atr14,
            cash_available=planned.cash,
            regime=ctx.regime,
            risk_per_trade_pct=self.cfg.limit("risk_per_trade_pct"),
            atr_stop_mult=self.cfg.limit("atr_stop_mult"),
            max_position_pct=self.cfg.limit("max_position_pct"),
            target_position_pct=self.cfg.limit("target_position_pct"),
            min_cash_pct=self.cfg.limit("min_cash_pct"),
            min_notional=self.cfg.limit("min_notional"),
        )
        if not sized.ok:
            return self._veto(
                intent, "position_sizing_atr", sized.reason_code,
                self._sizing_message(sized.reason_code, ticker),
            )

        # Recorte a la holgura de cada tope. Se hace ahora y no antes porque
        # para recortar hay que saber primero cuanto se queria comprar.
        notional = min(sized.notional, position_room, sector_room, gross_room,
                       cash_room)

        # 19. no_short_no_leverage
        if notional <= 0:
            return self._veto(
                intent, "no_short_no_leverage", "NO_ROOM",
                f"No queda margen para comprar {ticker} sin apalancarse.",
            )

        # 14. min_notional — despues de todos los recortes.
        min_notional = self.cfg.limit("min_notional")
        if notional < min_notional:
            return self._veto(
                intent, "min_notional", "BELOW_MIN_NOTIONAL",
                f"Tras aplicar los limites quedan {notional:.2f} y el minimo "
                f"del broker es {min_notional:.2f}. No se relaja ningun limite "
                "para que quepa la orden.",
                notional, min_notional,
            )

        resized = notional < sized.notional - 1e-9
        if resized:
            self._record("resize", "info", ticker, sized.notional, notional,
                         "resize")

        qty = notional / intent.ref_price
        stop_distance = intent.ref_price - sized.stop_price
        return RiskVerdict(
            intent=intent,
            decision=Decision.RESIZE if resized else Decision.APPROVE,
            rule_id="position_sizing_atr",
            reason_code="RESIZED" if resized else "APPROVED",
            reason_text=(
                f"Recortado de {sized.notional:.2f} a {notional:.2f} para "
                f"respetar los limites."
                if resized else
                f"Aprobado {notional:.2f}, limitado por {sized.capped_by}."
            ),
            qty_approved=qty,
            notional_approved=notional,
            stop_price=sized.stop_price,
            risk_amount=qty * stop_distance,
            notes={"capped_by": sized.capped_by, "sector": sector,
                   "atr14": atr14, "regime": ctx.regime},
        )

    # ------------------------------------------------------------------
    def _universe_gate(self, intent: Intent, ctx: StrategyContext) -> RiskVerdict | None:
        ticker = intent.ticker
        if ctx.universe_allowed and ticker not in ctx.universe_allowed:
            return self._veto(
                intent, "universe_gate", "OUT_OF_UNIVERSE",
                f"{ticker} no esta en los indices permitidos por el mandato.",
            )

        min_price = float(self.cfg.universe.get("min_price", 0.0))
        if intent.ref_price < min_price:
            return self._veto(
                intent, "universe_gate", "PRICE_TOO_LOW",
                f"{ticker} cotiza a {intent.ref_price:.2f} y el minimo son "
                f"{min_price:.2f}.",
                intent.ref_price, min_price,
            )

        min_dv = float(self.cfg.universe.get("min_dollar_volume_20d", 0.0))
        dv = ctx.dollar_volume_20d.get(ticker)
        if min_dv and (dv is None or dv < min_dv):
            return self._veto(
                intent, "universe_gate", "ILLIQUID",
                f"{ticker} negocia {(dv or 0) / 1e6:.1f} M al dia y el minimo "
                f"son {min_dv / 1e6:.0f} M. Las horquillas se comerian la "
                "ventaja de la senal.",
                dv or 0.0, min_dv,
            )

        coverage = ctx.score(ticker, "coverage")
        min_coverage = self.cfg.limit("min_data_coverage")
        if coverage is not None and coverage < min_coverage:
            return self._veto(
                intent, "universe_gate", "LOW_COVERAGE",
                f"Solo hay datos del {coverage:.0%} de los factores de {ticker}; "
                f"el minimo es {min_coverage:.0%}.",
                coverage, min_coverage,
            )
        return None

    def _evidence_gate(self, intent: Intent, ctx: StrategyContext) -> RiskVerdict | None:
        """Solo se opera lo que la fase 3 haya validado.

        Es el limite que da sentido a todo lo anterior: sin el, el bot operaria
        senales que nadie ha comprobado que funcionen, que es exactamente el
        fallo que este proyecto trata de evitar.
        """
        signals = intent.rationale.get("signals") or []
        if not signals:
            return None  # no la dispara una senal; el ranking basta
        for signal_id in signals:
            if ctx.evidence.get(signal_id) != "validada":
                return self._veto(
                    intent, "evidence_gate", "EVIDENCE_NOT_VALIDATED",
                    f"La senal {signal_id} no esta validada contra su historico "
                    f"(estado: {ctx.evidence.get(signal_id) or 'sin datos'}).",
                )
        return None

    # ------------------------------------------------------------------
    def approve(self, verdicts: list[RiskVerdict]) -> list[ApprovedOrder]:
        """Acuna las ordenes. Unico sitio del sistema donde se usa la llave."""
        orders = []
        for verdict in verdicts:
            if not verdict.approved:
                continue
            intent = verdict.intent
            orders.append(
                ApprovedOrder(
                    _MINT,
                    intent_id=intent.intent_id,
                    ticker=intent.ticker,
                    side=intent.side,
                    intent_type=intent.intent_type,
                    ref_price=intent.ref_price,
                    qty=verdict.qty_approved,
                    notional=(verdict.notional_approved
                              if intent.side is Side.BUY else None),
                    stop_price=verdict.stop_price,
                    risk_amount=verdict.risk_amount,
                    rule_notes={"rule_id": verdict.rule_id,
                                "reason_code": verdict.reason_code,
                                **verdict.notes},
                )
            )
        return orders

    # ------------------------------------------------------------------
    @staticmethod
    def _opened_at(ctx: StrategyContext, ticker: str) -> date | None:
        row = ctx.bot_positions.get(ticker) or {}
        opened = row.get("opened_at")
        if opened is None:
            return None
        return opened.date() if hasattr(opened, "date") else opened

    @staticmethod
    def _sizing_message(reason_code: str, ticker: str) -> str:
        return {
            "NO_SIZING_INPUTS": f"Faltan precio o ATR de {ticker}.",
            "STOP_BELOW_ZERO": (
                f"{ticker} es tan volatil respecto a su precio que el stop "
                "caeria por debajo de cero: no se puede proteger la posicion."
            ),
            "POSITION_TOO_SMALL_FOR_RISK": (
                f"El tamano que sale para {ticker} esta por debajo del minimo "
                "del broker y subirlo romperia el tope por activo."
            ),
            "MIN_NOTIONAL_ABOVE_CASH": (
                f"El minimo que acepta el broker para {ticker} es mayor que el "
                "efectivo disponible sin tocar la reserva."
            ),
        }.get(reason_code, f"No se puede dimensionar {ticker}.")

    def _record(self, rule_id: str, severity: str, ticker: str | None,
                observed: float | None, limit_value: float | None,
                action: str) -> None:
        self.violations.append({
            "rule_id": rule_id, "severity": severity, "ticker": ticker,
            "observed": observed, "limit_value": limit_value,
            "headroom": (None if observed is None or limit_value is None
                         else limit_value - observed),
            "action_taken": action,
        })

    @staticmethod
    def _veto(intent: Intent, rule_id: str, reason_code: str, text: str,
              observed: float | None = None,
              limit_value: float | None = None) -> RiskVerdict:
        return RiskVerdict(
            intent=intent, decision=Decision.VETO, rule_id=rule_id,
            reason_code=reason_code, reason_text=text,
            observed=observed, limit_value=limit_value,
        )


@dataclass
class _PlannedState:
    """Cartera tal y como quedaria si se aprobase lo evaluado hasta ahora.

    Sin esto, cinco intenciones evaluadas contra la cartera actual podrian
    aprobarse todas y romper juntas un tope que ninguna rompia por separado.
    """

    cash: float
    gross_exposure: float
    positions: dict[str, float]
    sectors: dict[str, float]
    orders_today: int
    new_positions_today: int
    tickers_ordered_today: set[str]
    daytrade_count: int
    opened_today: set[str]

    @classmethod
    def from_context(cls, ctx: StrategyContext) -> _PlannedState:
        return cls(
            cash=ctx.cash,
            gross_exposure=sum(float(p.get("market_value", 0.0))
                               for p in ctx.positions.values()),
            positions={t: float(p.get("market_value", 0.0))
                       for t, p in ctx.positions.items()},
            sectors=ctx.sector_exposure(),
            orders_today=ctx.orders_today,
            new_positions_today=0,
            tickers_ordered_today=set(ctx.tickers_ordered_today),
            daytrade_count=0,
            opened_today=set(),
        )

    def apply(self, verdict: RiskVerdict, ctx: StrategyContext) -> None:
        intent = verdict.intent
        self.orders_today += 1
        self.tickers_ordered_today.add(intent.ticker)

        if intent.intent_type is IntentType.OPEN and intent.ticker not in self.positions:
            self.new_positions_today += 1

        if intent.side is Side.BUY:
            amount = verdict.notional_approved or 0.0
            self.cash -= amount
            self.gross_exposure += amount
            self.positions[intent.ticker] = self.positions.get(intent.ticker, 0.0) + amount
            sector = ctx.sector(intent.ticker)
            self.sectors[sector] = self.sectors.get(sector, 0.0) + amount
            self.opened_today.add(intent.ticker)
        else:
            amount = (verdict.qty_approved or 0.0) * intent.ref_price
            self.cash += amount
            self.gross_exposure = max(self.gross_exposure - amount, 0.0)
            remaining = self.positions.get(intent.ticker, 0.0) - amount
            if remaining <= 1e-9:
                self.positions.pop(intent.ticker, None)
            else:
                self.positions[intent.ticker] = remaining
            sector = ctx.sector(intent.ticker)
            self.sectors[sector] = max(self.sectors.get(sector, 0.0) - amount, 0.0)
            if intent.ticker in self.opened_today:
                self.daytrade_count += 1
