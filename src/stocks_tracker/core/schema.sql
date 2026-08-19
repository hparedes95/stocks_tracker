-- Esquema del almacen analitico (DuckDB).
-- Un solo escritor (el ETL) y muchos lectores (la UI, en read_only).

-- ============ MAESTROS ============
CREATE TABLE IF NOT EXISTS instruments (
  ticker VARCHAR PRIMARY KEY, name VARCHAR, asset_class VARCHAR, exchange VARCHAR,
  currency VARCHAR, country VARCHAR, gics_sector VARCHAR, gics_industry VARCHAR,
  investment_type VARCHAR, market_cap DOUBLE, is_active BOOLEAN DEFAULT TRUE,
  first_seen DATE, last_seen DATE, tv_symbol VARCHAR, tv_exchange VARCHAR,
  tv_verified BOOLEAN DEFAULT FALSE, tv_source VARCHAR, updated_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS universe_membership (
  universe VARCHAR, ticker VARCHAR, valid_from DATE, valid_to DATE,
  PRIMARY KEY (universe, ticker, valid_from)
);
CREATE TABLE IF NOT EXISTS prices_daily (
  ticker VARCHAR, date DATE, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
  adj_close DOUBLE, volume BIGINT, source VARCHAR, ingested_at TIMESTAMP,
  PRIMARY KEY (ticker, date)
);
CREATE TABLE IF NOT EXISTS corporate_actions (
  ticker VARCHAR, date DATE, action_type VARCHAR, value DOUBLE,
  PRIMARY KEY (ticker, date, action_type)
);
CREATE TABLE IF NOT EXISTS fundamentals_snapshot (
  ticker VARCHAR, as_of DATE, trailing_pe DOUBLE, forward_pe DOUBLE, peg_ratio DOUBLE,
  price_to_book DOUBLE, price_to_sales DOUBLE, ev_to_ebitda DOUBLE, ev_to_revenue DOUBLE,
  fcf_yield DOUBLE, earnings_yield DOUBLE, gross_margin DOUBLE, operating_margin DOUBLE,
  profit_margin DOUBLE, roe DOUBLE, roa DOUBLE, revenue_growth_yoy DOUBLE,
  earnings_growth_yoy DOUBLE, debt_to_equity DOUBLE, net_debt_to_ebitda DOUBLE,
  current_ratio DOUBLE, dividend_yield DOUBLE, payout_ratio DOUBLE,
  shares_outstanding DOUBLE, beta DOUBLE, market_cap DOUBLE, currency VARCHAR,
  source VARCHAR, completeness DOUBLE, PRIMARY KEY (ticker, as_of)
);
CREATE TABLE IF NOT EXISTS earnings_events (
  ticker VARCHAR, report_date DATE, period VARCHAR, eps_estimate DOUBLE,
  eps_actual DOUBLE, surprise_pct DOUBLE, PRIMARY KEY (ticker, report_date)
);
CREATE TABLE IF NOT EXISTS macro_series (
  series_id VARCHAR, date DATE, value DOUBLE, source VARCHAR,
  PRIMARY KEY (series_id, date)
);
CREATE TABLE IF NOT EXISTS indicators_daily (
  ticker VARCHAR, date DATE, close DOUBLE, sma20 DOUBLE, sma50 DOUBLE, sma200 DOUBLE,
  ema12 DOUBLE, ema26 DOUBLE, macd DOUBLE, macd_signal DOUBLE, macd_hist DOUBLE,
  rsi14 DOUBLE, adx14 DOUBLE, plus_di DOUBLE, minus_di DOUBLE, atr14 DOUBLE, atr_pct DOUBLE,
  bb_upper DOUBLE, bb_lower DOUBLE, bb_width DOUBLE, bb_pctb DOUBLE,
  realized_vol_20 DOUBLE, realized_vol_60 DOUBLE, realized_vol_252 DOUBLE, obv DOUBLE,
  rel_volume_20 DOUBLE, roc_1m DOUBLE, roc_3m DOUBLE, roc_6m DOUBLE, roc_12m DOUBLE,
  mom_12_1 DOUBLE, ret_1d DOUBLE, ret_5d DOUBLE, dist_52w_high DOUBLE, dist_52w_low DOUBLE,
  drawdown DOUBLE, max_dd_1y DOUBLE, above_sma200 BOOLEAN, above_sma50 BOOLEAN,
  golden_cross BOOLEAN, death_cross BOOLEAN, days_above_sma200 INTEGER,
  rs_vs_bench_3m DOUBLE, support_near DOUBLE, resistance_near DOUBLE,
  PRIMARY KEY (ticker, date)
);
CREATE TABLE IF NOT EXISTS factor_scores (
  ticker VARCHAR, date DATE, weights_hash VARCHAR, peer_group VARCHAR,
  value_z DOUBLE, growth_z DOUBLE, quality_z DOUBLE, momentum_z DOUBLE,
  lowvol_z DOUBLE, dividend_z DOUBLE, technical_z DOUBLE, composite DOUBLE,
  composite_rank_sector INTEGER, composite_pctile DOUBLE, coverage DOUBLE,
  PRIMARY KEY (ticker, date, weights_hash)
);
CREATE TABLE IF NOT EXISTS factor_contributions (
  ticker VARCHAR, date DATE, weights_hash VARCHAR, factor VARCHAR, zscore DOUBLE,
  weight DOUBLE, contribution DOUBLE, PRIMARY KEY (ticker, date, weights_hash, factor)
);
CREATE TABLE IF NOT EXISTS signals (
  ticker VARCHAR, date DATE, signal_id VARCHAR, direction VARCHAR, strength DOUBLE,
  detail VARCHAR, PRIMARY KEY (ticker, date, signal_id)
);

-- Evidencia estadistica. Los p-values ajustados controlan FDR dentro de la
-- familia de hipotesis que valida run_backtest (senal x horizonte x ambito).
CREATE TABLE IF NOT EXISTS signal_evidence (
  signal_id VARCHAR, scope VARCHAR, horizon_days INTEGER, evidence VARCHAR,
  ic_ir DOUBLE, hit_rate DOUBLE, avg_excess_ret DOUBLE, n_obs INTEGER,
  oos_from DATE, oos_to DATE, costs_bps_assumed DOUBLE, updated_at TIMESTAMP,
  p_value DOUBLE, adjusted_p_value DOUBLE, multiple_testing_method VARCHAR,
  data_quality_status VARCHAR, fundamentals_point_in_time BOOLEAN,
  PRIMARY KEY (signal_id, scope, horizon_days)
);

CREATE TABLE IF NOT EXISTS breadth_daily (
  date DATE, scope VARCHAR, n_constituents INTEGER, pct_above_sma50 DOUBLE,
  pct_above_sma200 DOUBLE, advances INTEGER, declines INTEGER, ad_line DOUBLE,
  new_highs_52w INTEGER, new_lows_52w INTEGER, pct_rsi_overbought DOUBLE,
  pct_rsi_oversold DOUBLE, median_ret_1d DOUBLE, median_ret_1m DOUBLE,
  avg_pairwise_corr DOUBLE, PRIMARY KEY (date, scope)
);
CREATE TABLE IF NOT EXISTS sector_rotation (
  date DATE, etf VARCHAR, sector VARCHAR, ratio DOUBLE, momentum DOUBLE,
  cuadrante VARCHAR, estela_ratio VARCHAR, estela_momentum VARCHAR,
  PRIMARY KEY (date, etf)
);
CREATE TABLE IF NOT EXISTS regime_daily (
  date DATE PRIMARY KEY, vix DOUBLE, vix_percentile_1y DOUBLE, copper_gold_ratio DOUBLE,
  dxy_ret_3m DOUBLE, gold_ret_3m DOUBLE, oil_ret_3m DOUBLE, pct_above_sma200 DOUBLE,
  spy_vs_ief_3m DOUBLE, xly_vs_xlp_3m DOUBLE, risk_score DOUBLE, regime VARCHAR,
  components VARCHAR
);
CREATE TABLE IF NOT EXISTS watchlist (
  ticker VARCHAR, list_name VARCHAR DEFAULT 'default', added_at TIMESTAMP,
  added_price DOUBLE, note VARCHAR, target_price DOUBLE, PRIMARY KEY (ticker, list_name)
);
