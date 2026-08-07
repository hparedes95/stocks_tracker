# Plan de implementación: `stocks_tracker`

## Dashboard de monitorización de mercado y detección de oportunidades

> **Aviso que debe aparecer en el código, el README y el pie de cada página del
> dashboard:** esta herramienta es un **sistema de apoyo a la decisión**, no
> asesoramiento financiero. Ninguna señal predice el mercado. Todos los resultados
> son **probabilísticos** y basados en datos con retardo y de calidad variable.

> **Nota**: este documento es el plan base. Las secciones §5 (diseño del
> dashboard), §8 (fases) y partes de §2, §3, §9 y §10 quedan modificadas por
> [`01-adenda-tradingview-asistente.md`](01-adenda-tradingview-asistente.md) y
> ampliadas por [`02-adenda-bot-trading.md`](02-adenda-bot-trading.md). Cada
> adenda abre con un índice de sustituciones.

---

## 0. Resumen ejecutivo de decisiones técnicas

| Decisión | Elección | Motivo |
|---|---|---|
| Lenguaje / UI | Python 3.11+, Streamlit multipágina con `st.navigation` | `st.navigation` es hoy la forma preferida y más configurable de hacer multipágina |
| Almacén analítico | **DuckDB** (`data/warehouse.duckdb`) + Parquet para snapshots crudos | Columnar, SQL completo, cero servidor, escanea 5-10M filas de OHLCV en ms. Un solo fichero, fácil de borrar/reconstruir |
| Concurrencia | ETL = único escritor (proceso batch). Streamlit abre DuckDB en `read_only=True` | DuckDB permite 1 escritor; el patrón "ETL escribe / UI lee" evita bloqueos |
| Indicadores | Implementación propia vectorizada en pandas/numpy (`core/indicators.py`) + `ta` (bukosabino) como referencia cruzada en tests | `pandas-ta` está en riesgo de discontinuación; no conviene que el corazón del proyecto dependa de una librería en duda. Los ~15 indicadores necesarios son 200 líneas de numpy |
| Aislamiento de proveedores | Capa `providers/` con `Protocol`s (`PriceProvider`, `FundamentalsProvider`, `MacroProvider`, `NewsProvider`) + registro y cadena de *fallback* | yfinance es una API **no oficial** que se rompe con los rediseños de Yahoo; hay que poder sustituirla sin tocar el resto |
| Gestor de entorno | `uv` (o venv + pip), `pyproject.toml` | Reproducibilidad y velocidad |

---

## 1. Arquitectura por capas

```
┌────────────────────────────────────────────────────────────────────────┐
│ L0  CONFIGURACIÓN         config/*.yaml  →  core/config.py (pydantic)   │
└────────────────────────────────────────────────────────────────────────┘
            │
┌───────────▼────────────────────────────────────────────────────────────┐
│ L1  PROVEEDORES (adaptadores)   providers/                              │
│     yfinance | stooq | FRED | Finnhub/Marketaux | Wikipedia (universo)  │
│     Contrato: Protocols tipados. Devuelven DataFrames con esquema fijo. │
│     Aquí y SOLO aquí viven: reintentos, backoff, rate-limit, HTTP cache │
└───────────┬────────────────────────────────────────────────────────────┘
            │  (DataFrames normalizados)
┌───────────▼────────────────────────────────────────────────────────────┐
│ L2  INGESTA / ETL     ingest/                                          │
│     Descarga incremental + validación + UPSERT idempotente en DuckDB   │
│     Registro de ejecución en tabla ingest_log (auditoría y reintentos) │
└───────────┬────────────────────────────────────────────────────────────┘
            │
┌───────────▼────────────────────────────────────────────────────────────┐
│ L3  ALMACÉN       data/warehouse.duckdb  +  data/raw/*.parquet          │
│     Tablas: prices_daily, fundamentals_snapshot, universe, macro_series │
│     Materializadas: indicators_daily, factor_scores, signals, alerts    │
└───────────┬────────────────────────────────────────────────────────────┘
            │
┌───────────▼────────────────────────────────────────────────────────────┐
│ L4  CÁLCULO       core/indicators.py, core/breadth.py, core/macro.py    │
│     Puro: DataFrame in → DataFrame out. Sin I/O. 100% testeable.        │
└───────────┬────────────────────────────────────────────────────────────┘
            │
┌───────────▼────────────────────────────────────────────────────────────┐
│ L5  FACTORES + SCORING   core/factors.py, core/scoring.py               │
│     Winsorize → z-score intra-sector → pesos YAML → score compuesto     │
│     Genera contribuciones por factor (explicabilidad)                   │
└───────────┬───────────────┬────────────────────────────────────────────┘
            │               │
┌───────────▼───────────────┼────────────────────────────────────────────┐
│ L6a  UI Streamlit  app/   │ L6b  Alertas  alerts/  │ L6c Backtest       │
│      8 páginas            │  reglas → notificación │  backtest/         │
└───────────────────────────┴────────────────────────┴────────────────────┘
```

### Por qué esta separación

1. **L4/L5 son funciones puras**: se testean sin red, sin BD, con series
   sintéticas. Es donde vive el 90 % del riesgo de bugs silenciosos (un `shift()`
   mal puesto = look-ahead bias).
2. **L1 aislada**: si Yahoo se rompe (ha pasado tras el rediseño de febrero 2025),
   se escribe un `StooqPriceProvider` nuevo y nada más cambia.
3. **L3 materializada**: la UI **nunca** calcula indicadores al vuelo para 700
   valores. Lee `factor_scores` ya calculado. Streamlit debe responder en <1 s.

### Estrategia anti rate-limit de yfinance (crítica)

Yahoo bloquea IPs con uso intensivo; `YFRateLimitError` (HTTP 429) es habitual y
usar `curl_cffi` con impersonación **no lo elimina**. Diseño defensivo:

1. **Descarga por lotes**: `yf.download(tickers=chunk, period=..., group_by="ticker", auto_adjust=False, threads=False)`
   con `chunk` de **40-50 tickers**. Un universo de ~750 valores = ~16 peticiones,
   no 750.
2. **`threads=False`** en el ETL nocturno. La concurrencia es exactamente lo que
   dispara el bloqueo. El tiempo total (5-15 min) es irrelevante en batch.
3. **Actualización incremental**: `SELECT ticker, MAX(date) FROM prices_daily GROUP BY ticker`
   → se pide solo `start = last_date + 1d`. Backfill inicial de 15 años una sola vez.
4. **Pausa con jitter** entre lotes: `time.sleep(random.uniform(1.5, 3.5))`.
5. **Reintentos con `tenacity`**: `retry(wait=wait_exponential(multiplier=5, max=300), stop=stop_after_attempt(4), retry=retry_if_exception_type(YFRateLimitError))`.
   Tras agotar, se marca el lote como `FAILED` en `ingest_log` y el ETL
   **continúa** (degradación elegante, nunca aborta).
6. **Presupuesto de peticiones por ejecución** (`max_requests_per_run` en YAML,
   por defecto 400). Si se supera, se para y se reanuda en la siguiente ejecución
   por los tickers con `last_date` más antiguo (cola por prioridad de antigüedad).
7. **Caché HTTP persistente** para `Ticker.info` / fundamentales: `requests_cache`
   con `expire_after=timedelta(days=7)` en `data/http_cache.sqlite`. Los
   fundamentales cambian trimestralmente; pedirlos a diario es tirar la cuota.
8. **Escalonado de fundamentales**: cada noche solo se refresca 1/7 del universo
   (`WHERE ticker_hash % 7 = day_of_week`). Todo el universo queda cubierto
   semanalmente.
