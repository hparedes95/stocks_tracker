"""Tests del sistema de alertas.

Lo que se protege aqui es, sobre todo, que el sistema no se vuelva ruido:

- el periodo de espera evita repetir el mismo aviso cada dia;
- las condiciones del YAML nunca ejecutan codigo arbitrario;
- ningun canal filtra un secreto al mostrar su estado o un error.
"""

from __future__ import annotations

import json
import os
from datetime import date, timedelta

import duckdb
import pandas as pd
import pytest

from stocks_tracker.alerts import evaluate as ev
from stocks_tracker.alerts import notify as nt
from stocks_tracker.alerts.rules import (
    SEVERITIES,
    ChannelConfig,
    Rule,
    get_channels,
    get_defaults,
    get_rules,
    severity_rank,
)
from stocks_tracker.core import db
from stocks_tracker.core.safe_eval import UnsafeExpressionError, compile_condition
from stocks_tracker.core.safe_eval import evaluate as safe_evaluate

TODAY = date(2024, 6, 28)


# ---------------------------------------------------------------------------
# Almacen de prueba
# ---------------------------------------------------------------------------
def _seed(path) -> None:
    """Universo minimo pero completo: dos valores, uno en cada situacion."""
    conn = duckdb.connect(str(path))
    conn.execute(db.schema_path().read_text(encoding="utf-8"))

    conn.execute(
        """
        INSERT INTO instruments (ticker, name, gics_sector, currency, investment_type)
        VALUES ('CAIDA', 'Valor en caida', 'Tecnologia', 'USD', 'equity'),
               ('SANO',  'Valor sano',     'Banca',      'USD', 'equity')
        """
    )
    # CAIDA: por debajo de la MM200 y con MACD negativo -> dispara las reglas
    # de ruptura. SANO: en tendencia, no dispara nada.
    conn.execute(
        """
        INSERT INTO indicators_daily
            (ticker, date, close, rsi14, adx14, macd_hist, above_sma200,
             drawdown, rel_volume_20, ret_1d)
        VALUES ('CAIDA', ?, 50.0, 28.0, 25.0, -1.5, FALSE, -0.30, 1.1, -0.03),
               ('SANO',  ?, 90.0, 55.0, 22.0,  0.8, TRUE,  -0.04, 1.0,  0.01)
        """,
        [TODAY, TODAY],
    )
    conn.execute(
        """
        INSERT INTO watchlist (ticker, list_name, target_price)
        VALUES ('CAIDA', 'default', NULL), ('SANO', 'default', NULL)
        """
    )
    conn.execute(
        """
        INSERT INTO positions (id, ticker, qty, avg_cost, currency, opened_at)
        VALUES ('p1', 'CAIDA', 10, 80.0, 'USD', ?)
        """,
        [TODAY - timedelta(days=100)],
    )
    conn.close()


@pytest.fixture
def warehouse(tmp_path, monkeypatch):
    """Apunta `connect()` a un almacen temporal. Ningun test toca el real."""
    path = tmp_path / "alerts.duckdb"
    _seed(path)

    class _Settings:
        warehouse_path = path

    monkeypatch.setattr(db, "get_settings", lambda: _Settings())
    return path


# ---------------------------------------------------------------------------
# Reglas del YAML
# ---------------------------------------------------------------------------
def test_configured_rules_are_well_formed():
    rules = get_rules()
    assert rules, "config/alerts.yaml no define ninguna regla"

    seen: set[str] = set()
    for rule in rules:
        assert rule.id not in seen, f"Id de regla duplicado: {rule.id}"
        seen.add(rule.id)
        assert rule.severity in SEVERITIES, f"Gravedad desconocida en {rule.id}"
        assert rule.cooldown_days > 0, (
            f"La regla {rule.id} no tiene periodo de espera: se repetiria a diario"
        )


def test_configured_conditions_compile_safely():
    """Si una condicion del YAML no compila, la regla nunca se dispararia."""
    for rule in get_rules():
        compile_condition(rule.when)


def test_scope_parsing():
    market = Rule(id="m", name="m", scope="market", when="True", message="x")
    universe = Rule(id="u", name="u", scope="universe:SP500", when="True", message="x")
    sector = Rule(id="s", name="s", scope="sector:Banca", when="True", message="x")

    assert market.is_market_scope and market.universe is None
    assert universe.universe == "SP500" and not universe.is_market_scope
    assert sector.sector == "Banca" and sector.universe is None


def test_severity_rank_orders_from_low_to_critical():
    ranks = [severity_rank(s) for s in SEVERITIES]
    assert ranks == sorted(ranks)
    # Una gravedad desconocida no puede colarse por delante de una critica.
    assert severity_rank("inventada") < severity_rank("critica")


def test_defaults_require_validated_signals():
    """Avisar de una senal sin evidencia historica es exactamente el ruido que
    el proyecto trata de evitar."""
    assert get_defaults().get("require_validated_signals") is True


