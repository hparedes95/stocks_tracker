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
def test_alpaca_is_only_imported_in_its_adapter():
    """El SDK del broker vive en un unico fichero.

    Sin esa frontera, cambiar de broker o probar sin broker obliga a tocar
    media docena de modulos, y el CI acabaria necesitando credenciales.
    """
    allowed = SRC / "trading" / "brokers" / "alpaca.py"
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
                     "trading/strategies/momentum_multifactor.py"):
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
def test_the_dashboard_shows_the_verdict_but_not_the_bot():
    """La fase 6 es "sin UI", y el motivo era concreto: si una pagina mostrase
    propuestas de una estrategia que nadie ha validado, el usuario las leeria
    como recomendaciones.

    Mostrar el INFORME de la validacion es lo contrario de ese riesgo: dice
    precisamente si esta validada o no, y tiene que verlo quien pone el dinero
    sin escribir un comando. Lo que sigue prohibido es la estrategia, el
    riesgo, las intenciones y las ordenes.
    """
    prohibido = ("trading.risk", "trading.strategies", "trading.run_bot",
                 "trading.execution", "trading.intents", "trading.journal")

    offenders = []
    for path in (SRC / "app").rglob("*.py"):
        source = path.read_text("utf-8")
        for modulo in prohibido:
            if modulo in source:
                offenders.append(f"{path.relative_to(SRC).as_posix()} -> {modulo}")

    assert not offenders, f"el dashboard usa el bot: {offenders}"


def test_the_dashboard_never_shows_orders_or_intents():
    """Ninguna pagina puede leer las tablas de operativa del bot todavia."""
    offenders = []
    for path in (SRC / "app").rglob("*.py"):
        source = path.read_text("utf-8")
        for tabla in ("FROM intents", "FROM orders", "FROM bot_positions"):
            if tabla in source:
                offenders.append(f"{path.relative_to(SRC).as_posix()} -> {tabla}")
    assert not offenders, f"el dashboard muestra operativa sin validar: {offenders}"


def test_paper_and_live_are_refused_in_phase_six():
    from stocks_tracker.core.config import ConfigError
    from stocks_tracker.trading.brokers.registry import get_broker

    for mode in ("paper", "live"):
        with pytest.raises(ConfigError, match="fase 7"):
            get_broker(mode)


def test_the_forbidden_limits_cannot_be_turned_on():
    """Relajar el mandato no puede ser editar una linea de YAML."""
    from stocks_tracker.core.config import ConfigError, TradingConfig

    for key in ("allow_shorting", "allow_leverage", "allow_options",
                "allow_extended_hours"):
        with pytest.raises(ConfigError, match=key):
            TradingConfig(raw={"risk": {key: True}})