9. **Caché de Streamlit**: `@st.cache_data(ttl=900)` sobre las funciones de lectura
   de DuckDB, `@st.cache_resource` para la conexión. La UI **nunca** llama a
   yfinance directamente salvo en el botón explícito "Actualizar ahora" de la
   ficha de valor.
10. **Sesión compartida** con `curl_cffi` (`impersonate="chrome"`) reutilizada en
    todo el proceso, y `User-Agent` estable.

---

## 2. Estructura de directorios

```
stocks_tracker/
├── pyproject.toml                 # deps, ruff, pytest, mypy
├── README.md                      # incl. DISCLAIMER destacado
├── Makefile                       # make setup / ingest / compute / run / test
├── .env.example                   # FRED_API_KEY, FINNHUB_KEY, TELEGRAM_*, SMTP_*
├── .gitignore                     # data/, .env, __pycache__
│
├── config/
│   ├── settings.yaml              # rutas, TTLs, límites de red, timezone
│   ├── universe.yaml              # definición de universos y tickers manuales
│   ├── factors.yaml               # catálogo de factores, pesos, direcciones
│   ├── macro.yaml                 # series FRED y tickers macro
│   ├── alerts.yaml                # reglas de alerta y canales
│   └── presets/                   # perfiles de pesos: value.yaml, momentum.yaml,
│                                  #   dividend.yaml, balanced.yaml
├── src/stocks_tracker/
│   ├── __init__.py
│   ├── core/
│   │   ├── config.py              # carga YAML → modelos pydantic (Settings, FactorSpec…)
│   │   ├── db.py                  # get_conn(read_only), migrate(), upsert_df()
│   │   ├── schema.sql             # DDL de todas las tablas (versionado)
│   │   ├── calendar.py            # días hábiles por mercado, alineado de series
│   │   ├── indicators.py          # SMA, EMA, MACD, RSI, ADX, ATR, BB, OBV, ROC…
│   │   ├── breadth.py             # amplitud de mercado y por sector
│   │   ├── relative.py            # fuerza relativa, rotación sectorial, correlaciones
│   │   ├── fundamental.py         # ratios derivados de fundamentals_snapshot
│   │   ├── macro.py               # curva de tipos, semáforo risk-on/risk-off
│   │   ├── sentiment.py           # agregación de noticias, Fear&Greed proxy
│   │   ├── factors.py             # cálculo de los 7 factores de estilo
│   │   ├── scoring.py             # winsorize, zscore_by_group, composite_score, explain
│   │   └── types.py               # Enums: Sector, AssetClass, SignalDirection…
│   ├── providers/
│   │   ├── base.py                # Protocols + excepciones + @rate_limited
│   │   ├── registry.py            # get_price_provider() con cadena de fallback
│   │   ├── yfinance_provider.py   # implementación principal
│   │   ├── stooq_provider.py      # fallback de precios (CSV, sin clave)
│   │   ├── fred_provider.py       # macro (fredapi)
│   │   ├── news_provider.py       # Finnhub / Marketaux (freemium)
│   │   └── universe_provider.py   # constituyentes: Wikipedia + YAML manual
│   ├── ingest/
│   │   ├── run_ingest.py          # CLI orquestador: --what prices|fundamentals|macro|news|all
│   │   ├── prices.py              # backfill + incremental por lotes
│   │   ├── fundamentals.py        # escalonado semanal
│   │   ├── macro.py
│   │   ├── news.py
│   │   ├── universe.py
│   │   └── validate.py            # controles de calidad (gaps, splits, outliers)
│   ├── compute/
│   │   └── run_compute.py         # CLI: indicadores → factores → scores → señales
│   ├── backtest/
│   │   ├── engine.py              # walk-forward, forward returns
│   │   ├── metrics.py             # hit rate, Sharpe, max DD, IC, deciles
│   │   └── run_backtest.py        # CLI
│   ├── alerts/
│   │   ├── rules.py               # DSL de reglas desde alerts.yaml
│   │   ├── evaluate.py            # evalúa reglas → tabla alerts
│   │   └── notify.py              # canales: file, email(SMTP), telegram
│   └── app/
│       ├── main.py                # entrypoint: st.navigation(...)
│       ├── pages/
│       │   ├── 1_vision_general.py
│       │   ├── 2_sectores.py
│       │   ├── 3_oportunidades.py
│       │   ├── 4_ficha_valor.py
│       │   ├── 5_cartera_watchlist.py
│       │   ├── 6_macro_riesgo.py
│       │   ├── 7_backtest.py
│       │   └── 8_alertas_config.py
│       ├── components/
│       │   ├── charts.py          # candlestick, heatmap, gauge, ranking bars
│       │   ├── tables.py          # st.dataframe con column_config y sparklines
│       │   ├── filters.py         # sidebar reutilizable (sector, universo, clase)
│       │   └── disclaimer.py      # render_disclaimer() en cada página
│       └── data_access.py         # TODAS las lecturas cacheadas de DuckDB
├── scripts/
│   ├── daily_update.sh            # ingest + compute + alerts (para cron)
│   └── bootstrap_backfill.py      # primera carga histórica, respetuosa con rate limits
├── tests/
│   ├── conftest.py                # fixtures: OHLCV sintético, DuckDB en memoria
│   ├── test_indicators.py
│   ├── test_scoring.py
│   ├── test_no_lookahead.py       # test crítico anti sesgo
│   ├── test_providers_contract.py # con FakeProvider, sin red
│   ├── test_backtest.py
│   └── test_ingest_idempotent.py
└── data/                          # .gitignored
    ├── warehouse.duckdb
    ├── http_cache.sqlite
    └── raw/                       # snapshots parquet por fecha (auditoría)
```

**Responsabilidades clave:**

- `core/db.py`: única puerta a DuckDB. `upsert_df(table, df, keys)` implementa
  `DELETE ... USING` + `INSERT` en transacción (idempotencia).
- `app/data_access.py`: única capa que hace SQL desde la UI. Todas las funciones
  con `@st.cache_data`. Ninguna página escribe SQL suelto.
- `providers/base.py`: define los contratos; si un proveedor no soporta algo,
  lanza `NotSupportedError` y el registro pasa al siguiente.

---

## 3. Modelo de datos (DuckDB, `core/schema.sql`)

