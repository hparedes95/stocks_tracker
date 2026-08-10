"""`momentum_multifactor_v1` — la estrategia nucleo, rebalanceo semanal.

Compra las mejores del ranking `bot_core` que ademas estan por encima de su
media de 200 sesiones y no vienen ya recalentadas. Vende cuando dejan de estar
entre las buenas, pierden la MM200 o tocan el stop.

Dos detalles que parecen menores y no lo son:

**Histeresis.** Se entra estando en el top N, pero solo se sale al caer por
debajo del percentil 60. Sin esa banda, un valor que oscila alrededor del
puesto siete se compra y se vende cada semana: cada vuelta cuesta horquilla,
consume la cuota de ordenes del dia y acerca al limite PDT, todo a cambio de
nada.

**Banda muerta en el rebalanceo.** Solo se ajusta el peso si se desvia mas de
5 puntos porcentuales del objetivo. Con 50 EUR, corregir una desviacion de 1 pp
significa mover 50 centimos: la comision es cero pero la horquilla no, y la
orden gasta cuota que puede hacer falta para algo que si importa.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ...core.config import get_trading_config
from ..context import StrategyContext
from ..intents import Intent, IntentType, Side
from ..sizing import trailing_stop


@dataclass
class MomentumMultifactor:
    strategy_id: str = "momentum_multifactor_v1"
    params: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        cfg = get_trading_config()
        defaults = cfg.strategy(self.strategy_id)
        self.params = {**defaults, **self.params}
        self._risk = cfg.risk

    # ------------------------------------------------------------------
    def should_run_today(self, ctx: StrategyContext) -> bool:
        """Rebalanceo semanal, o cualquier dia en que falten posiciones.

        Lo segundo importa: si un stop saco tres posiciones un miercoles,
        esperar al lunes deja la cartera a medio invertir casi una semana.
        """
        weekday = int(self.params.get("rebalance_weekday", 0))
        if ctx.as_of.weekday() == weekday:
            return True
        return len(ctx.positions) < int(self._risk.get("max_positions", 7))

    # ------------------------------------------------------------------
    def propose(self, ctx: StrategyContext) -> list[Intent]:
        intents: list[Intent] = []
        intents.extend(self._stop_exits(ctx))
        stopped = {i.ticker for i in intents}
        intents.extend(self._rank_exits(ctx, skip=stopped))
        leaving = {i.ticker for i in intents}
        intents.extend(self._entries(ctx, leaving=leaving))
        return intents

    # ------------------------------------------------------------------
    def _stop_exits(self, ctx: StrategyContext) -> list[Intent]:
        """Stops tocados. Van primero y son las unicas que el riesgo no frena."""
        out = []
        for ticker, held in ctx.positions.items():
            close = ctx.indicator(ticker, "close")
            if close is None:
                continue
            stop = self._current_stop(ctx, ticker, held)
            if stop is None or close > stop:
                continue
            out.append(
                Intent(
                    ticker=ticker, side=Side.SELL, intent_type=IntentType.STOP_EXIT,
                    ref_price=close, strategy_id=self.strategy_id,
                    qty_requested=float(held.get("qty", 0.0)),
                    stop_price=stop,
                    score_pctile=ctx.score(ticker),
                    regime=ctx.regime,
                    rationale={
                        "reasons": [
                            f"El cierre ({close:.2f}) ha tocado el stop ({stop:.2f})."
                        ],
                        "flags": ["stop"],
                        "signals": [],
                    },
                )
            )
        return out

    def _current_stop(self, ctx: StrategyContext, ticker: str, held: dict) -> float | None:
        atr14 = ctx.indicator(ticker, "atr14")
        if atr14 is None:
            return None
        mult = float(self.params.get("stop_atr_mult", 2.5))
        entry = float(held.get("avg_entry_price", 0.0))
        if entry <= 0:
            return None
        if not self.params.get("trailing", True):
            return entry - mult * atr14
        row = ctx.bot_positions.get(ticker) or {}
        highest = row.get("highest_close_since_entry")
        close = ctx.indicator(ticker, "close") or entry
        highest = max(float(highest or entry), close)
        return trailing_stop(entry, highest, atr14, mult)

    # ------------------------------------------------------------------
    def _rank_exits(self, ctx: StrategyContext, skip: set[str]) -> list[Intent]:
        exit_pctile = float(self.params.get("exit_pctile", 0.60))
        out = []
        for ticker, held in ctx.positions.items():
            if ticker in skip:
                continue
            close = ctx.indicator(ticker, "close")
            if close is None:
                continue
            pctile = ctx.score(ticker)
            above = ctx.indicator(ticker, "above_sma200")

            reasons = []
            if pctile is not None and pctile < exit_pctile:
                reasons.append(
                    f"Ha caido al percentil {pctile:.0%} del ranking, por debajo "
                    f"del {exit_pctile:.0%} que marca la salida."
                )
            if above is not None and not above:
                reasons.append("Ha perdido la media de 200 sesiones.")
            if not reasons:
                continue

            out.append(
                Intent(
                    ticker=ticker, side=Side.SELL, intent_type=IntentType.CLOSE,
                    ref_price=close, strategy_id=self.strategy_id,
                    qty_requested=float(held.get("qty", 0.0)),
                    score_pctile=pctile, regime=ctx.regime,
                    rationale={"reasons": reasons, "flags": ["salida"], "signals": []},
                )
            )
        return out

    # ------------------------------------------------------------------
    def _entries(self, ctx: StrategyContext, leaving: set[str]) -> list[Intent]:
        if ctx.scores.empty:
            return []

        max_positions = int(self._risk.get("max_positions", 7))
        keeping = {t for t in ctx.positions if t not in leaving}
        room = max_positions - len(keeping)
        if room <= 0:
            return []

        max_rsi = float(self.params.get("entry_max_rsi14", 75.0))
        min_coverage = float(self._risk.get("min_data_coverage", 0.5))

        ranked = ctx.scores.sort_values("composite_pctile", ascending=False)
        out: list[Intent] = []
        for ticker, row in ranked.iterrows():
            if len(out) >= room:
                break
            if ticker in keeping or ticker in leaving:
                continue

            close = ctx.indicator(ticker, "close")
            if close is None or close <= 0:
                continue
            # Los filtros de entrada son deliberadamente pocos y explicables.
            # Cada filtro adicional es un grado de libertad mas para sobreajustar
            # el backtest, y con 50 EUR el margen para eso es nulo.
            if not ctx.indicator(ticker, "above_sma200"):
                continue
            rsi = ctx.indicator(ticker, "rsi14")
            if rsi is not None and rsi >= max_rsi:
                continue
            coverage = row.get("coverage")
            if coverage is not None and float(coverage) < min_coverage:
                continue

            pctile = float(row["composite_pctile"]) if row.get("composite_pctile") else None
            atr14 = ctx.indicator(ticker, "atr14")
            mult = float(self.params.get("stop_atr_mult", 2.5))

            out.append(
                Intent(
                    ticker=ticker, side=Side.BUY, intent_type=IntentType.OPEN,
                    ref_price=close, strategy_id=self.strategy_id,
                    # El tamano lo pone el riesgo. Aqui va el objetivo bruto: la
                    # estrategia dice que quiere una posicion, no cuanto le
                    # dejan gastar.
                    notional_requested=ctx.equity
                    * float(self._risk.get("target_position_pct", 15.0)) / 100.0,
                    stop_price=(close - mult * atr14) if atr14 else None,
                    stop_atr_mult=mult,
                    score_pctile=pctile,
                    regime=ctx.regime,
                    rationale={
                        "reasons": [
                            f"Percentil {pctile:.0%} del ranking."
                            if pctile is not None else "En cabeza del ranking.",
                            "Por encima de su media de 200 sesiones.",
                            f"RSI en {rsi:.0f}, sin sobrecompra."
                            if rsi is not None else "Sin lectura de RSI.",
                        ],
                        "flags": ["entrada"],
                        "signals": [],
                    },
                )
            )
        return out
