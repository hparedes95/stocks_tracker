"""Behavioral/performance contracts; timings themselves are not CI assertions."""
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest
import streamlit as st

from stocks_tracker.app import runtime_reads
from stocks_tracker.core import db, indicators


@pytest.mark.parametrize('window', [1, 2, 7, 252, 1000])
@pytest.mark.parametrize('broken', [False, True])
def test_drawdown_matches_rolling_reference_across_block_boundaries(window, broken):
    values = 100 * np.exp(np.random.default_rng(12).normal(0, .05, 6000).cumsum())
    if broken:
        values[[12, 1100, 1500, 2200, 5800]] = [np.nan, np.inf, 0, -np.inf, np.nan]
    series = pd.Series(values, index=pd.date_range('2000-01-01', periods=len(values)), name='price')
    with np.errstate(divide='ignore', invalid='ignore'):
        expected = series.rolling(window, min_periods=window).apply(
            lambda x: np.min(x / np.maximum.accumulate(x) - 1), raw=True)
    pd.testing.assert_series_equal(indicators.max_drawdown(series, window), expected, check_exact=True)


def test_drawdown_short_nullable_and_future_data():
    series = pd.Series([1, 2, pd.NA, 3, 2, 4], dtype='Float64', name='price')
    assert indicators.max_drawdown(series, 10).isna().all()
    before = indicators.max_drawdown(series, 2)
    after = indicators.max_drawdown(pd.concat([series, pd.Series([1000, .01])], ignore_index=True), 2)
    np.testing.assert_equal(before.to_numpy(), after.iloc[:len(series)].to_numpy())
    assert indicators.max_drawdown(pd.Series([], dtype=float)).empty


@pytest.fixture
def warehouse(tmp_path, monkeypatch):
    path = tmp_path / 'cache.duckdb'
    monkeypatch.setattr(db, 'get_settings', lambda: SimpleNamespace(warehouse_path=path))
    st.cache_data.clear()
    db.migrate()
    yield path
    st.cache_data.clear()


def test_integrity_cache_reuses_reads_but_invalidates_committed_wal(warehouse):
    with patch.object(runtime_reads.integrity, 'revisar', wraps=runtime_reads.integrity.revisar) as check:
        before = runtime_reads.get_integrity()
        assert runtime_reads.get_integrity() == before
        assert check.call_count == 1
        with db.connect() as conn:
            conn.execute("INSERT INTO prices_daily(ticker,date,source,close) VALUES ('TEST',current_date,'synthetic',10)")
            key = runtime_reads._revision()
            assert key[1][1] is not None
        after = runtime_reads.get_integrity()
        assert after != before
        assert after[0].estado == runtime_reads.integrity.MAL
        assert check.call_count == 2
        after.clear()
        assert runtime_reads.get_integrity()  # caller cannot corrupt cached results


def test_revision_changes_on_wal_commit_and_database_replacement(warehouse, tmp_path):
    original = runtime_reads._revision()
    with db.connect() as conn:
        conn.execute("INSERT INTO instruments(ticker) VALUES ('ONE')")
        first = runtime_reads._revision()
        conn.execute("INSERT INTO instruments(ticker) VALUES ('TWO')")
        assert runtime_reads._revision() != first
    assert runtime_reads._revision() != original
    replacement = tmp_path / 'replacement.duckdb'
    import duckdb
    duckdb.connect(str(replacement)).close()
    before = runtime_reads._revision()
    replacement.replace(warehouse)
    assert runtime_reads._revision() != before


def test_holding_cache_keys_include_horizon_database_and_day(warehouse, monkeypatch):
    today = pd.Timestamp('2026-10-23').date()
    monkeypatch.setattr(runtime_reads.holding_analysis, 'date', SimpleNamespace(today=lambda: today))
    with patch.object(runtime_reads.holding_analysis, 'analyse', return_value=[{'ticker': 'TEST'}]) as analyse:
        assert runtime_reads.get_holding_analysis(horizon=63) == [{'ticker': 'TEST'}]
        runtime_reads.get_holding_analysis(horizon=63)
        assert analyse.call_count == 1
        runtime_reads.get_holding_analysis(horizon=126)
        today = pd.Timestamp('2026-10-24').date()
        runtime_reads.get_holding_analysis(horizon=126)
        assert analyse.call_count == 3
        with db.connect() as conn:
            conn.execute("INSERT INTO instruments(ticker) VALUES ('NEW')")
        runtime_reads.get_holding_analysis(horizon=126)
        assert analyse.call_count == 4


def test_schema_retries_failure_and_caches_only_success(warehouse):
    st.cache_resource.clear()
    try:
        with patch.object(db, 'migrate', side_effect=[db.AlmacenOcupado('locked'), None]) as migrate:
            with pytest.raises(db.AlmacenOcupado):
                runtime_reads.prepare_schema()
            runtime_reads.prepare_schema()
            runtime_reads.prepare_schema()
            assert migrate.call_count == 2
    finally:
        st.cache_resource.clear()