```sql
-- ============ MAESTROS ============
CREATE TABLE IF NOT EXISTS instruments (
  ticker        VARCHAR PRIMARY KEY,   -- 'AAPL', 'SAN.MC', 'BTC-USD', '^GSPC'
  name          VARCHAR,
  asset_class   VARCHAR,   -- equity|etf|index|crypto|commodity|fx
  exchange      VARCHAR,
  currency      VARCHAR,   -- USD|EUR|GBP
  country       VARCHAR,
  gics_sector   VARCHAR,   -- 11 sectores GICS
  gics_industry VARCHAR,
  investment_type VARCHAR, -- growth|value|dividend|etf|index|crypto|commodity
  market_cap    DOUBLE,
  is_active     BOOLEAN DEFAULT TRUE,
  first_seen    DATE,
  last_seen     DATE,      -- para mitigar survivorship bias
  updated_at    TIMESTAMP
);

CREATE TABLE IF NOT EXISTS universe_membership (   -- histórico de constituyentes
  universe   VARCHAR,      -- 'SP500','NASDAQ100','IBEX35','ESTOXX50','ETF_CORE'
  ticker     VARCHAR,
  valid_from DATE,
  valid_to   DATE,         -- NULL = vigente
  PRIMARY KEY (universe, ticker, valid_from)
);

-- ============ PRECIOS ============
CREATE TABLE IF NOT EXISTS prices_daily (
  ticker    VARCHAR,
  date      DATE,
  open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
  adj_close DOUBLE,          -- ajustado por splits+dividendos → usar SIEMPRE en retornos
  volume    BIGINT,
  source    VARCHAR,         -- 'yfinance'|'stooq'
  ingested_at TIMESTAMP,
  PRIMARY KEY (ticker, date)
);
CREATE INDEX IF NOT EXISTS idx_prices_date ON prices_daily(date);

CREATE TABLE IF NOT EXISTS corporate_actions (
  ticker VARCHAR, date DATE, action_type VARCHAR, value DOUBLE,  -- 'dividend'|'split'
  PRIMARY KEY (ticker, date, action_type)
);

-- ============ FUNDAMENTALES ============
CREATE TABLE IF NOT EXISTS fundamentals_snapshot (
  ticker VARCHAR, as_of DATE,
  trailing_pe DOUBLE, forward_pe DOUBLE, peg_ratio DOUBLE,
  price_to_book DOUBLE, price_to_sales DOUBLE, ev_to_ebitda DOUBLE, ev_to_revenue DOUBLE,
  fcf_yield DOUBLE, earnings_yield DOUBLE,
  gross_margin DOUBLE, operating_margin DOUBLE, profit_margin DOUBLE,
  roe DOUBLE, roa DOUBLE, roic DOUBLE,
  revenue_growth_yoy DOUBLE, earnings_growth_yoy DOUBLE, revenue_growth_3y DOUBLE,
  debt_to_equity DOUBLE, net_debt_to_ebitda DOUBLE, current_ratio DOUBLE, interest_coverage DOUBLE,
  dividend_yield DOUBLE, payout_ratio DOUBLE, dividend_growth_5y DOUBLE,
  shares_outstanding DOUBLE, buyback_yield DOUBLE,
  beta DOUBLE, market_cap DOUBLE, currency VARCHAR,
  source VARCHAR, completeness DOUBLE,   -- % de campos no nulos → gating de calidad
  PRIMARY KEY (ticker, as_of)
);

CREATE TABLE IF NOT EXISTS earnings_events (
  ticker VARCHAR, report_date DATE, period VARCHAR,
  eps_estimate DOUBLE, eps_actual DOUBLE, surprise_pct DOUBLE,
  revenue_estimate DOUBLE, revenue_actual DOUBLE,
  PRIMARY KEY (ticker, report_date)
);

-- ============ MACRO ============
CREATE TABLE IF NOT EXISTS macro_series (
  series_id VARCHAR,   -- 'T10Y2Y','DGS10','VIXCLS','BAMLH0A0HYM2','DTWEXBGS','UNRATE','CPIAUCSL'
  date DATE, value DOUBLE, source VARCHAR,
  PRIMARY KEY (series_id, date)
);

-- ============ CALCULADOS (materializados por run_compute) ============
CREATE TABLE IF NOT EXISTS indicators_daily (
  ticker VARCHAR, date DATE,
  sma20 DOUBLE, sma50 DOUBLE, sma200 DOUBLE, ema12 DOUBLE, ema26 DOUBLE,
  macd DOUBLE, macd_signal DOUBLE, macd_hist DOUBLE,
  rsi14 DOUBLE, adx14 DOUBLE, plus_di DOUBLE, minus_di DOUBLE,
  atr14 DOUBLE, atr_pct DOUBLE,
  bb_upper DOUBLE, bb_lower DOUBLE, bb_width DOUBLE, bb_pctb DOUBLE,
  realized_vol_20 DOUBLE, realized_vol_60 DOUBLE, realized_vol_252 DOUBLE,
  obv DOUBLE, obv_slope20 DOUBLE, rel_volume_20 DOUBLE,
  roc_1m DOUBLE, roc_3m DOUBLE, roc_6m DOUBLE, roc_12m DOUBLE, mom_12_1 DOUBLE,
  ret_1d DOUBLE, ret_5d DOUBLE,
  dist_52w_high DOUBLE, dist_52w_low DOUBLE, drawdown DOUBLE, max_dd_1y DOUBLE,
  support_20 DOUBLE, resistance_20 DOUBLE, support_60 DOUBLE, resistance_60 DOUBLE,
  above_sma200 BOOLEAN, golden_cross BOOLEAN, death_cross BOOLEAN,
  rs_vs_bench_3m DOUBLE, rs_vs_sector_3m DOUBLE, beta_252 DOUBLE,
  PRIMARY KEY (ticker, date)
);

CREATE TABLE IF NOT EXISTS factor_scores (
  ticker VARCHAR, date DATE, peer_group VARCHAR,  -- 'GICS:Information Technology' | 'ALL'
  value_z DOUBLE, growth_z DOUBLE, quality_z DOUBLE, momentum_z DOUBLE,
  lowvol_z DOUBLE, size_z DOUBLE, dividend_z DOUBLE, technical_z DOUBLE, sentiment_z DOUBLE,
  composite DOUBLE,              -- score final ponderado
  composite_rank_sector INTEGER, composite_pctile DOUBLE,
  coverage DOUBLE,               -- fracción de sub-métricas disponibles → fiabilidad
  weights_hash VARCHAR,          -- identifica el preset de pesos usado
  PRIMARY KEY (ticker, date, weights_hash)
);

CREATE TABLE IF NOT EXISTS factor_contributions (  -- explicabilidad
  ticker VARCHAR, date DATE, weights_hash VARCHAR,
  factor VARCHAR, raw_value DOUBLE, zscore DOUBLE, weight DOUBLE, contribution DOUBLE,
  PRIMARY KEY (ticker, date, weights_hash, factor)
);

CREATE TABLE IF NOT EXISTS signals (
  ticker VARCHAR, date DATE, signal_id VARCHAR,   -- 'GOLDEN_CROSS','RSI_OVERSOLD_UPTREND'…
  direction VARCHAR,      -- bullish|bearish|neutral
  strength DOUBLE,        -- 0..1
  detail JSON,
  PRIMARY KEY (ticker, date, signal_id)
);

CREATE TABLE IF NOT EXISTS breadth_daily (
  date DATE, scope VARCHAR,   -- 'SP500'|'IBEX35'|'GICS:Energy'
  pct_above_sma50 DOUBLE, pct_above_sma200 DOUBLE,
  advances INTEGER, declines INTEGER, ad_line DOUBLE,
  new_highs_52w INTEGER, new_lows_52w INTEGER, hl_index DOUBLE,
  pct_rsi_overbought DOUBLE, pct_rsi_oversold DOUBLE,
  median_ret_1m DOUBLE, avg_pairwise_corr_60 DOUBLE,
  PRIMARY KEY (date, scope)
);

CREATE TABLE IF NOT EXISTS regime_daily (
  date DATE PRIMARY KEY,
  yield_curve_10y2y DOUBLE, vix DOUBLE, vix_percentile_1y DOUBLE,
  dxy_ret_3m DOUBLE, gold_ret_3m DOUBLE, oil_ret_3m DOUBLE,
  credit_spread_hy DOUBLE, copper_gold_ratio DOUBLE,
  risk_score DOUBLE,        -- -100 (risk-off) .. +100 (risk-on)
  regime VARCHAR,           -- 'risk_on'|'neutral'|'risk_off'
  components JSON           -- desglose para explicabilidad
);

CREATE TABLE IF NOT EXISTS news_items (
  id VARCHAR PRIMARY KEY, ticker VARCHAR, published_at TIMESTAMP,
  headline VARCHAR, url VARCHAR, source VARCHAR, sentiment DOUBLE  -- -1..1
);
CREATE TABLE IF NOT EXISTS sentiment_daily (
  ticker VARCHAR, date DATE, n_articles INTEGER,
  sentiment_mean DOUBLE, sentiment_std DOUBLE, sentiment_momentum_5d DOUBLE,
  PRIMARY KEY (ticker, date)
);

-- ============ USUARIO ============
CREATE TABLE IF NOT EXISTS watchlist (
  ticker VARCHAR, list_name VARCHAR DEFAULT 'default',
  added_at TIMESTAMP, note VARCHAR, target_price DOUBLE,
  PRIMARY KEY (ticker, list_name)
);
CREATE TABLE IF NOT EXISTS positions (
  id INTEGER PRIMARY KEY, ticker VARCHAR, qty DOUBLE,
  avg_cost DOUBLE, currency VARCHAR, opened_at DATE, closed_at DATE, note VARCHAR
);
CREATE TABLE IF NOT EXISTS alerts (
  id VARCHAR PRIMARY KEY, rule_id VARCHAR, ticker VARCHAR,
  triggered_at TIMESTAMP, message VARCHAR, payload JSON,
  delivered BOOLEAN DEFAULT FALSE, channel VARCHAR, acknowledged BOOLEAN DEFAULT FALSE
);

-- ============ OPERACIÓN ============
CREATE TABLE IF NOT EXISTS ingest_log (
  run_id VARCHAR, started_at TIMESTAMP, finished_at TIMESTAMP,
  task VARCHAR, target VARCHAR, status VARCHAR,   -- OK|PARTIAL|FAILED|RATE_LIMITED
  rows_written INTEGER, requests_used INTEGER, error VARCHAR
);
CREATE TABLE IF NOT EXISTS data_quality (
  date DATE, ticker VARCHAR, check_name VARCHAR, passed BOOLEAN, detail VARCHAR
);
```

