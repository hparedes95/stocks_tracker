import duckdb
import numpy as np
import pandas as pd
import pytest

from stocks_tracker.core.db import schema_path
from stocks_tracker.core.portfolio_risk import analyse, returns_in_eur


def test_mixed_portfolio_measures_crypto_friday_to_monday_not_sunday_to_monday():
    with duckdb.connect(':memory:') as c:
        c.execute(schema_path().read_text('utf-8'))
        c.execute("INSERT INTO instruments(ticker,currency,asset_class,exchange,country) VALUES "
                  "('BTC-USD','USD','crypto',NULL,NULL), ('AAPL','USD','equity','NMS','US')")
        for day, price in [('2026-10-09',100), ('2026-10-10',110), ('2026-10-11',121), ('2026-10-12',133.1)]:
            c.execute("INSERT INTO prices_daily(ticker,date,close,adj_close,source) VALUES ('BTC-USD',?,?,?,'real-test')", [day,price,price])
            c.execute("INSERT INTO prices_daily(ticker,date,close,adj_close,source) VALUES ('EURUSD=X',?,1,1,'ecb')", [day])
        for day, price in [('2026-10-09',100), ('2026-10-12',101)]:
            c.execute("INSERT INTO prices_daily(ticker,date,close,adj_close,source) VALUES ('AAPL',?,?,?,'real-test')", [day,price,price])
        r = returns_in_eur(c, ['AAPL','BTC-USD'], as_of='2026-10-12')
        assert r.loc['2026-10-12','BTC-USD'] == pytest.approx(.331)
        assert r.loc['2026-10-12','AAPL'] == pytest.approx(.01)
        assert not r.index.dayofweek.isin([5,6]).any()
        c.execute("DELETE FROM prices_daily WHERE ticker='BTC-USD' AND date='2026-10-12'")
        assert pd.isna(returns_in_eur(c, ['AAPL','BTC-USD'], as_of='2026-10-12').loc['2026-10-12','BTC-USD'])


def test_daily_crypto_annualisation_uses_365_days():
    r = pd.DataFrame({'BTC-USD': np.linspace(-.01,.01,100)})
    r.attrs['annual_periods'] = 365
    result = analyse(r, {'BTC-USD':1000})
    assert result['annual_volatility'] == pytest.approx(r['BTC-USD'].std() * np.sqrt(365))
