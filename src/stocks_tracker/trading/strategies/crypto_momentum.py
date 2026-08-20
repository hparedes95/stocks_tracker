"""`crypto_momentum_v1` — seguimiento de tendencia con filtro de regimen.

No es ingeniosa a proposito. Es la familia de estrategias sistematicas mejor
documentada —momentum transversal con filtro de tendencia— y en cripto, con
seis monedas y un historico corto, lo ingenioso es lo que pierde dinero: cada
regla extra es un grado de libertad mas para ajustar el pasado.

**Ningun parametro esta optimizado sobre los datos.** Las medias son la de 200
y la de 50, los horizontes de momentum son 3 y 6 meses, y pesan igual. Son los
valores de manual, elegidos antes de mirar ningun resultado. Esa es la defensa
principal contra el sobreajuste: si no se busca, no se puede sobreajustar. Si
algun dia se ajusta uno de estos numeros para que el backtest mejore, lo que
mejora es el backtest y nada mas.

Como decide:

1. **Regimen.** Si bitcoin esta por debajo de su media de 200 sesiones, no se
   abre nada. En cripto los desplomes son del 70-80 %, y este interruptor es
   casi lo unico que separa perder un 20 % de perderlo casi todo. No liquida de
   golpe: las posiciones salen por sus propias reglas, que en un mercado asi se
   disparan solas en pocos dias.
2. **Ranking.** Se ordenan las monedas por el promedio de su puesto en momentum
   a 3 y a 6 meses. Dos horizontes en vez de uno porque asi ninguna rareza de
   un periodo concreto manda ella sola.
3. **Entrada.** Las mejores del ranking que ademas esten sobre su media de 50.
   El ranking dice cual es la mejor del grupo; la media de 50 dice si esa mejor
   esta subiendo o simplemente cae menos que las demas. Sin ese segundo filtro,
   en un mercado bajista se compra siempre la que menos baja.
4. **Salida.** Se cae del ranking, pierde su media de 50, o toca el stop.

**Histeresis.** Se entra estando entre las `max_positions` primeras y solo se
sale al caer por debajo de `max_positions + 1`. Sin esa banda, la moneda que
oscila alrededor del cuarto puesto se compra y se vende continuamente: con seis
monedas y posiciones de seis euros, cada vuelta se come en horquilla y comision
mas de lo que puede ganar.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ...core.config import get_trading_config
from ..context import StrategyContext
from ..intents import Intent, IntentType, Side
from ..sizing import trailing_stop

# El par que marca el regimen de todo el mercado. En cripto la correlacion con
# bitcoin es tan alta que mirar el regimen de cada moneda por separado da casi
# siempre la misma respuesta, con mas ruido.
REGIME_TICKER = "BTC/EUR"

# Horizontes de momentum, de manual y sin optimizar. `roc_3m` y `roc_6m` ya los
# calcula el motor de indicadores para todo lo que hay en el almacen.
MOMENTUM_FIELDS = ("roc_3m", "roc_6m")


def flag(ctx: StrategyContext, ticker: str, name: str) -> bool | None:
    """Un indicador booleano leido como booleano, con `None` si no hay dato.

    `ctx.indicator` devuelve SIEMPRE float —1.0 o 0.0 para los booleanos— asi
    que compararlo con `is True` no se cumple nunca y el filtro queda
    desactivado sin que nada falle: la estrategia dejaria de mirar la media de
    50 y seguiria diciendo en el dashboard que la mira.

    Los tres estados se distinguen a proposito. "No hay dato" no es "esta por
    debajo": para entrar, la duda tiene que impedir la compra; para vender, no
    puede provocarla, o un fallo de descarga se convertiria en una orden.
    """
    valor = ctx.indicator(ticker, name)
    return None if valor is None else bool(valor)


@dataclass
class CryptoMomentum:
    strategy_id: str = "crypto_momentum_v1"
    venue: str = "kraken"
    params: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        cfg = get_trading_config()
        venue = cfg.venue(self.venue)
        self._risk = venue.risk
        self.params = {
            "stop_atr_mult": venue.risk.get("atr_stop_mult", 4.0),
            "trailing": venue.risk.get("atr_trailing", True),
            "max_positions": venue.risk.get("max_positions", 4),
            "exit_rank_buffer": 1,
            **self.params,
        }
        self._universe = set((venue.universe or {}).get("allowed") or [])

    # ------------------------------------------------------------------
    def should_run_today(self, ctx: StrategyContext) -> bool:
        """Todos los dias. Cripto no cierra.

        En acciones el rebalanceo es semanal porque el mercado abre cinco dias
        y las senales tardan en cambiar. Aqui un fin de semana entero puede
        mover un 30 %, y esperar al lunes es tan arbitrario como esperar al
        jueves.

        Lo que evita que esto se convierta en operar a diario no es el
        calendario: son `max_orders_per_day: 2` y `min_holding_days: 21` del
        mandato, que son limites de riesgo y no de estrategia.
        """
        return True

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
    def bullish_regime(self, ctx: StrategyContext) -> bool | None:
        """Si bitcoin esta sobre su media de 200. `None` si no se sabe.

        La diferencia entre `False` y `None` importa: sin dato de bitcoin no se
        puede afirmar que el mercado este bajista, pero tampoco alcista, y en
        la duda no se abre nada. Tratar el `None` como alcista abriria
        posiciones justo cuando faltan datos, que es cuando peor idea es.
        """
        return flag(ctx, REGIME_TICKER, "above_sma200")

    # ------------------------------------------------------------------
    def ranking(self, ctx: StrategyContext) -> list[str]:
        """Monedas ordenadas de mejor a peor momentum.

        Se promedian PUESTOS y no rentabilidades. Promediar rentabilidades deja
        que una subida del 400 % en un horizonte tape lo que diga el otro; con
        puestos, los dos horizontes pesan igual sea cual sea la escala.
        """
        candidatos = []
        for ticker in sorted(self._tradeable(ctx)):
            valores = [ctx.indicator(ticker, campo) for campo in MOMENTUM_FIELDS]
            if any(v is None for v in valores):
                # Sin los dos horizontes no se puede comparar de forma justa
                # con las que si los tienen: quedaria fuera o dentro segun que
                # datos le falten, no segun como se este comportando.
                continue
            candidatos.append((ticker, valores))

        if not candidatos:
            return []

        puestos: dict[str, float] = {t: 0.0 for t, _ in candidatos}
        for i in range(len(MOMENTUM_FIELDS)):
            ordenado = sorted(candidatos, key=lambda c: c[1][i], reverse=True)
            for puesto, (ticker, _) in enumerate(ordenado):
                puestos[ticker] += puesto

        # Empates por nombre, para que dos ejecuciones den lo mismo.
        return sorted(puestos, key=lambda t: (puestos[t], t))

    def _tradeable(self, ctx: StrategyContext) -> set[str]:
        """La lista blanca del mandato, cruzada con lo que hay en el contexto."""
        if not ctx.indicators.empty:
            presentes = set(ctx.indicators.index)
        else:
            presentes = set()
        return {t for t in self._universe if t in presentes}

    # ------------------------------------------------------------------
    def _stop_exits(self, ctx: StrategyContext) -> list[Intent]:
        """Stops tocados. Van primero: el riesgo no frena una salida protectora."""
        out = []
        for ticker, held in ctx.positions.items():
            close = ctx.indicator(ticker, "close")
            if close is None:
                continue
            stop = self._current_stop(ctx, ticker, held)
            # `stop is None` y `close > stop` son cosas OPUESTAS y estuvieron en
            # el mismo `continue`: "no puedo protegerte" y "no hace falta
            # protegerte todavia". Quien avisa de la primera es
            # `run_bot._avisar_de_las_desprotegidas`, que lo registra tambien los
            # dias en que el bot no rebalancea. Aqui solo se sigue de largo.
            if stop is None:
                continue
            if close > stop:
                continue
            out.append(
                Intent(
                    ticker=ticker, side=Side.SELL, intent_type=IntentType.STOP_EXIT,
                    ref_price=close, strategy_id=self.strategy_id,
                    qty_requested=float(held.get("qty", 0.0)),
                    stop_price=stop, regime=ctx.regime,
                    rationale={
                        "reasons": [
                            f"El cierre ({close:.2f}) ha tocado el stop ({stop:.2f})."
                        ],
                        "flags": ["stop"], "signals": [],
                    },
                )
            )
        return out

    def _current_stop(self, ctx: StrategyContext, ticker: str,
                      held: dict) -> float | None:
        atr14 = ctx.indicator(ticker, "atr14")
        if atr14 is None:
            return None
        entry = float(held.get("avg_entry_price", 0.0))
        if entry <= 0:
            return None
        # 4x ATR y no 2,5x como en acciones: con la volatilidad de cripto, un
        # stop de 2,5x lo toca cualquier martes y la posicion se cierra por
        # ruido, no porque la idea haya dejado de valer.
        mult = float(self.params.get("stop_atr_mult", 4.0))
        if not self.params.get("trailing", True):
            return entry - mult * atr14
        row = ctx.bot_positions.get(ticker) or {}
        close = ctx.indicator(ticker, "close") or entry
        highest = max(float(row.get("highest_close_since_entry") or entry), close)
        return trailing_stop(entry, highest, atr14, mult)

    # ------------------------------------------------------------------
    def _rank_exits(self, ctx: StrategyContext, skip: set[str]) -> list[Intent]:
        orden = self.ranking(ctx)
        max_positions = int(self.params.get("max_positions", 4))
        buffer = int(self.params.get("exit_rank_buffer", 1))
        dentro = set(orden[: max_positions + buffer])

        out = []
        for ticker, held in ctx.positions.items():
            if ticker in skip:
                continue
            close = ctx.indicator(ticker, "close")
            if close is None:
                continue

            reasons = []
            if orden and ticker not in dentro:
                puesto = orden.index(ticker) + 1 if ticker in orden else len(orden) + 1
                reasons.append(
                    f"Ha caido al puesto {puesto} del ranking, fuera de los "
                    f"{max_positions + buffer} que marcan la salida."
                )
            if flag(ctx, ticker, "above_sma50") is False:
                reasons.append("Ha perdido la media de 50 sesiones.")
            if not reasons:
                continue

            out.append(
                Intent(
                    ticker=ticker, side=Side.SELL, intent_type=IntentType.CLOSE,
                    ref_price=close, strategy_id=self.strategy_id,
                    qty_requested=float(held.get("qty", 0.0)),
                    regime=ctx.regime,
                    rationale={"reasons": reasons, "flags": ["salida"], "signals": []},
                )
            )
        return out

    # ------------------------------------------------------------------
    def _entries(self, ctx: StrategyContext, leaving: set[str]) -> list[Intent]:
        if self.bullish_regime(ctx) is not True:
            # Ni bajista confirmado ni sin datos abren posiciones. Ver
            # `bullish_regime`.
            return []

        max_positions = int(self.params.get("max_positions", 4))
        keeping = {t for t in ctx.positions if t not in leaving}
        room = max_positions - len(keeping)
        if room <= 0:
            return []

        out: list[Intent] = []
        for ticker in self.ranking(ctx)[:max_positions]:
            if len(out) >= room:
                break
            if ticker in keeping or ticker in leaving:
                continue

            close = ctx.indicator(ticker, "close")
            if close is None or close <= 0:
                continue
            # El ranking dice cual es la mejor del grupo; la media de 50 dice
            # si esta subiendo o solo cae menos que las demas. En un mercado
            # bajista, sin este filtro se compra siempre la que menos baja.
            if flag(ctx, ticker, "above_sma50") is not True:
                continue

            atr14 = ctx.indicator(ticker, "atr14")
            mult = float(self.params.get("stop_atr_mult", 4.0))
            stop = (close - mult * atr14) if atr14 else None

            out.append(
                Intent(
                    ticker=ticker, side=Side.BUY, intent_type=IntentType.OPEN,
                    ref_price=close, strategy_id=self.strategy_id,
                    # El tamano lo pone el riesgo. Aqui va el objetivo bruto: la
                    # estrategia dice que quiere una posicion, no cuanto le
                    # dejan gastar. Mezclar las dos cosas produce vetos
                    # invisibles que nadie puede auditar.
                    notional_requested=ctx.equity
                    * float(self._risk.get("target_position_pct", 25.0)) / 100.0,
                    stop_price=stop, stop_atr_mult=mult, regime=ctx.regime,
                    rationale={
                        "reasons": [
                            f"Esta entre las {max_positions} de mejor momentum "
                            "a 3 y 6 meses.",
                            "Cotiza sobre su media de 50 sesiones.",
                            "Bitcoin esta sobre su media de 200: regimen alcista.",
                        ],
                        "flags": ["entrada"],
                        # Vacio a proposito. `signals` es para el CATALOGO de
                        # senales de acciones, cada una con su ficha de
                        # evidencia validada contra el historico. `roc_3m` no
                        # es una senal de ese catalogo sino un indicador de
                        # ranking, y declararlo aqui hace que el riesgo exija
                        # una evidencia que no existe ni puede existir: vetaria
                        # todas las entradas por un motivo que suena correcto.
                        #
                        # Lo que valida esta estrategia es la puerta, no una
                        # ficha por indicador.
                        "signals": [],
                    },
                )
            )
        return out