**Notas de modelado:**

- `adj_close` es la columna canónica para **todo** cálculo de retornos e
  indicadores; `close` solo para mostrar precio "de pantalla" y niveles de
  soporte/resistencia visuales.
- `fundamentals_snapshot` es *snapshot con fecha*, no serie contable. Es la única
  realidad que da yfinance sin coste. **Consecuencia**: los backtests de factores
  fundamentales sufren look-ahead severo hasta que se acumulen meses de snapshots
  reales. Debe advertirse en la UI de backtest (ver §6).
- `universe_membership` con `valid_from/valid_to` permite, a partir del día 1, ir
  construyendo un histórico libre de survivorship bias hacia adelante.
- Multidivisa: se guarda `currency` en `instruments`. Los z-scores se calculan
  sobre **retornos y ratios** (adimensionales), no sobre precios, así que la
  divisa no contamina el scoring. Solo la página de cartera convierte a EUR
  usando `EURUSD=X`.

---

## 4. Catálogo de factores y señales

Prioridad: **P0** = MVP fase 1, **P1** = fase 2-3, **P2** = fase 4+.

### 4.1 Técnicos — `core/indicators.py`

| Prio | Indicador | Datos | Qué mide | Nota de implementación |
|---|---|---|---|---|
| P0 | SMA 20/50/200, EMA 12/26 | adj_close | Tendencia | `rolling(min_periods=n)` — nunca `min_periods=1` |
| P0 | Golden/Death Cross | SMA50, SMA200 | Cambio de régimen de tendencia | Booleano de cruce, no de estado |
| P0 | MACD (12,26,9) + histograma | adj_close | Momentum de tendencia | EMA con `adjust=False` |
| P0 | RSI(14) | adj_close | Sobrecompra/sobreventa | Wilder smoothing (`ewm(alpha=1/14)`), no SMA |
| P0 | ROC 1m/3m/6m/12m | adj_close | Momentum absoluto | 21/63/126/252 sesiones |
| P0 | **Momentum 12-1** | adj_close | Retorno 12m excluyendo el último mes | Evita la reversión a corto plazo. Núcleo del factor momentum |
| P0 | Distancia a máximo/mínimo 52s | adj_close | Posición en el rango anual | `(price/rolling_max_252 - 1)` |
| P0 | Drawdown actual y máx. 1a | adj_close | Riesgo realizado | |
| P0 | Volatilidad realizada 20/60/252 | retornos log | Riesgo | Anualizada `×√252` |
| P0 | Volumen relativo 20d | volume | Interés inusual | `vol / rolling_mean(vol,20)` |
| P1 | ADX(14) + DI± | OHLC | Fuerza de la tendencia | Filtro clave: RSI funciona en rango, MACD en tendencia |
| P1 | ATR(14) y ATR% | OHLC | Volatilidad para dimensionar stops | ATR% = ATR/close |
| P1 | Bandas de Bollinger (20,2) + %B + width | adj_close | Extremos y compresión | `bb_width` bajo = "squeeze" |
| P1 | OBV + pendiente 20d | close, volume | Acumulación/distribución | Divergencia OBV vs precio = señal |
| P1 | Soportes/resistencias | OHLC | Niveles | Pivotes locales (`scipy.signal.argrelextrema`, order=5) agrupados por proximidad (1 % de tolerancia) |
| P1 | Fuerza relativa vs índice y vs sector | adj_close + benchmark | ¿Lo hace mejor que su entorno? | Ratio normalizado a 100 en t0 |
| P2 | Beta 252d, correlación rodante | retornos | Sensibilidad al mercado | |
| P2 | Gaps, patrón de velas básico | OHLC | Contexto | Baja prioridad, bajo valor predictivo |

### 4.2 Fundamentales / valoración — `core/fundamental.py`

| Prio | Métrica | Origen | Qué mide |
|---|---|---|---|
| P0 | PER trailing / forward | `Ticker.info` | Valoración sobre beneficios |
| P0 | P/B, P/S | info | Valoración sobre balance/ventas |
| P0 | Rentabilidad por dividendo, payout | info | Renta y sostenibilidad |
| P0 | ROE, margen neto | info | Calidad del negocio |
| P0 | Crecimiento de ingresos y BPA YoY | info | Growth |
| P1 | EV/EBITDA | `enterpriseValue`/`ebitda` | Valoración neutral a estructura de capital |
| P1 | FCF yield | `freeCashflow`/`marketCap` | La métrica de valor más robusta |
| P1 | Deuda neta/EBITDA, D/E, cobertura de intereses | balance sheet | Solvencia |
| P1 | ROIC, margen operativo/bruto | financials | Calidad |
| P1 | Sorpresas de resultados | `Ticker.earnings_dates` | Momentum fundamental (PEAD) |
| P2 | Crecimiento de dividendo 5a, buyback yield | dividends, shares | Política de retribución |
| P2 | Revisiones de estimaciones | Finnhub free | Predictor fuerte, cobertura limitada |

**Regla de higiene obligatoria**: antes de puntuar, `core/fundamental.py::sanitize()`
debe (a) descartar PER negativos o >200 → NaN, (b) descartar ratios cuando
`completeness < 0.5`, (c) recortar al percentil 1/99 dentro del sector.

### 4.3 Factores de estilo — `core/factors.py`

Cada factor es una **media de z-scores de sub-métricas**, calculada dentro del
grupo de comparación (`peer_group = sector GICS`, con fallback a universo completo
si el sector tiene <8 valores).

| Factor | Sub-métricas (signo aplicado) | Interpretación |
|---|---|---|
| `value` | −PER, −P/B, −EV/EBITDA, −P/S, +FCF yield, +earnings yield | Barato respecto a sus pares |
| `growth` | +crec. ingresos YoY, +crec. BPA YoY, +crec. ingresos 3a, +forward/trailing PE gap | Crece más rápido |
| `quality` | +ROE, +ROIC, +margen operativo, −deuda neta/EBITDA, +cobertura intereses, −volatilidad de márgenes | Negocio sólido |
| `momentum` | +mom 12-1, +ROC 6m, +RS vs sector 3m, +(precio>SMA200) | Tendencia consolidada |
| `lowvol` | −vol realizada 252, −beta, −max DD 1a, −ATR% | Estabilidad |
| `size` | −log(market cap) | Prima de tamaño (configurable; puede desactivarse) |
| `dividend` | +yield, +crec. dividendo 5a, −payout si >80 %, +años consecutivos | Renta sostenible |
| `technical` | Puntuación agregada de señales técnicas activas (§4.8) | Timing |
| `sentiment` | +sentimiento medio 30d, +momentum de sentimiento | Percepción |

