-- Esquema del almacen analitico (DuckDB).
-- Un solo escritor (el ETL) y muchos lectores (la UI, en read_only).

-- ============ MAESTROS ============
CREATE TABLE IF NOT EXISTS instruments (
  ticker          VARCHAR PRIMARY KEY,
  name            VARCHAR,
  asset_class     VARCHAR,          -- equity|etf|index|crypto|commodity|fx|macro
  exchange        VARCHAR,
  currency        VARCHAR,
  country         VARCHAR,
  gics_sector     VARCHAR,
  gics_industry   VARCHAR,
  investment_type VARCHAR,          -- growth|value|dividend|etf|index|crypto|commodity
  market_cap      DOUBLE,
  is_active       BOOLEAN DEFAULT TRUE,
  first_seen      DATE,
  last_seen       DATE,
  tv_symbol       VARCHAR,          -- 'NASDAQ:AAPL'
  tv_exchange     VARCHAR,
  tv_verified     BOOLEAN DEFAULT FALSE,
  tv_source       VARCHAR,          -- rule|override|manual
  updated_at      TIMESTAMP
);

CREATE TABLE IF NOT EXISTS universe_membership (
  universe   VARCHAR,
  ticker     VARCHAR,
  valid_from DATE,
  valid_to   DATE,                  -- NULL = vigente
  PRIMARY KEY (universe, ticker, valid_from)
);

-- ============ PRECIOS ============
CREATE TABLE IF NOT EXISTS prices_daily (
  ticker      VARCHAR,
  date        DATE,
  open        DOUBLE,
  high        DOUBLE,
  low         DOUBLE,
  close       DOUBLE,
  adj_close   DOUBLE,               -- canonico para retornos e indicadores
  volume      BIGINT,
  source      VARCHAR,
  ingested_at TIMESTAMP,
  PRIMARY KEY (ticker, date)
);

CREATE TABLE IF NOT EXISTS corporate_actions (
  ticker      VARCHAR,
  date        DATE,
  action_type VARCHAR,              -- dividend|split
  value       DOUBLE,
  PRIMARY KEY (ticker, date, action_type)
);

-- ============ FUNDAMENTALES ============
-- Snapshot con fecha, NO serie point-in-time. Ver docs/00 seccion 6 punto 3:
-- esto invalida el backtest de factores fundamentales hasta acumular historico.
CREATE TABLE IF NOT EXISTS fundamentals_snapshot (
  ticker              VARCHAR,
  as_of               DATE,
  trailing_pe         DOUBLE,
  forward_pe          DOUBLE,
  peg_ratio           DOUBLE,
  price_to_book       DOUBLE,
  price_to_sales      DOUBLE,
  ev_to_ebitda        DOUBLE,
  ev_to_revenue       DOUBLE,
  fcf_yield           DOUBLE,
  earnings_yield      DOUBLE,
  gross_margin        DOUBLE,
  operating_margin    DOUBLE,
  profit_margin       DOUBLE,
  roe                 DOUBLE,
  roa                 DOUBLE,
  revenue_growth_yoy  DOUBLE,
  earnings_growth_yoy DOUBLE,
  debt_to_equity      DOUBLE,
  net_debt_to_ebitda  DOUBLE,
  current_ratio       DOUBLE,
  dividend_yield      DOUBLE,
  payout_ratio        DOUBLE,
  shares_outstanding  DOUBLE,
  beta                DOUBLE,
  market_cap          DOUBLE,
  currency            VARCHAR,
  source              VARCHAR,
  completeness        DOUBLE,       -- fraccion de campos no nulos
  PRIMARY KEY (ticker, as_of)
);

CREATE TABLE IF NOT EXISTS earnings_events (
  ticker       VARCHAR,
  report_date  DATE,
  period       VARCHAR,
  eps_estimate DOUBLE,
  eps_actual   DOUBLE,
  surprise_pct DOUBLE,
  PRIMARY KEY (ticker, report_date)
);