# ---------------------------------------------------------------------------
# Evaluacion segura
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "expression",
    [
        "__import__('os').system('rm -rf /')",
        "open('/etc/passwd').read()",
        "close.__class__",
        "[x for x in range(10)]",
        "(lambda: 1)()",
    ],
)
def test_dangerous_expressions_are_rejected(expression):
    with pytest.raises((UnsafeExpressionError, SyntaxError)):
        compile_condition(expression)
    # Y a traves del camino que usan las reglas, se traduce en "no dispara".
    assert safe_evaluate(expression, {"close": 10.0}) is False


def test_missing_variable_does_not_fire():
    """Una regla que menciona una columna ausente debe callarse, no romper."""
    assert safe_evaluate("no_existe > 3", {"close": 10.0}) is False


def test_condition_evaluates_normally():
    variables = {"close": 50.0, "rsi14": 28.0, "above_sma200": False}
    assert safe_evaluate("above_sma200 == False and rsi14 < 35", variables) is True
    assert safe_evaluate("rsi14 > 70", variables) is False


# ---------------------------------------------------------------------------
# Ciclo completo y periodo de espera
# ---------------------------------------------------------------------------
def test_evaluate_fires_only_on_matching_tickers(warehouse):
    rule = Rule(
        id="prueba_ruptura", name="Ruptura", scope="watchlist",
        when="above_sma200 == False and macd_hist < 0",
        message="{ticker}: pierde la MM200 a {close:.2f}",
        severity="alta", cooldown_days=10,
    )
    alerts = ev.evaluate_rules((rule,))

    assert [a.ticker for a in alerts] == ["CAIDA"]
    assert alerts[0].message == "CAIDA: pierde la MM200 a 50.00"
    assert alerts[0].severity == "alta"


def test_cooldown_suppresses_the_second_run(warehouse):
    """La prueba que justifica el mecanismo: la condicion sigue siendo cierta
    al dia siguiente, pero no se vuelve a avisar."""
    rule = Rule(
        id="prueba_ruptura", name="Ruptura", scope="watchlist",
        when="above_sma200 == False", message="{ticker}", cooldown_days=10,
    )

    first = ev.evaluate_rules((rule,))
    assert len(first) == 1
    assert ev.persist(first) == 1

    second = ev.evaluate_rules((rule,))
    assert second == [], "El periodo de espera no ha suprimido el aviso repetido"


def test_cooldown_expires(warehouse):
    rule = Rule(
        id="prueba_corta", name="Ruptura", scope="watchlist",
        when="above_sma200 == False", message="{ticker}", cooldown_days=1,
    )
    alerts = ev.evaluate_rules((rule,))
    ev.persist(alerts)

    # Se envejece el aviso mas alla del periodo de espera.
    with db.connect() as conn:
        conn.execute(
            "UPDATE alerts SET triggered_at = triggered_at - INTERVAL 3 DAY"
        )

    assert len(ev.evaluate_rules((rule,))) == 1


def test_portfolio_scope_sees_average_cost(warehouse):
    rule = Rule(
        id="prueba_perdida", name="Perdida", scope="portfolio",
        when="pnl_pct < -0.2", message="{ticker}: {pnl_pct:.0%}", cooldown_days=5,
    )
    alerts = ev.evaluate_rules((rule,))
    # Coste medio 80, precio 50 -> -37,5%.
    assert len(alerts) == 1
    assert alerts[0].message == "CAIDA: -38%"