**Funciones de referencia:**

```python
def zscore_by_group(df, value_col, group_col, winsor=(0.02, 0.98), min_group=8) -> pd.Series
def build_factor(df, spec: FactorSpec) -> pd.DataFrame  # devuelve z + coverage
```

### 4.4 Mercado / breadth — `core/breadth.py`

| Prio | Métrica | Qué mide |
|---|---|---|
| P0 | % de valores sobre SMA200 y SMA50, por universo y por sector | Salud interna del mercado. <40 % = deterioro; >80 % = euforia |
| P0 | Nuevos máximos vs nuevos mínimos 52s (y su ratio) | Liderazgo real vs índice sostenido por pocos |
| P1 | Línea Avance-Descenso acumulada | Divergencia AD vs índice = aviso clásico |
| P1 | Amplitud por sector (mapa) | Dónde está la fortaleza interna |
| P1 | % RSI>70 y % RSI<30 | Extremos agregados |
| P1 | Correlación media por pares (60d) | Correlación alta = mercado dominado por macro (mal para stock-picking) |
| P2 | Dispersión de retornos, contribución de las 10 mayores capitalizaciones | Concentración |

### 4.5 Rotación sectorial — `core/relative.py`

- Retorno por sector a 1s/1m/3m/6m/12m (ETFs sectoriales US: XLK, XLF, XLV, XLE,
  XLI, XLY, XLP, XLU, XLB, XLRE, XLC; Europa: STOXX 600 sectoriales o media
  equiponderada de constituyentes).
- **Fuerza relativa** `sector/SPY` normalizada; pendiente de 20d → clasificación en
  cuadrantes tipo RRG (*ratio* vs *momentum*): `Leading / Weakening / Lagging / Improving`.
- **Mapa de calor** sector × horizonte temporal.
- **Ciclo económico**: regla heurística documentada (defensivos XLP/XLU/XLV
  liderando = fase tardía/contracción; cíclicos XLI/XLB/XLE = expansión temprana).
  **Marcar explícitamente como heurístico, no predictivo.**
- Rotación intra-sector: fuerza relativa de cada valor frente a su propio sector.

### 4.6 Macro y riesgo — `core/macro.py`

| Serie | Fuente | Señal |
|---|---|---|
| `T10Y2Y` (10a-2a) | FRED | Curva invertida (<0) = señal recesiva histórica; la **des-inversión** suele preceder la recesión |
| `DGS10`, `DGS2`, `DFII10` | FRED | Nivel y tipos reales |
| `BAMLH0A0HYM2` | FRED | Spread de crédito HY: el mejor termómetro de estrés. Ampliación rápida = risk-off |
| `VIXCLS` (FRED) / `^VIX` (yf) | ambos | Volatilidad implícita + percentil a 1 año |
| `DTWEXBGS` / `DX-Y.NYB` | FRED / yf | Dólar: fuerte = viento en contra para emergentes y materias primas |
| `GC=F` (oro), `CL=F` (petróleo), `HG=F` (cobre) | yf | Ratio **cobre/oro** = proxy de crecimiento vs miedo |
| `CPIAUCSL`, `UNRATE`, `INDPRO`, `PAYEMS`, `UMCSENT` | FRED | Contexto macro con retardo |
| `BTC-USD`, `ETH-USD` | yf | Proxy de apetito por riesgo en la parte alta del espectro |

**Semáforo risk-on/risk-off** (`regime_daily.risk_score`): media ponderada de
z-scores de 6-8 componentes (VIX invertido, spread HY invertido, cobre/oro,
breadth %>SMA200, pendiente de curva, momentum SPY vs bonos IEF, ratio XLY/XLP).
Salida −100..+100, tres regímenes con umbrales configurables. Se guarda el
**desglose JSON** para que el usuario vea qué componente empuja el semáforo.

### 4.7 Sentimiento — `core/sentiment.py`

- **Noticias**: Finnhub (free ~60 req/min) como principal y Marketaux (~100
  req/día) como alternativa; ambos devuelven sentimiento por ticker. Fallback
  local: `vaderSentiment` sobre titulares de `yf.Ticker(t).news`. Agregación
  diaria en `sentiment_daily` con decaimiento exponencial (semivida 3 días).
- **Put/Call ratio**: el CPC de CBOE ya no es libremente descargable de forma
  fiable. **Proxy**: skew implícita calculada desde `yf.Ticker(t).option_chain()`
  sobre índices (`SPY`), a coste de 2-3 peticiones/día. Marcar como P2 y opcional.
- **Fear & Greed proxy propio** (`compute_fear_greed()`), 5 componentes
  normalizados 0-100 y promediados, replicando el espíritu del índice de CNN sin
  depender de su API: (1) momentum SPY vs SMA125, (2) fuerza de precio = nuevos
  máx−mín 52s, (3) amplitud = %>SMA200, (4) VIX vs su media 50d invertido,
  (5) refugio = retorno SPY−TLT a 20d. Documentar que es una aproximación propia.

### 4.8 Señales discretas (tabla `signals`)

| signal_id | Condición | Racional |
|---|---|---|
| `GOLDEN_CROSS` | SMA50 cruza al alza SMA200 | Cambio de tendencia larga |
| `PULLBACK_IN_UPTREND` | close>SMA200 AND RSI14<40 AND ADX>20 | Corrección dentro de tendencia alcista (el patrón más útil para comprar) |
| `RSI_OVERSOLD_REVERSAL` | RSI cruza al alza 30 desde debajo | Reversión a la media |
| `MACD_BULL_CROSS` | MACD cruza señal al alza con hist<0 previo | Momentum girando |
| `52W_HIGH_BREAKOUT` | close = máx 252d AND vol_rel>1.5 | Ruptura con volumen |
| `BB_SQUEEZE` | bb_width en percentil <10 de 1 año | Compresión previa a expansión |
| `VOLUME_SPIKE` | vol_rel > 3 | Evento; requiere contexto de noticia |
| `NEW_DOWNTREND` | Death cross AND close<SMA200 | Señal de salida/evitar |
| `EARNINGS_SURPRISE_DRIFT` | surprise_pct>5 % en últimos 10 días | PEAD documentado académicamente |
| `SECTOR_LEADER` | RS vs índice en decil superior AND sector en cuadrante Leading | Confluencia |
| `VALUE_QUALITY_COMBO` | value_z>1 AND quality_z>0.5 AND momentum_z>0 | Value con calidad y sin cuchillo cayendo |

### 4.9 Score compuesto y lista de oportunidades — `core/scoring.py`

**Pipeline (por fecha, por peer_group):**

1. `sanitize()` — reglas de higiene por métrica.
2. `winsorize(0.02, 0.98)` dentro del peer group → limita el efecto de outliers y
   errores de datos.
3. `zscore_by_group()` — z-score robusto opcional (mediana / MAD × 1.4826)
   configurable, más resistente a colas gruesas.
4. Agregación de sub-métricas → z por factor, guardando `coverage` (fracción no nula).
5. **Penalización por cobertura**: `factor_z *= sqrt(coverage)` y se excluye el
   factor si `coverage < 0.4`. Crítico para IBEX/Europa, donde faltan campos.
6. **Renormalización de pesos**: si un factor se excluye, sus pesos se reparten
   proporcionalmente entre los presentes, para que un valor con datos incompletos
   no se penalice arbitrariamente en el score global (pero sí se marca en la UI
   con un icono de cobertura).
