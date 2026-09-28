"""Solo los botones del usuario pueden ejecutar la actualización de análisis."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from stocks_tracker.app import research_actions as actions


@pytest.mark.parametrize("download, count", [(False, 2), (True, 3)])
def test_manual_update_uses_only_research_modules(monkeypatch, download, count):
    run = Mock(return_value=SimpleNamespace(returncode=0))
    monkeypatch.setattr(actions.subprocess, "run", run)
    actions.refresh_research(cash=1500, download=download)
    assert run.call_count == count
    calls = [call.args[0] for call in run.call_args_list]
    assert calls[-1][-2:] == ["--caja", "1500"]
    assert all("trading" not in " ".join(call) for call in calls)
    assert all(call.kwargs["timeout"] == 900 for call in run.call_args_list)


def test_failure_stops_the_pipeline_without_leaking_output(monkeypatch):
    run = Mock(return_value=SimpleNamespace(returncode=1, stderr="secret-token"))
    monkeypatch.setattr(actions.subprocess, "run", run)
    with pytest.raises(RuntimeError) as err:
        actions.refresh_research(cash=0, download=True)
    assert run.call_count == 1
    assert "secret-token" not in str(err.value)


@pytest.mark.parametrize("cash", [-1, float("nan"), float("inf")])
def test_invalid_cash_never_starts_a_process(monkeypatch, cash):
    run = Mock()
    monkeypatch.setattr(actions.subprocess, "run", run)
    with pytest.raises(ValueError):
        actions.refresh_research(cash=cash)
    run.assert_not_called()
