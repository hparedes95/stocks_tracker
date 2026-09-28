"""Tests del vigilante de mercado en vivo.

El riesgo real de este componente no es que no detecte un desplome: con
umbrales tan gruesos, eso es aritmetica. El riesgo es que **avise tanto que lo
silencies**, y entonces el dia del crash el mensaje llegue a un canal que
tienes mudo. Por eso la mayor parte de estos tests son del control de escalado.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pandas as pd
import pytest

from stocks_tracker.providers.base import (
    QUOTE_COLUMNS,
    QuoteProvider,
    normalize_quotes,
)
from stocks_tracker.providers.synthetic_provider import SyntheticProvider
from stocks_tracker.watch import monitor
from stocks_tracker.watch import state as state_mod
from stocks_tracker.watch.config import WatchConfig, is_watch_time

NOW = datetime(2024, 6, 28, 16, 0, 0)

CFG = WatchConfig(
    raw={
        "escalation_only": True,
        "min_minutes_between": 10,
        "notify_recovery": True,
        "symbols": {
            "indices": ["SPY"],
            "volatility": ["^VIX"],
            "crypto": ["BTC-USD"],
        },
        "thresholds": {
            "index_drop": [
                {"pct": -1.5, "severity": "media", "label": "caida notable"},
                {"pct": -3.0, "severity": "alta", "label": "caida fuerte"},
                {"pct": -7.0, "severity": "critica", "label": "cortacircuitos nivel 1"},
            ],
            "crypto_drop": [
                {"pct": -7.0, "severity": "media", "label": "caida notable"},
                {"pct": -20.0, "severity": "critica", "label": "desplome"},
            ],
            "vix_level": [
                {"value": 30, "severity": "alta", "label": "VIX en zona de tension"},
                {"value": 40, "severity": "critica", "label": "VIX en zona de panico"},
            ],
            "vix_jump_pct": [
                {"pct": 20, "severity": "alta", "label": "salto del VIX"},
            ],
            "portfolio_drop": [
                {"pct": -3.0, "severity": "alta", "label": "tu cartera cae con fuerza"},
            ],
            "position_drop": [
                {"pct": -8.0, "severity": "alta", "label": "una posicion se hunde"},
            ],
        },
    }
)


# Con los tres escalones de cortacircuitos, para probar que -7 y -13 se
# distinguen aunque ambos sean "critica".
DEEP_CFG = WatchConfig(
    raw={
        **CFG.raw,
        "thresholds": {
            **CFG.raw["thresholds"],
            "index_drop": [
                *CFG.raw["thresholds"]["index_drop"],
                {"pct": -13.0, "severity": "critica",
                 "label": "cortacircuitos nivel 2"},
            ],
        },
    }
)


def quote(ticker: str, change_pct: float, price: float = 100.0) -> dict:
    previous = price / (1 + change_pct)
    return {
        "ticker": ticker, "as_of": NOW, "price": price,
        "previous_close": previous, "change_pct": None,
        "day_high": price, "day_low": price, "volume": 1000, "currency": "USD",
    }


def frame(*rows: dict) -> pd.DataFrame:
    return normalize_quotes(pd.DataFrame(list(rows)), "test")


# ---------------------------------------------------------------------------
# Normalizacion
# ---------------------------------------------------------------------------
def test_change_pct_is_recalculated_not_trusted():
    """Es el numero con el que se decide si hay desplome: no puede venir de
    fuera sin comprobar."""
    raw = pd.DataFrame([{**quote("SPY", -0.05, price=95.0), "change_pct": 0.99}])
    out = normalize_quotes(raw, "test")
    assert float(out["change_pct"].iloc[0]) == pytest.approx(-0.05, abs=1e-9)


def test_quotes_keep_the_canonical_schema():
    out = frame(quote("SPY", -0.01))
    for col in QUOTE_COLUMNS:
        assert col in out.columns


def test_rows_without_price_are_dropped():
    raw = pd.DataFrame([{**quote("SPY", -0.01), "price": None}])
    assert normalize_quotes(raw, "test").empty


def test_zero_previous_close_does_not_divide_by_zero():
    raw = pd.DataFrame([{**quote("SPY", -0.01), "previous_close": 0.0}])
    out = normalize_quotes(raw, "test")
    assert pd.isna(out["change_pct"].iloc[0])


def test_synthetic_provider_satisfies_the_quote_protocol():
    assert isinstance(SyntheticProvider(), QuoteProvider)


# ---------------------------------------------------------------------------
# Deteccion
# ---------------------------------------------------------------------------
def test_detects_the_worst_threshold_crossed_not_the_first():
    """Un -8% debe avisar de cortacircuitos, no de 'caida notable'."""
    breaches = monitor.evaluate_quotes(frame(quote("SPY", -0.08)), cfg=CFG)
    assert len(breaches) == 1
    assert breaches[0].severity == "critica"
    assert breaches[0].level == -7.0


def test_small_move_triggers_nothing():
    assert monitor.evaluate_quotes(frame(quote("SPY", -0.004)), cfg=CFG) == []


def test_a_rise_never_triggers_a_drop_alert():
    assert monitor.evaluate_quotes(frame(quote("SPY", 0.09)), cfg=CFG) == []


def test_crypto_uses_its_own_wider_thresholds():
    """Con los umbrales del indice, la cripto avisaria casi a diario."""
    assert monitor.evaluate_quotes(frame(quote("BTC-USD", -0.05)), cfg=CFG) == []
    assert monitor.evaluate_quotes(frame(quote("BTC-USD", -0.09)), cfg=CFG)


def test_vix_triggers_on_absolute_level_and_on_jump():
    breaches = monitor.evaluate_quotes(frame(quote("^VIX", 0.30, price=35.0)), cfg=CFG)
    kinds = {b.kind for b in breaches}
    assert kinds == {"vix_level", "vix_jump"}


def test_vix_falling_does_not_alert():
    assert monitor.evaluate_quotes(frame(quote("^VIX", -0.10, price=14.0)), cfg=CFG) == []


def test_portfolio_drop_is_weighted_by_position_size():
    """Una caida del 10% en la posicion grande pesa mas que un +1% en la chica."""
    portfolio = pd.DataFrame({"ticker": ["AAA", "BBB"], "qty": [100.0, 1.0]})
    quotes = frame(quote("AAA", -0.10, price=90.0), quote("BBB", 0.01, price=101.0))
    breaches = monitor.evaluate_quotes(quotes, portfolio, CFG)

    # 100x90 + 1x101 = 9101 frente a 100x100 + 1x100 = 10100 -> -9,89%.
    # Casi toda la caida de AAA, porque BBB pesa un 1% de la cartera.
    portfolio_breaches = [b for b in breaches if b.kind == "portfolio_drop"]
    assert len(portfolio_breaches) == 1
    assert portfolio_breaches[0].change_pct == pytest.approx(-9.89, abs=0.02)


def test_portfolio_is_skipped_when_a_position_has_no_quote():
    """Calcularla con datos incompletos diria que cae menos de lo que cae."""
    portfolio = pd.DataFrame({"ticker": ["AAA", "SIN_DATO"], "qty": [10.0, 10.0]})
    quotes = frame(quote("AAA", -0.10, price=90.0))
    assert [b for b in monitor.evaluate_quotes(quotes, portfolio, CFG)
            if b.kind == "portfolio_drop"] == []


def test_a_held_position_uses_position_thresholds():
    portfolio = pd.DataFrame({"ticker": ["AAA"], "qty": [10.0]})
    breaches = monitor.evaluate_quotes(frame(quote("AAA", -0.09)), portfolio, CFG)
    assert any(b.kind == "position_drop" for b in breaches)


# ---------------------------------------------------------------------------
# Escalado: lo que evita que silencies el canal
# ---------------------------------------------------------------------------
def test_the_same_level_does_not_alert_twice():
    """Un mercado parado en -3,2% cumple el umbral cada minuto durante horas."""
    st = state_mod.WatchState()
    quotes = frame(quote("SPY", -0.032))

    first = monitor.to_alerts(monitor.evaluate_quotes(quotes, cfg=CFG), st, CFG, NOW)
    second = monitor.to_alerts(
        monitor.evaluate_quotes(quotes, cfg=CFG), st, CFG, NOW + timedelta(minutes=30)
    )

    assert len(first) == 1
    assert second == [], "El segundo aviso es el que te hace silenciar el canal"


def test_getting_worse_does_alert_again():
    """Lo contrario tambien seria un fallo: pasar de -3% a -8% importa."""
    st = state_mod.WatchState()
    monitor.to_alerts(
        monitor.evaluate_quotes(frame(quote("SPY", -0.032)), cfg=CFG), st, CFG, NOW
    )
    escalated = monitor.to_alerts(
        monitor.evaluate_quotes(frame(quote("SPY", -0.08)), cfg=CFG), st, CFG,
        NOW + timedelta(minutes=30),
    )

    assert len(escalated) == 1
    assert escalated[0].severity == "critica"


def test_recovering_within_the_same_level_does_not_alert():
    st = state_mod.WatchState()
    monitor.to_alerts(
        monitor.evaluate_quotes(frame(quote("SPY", -0.08)), cfg=CFG), st, CFG, NOW
    )
    milder = monitor.to_alerts(
        monitor.evaluate_quotes(frame(quote("SPY", -0.04)), cfg=CFG), st, CFG,
        NOW + timedelta(minutes=30),
    )
    assert milder == []


def test_minimum_gap_applies_within_the_same_severity():
    """Dos avisos seguidos de la misma gravedad son cháchara."""
    st = state_mod.WatchState()
    monitor.to_alerts(
        monitor.evaluate_quotes(frame(quote("SPY", -0.02)), cfg=CFG), st, CFG, NOW
    )
    # -1.5% y -2.9% son el mismo escalon (media): no hay nada nuevo que contar.
    immediate = monitor.to_alerts(
        monitor.evaluate_quotes(frame(quote("SPY", -0.029)), cfg=CFG), st, CFG,
        NOW + timedelta(minutes=2),
    )
    assert immediate == []


def test_jumping_to_a_worse_level_ignores_the_minimum_gap():
    """De -3% a -8% son cortacircuitos: esperar diez minutos seria absurdo.

    Es justo el escenario para el que existe el vigilante, asi que el
    antirruido no puede ser lo que lo tape.
    """
    st = state_mod.WatchState()
    monitor.to_alerts(
        monitor.evaluate_quotes(frame(quote("SPY", -0.032)), cfg=CFG), st, CFG, NOW
    )
    two_minutes_later = monitor.to_alerts(
        monitor.evaluate_quotes(frame(quote("SPY", -0.08)), cfg=CFG), st, CFG,
        NOW + timedelta(minutes=2),
    )

    assert len(two_minutes_later) == 1
    assert two_minutes_later[0].severity == "critica"


def test_deeper_level_of_the_same_severity_still_alerts():
    """-7% y -13% son ambos "critica", pero son cortacircuitos nivel 1 y 2.

    Agruparlos por gravedad haria que el segundo se perdiera, que es el peor
    momento posible para callarse.
    """
    st = state_mod.WatchState()
    monitor.to_alerts(
        monitor.evaluate_quotes(frame(quote("SPY", -0.08)), cfg=DEEP_CFG), st,
        DEEP_CFG, NOW,
    )
    deeper = monitor.to_alerts(
        monitor.evaluate_quotes(frame(quote("SPY", -0.14)), cfg=DEEP_CFG), st,
        DEEP_CFG, NOW + timedelta(minutes=1),
    )
    assert len(deeper) == 1
    assert deeper[0].payload["level"] == -13.0


def test_a_new_session_rearms_everything():
    """Los niveles se miden contra el cierre anterior: al cambiar de dia dejan
    de significar nada."""
    st = state_mod.WatchState()
    monitor.to_alerts(
        monitor.evaluate_quotes(frame(quote("SPY", -0.08)), cfg=CFG), st, CFG, NOW
    )
    assert st.levels

    assert st.roll_over(date(2024, 6, 29)) is True
    assert st.levels == {}

    again = monitor.to_alerts(
        monitor.evaluate_quotes(frame(quote("SPY", -0.08)), cfg=CFG), st, CFG,
        NOW + timedelta(days=1),
    )
    assert len(again) == 1


def test_roll_over_is_idempotent_within_the_day():
    st = state_mod.WatchState()
    assert st.roll_over(date(2024, 6, 28)) is True
    assert st.roll_over(date(2024, 6, 28)) is False


def test_recovery_is_announced_once_and_rearms():
    st = state_mod.WatchState()
    monitor.to_alerts(
        monitor.evaluate_quotes(frame(quote("SPY", -0.08)), cfg=CFG), st, CFG, NOW
    )

    recovered = frame(quote("SPY", -0.002))
    first = monitor.recovery_alerts(recovered, st, CFG, NOW + timedelta(hours=1))
    second = monitor.recovery_alerts(recovered, st, CFG, NOW + timedelta(hours=2))

    assert len(first) == 1
    assert "recupera" in first[0].message
    assert second == [], "La recuperacion se anuncia una vez, no en bucle"


def test_alerts_are_marked_as_live():
    """La interfaz debe poder distinguirlos de los avisos de cierre."""
    st = state_mod.WatchState()
    alerts = monitor.to_alerts(
        monitor.evaluate_quotes(frame(quote("SPY", -0.08)), cfg=CFG), st, CFG, NOW
    )
    assert alerts[0].payload["live"] is True
    assert alerts[0].rule_id.startswith("watch_")


def test_message_says_the_number_and_what_it_means():
    st = state_mod.WatchState()
    alerts = monitor.to_alerts(
        monitor.evaluate_quotes(frame(quote("SPY", -0.08, price=400.0)), cfg=CFG),
        st, CFG, NOW,
    )
    message = alerts[0].message
    assert "SPY" in message and "-8.0%" in message
    assert "Cortacircuitos nivel 1" in message


def test_vix_keeps_its_capitalisation_in_the_message():
    st = state_mod.WatchState()
    alerts = monitor.to_alerts(
        monitor.evaluate_quotes(frame(quote("^VIX", 0.30, price=35.0)), cfg=CFG),
        st, CFG, NOW,
    )
    assert all("Vix" not in a.message for a in alerts)


# ---------------------------------------------------------------------------
# Estado en disco
# ---------------------------------------------------------------------------
def test_state_survives_a_restart(tmp_path):
    path = tmp_path / "watch_state.json"
    st = state_mod.WatchState()
    st.roll_over(date(2024, 6, 28))
    st.record("SPY:index_drop", -7.0, NOW)
    state_mod.save(st, path)

    restored = state_mod.load(path)
    assert restored.day == "2024-06-28"
    assert restored.level_of("SPY:index_drop") == -7.0


def test_corrupt_state_does_not_stop_the_watcher(tmp_path):
    """Perder la memoria es malo; dejar de vigilar es peor."""
    path = tmp_path / "watch_state.json"
    path.write_text("{ esto no es json", encoding="utf-8")
    assert state_mod.load(path).levels == {}


def test_missing_state_file_is_fine(tmp_path):
    assert state_mod.load(tmp_path / "no_existe.json").levels == {}


def test_state_is_written_atomically(tmp_path):
    """Un fichero a medias haria que el vigilante volviera a avisar de todo."""
    path = tmp_path / "watch_state.json"
    st = state_mod.WatchState()
    st.record("SPY:index_drop", -3.0, NOW)
    state_mod.save(st, path)

    assert path.exists()
    assert not path.with_suffix(".tmp").exists()


# ---------------------------------------------------------------------------
# Horario
# ---------------------------------------------------------------------------
def _schedule(**kwargs) -> WatchConfig:
    base = {"schedule": {"weekdays_only": True, "windows": ["15:30-22:00"]}}
    base["schedule"].update(kwargs)
    return WatchConfig(raw=base)


def test_windows_are_parsed():
    assert _schedule().windows == [(time(15, 30), time(22, 0))]


def test_malformed_window_is_ignored_not_fatal():
    assert _schedule(windows=["no valido", "15:30-22:00"]).windows == [
        (time(15, 30), time(22, 0))
    ]


def test_no_windows_means_always_watching(monkeypatch):
    from stocks_tracker.watch import config as watch_cfg

    monkeypatch.setattr(watch_cfg, "get_watch_config", lambda: _schedule(windows=[]))
    assert is_watch_time(datetime(2024, 6, 28, 3, 0)) is True


def test_weekend_is_skipped(monkeypatch):
    from stocks_tracker.watch import config as watch_cfg

    monkeypatch.setattr(watch_cfg, "get_watch_config", lambda: _schedule())
    # 2024-06-29 es sabado.
    assert is_watch_time(datetime(2024, 6, 29, 16, 0)) is False
    assert is_watch_time(datetime(2024, 6, 28, 16, 0)) is True


def test_outside_the_window_is_skipped(monkeypatch):
    from stocks_tracker.watch import config as watch_cfg

    monkeypatch.setattr(watch_cfg, "get_watch_config", lambda: _schedule())
    assert is_watch_time(datetime(2024, 6, 28, 8, 0)) is False


def test_interval_has_a_floor():
    """Sondear cada segundo no acelera nada y acerca el bloqueo de Yahoo."""
    assert WatchConfig(raw={"interval_seconds": 1}).interval_seconds == 15


# ---------------------------------------------------------------------------
# Configuracion real del proyecto
# ---------------------------------------------------------------------------
def test_shipped_config_uses_the_real_circuit_breakers():
    """-7, -13 y -20 son los cortacircuitos de la SEC, no cifras inventadas."""
    from stocks_tracker.watch.config import get_watch_config

    levels = [t.value for t in get_watch_config().thresholds("index_drop")]
    for official in (-7.0, -13.0, -20.0):
        assert official in levels


def test_shipped_thresholds_are_ordered_from_mild_to_severe():
    from stocks_tracker.watch.config import get_watch_config

    cfg = get_watch_config()
    for name in ("index_drop", "crypto_drop", "portfolio_drop", "position_drop"):
        values = [t.value for t in cfg.thresholds(name)]
        assert values == sorted(values, reverse=True), f"'{name}' mal ordenado"


def test_shipped_config_watches_few_symbols():
    """Cada simbolo es cuota de peticiones. Un desplome se ve en los indices."""
    from stocks_tracker.watch.config import get_watch_config

    assert len(get_watch_config().all_symbols) <= 15