7. `composite = Σ (w_i × factor_z_i)` → percentil y rank dentro del sector y
   dentro del universo.
8. `explain()` → filas en `factor_contributions` con `contribution = w_i × z_i`,
   ordenables. Es lo que alimenta el texto de justificación.

**Pesos** en `config/factors.yaml` con presets (`balanced`, `value`, `growth`,
`dividend`, `momentum`) y sliders en la UI que recalculan en caliente (el z-score
por factor ya está materializado; recombinar es una operación de milisegundos en
pandas — no requiere recomputar nada).

**Listado de "oportunidades"** = `composite_pctile >= umbral` **filtrado por
guardas de sensatez**, todas configurables:

- `above_sma200 = TRUE` (no comprar en tendencia bajista estructural) —
  desactivable para perfil contrarian.
- `rsi14 < 75` (no entrar en euforia).
- `market_cap > mínimo` y `volumen medio 20d × precio > mínimo` (liquidez).
- `drawdown > −60 %` (evitar valores en colapso).
- `coverage >= 0.5`.
- Régimen macro: si `regime = risk_off`, se reduce el número de sugerencias y se
  prioriza `lowvol_z` y `quality_z` (multiplicador de pesos por régimen,
  explícito en YAML).

**Justificación explicable** generada por plantilla determinista
(`explain.render(ticker)`), sin LLM:

> **IBE.MC — Score 78 (percentil 92 en Utilities)**
> A favor: `value` +1.4 (EV/EBITDA 8.1 vs mediana sectorial 11.3), `dividend` +1.1
> (yield 5.2 %, payout 68 %), `momentum` +0.6 (12-1 = +14 %, por encima de SMA200
> desde hace 84 sesiones).
> En contra: `growth` −0.8 (ingresos +1.2 % YoY), `quality` −0.3 (deuda
> neta/EBITDA 4.1).
> Señales activas: `PULLBACK_IN_UPTREND`, `MACD_BULL_CROSS`.
> Cobertura de datos: 82 %. Contexto: régimen `neutral` (score 12).

---

## 5. Diseño del dashboard (Streamlit)

> **Sustituido por la adenda 1** (§B.4 y §D). Se conserva aquí como referencia del
> diseño base.

`app/main.py` define la navegación con `st.navigation` + `st.Page`, agrupada en
secciones ("Mercado", "Selección", "Mi cartera", "Análisis"), y una barra lateral
común con: selector de universo, sector, preset de pesos, fecha de referencia, y
badge de frescura de datos (`última ingesta: hace 6 h`). Tablas con `st.dataframe`
+ `column_config` (barras de progreso para scores, `LineChartColumn` para
sparklines de 3 meses).

**Página 1 — Visión general del mercado**
Fila de KPIs (S&P 500, Nasdaq, IBEX 35, Euro Stoxx 50, VIX, EURUSD, oro,
petróleo, BTC); semáforo risk-on/risk-off con desglose; breadth (% > SMA200 y
SMA50 con bandas 20/50/80, barras de nuevos máx vs mín); línea AD acumulada
superpuesta al índice para ver divergencias; mini-heatmap de rendimiento por
sector; top 5 oportunidades del día y últimas alertas.

**Página 2 — Sectores y rotación**
Heatmap sector × horizonte (escala divergente centrada en 0); gráfico RRG con
estelas de 10 semanas y cuadrantes etiquetados; líneas de fuerza relativa por
sector; tabla por sector (nº valores, % > SMA200, mediana de PER, mediana de
momentum, score medio, mejores/peores 3); treemap capitalización × rendimiento.
Selector de vista: por **sector GICS** o por **tipo de inversión**.

**Página 3 — Screener / Oportunidades** *(página central)*
Panel de pesos de factores (sliders + preset) que recalcula el ranking en vivo
dentro de un `st.fragment`; filtros por universo, sector, tipo, capitalización,
precio, yield mínimo, PER máximo, guardas de sensatez y señales activas; tabla
rankeada con z de cada factor en color divergente, cobertura, señales y sparkline;
panel de explicación al seleccionar fila; exportación a CSV. Aviso permanente:
*"Ranking relativo dentro del universo filtrado. No implica recomendación de compra."*

**Página 4 — Ficha de valor**
Candlestick + volumen + medias + Bollinger; subgráficos RSI, MACD, ADX, OBV;
marcadores de señales históricas; tarjeta fundamental con la mediana del sector al
lado y el z-score; radar de los 7 factores; historial de resultados y sorpresas;
rendimiento relativo vs sector e índice; noticias con sentimiento; métricas de
riesgo y stop técnico de referencia (`close − 2×ATR14`), etiquetado como
*referencia técnica, no recomendación*.

**Página 5 — Cartera y watchlist**
Watchlist editable con score, señales y variación desde la fecha de adición;
posiciones con PnL, peso y exposición por sector y tipo; **diagnóstico de
concentración** (contribución al riesgo, correlación media, exposición factorial
agregada en radar); sugerencias de diversificación; curva de valor vs benchmark.

**Página 6 — Macro y riesgo**
Curva de tipos actual vs hace 3/12 meses; 10a-2a con zonas de inversión
sombreadas; VIX + percentil; spread HY; DXY; oro; petróleo; cobre/oro; panel FRED;
histórico del `risk_score` superpuesto al S&P 500; cripto como termómetro.

**Página 7 — Backtest / validación de señales**
Selector de señal o factor, universo, horizonte y periodo. Hit rate, retorno medio
y mediano, exceso vs benchmark, Sharpe, max DD, nº de observaciones, IC (Spearman)
con media y t-stat; distribución de retornos forward; curva de equity por deciles
y spread D10−D1; estabilidad por año. Banner rojo permanente con las advertencias
de §6.

**Página 8 — Alertas y configuración**
Editor de reglas; histórico de alertas con acuse de recibo; prueba de canales;
estado de la ingesta (`ingest_log`, tickers obsoletos, `data_quality`) y botón
"Ejecutar actualización ahora" con `st.status`.

---

## 6. Backtesting y validación

**Motor** (`backtest/engine.py`), deliberadamente simple y honesto:

```python
def forward_returns(prices: pd.DataFrame, horizons=(5,10,21,63)) -> pd.DataFrame
def event_study(signal_df, fwd_df, benchmark) -> EventStudyResult
def rank_ic(scores: pd.DataFrame, fwd: pd.DataFrame, method="spearman") -> pd.Series
def decile_portfolios(scores, fwd, n=10, rebalance="M") -> pd.DataFrame
def walk_forward(scores, fwd, train_years=3, test_years=1, step_years=1) -> list[FoldResult]
```

**Métricas** (`backtest/metrics.py`): hit rate (% de eventos con retorno forward
> 0 y > benchmark), retorno medio/mediano a N días, retorno en exceso, Sharpe
anualizado, Sortino, max drawdown, Calmar, turnover, IC medio + IC-IR
(`mean(IC)/std(IC)`), t-stat, spread D10−D1.

**Reglas de disciplina que el motor debe imponer por construcción:**

1. **Anti look-ahead**: la señal en el día `t` solo usa datos hasta el cierre de
   `t`, y el retorno se mide desde el **cierre de `t+1`** (no se puede comprar al
   cierre que generó la señal). Implementado con un único `.shift(1)` centralizado
   en `engine.py`, y verificado por `tests/test_no_lookahead.py` (test que
   perturba los datos futuros y comprueba que la señal pasada no cambia).
2. **Survivorship bias**: el universo se toma de `universe_membership` en la fecha
   correspondiente. Como el histórico de constituyentes no existe al arrancar, la
   fase 1 usa el universo actual y la UI muestra: *"Backtest con universo actual →
   sesgo de supervivencia al alza. Trate los resultados como cota superior
   optimista."* Desde el día 1 se registra la composición diaria para que en 1-2
   años el sesgo desaparezca hacia adelante.
