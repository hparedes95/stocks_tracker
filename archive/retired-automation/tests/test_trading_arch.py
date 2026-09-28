"""Invariantes de arquitectura del bot.

Estos tests no comprueban comportamiento sino que ciertas cosas sean
*imposibles*. Existen porque el resto de la seguridad del bot descansa sobre
ellas: si un dia alguien —yo incluido— crea una orden sin pasar por el riesgo,
ningun test de comportamiento lo detectaria, porque la orden seria valida en
todo lo demas.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from stocks_tracker.core.config import project_root

SRC = project_root() / "src" / "stocks_tracker"


def python_files() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def tree_of(path: Path) -> ast.AST:
    return ast.parse(path.read_text("utf-8"), filename=str(path))


# ---------------------------------------------------------------------------
# Aislamiento del broker
# ---------------------------------------------------------------------------
def test_no_stock_broker_sdk_is_imported_anywhere():
    """Alpaca era el broker del bot de acciones, que se retiro. La regla se
    queda —invertida— porque su SDK no tiene ya ningun sitio legitimo donde
    aparecer: si vuelve, es que ha vuelto tambien lo que se quito."""
    allowed = SRC / "trading" / "brokers" / "no-existe.py"
    offenders = []

    for path in python_files():
        if path == allowed:
            continue
        for node in ast.walk(tree_of(path)):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            if any(n.split(".")[0] == "alpaca" for n in names):
                offenders.append(str(path.relative_to(SRC)))

    assert not offenders, f"alpaca importado fuera de su adaptador: {offenders}"


def test_only_the_broker_adapters_touch_the_network():
    """La red vive en los adaptadores y en ningun sitio mas.

    La primera version de esta regla prohibia la red en TODO `trading/`, y era
    correcta mientras el bot solo leia del almacen. Un adaptador de broker
    tiene que hablar con el broker, asi que la regla se afina en vez de
    borrarse: lo que sigue prohibido —y es lo que importa— es que la
    estrategia, el riesgo, el contexto o el registro salgan a buscar datos.

    Si la estrategia descargase precios por su cuenta podria decidir sobre
    numeros distintos de los que muestra la pantalla, y el dashboard dejaria de
    explicar lo que hace el bot.
    """
    permitido = SRC / "trading" / "brokers"
    offenders = []

    for path in (SRC / "trading").rglob("*.py"):
        if permitido in path.parents:
            continue
        for node in ast.walk(tree_of(path)):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            if any(n.split(".")[0] in {"yfinance", "requests", "urllib", "httpx"}
                   for n in names):
                offenders.append(str(path.relative_to(SRC)))

    assert not offenders, f"acceso a la red fuera de los adaptadores: {offenders}"


def test_the_strategy_and_the_risk_are_network_free():
    """Los dos modulos donde un acceso a red seria mas danino, comprobados por
    nombre para que la regla no se afloje por accidente."""
    for relativo in ("trading/risk.py", "trading/context.py",
                     "trading/strategies/crypto_momentum.py"):
        source = (SRC / relativo).read_text("utf-8")
        for modulo in ("import requests", "import urllib", "import yfinance"):
            assert modulo not in source, f"{relativo} usa {modulo}"


# ---------------------------------------------------------------------------
# No hay puerta trasera a la ejecucion
# ---------------------------------------------------------------------------
def test_the_minting_key_is_only_known_by_risk():
    """`_MINT` es lo que impide fabricar ordenes aprobadas. Si aparece en un
    tercer fichero, la barrera ha dejado de existir sin que nadie lo note."""
    holders = []
    for path in python_files():
        source = path.read_text("utf-8")
        if "_MINT" in source:
            holders.append(path.relative_to(SRC).as_posix())

    assert sorted(holders) == ["trading/intents.py", "trading/risk.py"], (
        f"la llave de acunacion se ha filtrado a: {holders}"
    )


def test_approved_orders_are_only_built_by_the_risk_manager():
    """Recorre el AST buscando construcciones de `ApprovedOrder`."""
    builders = []
    for path in python_files():
        for node in ast.walk(tree_of(path)):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id == "ApprovedOrder":
                    builders.append(path.relative_to(SRC).as_posix())

    assert set(builders) <= {"trading/risk.py"}, (
        f"ordenes aprobadas construidas fuera del riesgo: {sorted(set(builders))}"
    )


def test_building_an_approved_order_by_hand_fails():
    """La barrera tiene que fallar en ejecucion, no solo en la revision."""
    from stocks_tracker.trading.intents import (
        ApprovedOrder,
        BypassError,
        IntentType,
        Side,
    )

    with pytest.raises(BypassError):
        ApprovedOrder(
            object(), intent_id="X", ticker="AAPL", side=Side.BUY,
            intent_type=IntentType.OPEN, ref_price=100.0, notional=10.0,
        )


def test_the_verdict_cannot_mint_its_own_order():
    """Un atajo tentador: pedirle la orden al veredicto. Tampoco vale."""
    from stocks_tracker.trading.intents import (
        BypassError,
        Decision,
        Intent,
        IntentType,
        RiskVerdict,
        Side,
    )

    intent = Intent(ticker="AAPL", side=Side.BUY, intent_type=IntentType.OPEN,
                    ref_price=100.0, strategy_id="s", notional_requested=10.0)
    verdict = RiskVerdict(intent=intent, decision=Decision.APPROVE,
                          rule_id="r", reason_code="c", reason_text="t")
    with pytest.raises(BypassError):
        verdict.to_order()


# ---------------------------------------------------------------------------
# Alcance de la fase 6
# ---------------------------------------------------------------------------
def test_the_dashboard_never_imports_what_decides():
    """El dashboard puede ENSENAR lo que el bot hizo, nunca DECIDIR.

    La regla original prohibia al dashboard tocar nada del bot, y el motivo
    sigue siendo bueno: una pagina que mostrase propuestas de una estrategia
    sin validar las convertiria en recomendaciones, y se ejecutarian a mano.

    Lo que se abrio despues es lo contrario de una propuesta —el registro de
    lo que ya paso y lo que espera confirmacion— y con el freno de mano hace
    falta verlo: una orden retenida que no sale en ninguna pantalla es una
    orden perdida.

    Lo que sigue prohibido, y es la parte que importa: importar lo que decide.
    Si el dashboard pudiera construir una estrategia o un veredicto de riesgo,
    podria generar propuestas nuevas, y volveriamos exactamente al riesgo
    original.
    """
    prohibido = ("trading.risk", "trading.strategies", "trading.run_bot",
                 "trading.execution", "trading.intents", "trading.journal")

    offenders = []
    for path in (SRC / "app").rglob("*.py"):
        source = path.read_text("utf-8")
        for modulo in prohibido:
            if modulo in source:
                offenders.append(f"{path.relative_to(SRC).as_posix()} -> {modulo}")

    assert not offenders, f"el dashboard usa lo que decide: {offenders}"


def test_only_one_module_reads_the_bot_tables():
    """Y en solo lectura. Concentrarlo en `bot_view` es lo que permite que la
    regla de arriba siga significando algo: con consultas sueltas por las
    paginas, cualquiera podria sacar intenciones vetadas y presentarlas como
    candidatas sin importar ningun modulo prohibido."""
    permitido = SRC / "app" / "bot_view.py"
    offenders = []
    for path in (SRC / "app").rglob("*.py"):
        if path == permitido:
            continue
        source = path.read_text("utf-8")
        for tabla in ("FROM intents", "FROM orders", "FROM bot_positions",
                      "FROM decision_log", "FROM bot_runs"):
            if tabla in source:
                offenders.append(f"{path.relative_to(SRC).as_posix()} -> {tabla}")
    assert not offenders, f"leen tablas del bot fuera de bot_view: {offenders}"


def test_the_bot_view_never_writes():
    """El dashboard abre la base en solo lectura y DuckDB admite un solo
    escritor. Una escritura desde aqui competiria con el ciclo del bot, y el
    que perdiera se quedaria sin poder anotar lo que acaba de hacer."""
    source = (SRC / "app" / "bot_view.py").read_text("utf-8")
    for verbo in ("INSERT", "UPDATE", "DELETE", "CREATE", "DROP"):
        assert verbo not in source, f"bot_view escribe en la base: {verbo}"
    assert source.count("connect(") == source.count("connect(read_only=True)"), (
        "alguna conexion de bot_view no es de solo lectura"
    )


def test_the_bot_page_says_it_is_not_advice():
    """Es la diferencia entre un registro y una recomendacion, y quien la lee
    no tiene por que deducirla."""
    pagina = (SRC / "app" / "pages" / "10_bot.py").read_text("utf-8")
    assert "no una lista de recomendaciones" in pagina
    assert "sugerencia para tu cartera" in pagina


def test_a_real_broker_needs_to_say_which_market():
    """Sin venue no se sabe ni con que credenciales ni contra que cartera. Caer
    en un mercado por defecto seria operar donde nadie pidio."""
    from stocks_tracker.core.config import ConfigError
    from stocks_tracker.trading.brokers.registry import get_broker

    for mode in ("paper", "live"):
        with pytest.raises(ConfigError, match="que mercado"):
            get_broker(mode)


def test_a_venue_without_an_adapter_is_refused():
    """En Polymarket cada orden se firma con la clave privada de la wallet y
    esa parte no esta escrita. Devolver otro adaptador mandaria las ordenes al
    mercado equivocado."""
    from stocks_tracker.core.config import ConfigError
    from stocks_tracker.trading.brokers.registry import get_broker

    with pytest.raises(ConfigError, match="polymarket"):
        get_broker("live", venue="polymarket")


def test_the_only_path_to_a_live_broker_goes_through_the_venue_check():
    """La comprobacion de credenciales, activacion y puerta superada no puede
    saltarse con un argumento: este es el unico sitio del programa donde se
    construye algo capaz de gastar dinero.

    Kraken esta `enabled: false` en el mandato y sin credenciales, asi que
    pedirlo tiene que fallar diciendo que falta, no devolver un adaptador.
    """
    from stocks_tracker.core.config import ConfigError
    from stocks_tracker.trading.brokers.registry import build_broker

    with pytest.raises(ConfigError):
        build_broker("kraken", mode="live")


def test_building_a_live_broker_calls_the_gate_itself():
    """Comprobado sobre el codigo y no solo sobre el resultado: si alguien
    quitara la llamada, el test de arriba podria seguir pasando por cualquier
    otro motivo —una credencial ausente, por ejemplo— y la barrera habria
    desaparecido sin que nada lo dijera."""
    fuente = (SRC / "trading" / "brokers" / "registry.py").read_text(encoding="utf-8")
    build = fuente[fuente.index("def build_broker"):]
    assert "require_tradeable(venue, str(mode))" in build
    assert build.index("require_tradeable") < build.index("KrakenBroker"), (
        "se construye el adaptador antes de comprobar que se puede usar"
    )


def test_the_forbidden_limits_cannot_be_turned_on():
    """Relajar el mandato no puede ser editar una linea de YAML."""
    from stocks_tracker.core.config import ConfigError, TradingConfig

    for key in ("allow_shorting", "allow_leverage", "allow_options",
                "allow_extended_hours"):
        with pytest.raises(ConfigError, match=key):
            TradingConfig(raw={"risk": {key: True}})


# ---------------------------------------------------------------------------
# El bot de acciones esta retirado
# ---------------------------------------------------------------------------
def test_the_stock_bot_is_gone():
    """Se retiro a peticion del usuario: operar en bolsa exigia permisos y
    tramites que no compensaban para lo que usa, que es analisis.

    El test existe para que no vuelva a medias. Un modulo huerfano que nadie
    llama no molesta, pero uno que alguien vuelve a enchufar sin querer si:
    tiene stops de 2,5x ATR, limite PDT y bloqueo por resultados trimestrales,
    y nada de eso vale para lo que queda.
    """
    assert not (SRC / "trading" / "strategies" / "momentum_multifactor.py").exists()

    from stocks_tracker.trading.run_bot import STRATEGY_BY_VENUE

    assert set(STRATEGY_BY_VENUE) == {"kraken"}


def test_there_is_no_default_market_to_fall_into():
    """Sin estrategia por defecto no se puede operar un mercado que nadie
    pidio. Es lo que convierte la retirada en algo comprobable."""
    from stocks_tracker.trading.run_bot import strategy_for

    with pytest.raises(ValueError, match="--venue"):
        strategy_for(None)


def test_the_analysis_layer_survived_untouched():
    """Lo que se quito fue el bot, no los datos. El dashboard, los indicadores,
    los factores y la validacion de senales no dependian de el y siguen ahi:
    es justamente lo que el usuario usa."""
    for modulo in ("compute/run_compute.py", "backtest/engine.py",
                   "backtest/run_backtest.py", "app/pages/7_validacion.py",
                   "app/pages/3_oportunidades.py", "app/pages/4_ficha_valor.py"):
        assert (SRC / modulo).exists(), f"se ha llevado por delante {modulo}"


def test_the_signals_page_is_only_about_signals_now():
    """El veredicto del bot se movio a la pagina del bot. Mezclarlos hacia que
    una pagina de analisis diario abriera con un asunto de operativa."""
    pagina = (SRC / "app" / "pages" / "7_validacion.py").read_text("utf-8")
    assert "trading import gate" not in pagina
    bot = (SRC / "app" / "pages" / "10_bot.py").read_text("utf-8")
    assert "trading import gate" in bot
