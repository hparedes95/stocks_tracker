"""Solo los botones del usuario pueden ejecutar la actualización de análisis."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from stocks_tracker.app import research_actions as actions


@pytest.fixture(autouse=True)
def complete_market(monkeypatch):
    from stocks_tracker.core import snaptrade_portfolio as snap
    monkeypatch.setattr(snap, 'refresh_active', lambda: False)
    from stocks_tracker.ingest import run_ingest
    monkeypatch.setattr(run_ingest, 'needs_update', lambda: run_ingest.Frescura(False, 'complete'))


@pytest.mark.parametrize("download, count", [(False, 2), (True, 5)])
def test_manual_update_uses_only_research_modules(monkeypatch, download, count):
    run = Mock(return_value=SimpleNamespace(returncode=0))
    monkeypatch.setattr(actions.subprocess, "run", run)
    actions.refresh_research(cash=1500, download=download)
    assert run.call_count == count
    calls = [call.args[0] for call in run.call_args_list]
    assert calls[-1][-2:] == ["--caja", "1500"]
    if download:
        assert calls[0][-1] == 'stocks_tracker.ingest.holding_refresh'
    assert all("trading" not in " ".join(call) for call in calls)
    assert all(call.kwargs["timeout"] == 900 for call in run.call_args_list)


def test_frozen_copy_recalculates_only_personal_advice(monkeypatch):
    from stocks_tracker.core import market_bundle
    monkeypatch.setattr(market_bundle, 'frozen_market', lambda: True)
    run = Mock(return_value=SimpleNamespace(returncode=0))
    monkeypatch.setattr(actions.subprocess, 'run', run)
    actions.refresh_research(cash=100, download=True)
    assert run.call_count == 1
    assert 'stocks_tracker.compute.run_advice' in run.call_args.args[0]


def test_download_refreshes_broker_before_fetching_holdings(monkeypatch):
    from stocks_tracker.core import snaptrade_portfolio as snap
    events = []
    monkeypatch.setattr(snap, 'refresh_active', lambda: events.append('broker'))
    def run(args, **kwargs):
        events.append(args[2])
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(actions.subprocess, 'run', run)
    actions.refresh_research(cash=0, download=True)
    assert events[:2] == ['broker', 'stocks_tracker.ingest.holding_refresh']
    events.clear()
    actions.refresh_research(cash=0, download=False)
    assert 'broker' not in events


def test_failed_broker_refresh_never_runs_advice_on_old_snapshot(monkeypatch):
    from stocks_tracker.core import snaptrade_portfolio as snap
    from stocks_tracker.providers.snaptrade import SnapTradeError
    monkeypatch.setattr(snap, 'refresh_active', Mock(side_effect=SnapTradeError('Conexión caducada.')))
    run = Mock()
    monkeypatch.setattr(actions.subprocess, 'run', run)
    with pytest.raises(SnapTradeError, match='caducada'):
        actions.refresh_research(cash=0, download=True)
    run.assert_not_called()


def test_failure_stops_the_pipeline_without_leaking_output(monkeypatch):
    run = Mock(return_value=SimpleNamespace(returncode=1, stderr="secret-token"))
    monkeypatch.setattr(actions.subprocess, "run", run)
    with pytest.raises(RuntimeError) as err:
        actions.refresh_research(cash=0, download=True)
    assert run.call_count == 1
    assert "secret-token" not in str(err.value)


@pytest.mark.parametrize('code,stderr,expected', [
    (75, 'private account details', 'otro proceso'),
    (1, 'PermissionError: secret-token', 'denegado'),
    (1, 'OSError: [WinError 112] secret-token', 'espacio'),
    (1, 'ModuleNotFoundError: secret-token', 'dependencia'),
])
def test_known_failures_are_actionable_and_never_echo_raw_output(monkeypatch, code, stderr, expected):
    monkeypatch.setattr(actions.subprocess, 'run', Mock(return_value=SimpleNamespace(returncode=code, stderr=stderr)))
    with pytest.raises(RuntimeError) as error:
        actions.refresh_research(cash=0, download=True)
    assert expected in str(error.value)
    assert 'secret-token' not in str(error.value) and 'private account' not in str(error.value)


def test_holding_refresh_reports_writer_contention_as_temporary(monkeypatch, capsys):
    from contextlib import contextmanager

    from stocks_tracker.core.locking import AlreadyRunning
    from stocks_tracker.ingest import holding_refresh

    @contextmanager
    def busy(task):
        raise AlreadyRunning('private lock metadata')
        yield

    monkeypatch.setattr(holding_refresh, 'single_writer', busy)
    refresh = Mock()
    monkeypatch.setattr(holding_refresh, 'refresh', refresh)
    assert holding_refresh.main() == 75
    refresh.assert_not_called()
    output = capsys.readouterr().out
    assert 'otro proceso' in output and 'private' not in output


def test_incomplete_download_cannot_replace_advice_with_old_session(monkeypatch):
    from stocks_tracker.ingest import run_ingest
    monkeypatch.setattr(run_ingest, 'needs_update', lambda: run_ingest.Frescura(False, 'Falta el cierre', True))
    run = Mock(return_value=SimpleNamespace(returncode=0))
    monkeypatch.setattr(actions.subprocess, 'run', run)
    with pytest.raises(RuntimeError, match='no ha completado'):
        actions.refresh_research(cash=100, download=True)
    assert run.call_count == 4
    assert not any('stocks_tracker.compute.run_advice' in c.args[0] for c in run.call_args_list)


@pytest.mark.parametrize("cash", [-1, float("nan"), float("inf")])
def test_invalid_cash_never_starts_a_process(monkeypatch, cash):
    run = Mock()
    monkeypatch.setattr(actions.subprocess, "run", run)
    with pytest.raises(ValueError):
        actions.refresh_research(cash=cash)
    run.assert_not_called()
