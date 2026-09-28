"""`StrategyContext`: la unica lectura del almacen que hace el bot.

Todo lo que estrategias y riesgo necesitan saber se junta aqui, de una vez y en
solo lectura. El motivo no es la eficiencia sino el determinismo: si cada
estrategia consultase la base por su cuenta, dos de ellas podrian ver datos de
fechas distintas dentro del mismo ciclo, y reconstruir despues por que se tomo
una decision seria imposible.

Regla que atraviesa el modulo: **el bot no calcula ni un indicador propio**.
Todo sale de lo que ya computo la capa 5. Si el bot recalculase el ATR por su
cuenta, podria operar con un numero distinto del que muestra el dashboard, y la
pantalla dejaria de explicar lo que hace el bot.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from ..core.config import get_trading_config
from ..core.db import connect
from ..core.scoring import preset_hash


@dataclass
class StrategyContext:
    """Foto completa del mundo en una fecha. Solo lectura."""

    as_of: date
    mode: str
    equity: float
    cash: float
    # ticker -> {qty, avg_entry_price, market_value, current_price}
    positions: dict[str, dict] = field(default_factory=dict)
    # ticker -> {stop_price, highest_close_since_entry, opened_at, ...}
    bot_positions: dict[str, dict] = field(default_factory=dict)

    scores: pd.DataFrame = field(default_factory=pd.DataFrame)
    indicators: pd.DataFrame = field(default_factory=pd.DataFrame)
    signals: pd.DataFrame = field(default_factory=pd.DataFrame)
    evidence: dict[str, str] = field(default_factory=dict)
    # ticker -> veredicto del contraste entre proveedores. Vacio mientras no se
    # haya ejecutado ninguna auditoria cruzada, y entonces la regla 21 no veta a
    # nadie: no haberlo mirado no es lo mismo que estar mal.
    consenso: dict[str, str] = field(default_factory=dict)
    sectors: dict[str, str] = field(default_factory=dict)
    dollar_volume_20d: dict[str, float] = field(default_factory=dict)
    earnings: dict[str, list[date]] = field(default_factory=dict)
    universe_allowed: set[str] = field(default_factory=set)

    regime: str = "neutral"
    risk_score: float = 0.0
    last_price_date: date | None = None
    orders_today: int = 0
    tickers_ordered_today: set[str] = field(default_factory=set)
    peak_equity: float = 0.0
    day_start_equity: float = 0.0
    state: str = "RUNNING"

    # ------------------------------------------------------------------
    @property
    def data_age_hours(self) -> float:
        """Antiguedad del ultimo cierre disponible.

        Se mide contra `as_of` y no contra la hora actual para que el backtest
        no se autoveten­te: en una simulacion de 2019 la hora del reloj no dice
        nada sobre la frescura del dato.
        """
        if self.last_price_date is None:
            return float("inf")
        return (self.as_of - self.last_price_date).days * 24.0

    def indicator(self, ticker: str, name: str) -> float | None:
        if self.indicators.empty or ticker not in self.indicators.index:
            return None
        value = self.indicators.at[ticker, name] if name in self.indicators.columns else None
        return float(value) if value is not None and pd.notna(value) else None

    def score(self, ticker: str, name: str = "composite_pctile") -> float | None:
        if self.scores.empty or ticker not in self.scores.index:
            return None
        value = self.scores.at[ticker, name] if name in self.scores.columns else None
        return float(value) if value is not None and pd.notna(value) else None

    def price(self, ticker: str) -> float | None:
        held = self.positions.get(ticker)
        if held and held.get("current_price"):
            return float(held["current_price"])
        return self.indicator(ticker, "close")

    def sector(self, ticker: str) -> str:
        return self.sectors.get(ticker) or "Sin sector"

    def sector_exposure(self) -> dict[str, float]:
        """Valor de mercado por sector. Base de la regla de concentracion."""
        out: dict[str, float] = {}
        for ticker, held in self.positions.items():
            out[self.sector(ticker)] = out.get(self.sector(ticker), 0.0) + float(
                held.get("market_value", 0.0)
            )
        return out

    def has_earnings_near(self, ticker: str, before: int, after: int) -> bool:
        """Resultados dentro de la ventana de bloqueo.

        Comprar tres dias antes de resultados es apostar a un evento binario que
        ninguna de nuestras senales predice. No es gestion de riesgo fina: es
        no jugar a algo que el sistema no sabe jugar.
        """
        for report in self.earnings.get(ticker, []):
            delta = (report - self.as_of).days
            if -after <= delta <= before:
                return True
        return False


# ---------------------------------------------------------------------------
# Construccion
# ---------------------------------------------------------------------------
def _universe_tickers(conn, allowed: list[str], as_of: date) -> set[str]:
    if not allowed:
        return set()
    placeholders = ", ".join("?" for _ in allowed)
    rows = conn.execute(
        f"""
        SELECT DISTINCT ticker FROM universe_membership
        WHERE universe IN ({placeholders})
          AND valid_from <= ?
          AND (valid_to IS NULL OR valid_to >= ?)
        """,
        [*allowed, as_of, as_of],
    ).fetchall()
    return {r[0] for r in rows}


def scope(mode: str, venue: str | None = None) -> str:
    """Clave de estado del bot: `live` o `live:kraken`.

    El mandato dice dos carteras y nunca un bote comun. Todo el estado del bot
    —posiciones, equity, maximo historico, kill switch, ordenes del dia— se
    guarda por esta clave, asi que con `venue` puesto cada mercado lleva su
    contabilidad aparte. Sin esto, una racha mala en cripto consumiria la cuota
    de ordenes de Polymarket y su kill switch pararia los dos.
    """
    return f"{mode}:{venue}" if venue else mode


def build_context(
    as_of: date | None = None,
    mode: str = "simulated",
    strategy_id: str = "momentum_multifactor_v1",
    broker=None,
    venue: str | None = None,
) -> StrategyContext:
    """Lee el almacen y el estado del broker y devuelve la foto del dia.

    Con `venue`, la foto es la de ESA cartera: su universo, su capital y su
    estado, sin ver nada de las demas.
    """
    cfg = get_trading_config()
    clave = scope(mode, venue)

    if venue:
        vcfg = cfg.venue(venue)
        allowed = list((vcfg.universe or {}).get("allowed") or [])
        capital = vcfg.initial_equity
        # El ranking por factores es de acciones: mezcla fundamentales y no
        # existe para cripto. La estrategia del venue ordena por su cuenta a
        # partir de los indicadores, asi que aqui no se consulta.
        wanted_hash = None
    else:
        preset = cfg.strategy(strategy_id).get("preset", "bot_core")
        wanted_hash = preset_hash(preset)
        allowed = list(cfg.universe.get("allowed") or [])
        capital = cfg.initial_equity

    with connect(read_only=True) as conn:
        last_row = conn.execute("SELECT MAX(date) FROM prices_daily").fetchone()
        last_price_date = last_row[0] if last_row else None
        if as_of is None:
            as_of = last_price_date
        if as_of is None:
            raise ValueError("No hay precios en el almacen: nada que contextualizar")

        if venue:
            # En un venue, `allowed` ya ES la lista de tickers del mandato
            # ("BTC/EUR", ...), no nombres de universo. Pasarla por
            # `_universe_tickers` buscaria un universo llamado "BTC/EUR" y
            # devolveria el conjunto vacio: el bot se quedaria sin candidatos
            # en silencio, con todos los datos descargados.
            universe = set(allowed)
        else:
            universe = _universe_tickers(conn, allowed, as_of)

        # `factor_scores` guarda un ranking por preset. Leer sin filtrar por
        # weights_hash devolveria una fila por preset y multiplicaria el
        # universo candidato por cinco, con posiciones repetidas.
        if wanted_hash is None:
            scores = pd.DataFrame(columns=["ticker"])
        else:
            scores = conn.execute(
                """
                SELECT ticker, composite, composite_pctile, coverage,
                       quality_z, lowvol_z, momentum_z, value_z
                FROM factor_scores
                WHERE date = ? AND weights_hash = ?
                """,
                [as_of, wanted_hash],
            ).fetchdf()

        # `above_sma50`, `roc_3m` y `roc_6m` los usa la estrategia de cripto
        # para su ranking. Se piden siempre: son dos columnas mas de una fila
        # por ticker, y tenerlas ausentes en el contexto hace que la estrategia
        # se quede en silencio sin candidatos, que es el fallo mas dificil de
        # ver de todos.
        indicators = conn.execute(
            """
            SELECT ticker, close, atr14, atr_pct, rsi14, above_sma200, sma200,
                   above_sma50, sma50, roc_3m, roc_6m,
                   rel_volume_20, ret_1d
            FROM indicators_daily WHERE date = ?
            """,
            [as_of],
        ).fetchdf()

        signals = conn.execute(
            "SELECT ticker, signal_id, direction, strength FROM signals WHERE date = ?",
            [as_of],
        ).fetchdf()

        evidence_rows = conn.execute(
            """
            SELECT signal_id, evidence FROM signal_evidence
            WHERE scope = 'equity_us'
            """
        ).fetchall()

        # El veredicto MAS RECIENTE de cada valor, y no el de `as_of`. La
        # auditoria cruzada no corre todos los dias sobre todos los valores —el
        # presupuesto de las APIs gratuitas no da—, asi que exigir el del dia
        # exacto dejaria la regla sin datos casi siempre y sin efecto ninguno.
        consenso_rows = conn.execute(
            """
            SELECT ticker, veredicto FROM price_consensus
            QUALIFY ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY date DESC) = 1
            """
        ).fetchall()

        instruments = conn.execute(
            "SELECT ticker, gics_sector FROM instruments"
        ).fetchdf()

        # Volumen en dinero de las ultimas 20 sesiones: filtro de liquidez. Una
        # posicion de 10 EUR nunca movera el mercado, pero un valor que apenas
        # se negocia tiene horquillas que se comen la ventaja de cualquier
        # senal.
        dollar_volume = conn.execute(
            """
            SELECT ticker, AVG(close * volume) AS dv
            FROM (
                SELECT ticker, close, volume,
                       ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY date DESC) AS rn
                FROM prices_daily WHERE date <= ?
            )
            WHERE rn <= 20 GROUP BY ticker
            """,
            [as_of],
        ).fetchdf()

        earnings_rows = conn.execute(
            """
            SELECT ticker, report_date FROM earnings_events
            WHERE report_date BETWEEN ? - INTERVAL 40 DAY AND ? + INTERVAL 40 DAY
            """,
            [as_of, as_of],
        ).fetchall()

        regime_row = conn.execute(
            "SELECT regime, risk_score FROM regime_daily WHERE date <= ? "
            "ORDER BY date DESC LIMIT 1",
            [as_of],
        ).fetchone()

        state_row = conn.execute(
            "SELECT state, peak_equity, day_start_equity FROM bot_state WHERE mode = ?",
            [clave],
        ).fetchone()

        bot_positions = conn.execute(
            "SELECT * FROM bot_positions WHERE mode = ?", [clave]
        ).fetchdf()

        orders_today = conn.execute(
            "SELECT ticker FROM orders WHERE mode = ? AND submitted_at::DATE = ?",
            [clave, as_of],
        ).fetchall()

    if universe:
        if not scores.empty:
            scores = scores[scores["ticker"].isin(universe)]
        if not indicators.empty:
            indicators = indicators[indicators["ticker"].isin(universe)]

    scores = scores.set_index("ticker") if not scores.empty else scores
    indicators = indicators.set_index("ticker") if not indicators.empty else indicators

    earnings: dict[str, list[date]] = {}
    for ticker, report_date in earnings_rows:
        earnings.setdefault(ticker, []).append(report_date)

    equity = capital
    cash = capital
    positions: dict[str, dict] = {}
    if broker is not None:
        account = broker.get_account()
        equity, cash = account.equity, account.cash
        positions = {
            p.symbol: {
                "qty": p.qty, "avg_entry_price": p.avg_entry_price,
                "market_value": p.market_value, "current_price": p.current_price,
            }
            for p in broker.get_positions()
        }

    return StrategyContext(
        as_of=as_of,
        # El contexto lleva la clave con venue: es la que usan el riesgo, el
        # kill switch y el registro para no mezclar carteras.
        mode=clave,
        equity=equity,
        cash=cash,
        positions=positions,
        bot_positions={
            row["ticker"]: dict(row) for _, row in bot_positions.iterrows()
        } if not bot_positions.empty else {},
        scores=scores,
        indicators=indicators,
        signals=signals,
        evidence={sid: ev for sid, ev in evidence_rows},
        consenso={str(t): str(v) for t, v in consenso_rows},
        sectors=dict(zip(instruments["ticker"], instruments["gics_sector"], strict=False))
        if not instruments.empty else {},
        dollar_volume_20d=dict(zip(dollar_volume["ticker"], dollar_volume["dv"],
                                   strict=False)) if not dollar_volume.empty else {},
        earnings=earnings,
        regime=str(regime_row[0]) if regime_row and regime_row[0] else "neutral",
        risk_score=float(regime_row[1]) if regime_row and regime_row[1] is not None else 0.0,
        last_price_date=last_price_date,
        orders_today=len(orders_today),
        tickers_ordered_today={r[0] for r in orders_today},
        peak_equity=float(state_row[1]) if state_row and state_row[1] else equity,
        day_start_equity=float(state_row[2]) if state_row and state_row[2] else equity,
        state=str(state_row[0]) if state_row and state_row[0] else "RUNNING",
        universe_allowed=universe,
    )
