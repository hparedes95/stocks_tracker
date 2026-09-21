from __future__ import annotations

import json

from stocks_tracker.core import observability


def test_eventos_son_json_y_omiten_secretos(tmp_path, monkeypatch):
    class Stub:
        logs_dir = tmp_path

    monkeypatch.setattr(observability, "get_settings", lambda: Stub())
    assert observability.emit(
        "provider.failed", provider="demo", api_key="no-debe-salir", rows=3
    )

    record = json.loads(next(tmp_path.glob("events-*.jsonl")).read_text("utf-8"))
    assert record["event"] == "provider.failed"
    assert record["rows"] == 3
    assert "api_key" not in record


def test_un_fallo_del_log_no_rompe_el_proceso(tmp_path, monkeypatch):
    class Stub:
        logs_dir = tmp_path / "un-fichero"

    Stub.logs_dir.write_text("ocupado", encoding="utf-8")
    monkeypatch.setattr(observability, "get_settings", lambda: Stub())

    assert observability.emit("algo.ocurrio") is False