-- ============ MACRO ============
CREATE TABLE IF NOT EXISTS macro_series (
  series_id VARCHAR,
  date      DATE,
  value     DOUBLE,
  source    VARCHAR,
  PRIMARY KEY (series_id, date)
);

-- ============ CALCULADOS ============
CREATE TABLE IF NOT EXISTS indicators_daily (
  ticker           VARCHAR,
  date             DATE,
  close            DOUBLE,
  sma20            DOUBLE,
  sma50            DOUBLE,
  sma200           DOUBLE,
  ema12            DOUBLE,
  ema26            DOUBLE,
  macd             DOUBLE,
  macd_signal      DOUBLE,
  macd_hist        DOUBLE,
  rsi14            DOUBLE,
  adx14            DOUBLE,
  plus_di          DOUBLE,
  minus_di         DOUBLE,
  atr14            DOUBLE,
  atr_pct          DOUBLE,
  bb_upper         DOUBLE,
  bb_lower         DOUBLE,
  bb_width         DOUBLE,
  bb_pctb          DOUBLE,
  realized_vol_20  DOUBLE,
  realized_vol_60  DOUBLE,
  realized_vol_252 DOUBLE,
  obv              DOUBLE,
  rel_volume_20    DOUBLE,
  roc_1m           DOUBLE,
  roc_3m           DOUBLE,
  roc_6m           DOUBLE,
  roc_12m          DOUBLE,
  mom_12_1         DOUBLE,
  ret_1d           DOUBLE,
  ret_5d           DOUBLE,
  dist_52w_high    DOUBLE,
  dist_52w_low     DOUBLE,
  drawdown         DOUBLE,
  max_dd_1y        DOUBLE,
  above_sma200     BOOLEAN,
  above_sma50      BOOLEAN,
  golden_cross     BOOLEAN,
  death_cross      BOOLEAN,
  days_above_sma200 INTEGER,
  rs_vs_bench_3m   DOUBLE,
  support_near     DOUBLE,        -- soporte inmediatamente por debajo del precio
  resistance_near  DOUBLE,        -- resistencia inmediatamente por encima
  PRIMARY KEY (ticker, date)
);

CREATE TABLE IF NOT EXISTS factor_scores (
  ticker                VARCHAR,
  date                  DATE,
  weights_hash          VARCHAR,
  peer_group            VARCHAR,
  value_z               DOUBLE,
  growth_z              DOUBLE,
  quality_z             DOUBLE,
  momentum_z            DOUBLE,
  lowvol_z              DOUBLE,
  dividend_z            DOUBLE,
  technical_z           DOUBLE,
  composite             DOUBLE,
  composite_rank_sector INTEGER,
  composite_pctile      DOUBLE,
  coverage              DOUBLE,
  PRIMARY KEY (ticker, date, weights_hash)
);

CREATE TABLE IF NOT EXISTS factor_contributions (
  ticker       VARCHAR,
  date         DATE,
  weights_hash VARCHAR,
  factor       VARCHAR,
  zscore       DOUBLE,
  weight       DOUBLE,
  contribution DOUBLE,
  PRIMARY KEY (ticker, date, weights_hash, factor)
);

CREATE TABLE IF NOT EXISTS signals (
  ticker    VARCHAR,
  date      DATE,
  signal_id VARCHAR,
  direction VARCHAR,                -- bullish|bearish|neutral
  strength  DOUBLE,                 -- 0..1
  detail    VARCHAR,
  PRIMARY KEY (ticker, date, signal_id)
);

