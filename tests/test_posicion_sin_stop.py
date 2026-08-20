"""Una posicion abierta que se queda sin stop tiene que decirlo.

Esto es lo mas grave que ha salido de revisar el log de la segunda instalacion,
y no era lo que iba buscando.

Todos los stops del bot se miden en ATR. `_stop_exits` hacia:

    stop = self._current_stop(ctx, ticker, held)
    if stop is None or close > stop:
        continue

Esas dos condiciones son cosas OPUESTAS metidas en el mismo `continue`:

- `close > stop`  -> "el stop no se ha tocado". Todo bien, sigue.
- `stop is None`  -> "no puedo calcularte el stop". No tiene nada de bien.

Una posicion que perdia el ATR se quedaba sin stop en silencio absoluto: ni un
mensaje, ni una fila en el registro, nada. El bot seguia dando sus vueltas cada
seis horas informando de normalidad, y esa posicion podia caer lo que quisiera
sin que nada la cerrase.

El ATR desaparece por motivos de lo mas normales: un valor recien anadido, un
hueco en la serie, o una barra apartada por incoherente —que deja el rango del
dia a nulo y con el catorce sesiones de ATR—. O sea que la cuarentena que acabo
de meter es una via nueva para llegar a este agujero.

NO se cierra la posicion sola. El ATR falta por un fallo de DATOS, y liquidar
por una barra rara del proveedor es justo la clase de perdida que hay que
evitar. Se avisa, y decide quien tiene el dinero.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from stocks_tracker.trading import journal, run_bot
from stocks_tracker.trading.context import StrategyContext

HOY = date(2026, 8, 20)


def contexto(atr_por_ticker: dict[str, float | None]) -> StrategyContext:
    """Una cartera con una posicion por ticker y el ATR que se le diga."""
    indicadores = pd.DataFrame(
        [{"ticker": t, "close": 100.0, "atr14": atr}
         for t, atr in atr_por_ticker.items()],
        columns=["ticker", "close", "atr14"],
    ).set_index("ticker")
    return StrategyContext(
        as_of=HOY, mode="paper", equity=10_000.0, cash=1_000.0,
        positions={t: {"qty": 10.0, "avg_entry_price": 90.0,
                       "current_price": 100.0} for t in atr_por_ticker},
        indicators=indicadores,
    )


def diario() -> journal.Journal:
    return journal.Journal(run_id="run-1", mode="paper", strategy_id="test")


# ---------------------------------------------------------------------------
# Detectarlas
# ---------------------------------------------------------------------------

def test_una_posicion_sin_atr_esta_sin_proteccion():
    ctx = contexto({"AAA": 2.0, "BBB": None})

    assert run_bot.posiciones_sin_stop(ctx) == ["BBB"]


def test_un_atr_nan_cuenta_igual_que_uno_ausente():
    """Es la forma en la que llega de verdad: la barra apartada deja el rango a
    nulo y el ATR sale NaN, no None."""
    ctx = contexto({"AAA": 2.0, "BBB": float("nan")})

    assert run_bot.posiciones_sin_stop(ctx) == ["BBB"]


def test_lo_que_no_esta_en_cartera_no_cuenta():
    """Medio universo puede no tener ATR sin que eso sea un problema: solo
    importa lo que se tiene abierto."""
    ctx = contexto({"AAA": 2.0})
    ctx.indicators.loc["ZZZ"] = {"close": 50.0, "atr14": None}

    assert run_bot.posiciones_sin_stop(ctx) == []


def test_sin_posiciones_no_hay_nada_que_avisar():
    assert run_bot.posiciones_sin_stop(contexto({})) == []


# ---------------------------------------------------------------------------
# Y decirlo
# ---------------------------------------------------------------------------

def decisiones(log: journal.Journal) -> list[tuple]:
    return list(log._decisions)


def test_se_registra_una_decision_por_cada_posicion_desprotegida():
    log, resultado = diario(), run_bot.CycleResult(run_id="run-1")

    run_bot._avisar_de_las_desprotegidas(contexto({"AAA": 2.0, "BBB": None}),
                                         log, resultado)

    filas = decisiones(log)
    assert len(filas) == 1
    # (id, run_id, cuando, modo, estrategia, ticker, decision, codigo, texto, ctx)
    assert filas[0][5] == "BBB"
    assert filas[0][6] == "SIN_PROTECCION"
    assert filas[0][7] == "NO_ATR"
    assert resultado.sin_proteccion == ["BBB"]


def test_no_se_registra_nada_cuando_todo_tiene_stop():
    """Un aviso que sale siempre entrena a ignorar los avisos."""
    log, resultado = diario(), run_bot.CycleResult(run_id="run-1")

    run_bot._avisar_de_las_desprotegidas(contexto({"AAA": 2.0}), log, resultado)

    assert decisiones(log) == []
    assert resultado.sin_proteccion == []


def test_el_aviso_no_genera_ninguna_orden_de_venta():
    """Liquidar porque el proveedor mando una barra rara es justo la perdida
    que hay que evitar."""
    log, resultado = diario(), run_bot.CycleResult(run_id="run-1")

    run_bot._avisar_de_las_desprotegidas(contexto({"BBB": None}), log, resultado)

    assert log._intents == []
    assert resultado.n_intents == 0


# ---------------------------------------------------------------------------
# Y el dia que el bot no hace nada, tambien
# ---------------------------------------------------------------------------

class EstrategiaQuieta:
    strategy_id = "quieta"

    def should_run_today(self, ctx) -> bool:
        return False

    def propose(self, ctx) -> list:
        raise AssertionError("no deberia proponer nada hoy")


class RiesgoQueNoSeUsa:
    violations: list = []

    def evaluate(self, intents, ctx):
        raise AssertionError("no deberia evaluar nada hoy")


def test_se_avisa_tambien_los_dias_sin_rebalanceo():
    """El fallo mas facil de cometer al arreglar esto: poner el aviso despues
    del `if not should_run_today: return`. Una posicion sin stop lo esta
    tambien los dias en que el bot no toca nada, que son la mayoria."""
    log = diario()

    resultado = run_bot.run_cycle(
        contexto({"AAA": 2.0, "BBB": None}), EstrategiaQuieta(),
        RiesgoQueNoSeUsa(), log,
    )

    codigos = [d[6] for d in decisiones(log)]
    assert "SIN_PROTECCION" in codigos, (
        "el aviso se pierde justo los dias en que nadie mira la consola"
    )
    assert resultado.sin_proteccion == ["BBB"]
    # Y el motivo de no hacer nada se sigue registrando.
    assert "SKIPPED_NO_SIGNAL" in codigos


def test_el_ciclo_normal_tambien_avisa():
    """Guardarrail del de arriba: que no se avise SOLO en la rama que no hace
    nada."""
    class Estrategia:
        strategy_id = "vacia"

        def should_run_today(self, ctx) -> bool:
            return True

        def propose(self, ctx) -> list:
            return []

    log = diario()
    run_bot.run_cycle(contexto({"BBB": None}), Estrategia(),
                      RiesgoQueNoSeUsa(), log)

    assert "SIN_PROTECCION" in [d[6] for d in decisiones(log)]


def test_el_texto_dice_que_no_se_cierra_sola():
    """Quien lo lea tiene que entender que la pelota esta en su tejado."""
    log, resultado = diario(), run_bot.CycleResult(run_id="run-1")
    run_bot._avisar_de_las_desprotegidas(contexto({"BBB": None}), log, resultado)

    texto = decisiones(log)[0][8].lower()
    assert "no se cierra sola" in texto
    assert "no esta protegida" in texto


# ---------------------------------------------------------------------------
# La estrategia ya no confunde las dos cosas
# ---------------------------------------------------------------------------

def test_el_codigo_ya_no_mete_las_dos_condiciones_en_el_mismo_continue():
    """Guardarrail sobre la forma del codigo, y no sobre lo que hace.

    Es la unica manera de proteger esto: las dos ramas hacen lo mismo —seguir
    de largo—, asi que ningun test de comportamiento distingue la version buena
    de la mala. Lo que se protege aqui es que quien lea el codigo vea que son
    dos casos distintos, porque volver a juntarlos es como se perdio la primera
    vez.
    """
    from stocks_tracker.core.config import project_root

    src = (project_root()
           / "src/stocks_tracker/trading/strategies/crypto_momentum.py"
           ).read_text("utf-8")

    assert "if stop is None or close > stop:" not in src, (
        "vuelven a estar juntas 'no puedo protegerte' y 'no hace falta todavia'"
    )
    assert "if stop is None:" in src