3. **Look-ahead fundamental**: los ratios de `fundamentals_snapshot` son actuales,
   no point-in-time. Cualquier backtest de factores fundamentales anterior a la
   primera ingesta se marca como **NO VÁLIDO** en la UI (`validity: technical_only`
   vs `full`). Solo los factores técnicos son backtesteables con rigor desde el
   principio.
4. **Costes**: aplicar comisión + slippage configurable (por defecto 0,10 % por
   operación). Sin costes, cualquier estrategia de alta rotación parece rentable.
5. **Sobreajuste**: la UI muestra el número de configuraciones probadas en la
   sesión y advierte cuando se superan ~20. Walk-forward con
   `train_years=3/test_years=1` y reporte **solo de métricas out-of-sample** en la
   cabecera; las in-sample van en un expander secundario.
6. **Significancia**: mostrar siempre `n` y el t-stat. Con n<100 eventos, banner de
   "muestra insuficiente".
7. **Benchmark obligatorio**: toda métrica se muestra junto a la del universo
   equiponderado en el mismo periodo. Un hit rate del 62 % no significa nada si el
   mercado subió el 65 % de los días.

---

## 7. Alertas

**Definición declarativa** en `config/alerts.yaml`:

```yaml
channels:
  file:     {enabled: true,  path: data/alerts.jsonl}
  telegram: {enabled: false, bot_token_env: TELEGRAM_BOT_TOKEN, chat_id_env: TELEGRAM_CHAT_ID}
  email:    {enabled: false, smtp_host_env: SMTP_HOST, to_env: ALERT_EMAIL}
rules:
  - id: watchlist_pullback
    scope: watchlist            # watchlist | portfolio | universe:SP500 | sector:Energy
    when: "above_sma200 and rsi14 < 35 and adx14 > 20"
    severity: high
    cooldown_days: 10           # anti-spam
    message: "{ticker}: corrección en tendencia alcista (RSI {rsi14:.0f}, {price:.2f})"
  - id: score_top_decile
    scope: universe:SP500
    when: "composite_pctile > 0.95 and coverage > 0.6"
    cooldown_days: 20
  - id: stop_technical
    scope: portfolio
    when: "close < sma200 and macd_hist < 0"
    severity: critical
  - id: regime_flip
    scope: market
    when: "regime != prev_regime"
  - id: price_target
    scope: watchlist
    when: "close <= target_price"
```

**Implementación**: `alerts/rules.py` compila cada `when` con un evaluador seguro
(`asteval` o `pandas.DataFrame.eval` con lista blanca de columnas — **nunca
`eval()` puro**) contra el join `indicators_daily × factor_scores × signals` del
último día. `alerts/evaluate.py` deduplica con `cooldown_days` consultando
`alerts` y escribe las nuevas. `alerts/notify.py` entrega por los canales activos
y marca `delivered`.

**Programación**: `scripts/daily_update.sh` (ingest → compute → alerts) vía **cron**
a las 23:15 CET de lunes a viernes (mercado US cerrado, datos consolidados) más una
pasada ligera a las 09:00 para Europa. Alternativa portable si la máquina no está
siempre encendida: `APScheduler` en un proceso `daemon.py`. Recomendación: cron +
`flock` para evitar solapes, log rotado en `data/logs/`.

---

## 8. Plan por fases

> **Sustituido por la adenda 1 §G** (fases reordenadas) y ampliado por la
> adenda 2 §11 (fases 6-10 del bot). Se conserva como referencia del desglose base.

### Fase 0 — Esqueleto (0,5 día)

`pyproject.toml`, `Makefile`, `.env.example`, `core/config.py`, `core/db.py`,
`schema.sql`, `providers/base.py` con Protocols, `tests/conftest.py`, CI local con
ruff+pytest.
**Entregable**: `make setup && make test` verde; `python -m stocks_tracker.core.db --migrate`
crea el warehouse vacío.

### Fase 1 — MVP utilizable (2-3 días) ★

`yfinance_provider` + `universe_provider`; backfill de 10 años para ~250 tickers;
indicadores P0 + `run_compute`; scoring con `momentum`, `technical`, `value` básico
y `dividend`; páginas 1, 3 y 4.
**Entregable**: dashboard que arranca con `make run`, muestra el estado del mercado
y un ranking de oportunidades explicado, con datos actualizables por un comando.

### Fase 2 — Profundidad de mercado (2-3 días)

Ampliación a S&P 500 completo + Nasdaq 100 + Euro Stoxx 50 (~750 tickers);
fundamentales escalonados + factores `growth`, `quality`, `lowvol`, `size`;
`breadth.py` y `relative.py` completos; páginas 2 y 6.

### Fase 3 — Validación (2 días)

Motor de backtest, métricas, walk-forward, página 7. **Poda**: eliminar o degradar
las señales que no superen el umbral (IC-IR > 0,3 y significancia out-of-sample).
**Entregable**: cada señal lleva una etiqueta de evidencia histórica.

### Fase 4 — Operativa diaria (1-2 días)

Alertas completas, `daily_update.sh`, cron, página 8, watchlist y cartera (página 5).

### Fase 5 — Refinamiento (continuo)

Sentimiento y noticias, Fear&Greed proxy, proveedor de fallback Stooq, mejora de
calidad de datos europeos, presets de pesos, exportaciones, tema visual, docs.

---

## 9. Dependencias y configuración

**`pyproject.toml` (deps de producción):**

```
python = ">=3.11,<3.14"
streamlit>=1.40           # st.navigation, st.fragment, column_config
pandas>=2.2
numpy>=2.0
duckdb>=1.1
pyarrow>=17
yfinance>=0.2.60          # fijar rango y probar antes de subir versión mayor
curl-cffi>=0.7,<0.15      # requerido por yfinance para las sesiones
requests>=2.32
requests-cache>=1.2
tenacity>=9.0
fredapi>=0.5
plotly>=5.24
pyyaml>=6.0
pydantic>=2.9
pydantic-settings>=2.5
python-dotenv>=1.0
scipy>=1.14               # argrelextrema, estadística
lxml>=5.3                 # pd.read_html para constituyentes
beautifulsoup4>=4.12
html5lib
vaderSentiment>=3.3       # fallback de sentimiento local
asteval>=1.0              # evaluación segura de reglas de alerta
rich>=13.9                # logs del ETL
```

**Dev:** `pytest`, `pytest-cov`, `hypothesis`, `ruff`, `mypy`, `ta>=0.11` (solo
como oráculo de comparación en tests), `freezegun`.

**Deliberadamente NO se incluye**: `pandas-ta` (riesgo de discontinuación),
`TA-Lib` (dependencia C que complica la instalación), ningún broker/API de pago.

**`config/factors.yaml` (extracto):**

```yaml
peer_group: gics_sector
min_group_size: 8
winsorize: [0.02, 0.98]
robust_zscore: true
coverage_floor: 0.4
presets:
  balanced:  {value: 0.20, growth: 0.15, quality: 0.20, momentum: 0.20, lowvol: 0.10, dividend: 0.10, technical: 0.05}
  value:     {value: 0.40, quality: 0.25, dividend: 0.15, momentum: 0.10, lowvol: 0.10}
  dividend:  {dividend: 0.40, quality: 0.25, value: 0.20, lowvol: 0.15}
regime_multipliers:
  risk_off:  {lowvol: 1.5, quality: 1.3, momentum: 0.6}
  risk_on:   {momentum: 1.3, growth: 1.2, lowvol: 0.7}
factors:
  value:
    submetrics:
      - {field: trailing_pe,  sign: -1, max_valid: 200, min_valid: 0}
      - {field: ev_to_ebitda, sign: -1, max_valid: 60,  min_valid: 0}
      - {field: fcf_yield,    sign: +1}
      - {field: price_to_book,sign: -1, max_valid: 30,  min_valid: 0}
```

