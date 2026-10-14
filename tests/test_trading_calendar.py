import pandas as pd

from stocks_tracker.core.trading_calendar import check_history, dates_for_horizon, sessions


def test_holiday_does_not_count_as_a_missing_price():
    days = sessions('XNYS', '2025-12-22', '2025-12-30')
    assert pd.Timestamp('2025-12-25') not in days
    assert not check_history(pd.DataFrame({'date': days}), 'XNYS')['reason']
    assert check_history(pd.DataFrame({'date': days.delete(2)}), 'XNYS')['reason']


def test_horizon_counts_exchange_sessions_not_downloaded_rows():
    entry, end = dates_for_horizon('XNYS', '2025-12-24', 21)
    assert entry == pd.Timestamp('2025-12-26')
    assert len(sessions('XNYS', entry, end)) == 22


def test_market_calendar_distinguishes_foreign_holidays():
    assert pd.Timestamp('2025-07-04') not in sessions('XNYS', '2025-07-01', '2025-07-08')
    assert pd.Timestamp('2025-07-04') in sessions('XETR', '2025-07-01', '2025-07-08')


def test_new_year_holiday_at_calendar_boundary_is_supported():
    assert sessions('XNYS', '2025-01-01', '2025-01-03').tolist() == [pd.Timestamp('2025-01-02'), pd.Timestamp('2025-01-03')]
    entry, _ = dates_for_horizon('XNYS', '2024-12-31', 21)
    assert entry == pd.Timestamp('2025-01-02')