def test_market_rule_fires_once_without_ticker(warehouse):
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO regime_daily (date, regime, risk_score, vix)
            VALUES (?, 'riesgo', -2.0, 28.0), (?, 'neutral', 0.5, 17.0)
            """,
            [TODAY, TODAY - timedelta(days=1)],
        )

    rule = Rule(
        id="prueba_regimen", name="Cambio", scope="market", when="regime_changed",
        message="Semaforo a {regime}", severity="alta", cooldown_days=3,
    )
    alerts = ev.evaluate_rules((rule,))

    assert len(alerts) == 1
    assert alerts[0].ticker is None
    assert alerts[0].message == "Semaforo a riesgo"


def test_persist_and_acknowledge_roundtrip(warehouse):
    rule = Rule(
        id="prueba_ruptura", name="Ruptura", scope="watchlist",
        when="above_sma200 == False", message="{ticker}", cooldown_days=10,
    )
    alerts = ev.evaluate_rules((rule,))
    ev.persist(alerts)

    pending = ev.recent(days=30, only_unacknowledged=True)
    assert len(pending) == 1

    ev.acknowledge([alerts[0].id])
    assert ev.recent(days=30, only_unacknowledged=True).empty
    assert len(ev.recent(days=30)) == 1


def test_purge_removes_old_alerts(warehouse):
    rule = Rule(
        id="prueba_ruptura", name="Ruptura", scope="watchlist",
        when="above_sma200 == False", message="{ticker}", cooldown_days=10,
    )
    ev.persist(ev.evaluate_rules((rule,)))

    with db.connect() as conn:
        conn.execute(
            "UPDATE alerts SET triggered_at = triggered_at - INTERVAL 400 DAY"
        )

    assert ev.purge_older_than(365) == 1
    assert ev.recent(days=1000).empty


def test_evaluate_returns_nothing_without_data(tmp_path, monkeypatch):
    """Un almacen vacio no puede provocar una excepcion en cron."""
    path = tmp_path / "vacio.duckdb"
    conn = duckdb.connect(str(path))
    conn.execute(db.schema_path().read_text(encoding="utf-8"))
    conn.close()

    class _Settings:
        warehouse_path = path

    monkeypatch.setattr(db, "get_settings", lambda: _Settings())
    assert ev.evaluate_rules(get_rules()) == []


# ---------------------------------------------------------------------------
# Entrega
# ---------------------------------------------------------------------------
def _alert(severity: str, message: str) -> ev.Alert:
    return ev.Alert(
        id=f"id-{message}", rule_id="r", ticker="AAA",
        triggered_at=pd.Timestamp("2024-06-28 20:00:00"),
        message=message, severity=severity, payload={"close": 1.0},
    )


def test_digest_groups_by_severity_in_order():
    text = nt.format_digest(
        [_alert("media", "media-1"), _alert("critica", "critica-1"),
         _alert("media", "media-2")]
    )
    assert text.index("CRITICA") < text.index("MEDIA")
    assert "3 alertas" in text


def test_file_channel_writes_one_json_line_per_alert(tmp_path, monkeypatch):
    monkeypatch.setattr(nt, "project_root", lambda: tmp_path)
    result = nt.deliver_file(
        [_alert("alta", "uno"), _alert("baja", "dos")],
        {"path": "salida/alertas.jsonl"},
    )

    assert result.ok and result.sent == 2
    lines = (tmp_path / "salida" / "alertas.jsonl").read_text(
        encoding="utf-8"
    ).strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["message"] == "uno"


def test_delivery_continues_when_one_channel_fails(tmp_path, monkeypatch):
    """Perder un aviso porque Telegram estaba caido, teniendo el fichero local
    disponible, seria el peor resultado posible."""
    monkeypatch.setattr(nt, "project_root", lambda: tmp_path)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    channels = (
        ChannelConfig("telegram", True, {"bot_token_env": "TELEGRAM_BOT_TOKEN",
                                         "chat_id_env": "TELEGRAM_CHAT_ID"}),
        ChannelConfig("file", True, {"path": "alertas.jsonl"}),
    )
    results = {r.channel: r for r in nt.deliver([_alert("alta", "uno")], channels)}

    assert results["telegram"].ok is False
    assert results["file"].ok is True


def test_disabled_channels_are_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(nt, "project_root", lambda: tmp_path)
    channels = (ChannelConfig("file", False, {"path": "alertas.jsonl"}),)
    assert nt.deliver([_alert("alta", "uno")], channels) == []
    assert not (tmp_path / "alertas.jsonl").exists()


def test_unknown_channel_reports_instead_of_crashing():
    channels = (ChannelConfig("paloma_mensajera", True, {}),)
    results = nt.deliver([_alert("alta", "uno")], channels)
    assert len(results) == 1 and results[0].ok is False


def test_channel_status_never_reveals_secrets(monkeypatch):
    secret = "1234567890:AAHsuperSecretoQueNoDebeSalir"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", secret)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "999")

    rendered = json.dumps(nt.channel_status(), ensure_ascii=False)
    assert secret not in rendered
    assert "999" not in rendered
    # Pero si debe decir que canales existen y cuales estan listos.
    assert "telegram" in rendered


def test_channel_status_lists_missing_variables_by_name(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    telegram = next(s for s in nt.channel_status() if s["canal"] == "telegram")
    assert "TELEGRAM_BOT_TOKEN" in telegram["faltan"]


def test_redact_strips_the_token_from_an_error(monkeypatch):
    secret = "1234567890:AAHsuperSecretoQueNoDebeSalir"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", secret)
    message = f"HTTPSConnectionPool: /bot{secret}/sendMessage timed out"
    redacted = nt._redact(message)
    assert secret not in redacted
    assert "<TELEGRAM_BOT_TOKEN>" in redacted


def test_configured_channels_do_not_store_credentials():
    """Las claves van en el entorno; el YAML solo dice como se llaman.

    Este test se rompe el dia que alguien pegue un token en alerts.yaml.
    """
    for channel in get_channels():
        for key, value in channel.settings.items():
            if not isinstance(value, str):
                continue
            if key.endswith("_env"):
                # Es un nombre de variable, y debe existir como tal, no como valor.
                assert value.isupper(), f"{value} no parece un nombre de variable"
                continue
            assert ":" not in value, (
                f"El canal {channel.name} parece contener un secreto en '{key}'"
            )


def test_no_secret_names_are_hardcoded_with_values():
    """Ninguna variable de credenciales debe tener valor por defecto en el codigo."""
    for name in ("TELEGRAM_BOT_TOKEN", "SMTP_PASSWORD"):
        assert os.environ.get(name) in (None, ""), (
            f"{name} esta definida en el entorno de tests: el test no probaria nada"
        )
