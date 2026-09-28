"""El freno debe ser fácil de disparar y difícil de rearmar."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from stocks_tracker.core import db
from stocks_tracker.trading import killswitch as ks


@pytest.fixture
def warehouse(tmp_path, monkeypatch):
    class Stub:
        warehouse_path = tmp_path / "kill.duckdb"
        logs_dir = tmp_path / "logs"

    class Trading:
        kill_switch = {"cooldown_hours": 12}

    monkeypatch.setattr(db, "get_settings", lambda: Stub())
    monkeypatch.setattr(ks, "get_trading_config", lambda: Trading())
    db.migrate()
    return Stub.warehouse_path


def test_estado_inicial_y_permisos(warehouse):
    state = ks.read_state("paper")
    assert state.state is ks.State.RUNNING
    assert state.can_open and state.can_protect
    assert not ks.BotState("x", ks.State.HALTED).can_protect


def test_disparo_liquidacion_y_estado_final(warehouse):
    assert ks.trip("paper", "max_drawdown", "demasiada caida", "flatten") is ks.State.FLATTEN_PENDING
    assert ks.read_state("paper").can_protect
    ks.mark_flattened("paper")
    assert ks.read_state("paper").state is ks.State.HALTED


def test_solo_la_perdida_diaria_se_rearma_automaticamente(warehouse):
    ks.trip("paper", "daily_loss", "limite", "halt_new")
    assert ks.clear_daily_halt("paper") is True
    assert ks.read_state("paper").state is ks.State.RUNNING

    ks.trip("paper", "max_drawdown", "limite", "halt_new")
    assert ks.clear_daily_halt("paper") is False


def test_inicio_de_dia_y_maximo_solo_avanzan(warehouse):
    ks.start_day("paper", 100.0, date(2026, 9, 21))
    ks.update_peak("paper", 120.0)
    ks.update_peak("paper", 110.0)
    state = ks.read_state("paper")
    assert state.day_start_equity == 100.0
    assert state.peak_equity == 120.0


def test_rearme_exige_frase_parada_y_cooldown(warehouse):
    with pytest.raises(ValueError, match="frase"):
        ks.rearm("paper", "si", "nota")
    with pytest.raises(ValueError, match="no esta parado"):
        ks.rearm("paper", "REARMAR BOT PAPER", "nota")

    ks.trip("paper", "max_drawdown", "limite", "flatten")
    with pytest.raises(ValueError, match="Faltan"):
        ks.rearm("paper", "REARMAR BOT PAPER", "nota")


def test_rearme_manual_reinicia_el_pico_tras_drawdown(warehouse):
    ks._write(
        "paper", state=str(ks.State.HALTED), halt_rule="max_drawdown",
        halt_detail="limite", halted_at=datetime.now() - timedelta(days=1),
        peak_equity=200.0,
    )
    ks.rearm("paper", "REARMAR BOT PAPER", "revisado")
    state = ks.read_state("paper")
    assert state.state is ks.State.RUNNING
    assert state.peak_equity == 0.0
    with db.connect(read_only=True) as conn:
        row = conn.execute(
            "SELECT rearmed_by, rearm_note FROM bot_state WHERE mode='paper'"
        ).fetchone()
    assert row == ("cli", "revisado")


def test_rearme_live_exige_confirmacion_externa(warehouse, monkeypatch):
    ks._write(
        "live", state=str(ks.State.HALTED), halt_rule="manual",
        halted_at=datetime.now() - timedelta(days=1),
    )
    monkeypatch.delenv("ALPACA_LIVE_CONFIRMED", raising=False)
    with pytest.raises(ValueError, match="ALPACA_LIVE_CONFIRMED"):
        ks.rearm("live", "REARMAR BOT LIVE", "nota")
    monkeypatch.setenv("ALPACA_LIVE_CONFIRMED", "1")
    ks.rearm("live", "REARMAR BOT LIVE", "nota")
    assert ks.read_state("live").state is ks.State.RUNNING


def test_cli_muestra_estado_y_reporta_rearme_fallido(warehouse, capsys):
    assert ks.main(["status", "--mode", "paper"]) == 0
    assert "RUNNING" in capsys.readouterr().out
    assert ks.main([
        "rearm", "--mode", "paper", "--confirm", "mal", "--note", "nota"
    ]) == 1
    assert "No se ha rearmado" in capsys.readouterr().err