**`config/universe.yaml`:** universos con su fuente (`wikipedia_sp500`, `manual`),
benchmark asociado, divisa, sufijo de mercado (`.MC` Madrid, `.DE`, `.PA`, `.MI`,
`.AS`, `.L`), y listas manuales de ETFs (SPY, QQQ, IWM, XL*, VGK, EWP, TLT, IEF,
HYG, GLD, USO, DBC), índices (`^GSPC`, `^NDX`, `^IBEX`, `^STOXX50E`, `^VIX`),
cripto (BTC-USD, ETH-USD) y materias primas (GC=F, CL=F, HG=F, DX-Y.NYB).

**Tests mínimos (los imprescindibles):**

1. `test_indicators.py` — valores de RSI/MACD/ATR contra casos conocidos y contra
   la librería `ta` con tolerancia 1e-6; comprobar que los primeros `n-1` valores
   son NaN.
2. `test_no_lookahead.py` — para cada indicador, alterar los datos posteriores a
   `t` y verificar que el valor en `t` no cambia. Es el test que más bugs caros evita.
3. `test_scoring.py` — z-scores con media 0/desv 1 por grupo; grupos pequeños caen
   al fallback; renormalización de pesos con factores ausentes suma 1;
   winsorización correcta.
4. `test_ingest_idempotent.py` — ejecutar el mismo lote dos veces no duplica filas
   ni cambia recuentos.
5. `test_providers_contract.py` — `FakeProvider` cumple el Protocol; ante
   `RateLimitError` el orquestador degrada sin abortar y registra `PARTIAL`.
6. `test_backtest.py` — con una serie sintética de tendencia conocida, una señal
   trivial debe dar el hit rate esperado analíticamente.
7. `test_config.py` — todos los YAML validan contra los modelos pydantic; los
   pesos de cada preset suman 1.

---

## 10. Riesgos y limitaciones

| Riesgo | Impacto | Mitigación |
|---|---|---|
| **yfinance es una API no oficial** que Yahoo puede romper en cualquier momento (ya ocurrió con el rediseño de febrero 2025) | El sistema deja de actualizarse | Capa `providers/` con Protocols; `StooqPriceProvider` como fallback desde fase 5; el warehouse conserva el histórico aunque la fuente caiga; `ingest_log` + alerta si no hay datos nuevos en 48 h |
| **Rate limiting / 429** — persiste incluso con `curl_cffi` e impersonación | Ingesta incompleta | Lotes, `threads=False`, presupuesto de peticiones, backoff, cola por antigüedad, ejecución nocturna. Nunca desde la UI |
| **Fundamentales europeos/IBEX pobres** en Yahoo (campos nulos, EV/EBITDA ausente, `payout` erróneo) | Scoring sesgado contra Europa | `completeness` + penalización `sqrt(coverage)` + exclusión de factores por debajo del umbral + icono visible en la tabla. Comparar solo dentro del sector y, opcionalmente, dentro de la región |
| **Snapshots fundamentales, no point-in-time** | Backtest fundamental inválido | Marcar los backtests como `technical_only` hasta acumular histórico propio; nunca presentar métricas fundamentales retroactivas como válidas |
| **Datos con retardo** (15-20 min o cierre) | No sirve para intradía | El proyecto se posiciona explícitamente como **swing/posición** (horizonte semanas-meses); mostrar el timestamp de los datos en toda página |
| **Survivorship bias** en el universo | Backtests optimistas | Registro diario de `universe_membership`; banner en la página 7 |
| **Sobreajuste** por probar muchas combinaciones de pesos | Falsa confianza | Walk-forward, out-of-sample destacado, contador de configuraciones probadas, exigir `n` y t-stat |
| **Divisas y horarios distintos** entre US/EU/cripto | Comparaciones sesgadas, breadth mal calculada | Calendario por mercado en `core/calendar.py`; breadth siempre por universo, nunca mezclando mercados; scoring sobre magnitudes adimensionales |
| **Cripto 24/7 vs bolsa 5 días** | Desalineación de series | Reindexar cripto a días hábiles al hacer correlaciones; nunca imputar precios de acciones en fin de semana |
| **Claves de API freemium** (Finnhub ~60/min, Marketaux ~100/día, Alpha Vantage ~25/día) y cambios frecuentes de límites | Sentimiento inestable | Todo lo dependiente de terceros es **opcional y degradable**: si falta la clave, el factor `sentiment` se excluye y los pesos se renormalizan. Ningún elemento del núcleo depende de una clave |
| **Riesgo de mal uso** | El más importante | Disclaimer en README, pie de todas las páginas y en cada exportación CSV. Lenguaje probabilístico obligatorio en toda la UI ("percentil", "históricamente", "en el X % de los casos"), prohibido "comprar"/"vender"/"predice". Las guardas de sensatez nunca se desactivan silenciosamente |

**Capa de abstracción de proveedor — contrato concreto** (`providers/base.py`):

```python
class PriceProvider(Protocol):
    name: str
    def fetch_ohlcv(self, tickers: list[str], start: date, end: date,
                    interval: str = "1d") -> pd.DataFrame: ...
        # cols fijas: ticker,date,open,high,low,close,adj_close,volume
    def fetch_actions(self, ticker: str) -> pd.DataFrame: ...
    def supports(self, ticker: str) -> bool: ...

class FundamentalsProvider(Protocol):
    def fetch_snapshot(self, tickers: list[str]) -> pd.DataFrame: ...   # esquema = fundamentals_snapshot
    def fetch_earnings(self, ticker: str) -> pd.DataFrame: ...
```

`providers/registry.py` lee `settings.yaml → providers.price: [yfinance, stooq]` y
encadena; cada llamada anota en `prices_daily.source` de dónde vino cada fila, para
poder auditar y purgar por proveedor. **Ninguna función fuera de `providers/` puede
importar `yfinance`** — verificarlo con un test de arquitectura
(`test_no_direct_yfinance_import`) que hace grep sobre el AST de `src/`.

---

## Ficheros críticos para la implementación

En orden de dependencia:

- `src/stocks_tracker/core/schema.sql` — el modelo de datos condiciona todo lo demás
- `src/stocks_tracker/providers/base.py` — contratos que aíslan el riesgo de yfinance
- `src/stocks_tracker/core/indicators.py` — núcleo de cálculo, sin I/O, 100 % testeable
- `src/stocks_tracker/core/scoring.py` — z-scores intra-sector, score compuesto y explicabilidad
- `config/factors.yaml` — catálogo y pesos, es la "configuración de producto" del sistema
- `src/stocks_tracker/app/main.py` — entrypoint de Streamlit con `st.navigation`

## Referencias

- [yfinance CHANGELOG](https://github.com/ranaroussi/yfinance/blob/main/CHANGELOG.rst)
- [yfinance rate limiting, issue #2411](https://github.com/ranaroussi/yfinance/issues/2411)
- [Why the yfinance library broke](https://deepcharts.substack.com/p/why-did-the-yfinance-python-library)
- [Streamlit — st.navigation](https://docs.streamlit.io/develop/api-reference/navigation/st.navigation)
- [ta (bukosabino)](https://github.com/bukosabino/ta) · [pandas-ta-classic](https://github.com/xgboosted/pandas-ta-classic)
- [FRED API](https://fred.stlouisfed.org/docs/api/api_key.html) · [fredapi](https://github.com/mortada/fredapi)
- [Marketaux](https://www.marketaux.com/)
