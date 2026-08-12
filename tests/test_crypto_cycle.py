"""La cadena de cripto entera: almacen -> contexto -> estrategia -> riesgo -> broker.

Los tests de cada pieza por separado no dicen si encajan. Aqui se monta un
almacen de verdad con velas inventadas y se recorre el ciclo completo. Es lo
que habria destapado, sin tener que descargar nada, que la estrategia estaba
escrita y no la llamaba nadie.

Las velas son sinteticas A PROPOSITO y eso NO contamina nada: lo que se
comprueba es el cableado —que el universo llegue, que la cartera no se mezcle,
que el freno pare la orden— y no si la estrategia gana dinero. Para eso esta
la puerta, y la puerta bloquea si detecta precios sinteticos.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from stocks_tracker.core import db
from stocks_tracker.trading import run_bot
from stocks_tracker.trading.context import build_context, scope

PARES = ["BTC/EUR", "ETH/EUR", "SOL/EUR", "ADA/EUR"]


@pytest.fixture
def warehouse(tmp_path, monkeypatch):
    class Stub:
        warehouse_path = tmp_path / "test.duckdb"

    monkeypatch.setattr(db, "get_settings", lambda: Stub())
    db.migrate()
    return Stub.warehouse_path


def sembrar(dias: int = 500, subiendo: bool = True) -> date:
    """Velas y indicadores coherentes para los pares del mandato."""
    hoy = date(2026, 6, 1)
    inicio = hoy - timedelta(days=dias)

    precios, indicadores, fichas = [], [], []
    for n, par in enumerate(PARES):
        base = 100.0 * (n + 1)
        for i in range(dias):
            d = inicio + timedelta(days=i)
            precio = base * (1 + i * 0.001) if subiendo else base * (1 - i * 0.0005)
            precios.append({
                "ticker": par, "date": d, "open": precio, "high": precio * 1.02,
                "low": precio * 0.98, "close": precio, "adj_close": precio,
                # Elegido para caer ENTRE los dos umbrales: por encima del
                # minimo de liquidez cripto (1 M al dia) y por debajo del de
                # acciones (20 M). Asi, si el ciclo aplicara el mandato
                # equivocado, el riesgo vetaria y se veria. Con volumenes que
                # cruzan los dos, el test pasa con cualquiera de los dos
                # mandatos y no comprueba nada.
                "volume": 10_000, "source": "yfinance",
            })
            indicadores.append({
                "ticker": par, "date": d, "close": precio,
                "atr14": precio * 0.05, "atr_pct": 5.0, "rsi14": 55.0,
                "sma50": precio * 0.95, "sma200": precio * 0.9,
                "above_sma50": subiendo, "above_sma200": subiendo,
                # Momentum decreciente por orden de la lista, para que el
                # ranking sea determinista y comprobable.
                "roc_3m": 0.50 - n * 0.10, "roc_6m": 0.60 - n * 0.10,
                "rel_volume_20": 1.0, "ret_1d": 0.001,
            })
        fichas.append({"ticker": par, "asset_class": "crypto", "gics_sector": "Crypto"})
    ultima = inicio + timedelta(days=dias - 1)

    with db.connect() as conn:
        db.upsert_df(conn, "prices_daily", pd.DataFrame(precios),
                     keys=["ticker", "date"])
        db.upsert_df(conn, "indicators_daily", pd.DataFrame(indicadores),
                     keys=["ticker", "date"])
        db.upsert_df(conn, "instruments", pd.DataFrame(fichas), keys=["ticker"])
    # La ULTIMA fecha con datos, no la de manana: pedir el contexto de un dia
    # sin velas devuelve indicadores vacios y el fallo parece de otra cosa.
    return ultima


# ---------------------------------------------------------------------------
# El contexto del venue
# ---------------------------------------------------------------------------
def test_the_venue_context_only_sees_its_own_universe(warehouse):
    """Si viera el resto del almacen, el bot de cripto podria comprar acciones."""
    hoy = sembrar()
    with db.connect() as conn:
        db.upsert_df(conn, "instruments",
                     pd.DataFrame([{"ticker": "AAPL", "asset_class": "equity"}]),
                     keys=["ticker"])
    ctx = build_context(as_of=hoy, mode="simulated", venue="kraken")
    assert "AAPL" not in ctx.universe_allowed
    assert "BTC/EUR" in ctx.universe_allowed


def test_the_venue_context_carries_its_own_capital(warehouse):
    """El mandato da 25 EUR a cripto, no los 55 del bote general."""
    hoy = sembrar()
    ctx = build_context(as_of=hoy, mode="simulated", venue="kraken")
    assert ctx.equity == 25.0


def test_each_venue_keeps_its_own_books(warehouse):
    """Dos carteras, nunca un bote comun. Sin esto, una racha mala en cripto
    consumiria la cuota de ordenes de Polymarket y el kill switch de uno
    pararia al otro."""
    assert scope("live", "kraken") == "live:kraken"
    assert scope("live", "polymarket") == "live:polymarket"
    assert scope("live") == "live"

    hoy = sembrar()
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO bot_positions (ticker, mode, qty, avg_entry_price) "
            "VALUES ('BTC/EUR', 'simulated:kraken', 0.1, 100.0)"
        )
    propio = build_context(as_of=hoy, mode="simulated", venue="kraken")
    ajeno = build_context(as_of=hoy, mode="simulated", venue="polymarket")
    assert "BTC/EUR" in propio.bot_positions
    assert ajeno.bot_positions == {}, "un venue ve las posiciones del otro"


def test_the_context_carries_the_indicators_the_strategy_needs(warehouse):
    """Si faltasen `roc_3m` o `above_sma50`, la estrategia se quedaria sin
    candidatos en silencio: el fallo mas dificil de ver de todos."""
    hoy = sembrar()
    ctx = build_context(as_of=hoy, mode="simulated", venue="kraken")
    for campo in ("roc_3m", "roc_6m", "above_sma50", "atr14", "close"):
        assert ctx.indicator("BTC/EUR", campo) is not None, f"falta {campo}"


# ---------------------------------------------------------------------------
# La estrategia que se elige
# ---------------------------------------------------------------------------
def test_the_venue_picks_the_crypto_strategy():
    assert run_bot.strategy_for("kraken").strategy_id == "crypto_momentum_v1"


def test_without_a_venue_there_is_no_default_strategy():
    """Antes caia en la de acciones. Retirada esa, devolver cualquier otra por
    defecto seria operar en un mercado que nadie pidio."""
    with pytest.raises(ValueError, match="--venue"):
        run_bot.strategy_for(None)


def test_a_venue_without_a_strategy_says_so_instead_of_trading_stocks():
    """Caer en la de acciones seria operar cripto con reglas de bolsa: stops de
    2,5x ATR, limite PDT y bloqueo por resultados trimestrales."""
    with pytest.raises(ValueError, match="polymarket"):
        run_bot.strategy_for("polymarket")


# ---------------------------------------------------------------------------
# El ciclo completo
# ---------------------------------------------------------------------------
def test_the_whole_chain_approves_something(warehouse):
    """La prueba que faltaba: que la estrategia de cripto llegue a proponer y
    que el riesgo lo APRUEBE, pasando por el contexto real.

    Comprobar solo que se registran decisiones no sirve: un veto tambien se
    registra. Con esa version debil, el ciclo pasaba el test mientras vetaba
    las cuatro entradas por aplicar a cripto el minimo de liquidez de bolsa
    —20 millones al dia—, que es exactamente el fallo que habia.
    """
    sembrar()
    resultado = run_bot._propose_once("simulated", venue="kraken")
    assert resultado == 0

    with db.connect(read_only=True) as conn:
        aprobadas = conn.execute(
            "SELECT COUNT(*) FROM intents WHERE risk_verdict = 'APPROVE'"
        ).fetchone()[0]
        motivos = conn.execute(
            "SELECT reason_code, COUNT(*) FROM decision_log "
            "WHERE mode = 'simulated:kraken' GROUP BY 1"
        ).fetchall()
    codigos = dict(motivos)
    assert "ILLIQUID" not in codigos, (
        "vetado por liquidez: se esta aplicando el minimo de bolsa (20 M al "
        f"dia) a cripto, cuyo mandato pide 1 M. Motivos: {motivos}"
    )
    # Exactamente el tope de ordenes diarias del mandato CRIPTO, que son 2. El
    # de acciones son 6, asi que este numero distingue los dos mandatos por si
    # solo: con el equivocado saldrian 4.
    tope = int(run_bot.config_for("kraken").limit("max_orders_per_day"))
    assert aprobadas == tope, f"aprobadas {aprobadas}, se esperaban {tope}: {motivos}"
    assert codigos.get("MAX_ORDERS_PER_DAY") == len(PARES) - tope, (
        "las que sobran deberian quedar vetadas por el tope diario"
    )


def test_a_bear_regime_produces_no_buys(warehouse):
    """El interruptor que separa perder un 20 % de perderlo casi todo, visto
    de punta a punta y no solo en la estrategia aislada."""
    sembrar(subiendo=False)
    run_bot._propose_once("simulated", venue="kraken")

    with db.connect(read_only=True) as conn:
        aprobadas = conn.execute(
            "SELECT COUNT(*) FROM intents "
            "WHERE side = 'buy' AND risk_verdict = 'APPROVE'"
        ).fetchone()[0]
    assert aprobadas == 0


# ---------------------------------------------------------------------------
# El freno de mano, en el ciclo
# ---------------------------------------------------------------------------
class BrokerEspia:
    """Acepta todo y apunta lo que le llega."""

    def __init__(self):
        self.enviadas = []

    def submit_order(self, request):
        self.enviadas.append(request)
        return request

    def get_account(self):
        from stocks_tracker.trading.brokers.base import Account

        return Account(account_id="x", currency="EUR", cash=25.0, equity=25.0,
                       buying_power=25.0, last_equity=25.0, daytrade_count=0,
                       pattern_day_trader=False, trading_blocked=False,
                       account_blocked=False, shorting_enabled=False)

    def get_positions(self):
        return []


def test_an_oversized_order_does_not_reach_the_broker(warehouse, monkeypatch):
    """El freno de mano, en el sitio donde importa: entre el riesgo y el
    broker. Que `autonomy.py` calcule bien no sirve de nada si el ciclo no lo
    consulta antes de enviar."""
    sembrar()
    espia = BrokerEspia()
    # Tope ridiculo para que cualquier orden lo cruce.
    monkeypatch.setattr(run_bot.autonomy, "brake_settings",
                        lambda cfg, venue=None: {"confirm_above_eur": 0.01,
                                                 "confirm_first_live_order": False,
                                                 "confirm_when_drawdown_over_pct": 0})
    monkeypatch.setattr(run_bot.get_trading_config().__class__, "autonomy_for",
                        lambda self, mode: "guarded")

    run_bot._propose_once("simulated", venue="kraken", broker=espia)
    assert espia.enviadas == [], "una orden frenada ha llegado al broker"

    with db.connect(read_only=True) as conn:
        pendientes = conn.execute(
            "SELECT COUNT(*) FROM decision_log "
            "WHERE decision = 'PENDING_CONFIRMATION'"
        ).fetchone()[0]
    assert pendientes > 0, "no ha quedado constancia de lo que espera"


def test_without_brakes_the_order_reaches_the_broker(warehouse, monkeypatch):
    """El contrario del anterior: si nada llegara nunca al broker, el test de
    arriba pasaria por el motivo equivocado."""
    sembrar()
    espia = BrokerEspia()
    monkeypatch.setattr(run_bot.get_trading_config().__class__, "autonomy_for",
                        lambda self, mode: "auto")

    run_bot._propose_once("simulated", venue="kraken", broker=espia)
    assert espia.enviadas, "no ha enviado nada ni sin frenos"