-- Evidencia historica por senal Y ambito. Una senal validada en acciones NO
-- esta validada en cripto: los regimenes y la microestructura no se parecen.
CREATE TABLE IF NOT EXISTS signal_evidence (
  signal_id         VARCHAR,
  scope             VARCHAR,        -- equity_us|equity_eu|crypto
  horizon_days      INTEGER,
  evidence          VARCHAR,        -- validada|debil|no_validada|sin_datos
  ic_ir             DOUBLE,
  hit_rate          DOUBLE,
  avg_excess_ret    DOUBLE,
  n_obs             INTEGER,        -- eventos
  n_dates           INTEGER,        -- fechas distintas: el n que sostiene el t
  t_stat            DOUBLE,         -- HAC agrupado por fecha
  p_value           DOUBLE,
  q_value           DOUBLE,         -- Benjamini-Hochberg sobre toda la familia
  n_tests           INTEGER,        -- pruebas hechas en la tanda
  ci_low            DOUBLE,         -- intervalo del exceso medio al 95 %
  ci_high           DOUBLE,
  oos_from          DATE,
  oos_to            DATE,
  costs_bps_assumed DOUBLE,
  updated_at        TIMESTAMP,
  PRIMARY KEY (signal_id, scope, horizon_days)
);

CREATE TABLE IF NOT EXISTS breadth_daily (
  date                DATE,
  scope               VARCHAR,      -- 'SP100'|'IBEX35'|'GICS:Energy'
  n_constituents      INTEGER,
  pct_above_sma50     DOUBLE,
  pct_above_sma200    DOUBLE,
  advances            INTEGER,
  declines            INTEGER,
  ad_line             DOUBLE,
  new_highs_52w       INTEGER,
  new_lows_52w        INTEGER,
  pct_rsi_overbought  DOUBLE,
  pct_rsi_oversold    DOUBLE,
  median_ret_1d       DOUBLE,
  median_ret_1m       DOUBLE,
  avg_pairwise_corr   DOUBLE,     -- correlacion media entre pares (60 sesiones)
  PRIMARY KEY (date, scope)
);

-- Posicion de cada sector en el grafico de rotacion. Describe donde esta cada
-- sector AHORA; los cuadrantes ordenan lo que ya ha pasado, no lo que vendra.
CREATE TABLE IF NOT EXISTS sector_rotation (
  date             DATE,
  etf              VARCHAR,
  sector           VARCHAR,
  ratio            DOUBLE,        -- fuerza relativa normalizada (100 = como el indice)
  momentum         DOUBLE,        -- momentum de esa fuerza relativa
  cuadrante        VARCHAR,       -- Lidera|Se debilita|Rezagado|Mejora
  estela_ratio     VARCHAR,       -- JSON con el recorrido de las ultimas semanas
  estela_momentum  VARCHAR,
  PRIMARY KEY (date, etf)
);

CREATE TABLE IF NOT EXISTS regime_daily (
  date              DATE PRIMARY KEY,
  vix               DOUBLE,
  vix_percentile_1y DOUBLE,
  copper_gold_ratio DOUBLE,
  dxy_ret_3m        DOUBLE,
  gold_ret_3m       DOUBLE,
  oil_ret_3m        DOUBLE,
  pct_above_sma200  DOUBLE,
  spy_vs_ief_3m     DOUBLE,
  xly_vs_xlp_3m     DOUBLE,
  risk_score        DOUBLE,         -- -100 (risk-off) .. +100 (risk-on)
  regime            VARCHAR,
  components        VARCHAR         -- JSON con el desglose, para explicabilidad
);

-- ============ USUARIO ============
CREATE TABLE IF NOT EXISTS watchlist (
  ticker       VARCHAR,
  list_name    VARCHAR DEFAULT 'default',
  added_at     TIMESTAMP,
  added_price  DOUBLE,
  note         VARCHAR,
  target_price DOUBLE,
  PRIMARY KEY (ticker, list_name)
);

-- Posiciones reales del usuario, introducidas a mano. No se conecta con ningun
-- broker: sirve para ver la cartera junto al analisis y para que las alertas de
-- ambito `portfolio` sepan que se tiene en cartera.
CREATE TABLE IF NOT EXISTS positions (
  id         VARCHAR PRIMARY KEY,
  ticker     VARCHAR,
  qty        DOUBLE,
  avg_cost   DOUBLE,
  currency   VARCHAR,
  opened_at  DATE,
  closed_at  DATE,
  note       VARCHAR,
  updated_at TIMESTAMP
);

