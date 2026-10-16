"""Richer fictional data for isolated visual QA, never called by the launcher."""
import numpy as np
import pandas as pd

from stocks_tracker.core import db


def populate(conn):
    """Only modifies the explicitly supplied, disposable preview connection."""
    days = conn.execute("SELECT date FROM prices_daily WHERE ticker='AAPL' ORDER BY date").fetchdf().date
    x = np.arange(len(days))
    for ticker, sector in [('MSFT', 'Technology'), ('NVDA', 'Technology'),
                           ('CAT', 'Industrials'), ('HON', 'Industrials'), ('GE', 'Industrials')]:
        db.upsert_df(conn, 'instruments', pd.DataFrame([{
            'ticker':ticker, 'name':f'{ticker} · ejemplo ficticio', 'asset_class':'equity',
            'currency':'USD', 'exchange':'NMS', 'country':'US', 'gics_sector':sector,
            'market_cap':1e11, 'is_active':True, 'investment_type':'growth'}]), keys=['ticker'])
        values = 100 + x * .15 + 2 * np.sin(x / 3 + 1.5)
        db.upsert_df(conn, 'prices_daily', pd.DataFrame({
            'ticker':ticker, 'date':days, 'open':values, 'high':values * 1.01,
            'low':values * .99, 'close':values, 'adj_close':values,
            'volume':1_000_000, 'source':'yfinance'}), keys=['ticker', 'date'])
    conn.execute("UPDATE instruments SET name=coalesce(name, ticker || ' · ejemplo ficticio'), "
                 "country='US', gics_sector=coalesce(gics_sector, 'Technology'), market_cap=1e11")
    conn.execute('UPDATE prices_daily SET open=close, high=close*1.01, low=close*.99, volume=1000000')
    equities = conn.execute("SELECT ticker FROM instruments WHERE asset_class='equity'").fetchdf().ticker
    for ticker in equities:
        db.upsert_df(conn, 'universe_membership', pd.DataFrame([{
            'universe':'SP100', 'ticker':ticker, 'valid_from':days.iloc[0]}]),
            keys=['universe', 'ticker', 'valid_from'])
        db.upsert_df(conn, 'fundamentals_snapshot', pd.DataFrame([{
            'ticker':ticker, 'as_of':days.iloc[-1], 'source':'yfinance',
            'trailing_pe':22., 'price_to_book':4., 'fcf_yield':.04,
            'profit_margin':.2, 'operating_margin':.25, 'roe':.2,
            'revenue_growth_yoy':.12, 'earnings_growth_yoy':.1,
            'net_debt_to_ebitda':1., 'dividend_yield':.02, 'payout_ratio':.3,
            'completeness':.9}]), keys=['ticker', 'as_of'])
    # Populate a chart rather than stopping at the missing-regime notice.
    db.upsert_df(conn, 'regime_daily', pd.DataFrame({
        'date':days.iloc[-90:], 'vix':20 + np.sin(x[-90:] / 8),
        'vix_percentile_1y':.4, 'pct_above_sma200':65., 'risk_score':15.,
        'regime':'neutral', 'components':"{'breadth': 20, 'vix': 10}"}), keys=['date'])
    conn.execute("INSERT OR IGNORE INTO watchlist(ticker,list_name,added_price) VALUES ('MSFT','default',100)")