-- Alertas disparadas. `triggered_at` sirve tambien para el periodo de espera:
-- sin el, la misma alerta se repetiria cada dia mientras la condicion siga
-- siendo cierta, y en una semana dejarian de leerse.
CREATE TABLE IF NOT EXISTS alerts (
  id           VARCHAR PRIMARY KEY,
  rule_id      VARCHAR,
  ticker       VARCHAR,           -- NULL en las reglas de ambito 'market'
  triggered_at TIMESTAMP,
  message      VARCHAR,
  payload      VARCHAR,           -- JSON con los valores que dispararon la regla
  delivered    BOOLEAN DEFAULT FALSE,
  channel      VARCHAR,
  acknowledged BOOLEAN DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_alerts_rule ON alerts(rule_id, triggered_at);

-- ============ OPERACION ============
CREATE TABLE IF NOT EXISTS ingest_log (
  run_id       VARCHAR,
  started_at   TIMESTAMP,
  finished_at  TIMESTAMP,
  task         VARCHAR,
  target       VARCHAR,
  status       VARCHAR,             -- OK|PARTIAL|FAILED|RATE_LIMITED
  rows_written INTEGER,
  requests_used INTEGER,
  error        VARCHAR
);

-- Que problemas tienen los datos, comprobado ANTES de calcular con ellos.
-- Se guarda historico y no solo el estado de hoy: la pregunta que hay que
-- poder contestar es "¿desde cuando pasa esto?", y con una foto del ultimo
-- momento no se contesta.
CREATE TABLE IF NOT EXISTS data_quality (
  date       DATE,                  -- a que fecha se refiere el problema
  ticker     VARCHAR,               -- NULL si afecta al conjunto
  check_name VARCHAR,
  passed     BOOLEAN,
  detail     VARCHAR,
  severity   VARCHAR,               -- info|aviso|bloquea
  checked_at TIMESTAMP,
  run_id     VARCHAR
);

-- ============ BOT DE TRADING (fase 6) ============
-- Herramienta personal de experimentacion. El usuario opera su propio dinero
-- bajo su exclusiva responsabilidad. Ninguna metrica pasada garantiza
-- resultados futuros. Esto no es asesoramiento financiero ni fiscal.

CREATE TABLE IF NOT EXISTS strategies (
  strategy_id    VARCHAR PRIMARY KEY,   -- 'momentum_multifactor_v1'
  name           VARCHAR,
  version        VARCHAR,
  enabled        BOOLEAN DEFAULT FALSE,
  mode           VARCHAR,               -- simulated|paper|live
  params         VARCHAR,               -- JSON: copia congelada del YAML al activar
  params_hash    VARCHAR,               -- detecta cambios de parametros a posteriori
  activated_at   TIMESTAMP,
  deactivated_at TIMESTAMP,
  note           VARCHAR
);

CREATE TABLE IF NOT EXISTS bot_runs (
  run_id      VARCHAR PRIMARY KEY,      -- ULID
  strategy_id VARCHAR,
  mode        VARCHAR,
  phase       VARCHAR,                  -- propose|execute|monitor|reconcile|eod
  started_at  TIMESTAMP,
  finished_at TIMESTAMP,
  status      VARCHAR,                  -- OK|PARTIAL|FAILED|HALTED
  market_open BOOLEAN,
  equity_start DOUBLE,
  equity_end   DOUBLE,
  n_intents   INTEGER,
  n_approved  INTEGER,
  n_submitted INTEGER,
  n_filled    INTEGER,
  error       VARCHAR
);

CREATE TABLE IF NOT EXISTS intents (
  intent_id          VARCHAR PRIMARY KEY,   -- ULID
  run_id             VARCHAR,
  strategy_id        VARCHAR,
  created_at         TIMESTAMP,
  ticker             VARCHAR,
  tv_symbol          VARCHAR,
  side               VARCHAR,               -- buy|sell
  intent_type        VARCHAR,               -- open|add|trim|close|stop_exit|rebalance
  qty_requested      DOUBLE,
  notional_requested DOUBLE,
  qty_approved       DOUBLE,                -- tras RESIZE del riesgo
  notional_approved  DOUBLE,
  ref_price          DOUBLE,
  stop_price         DOUBLE,
  stop_atr_mult      DOUBLE,
  risk_amount        DOUBLE,                -- dinero en riesgo hasta el stop
  rationale          VARCHAR,               -- JSON
  score_pctile       DOUBLE,
  regime             VARCHAR,
  risk_verdict       VARCHAR,               -- APPROVE|RESIZE|VETO
  risk_notes         VARCHAR,               -- JSON: reglas evaluadas y su holgura
  status             VARCHAR,               -- PENDING|APPROVED|...|FILLED|FAILED
  expires_at         TIMESTAMP,
  decided_by         VARCHAR,
  decided_at         TIMESTAMP,
  decision_note      VARCHAR
);
CREATE INDEX IF NOT EXISTS idx_intents_status ON intents(status, expires_at);

CREATE TABLE IF NOT EXISTS orders (
  client_order_id  VARCHAR PRIMARY KEY,  -- DETERMINISTA: 'st-{intent_id}' -> idempotencia
  broker_order_id  VARCHAR,
  intent_id        VARCHAR,
  run_id           VARCHAR,
  mode             VARCHAR,
  broker           VARCHAR,
  ticker           VARCHAR,
  side             VARCHAR,
  order_type       VARCHAR,
  tif              VARCHAR,
  qty              DOUBLE,
  notional         DOUBLE,
  limit_price      DOUBLE,
  stop_price       DOUBLE,
  status           VARCHAR,
  filled_qty       DOUBLE DEFAULT 0,
  filled_avg_price DOUBLE,
  submitted_at     TIMESTAMP,
  updated_at       TIMESTAMP,
  filled_at        TIMESTAMP,
  reject_reason    VARCHAR,
  raw              VARCHAR                -- JSON
);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(mode, status);

CREATE TABLE IF NOT EXISTS fills (
  fill_id         VARCHAR PRIMARY KEY,
  client_order_id VARCHAR,
  broker_order_id VARCHAR,
  ticker          VARCHAR,
  side            VARCHAR,
  qty             DOUBLE,
  price           DOUBLE,
  filled_at       TIMESTAMP,
  commission      DOUBLE DEFAULT 0,
  slippage_bps    DOUBLE,                -- frente al ref_price del intent
  mode            VARCHAR
);

CREATE TABLE IF NOT EXISTS portfolio_snapshots (
  snapshot_at        TIMESTAMP,
  mode               VARCHAR,
  strategy_id        VARCHAR,
  cash               DOUBLE,
  equity             DOUBLE,
  long_market_value  DOUBLE,
  buying_power       DOUBLE,
  n_positions        INTEGER,
  gross_exposure_pct DOUBLE,
  daytrade_count     INTEGER,
  peak_equity        DOUBLE,
  drawdown_pct       DOUBLE,
  pnl_day            DOUBLE,
  pnl_total          DOUBLE,
  benchmark_equity   DOUBLE,
  positions          VARCHAR,            -- JSON: foto completa para auditoria
  PRIMARY KEY (snapshot_at, mode)
);

-- Estado gestionado por NOSOTROS, no por el broker: Alpaca rechaza bracket y
-- OCO en ordenes fraccionadas, asi que los stops son sinteticos y viven aqui.
CREATE TABLE IF NOT EXISTS bot_positions (
  ticker                    VARCHAR,
  mode                      VARCHAR,
  strategy_id               VARCHAR,
  qty                       DOUBLE,
  avg_entry_price           DOUBLE,
  opened_at                 TIMESTAMP,
  stop_price                DOUBLE,
  stop_type                 VARCHAR,     -- atr_fixed|atr_trailing
  highest_close_since_entry DOUBLE,
  target_weight             DOUBLE,
  entry_intent_id           VARCHAR,
  max_hold_until            DATE,
  last_reviewed_at          TIMESTAMP,
  PRIMARY KEY (ticker, mode)
);

-- Invariante de auditoria: cualquier ticker candidato de cualquier ciclo deja
-- AL MENOS una fila aqui, incluidos los descartados. Sin eso, la pregunta
-- "por que no compro X el dia Y" no tiene respuesta.
CREATE TABLE IF NOT EXISTS decision_log (
  decision_id VARCHAR PRIMARY KEY,
  run_id      VARCHAR,
  -- 'at' a secas es palabra reservada en DuckDB: obligaria a entrecomillarla
  -- en cada consulta, incluidas las que se escriben a mano para investigar.
  logged_at   TIMESTAMP,
  mode        VARCHAR,
  strategy_id VARCHAR,
  ticker      VARCHAR,
  decision    VARCHAR,
  reason_code VARCHAR,                   -- enum estable, apto para filtrar en SQL
  reason_text VARCHAR,                   -- frase legible
  context     VARCHAR                    -- JSON
);
CREATE INDEX IF NOT EXISTS idx_decision_ticker_date ON decision_log(ticker, logged_at);

CREATE TABLE IF NOT EXISTS risk_violations (
  id           VARCHAR PRIMARY KEY,
  logged_at    TIMESTAMP,
  run_id       VARCHAR,
  mode         VARCHAR,
  rule_id      VARCHAR,
  severity     VARCHAR,                  -- info|warn|block|kill
  ticker       VARCHAR,
  observed     DOUBLE,
  limit_value  DOUBLE,
  headroom     DOUBLE,
  action_taken VARCHAR,                  -- resize|veto|halt_new|flatten
  detail       VARCHAR                   -- JSON
);

CREATE TABLE IF NOT EXISTS bot_state (
  mode             VARCHAR PRIMARY KEY,
  state            VARCHAR,              -- RUNNING|HALT_NEW|FLATTEN_PENDING|HALTED
  autonomy         VARCHAR,              -- semi|auto
  halted_at        TIMESTAMP,
  halt_rule        VARCHAR,
  halt_detail      VARCHAR,
  rearmed_at       TIMESTAMP,
  rearmed_by       VARCHAR,
  rearm_note       VARCHAR,
  peak_equity      DOUBLE,
  day_start_equity DOUBLE,
  day_start_date   DATE,
  updated_at       TIMESTAMP
);

-- ============ SESION VIGENTE ============
-- La fecha sobre la que se muestra y se calcula TODO. Es una vista y no una
-- consulta repetida en cada sitio porque el dia que dejaron de coincidir el
-- dashboard se vacio: unas consultas miraban el ultimo dia de indicadores
-- (donde habia un solo valor, el que acabo de descargarse antes que los demas)
-- y otras el ultimo dia de scores, y los JOIN entre ambas no devolvian nada.
--
-- "Vigente" no es "la mas reciente": es la mas reciente que reune al menos al
-- 60 % de los valores del dia mas poblado. Asi ni el bitcoin cotizando un
-- domingo ni un ticker que va por delante arrastran a los seiscientos.
CREATE OR REPLACE VIEW current_session AS
WITH counts AS (
  SELECT i.date AS date, COUNT(*) AS n
  FROM indicators_daily i
  JOIN instruments inst USING (ticker)
  WHERE inst.asset_class IN ('equity', 'etf')
  GROUP BY i.date
  ORDER BY i.date DESC
  LIMIT 30
)
SELECT date, n
FROM counts
WHERE n >= (SELECT MAX(n) FROM counts) * 0.6
ORDER BY date DESC
LIMIT 1;

-- Informes de la puerta 1. Se guardan para que el veredicto no dependa de que
-- alguien mire una consola: la validacion se ejecuta sola y el resultado se lee
-- en el dashboard.
CREATE TABLE IF NOT EXISTS gate_reports (
  report_id   VARCHAR PRIMARY KEY,
  logged_at   TIMESTAMP,
  strategy_id VARCHAR,
  preset      VARCHAR,
  passed      BOOLEAN,
  blockers    VARCHAR,               -- JSON
  checks      VARCHAR,               -- JSON
  sessions    INTEGER,
  trades      INTEGER,
  equity_start DOUBLE,
  equity_end   DOUBLE,
  data_from   DATE,
  data_to     DATE
);

-- ============ DIARIO DE DECISIONES ============
-- Por que compraste, ESCRITO ANTES de saber si salio bien.
--
-- Existe para corregir el sesgo retrospectivo, que no se corrige con buena
-- intencion: cuando algo sale bien, el recuerdo del motivo se reescribe solo
-- para que encaje, y se aprende una leccion que nunca ocurrio. La unica defensa
-- conocida es dejarlo por escrito antes y releerlo despues sin retocarlo.
--
-- La foto del momento (precio, percentil, RSI, regimen) se guarda AUTOMATICA y
-- no se teclea: es lo que de verdad se sabia ese dia, no lo que se recuerda
-- haber sabido.
--
-- Se registran tambien las decisiones de NO comprar y de esperar. Son la mitad
-- de las decisiones que se toman y no dejan rastro en ningun sitio: sin ellas
-- el diario solo guarda los aciertos posibles.
CREATE TABLE IF NOT EXISTS decision_journal (
  id                VARCHAR PRIMARY KEY,
  created_at        TIMESTAMP,
  ticker            VARCHAR,
  accion            VARCHAR,        -- comprar | vender | no_comprar | esperar
  tesis             VARCHAR,        -- por que, con tus palabras
  que_me_haria_salir VARCHAR,       -- que tendria que pasar para cambiar de idea
  horizonte_dias    INTEGER,
  conviccion        INTEGER,        -- 1 a 5
  -- Foto del momento, automatica
  precio            DOUBLE,
  precio_mercado    DOUBLE,         -- el proxy de mercado ese dia
  composite_pctile  DOUBLE,
  rsi14             DOUBLE,
  drawdown          DOUBLE,
  above_sma200      BOOLEAN,
  -- Revision posterior
  revisado_at       TIMESTAMP,
  veredicto         VARCHAR,        -- acierto | suerte | mala_suerte | error
  nota_revision     VARCHAR
);

-- ============ MIGRACIONES DE COLUMNAS ============
-- `CREATE TABLE IF NOT EXISTS` no anade columnas a una tabla que ya existe, asi
-- que un almacen creado con una version anterior se quedaria sin ellas. Y no
-- daria error: `upsert_df` recorta el payload a las columnas de la tabla, de
-- modo que los valores nuevos se perderian en silencio, que es peor.
--
-- `ADD COLUMN IF NOT EXISTS` es idempotente, asi que esto puede ejecutarse en
-- cada arranque sin comprobar nada.
ALTER TABLE signal_evidence ADD COLUMN IF NOT EXISTS n_dates INTEGER;
ALTER TABLE signal_evidence ADD COLUMN IF NOT EXISTS t_stat DOUBLE;
ALTER TABLE signal_evidence ADD COLUMN IF NOT EXISTS p_value DOUBLE;
ALTER TABLE signal_evidence ADD COLUMN IF NOT EXISTS q_value DOUBLE;
ALTER TABLE signal_evidence ADD COLUMN IF NOT EXISTS n_tests INTEGER;
ALTER TABLE signal_evidence ADD COLUMN IF NOT EXISTS ci_low DOUBLE;
ALTER TABLE signal_evidence ADD COLUMN IF NOT EXISTS ci_high DOUBLE;
ALTER TABLE data_quality ADD COLUMN IF NOT EXISTS severity VARCHAR;
ALTER TABLE data_quality ADD COLUMN IF NOT EXISTS checked_at TIMESTAMP;
ALTER TABLE data_quality ADD COLUMN IF NOT EXISTS run_id VARCHAR;
