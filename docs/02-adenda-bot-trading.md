# Adenda 2 — Capa de trading automatizado (Alpaca, papel → real)

Amplía [`00-plan-general.md`](00-plan-general.md) y
[`01-adenda-tradingview-asistente.md`](01-adenda-tradingview-asistente.md).

**Dos hallazgos condicionan la arquitectura** y se desarrollan en §4 y §9:

1. Las órdenes fraccionadas/notional **deben ser órdenes simples** — Alpaca las
   rechaza dentro de bracket u OCO (error `42210000`), así que **los stops los
   gestiona nuestro bot, no el bróker**.
2. La **regla PDT de FINRA** limita a **3 day trades en 5 días hábiles** las cuentas
   por debajo de 25 000 $, lo que con 50 € es una restricción dura, no teórica.

---

## A. Índice de sustituciones y ampliaciones

| Sección | Estado | Qué cambia |
|---|---|---|
| §1 Arquitectura por capas | **Ampliada** | §1: capa L7 `trading/` sobre L5 (scoring) |
| §2 Estructura de directorios | **Ampliada** | §E: paquete `trading/` completo |
| §3 Modelo de datos | **Ampliado** | §3: 8 tablas nuevas |
| §5 / B.4 Diseño del dashboard | **Ampliado** | §7: página **9 "Bot de trading"** + entrada en `main.py` |
| §6 / D.4 Validación | **Ampliada** | §8: el backtest de fase 3 pasa a ser también la **puerta de acceso** al bot |
| §7 Alertas | **Ampliada** | §5: canal Telegram con **botones inline de aprobación** |
| §8 / G Fases | **Ampliado** | §11: fases 6-10, todas **posteriores a la fase 3** |
| §9 Dependencias / tests | **Ampliado** | §E, §I |
| §10 / H Riesgos | **Ampliado** | §10: tabla específica de trading |
| Todo lo demás | **Sin cambios** | — |

**Encuadre obligatorio**, a repetir en README, página 9 y `config/trading.yaml`:

> Herramienta **personal de experimentación**. El usuario opera **su propio dinero
> bajo su exclusiva responsabilidad**. Ninguna métrica pasada garantiza resultados
> futuros. Esto no es asesoramiento financiero ni fiscal.

---

## 1. Arquitectura del bot (capa L7)

```
L5 scoring (YA EXISTE)  ──►  factor_scores · factor_contributions · signals · regime_daily
                                        │  (única fuente de decisión; el bot NO recalcula nada)
                                        ▼
┌───────────────────────────────────────────────────────────────────────────┐
│ L7  trading/                                                              │
│                                                                           │
│  ① CONTEXTO      trading/context.py::build_context(date) → StrategyContext │
│                  (lectura de sólo lectura del warehouse + estado real)     │
│           ▼                                                               │
│  ② INTENCIÓN    trading/strategies/*  →  list[Intent]                     │
│                  "QUÉ y CUÁNTO". Ignora deliberadamente los límites.       │
│           ▼                                                               │
│  ③ RIESGO       trading/risk.py::RiskManager.evaluate(intents, ctx)        │
│                  → list[RiskVerdict(APPROVE | RESIZE | VETO, motivo)]      │
│                  ÚNICO punto de aplicación de límites. Falla cerrado.      │
│           ▼                                                               │
│  ④ APROBACIÓN   trading/approval.py  (modo semiautomático)                 │
│                  PENDING → APPROVED/REJECTED/EXPIRED  (UI o Telegram)      │
│           ▼                                                               │
│  ⑤ EJECUCIÓN    trading/execution.py → BrokerAdapter.submit_order()        │
│                  client_order_id determinista = idempotencia               │
│           ▼                                                               │
│  ⑥ CONCILIACIÓN trading/reconcile.py                                       │
│                  estado real del bróker vs estado esperado en BD           │
│                  Se ejecuta AL ARRANCAR y AL CERRAR cada ciclo             │
│                                                                           │
│  ⑦ KILL SWITCH  trading/killswitch.py — transversal, consultado en ①③⑤     │
└───────────────────────────────────────────────────────────────────────────┘
```

### Por qué el riesgo VETA en lugar de vivir dentro del generador de señales

Es la decisión estructural más importante de esta adenda:

1. **Punto único de aplicación.** Si los límites viven dentro de cada estrategia,
   añadir una cuarta estrategia significa reimplementar (y poder olvidar) el tope
   del 25 % por activo. Con el veto, **una estrategia nueva no puede saltarse un
   límite ni por error ni por descuido**: no tiene acceso al camino de ejecución.
2. **Fallo cerrado.** `RiskManager` devuelve `VETO` por defecto ante cualquier
   excepción, dato faltante o estado inconsistente. Un generador de señales que
   también controla el riesgo tiende a fallar abierto (si el cálculo del límite
   peta, la orden pasa).
3. **Auditabilidad.** Cada veto se escribe en `risk_violations` con la regla y los
   números. La pregunta "¿por qué no compró X el día Y?" tiene respuesta en SQL sin
   leer código.
4. **Testabilidad aislada.** `RiskManager` se prueba con carteras sintéticas y sin
   bróker ni estrategias. Es el módulo con cobertura obligatoria del 100 % de ramas.
5. **Separación de responsabilidades.** La estrategia responde "¿esto es una buena
   idea?"; el riesgo responde "¿puedo permitirme esta idea ahora mismo?". Son
   preguntas distintas con dueños distintos, y mezclarlas produce estrategias que se
   auto-censuran de forma opaca.
6. **`RESIZE` en vez de `VETO` cuando procede.** El riesgo puede recortar el tamaño
   (p. ej. de 15 € a 9 € para respetar el tope del 20 %) sin descartar la idea, y
   deja constancia del recorte. Sería imposible si el límite estuviese enterrado en
   la estrategia.

**Regla de arquitectura verificada por test**: `trading/execution.py` **solo** acepta
objetos `ApprovedOrder`, y `ApprovedOrder` **solo** puede construirse desde
`RiskManager` (constructor privado + factory). No hay ruta desde `Intent` a
`submit_order` que evite el riesgo.

---

## 2. Abstracción de bróker

### `trading/brokers/base.py`

```python
class BrokerMode(StrEnum):
    SIMULATED = "simulated"; PAPER = "paper"; LIVE = "live"

@dataclass(frozen=True)
class Account:
    account_id: str; currency: str; cash: float; equity: float
    buying_power: float; last_equity: float
    pattern_day_trader: bool; daytrade_count: int
    trading_blocked: bool; account_blocked: bool; shorting_enabled: bool

@dataclass(frozen=True)
class Position:
    symbol: str; qty: float; avg_entry_price: float; market_value: float
    unrealized_pl: float; unrealized_plpc: float; current_price: float

@dataclass(frozen=True)
class Order:
    broker_order_id: str; client_order_id: str; symbol: str; side: str
    qty: float | None; notional: float | None; order_type: str; tif: str
    status: str; filled_qty: float; filled_avg_price: float | None
    submitted_at: datetime; filled_at: datetime | None; reject_reason: str | None

@dataclass(frozen=True)
class Clock:
    timestamp: datetime; is_open: bool; next_open: datetime; next_close: datetime

class BrokerAdapter(Protocol):
    mode: BrokerMode
    name: str
    def get_account(self) -> Account: ...
    def get_positions(self) -> list[Position]: ...
    def get_position(self, symbol: str) -> Position | None: ...
    def get_orders(self, status: str = "open", after: datetime | None = None) -> list[Order]: ...
    def get_order_by_client_id(self, client_order_id: str) -> Order | None: ...
    def submit_order(self, req: OrderRequest) -> Order: ...
    def cancel_order(self, broker_order_id: str) -> None: ...
    def cancel_all_orders(self) -> int: ...
    def close_position(self, symbol: str, qty: float | None = None) -> Order: ...
    def close_all_positions(self, cancel_orders: bool = True) -> list[Order]: ...
    def get_clock(self) -> Clock: ...
    def get_latest_price(self, symbols: list[str]) -> dict[str, float]: ...
    def is_fractionable(self, symbol: str) -> bool: ...
    def supports(self, feature: str) -> bool: ...   # 'fractional','notional','bracket','shorting'

# Excepciones: BrokerError, BrokerAuthError, BrokerRejectedError,
#              BrokerRateLimitError, BrokerUnavailableError, InsufficientFundsError
```

### `trading/brokers/alpaca.py` — `AlpacaBroker`

- SDK `alpaca-py`: `TradingClient(key, secret, paper=True|False)`. **Paper y live se
  distinguen únicamente por `paper=` y por el par de claves**; ninguna otra
  diferencia de código (§10).
- Órdenes: `MarketOrderRequest(symbol=..., notional=..., side=OrderSide.BUY, time_in_force=TimeInForce.DAY, client_order_id=...)`.
  Se usa **`notional`** para las compras (más natural con 50 € y ~7 € por posición) y
  **`qty` fraccionaria** para las ventas parciales.
- **Restricciones que el adaptador debe conocer y exponer vía `supports()`**:
  `notional >= 1.0 USD`; las órdenes notional **no se pueden reemplazar** (`replace`
  es rechazado → siempre cancel+resubmit); fraccionadas y notional admiten
  market/limit/stop/stop-limit **solo con `time_in_force=DAY`**; **fraccionadas y
  notional deben ser órdenes simples**: nada de bracket ni OCO. Por eso
  `supports("bracket") is False` cuando la orden es fraccionada, y `sizing.py` lo
  consulta.
- Precios para vigilancia de stops: `get_latest_price()` usa el feed gratuito
  **IEX** de Alpaca (`StockLatestTradeRequest`), no yfinance. Es el único punto del
  sistema con datos casi en tiempo real, y solo se usa para stops (§9).
- Reintentos con `tenacity` ante `BrokerRateLimitError` (200 req/min en el plan
  gratuito). **Nunca reintentar `submit_order` sin comprobar antes por
  `client_order_id`.**

### `trading/brokers/simulated.py` — `SimulatedBroker`

- Ejecuta contra `prices_daily` sin red. Fills al **cierre siguiente** (`open` del
  día siguiente si se configura `fill_at: next_open`), con `slippage_bps` y
  `commission_bps` de `config/trading.yaml`.
- Mantiene cash, posiciones y equity en memoria y los vuelca a
  `portfolio_snapshots` con `mode='simulated'`.
- Implementa `get_clock()` desde `core/calendar.py`, y `daytrade_count` para poder
  **probar la regla PDT en el backtest**.
- Es lo que usa la fase 6 y la suite de tests: **cero llamadas de red en el CI**.

### `trading/brokers/registry.py`

```python
def get_broker(mode: BrokerMode) -> BrokerAdapter
    # SIMULATED → SimulatedBroker
    # PAPER     → AlpacaBroker(paper=True,  key=ALPACA_PAPER_KEY_ID, ...)
    # LIVE      → AlpacaBroker(paper=False, key=ALPACA_LIVE_KEY_ID, ...)
    #             exige además ALPACA_LIVE_CONFIRMED (§10) o lanza ConfigError
```

**Test de arquitectura obligatorio** (`tests/test_arch_broker_isolation.py`): recorre
el AST de todo `src/` y falla si `alpaca` aparece importado fuera de
`trading/brokers/alpaca.py`. Mismo patrón que el test que ya aísla `yfinance` en
`providers/`.

---

## 3. Modelo de datos (ampliación de `core/schema.sql`)

```sql
-- ============ ESTRATEGIAS Y CICLOS ============
CREATE TABLE IF NOT EXISTS strategies (
  strategy_id VARCHAR PRIMARY KEY,      -- 'momentum_multifactor_v1'
  name VARCHAR, version VARCHAR, enabled BOOLEAN DEFAULT FALSE,
  mode VARCHAR,                          -- simulated|paper|live
  params JSON,                           -- copia congelada del YAML al activar
  params_hash VARCHAR,                   -- detecta cambios de parámetros a posteriori
  activated_at TIMESTAMP, deactivated_at TIMESTAMP, note VARCHAR
);

CREATE TABLE IF NOT EXISTS bot_runs (
  run_id VARCHAR PRIMARY KEY,            -- ULID
  strategy_id VARCHAR, mode VARCHAR, phase VARCHAR,   -- propose|execute|monitor|reconcile|eod
  started_at TIMESTAMP, finished_at TIMESTAMP,
  status VARCHAR,                        -- OK|PARTIAL|FAILED|HALTED
  market_open BOOLEAN, equity_start DOUBLE, equity_end DOUBLE,
  n_intents INTEGER, n_approved INTEGER, n_submitted INTEGER, n_filled INTEGER,
  error VARCHAR
);

-- ============ INTENCIONES / PROPUESTAS ============
CREATE TABLE IF NOT EXISTS intents (
  intent_id VARCHAR PRIMARY KEY,         -- ULID
  run_id VARCHAR, strategy_id VARCHAR, created_at TIMESTAMP,
  ticker VARCHAR, tv_symbol VARCHAR, side VARCHAR,          -- buy|sell
  intent_type VARCHAR,                   -- open|add|trim|close|stop_exit|rebalance
  qty_requested DOUBLE, notional_requested DOUBLE,
  qty_approved DOUBLE, notional_approved DOUBLE,            -- tras RESIZE del riesgo
  ref_price DOUBLE,                      -- precio de referencia al proponer
  stop_price DOUBLE, stop_atr_mult DOUBLE,
  risk_amount DOUBLE,                    -- € en riesgo hasta el stop
  rationale JSON,                        -- {reasons:[], flags:[], contributions:[], signals:[]}
  score_pctile DOUBLE, regime VARCHAR,
  risk_verdict VARCHAR,                  -- APPROVE|RESIZE|VETO
  risk_notes JSON,                       -- reglas evaluadas y su holgura
  status VARCHAR,                        -- PENDING|APPROVED|REJECTED|EXPIRED|VETOED|SUBMITTED|FILLED|FAILED|SUPERSEDED
  expires_at TIMESTAMP,
  decided_by VARCHAR,                    -- 'user:ui'|'user:telegram'|'auto'|'system:expiry'
  decided_at TIMESTAMP, decision_note VARCHAR
);
CREATE INDEX IF NOT EXISTS idx_intents_status ON intents(status, expires_at);

-- ============ ÓRDENES Y EJECUCIONES ============
CREATE TABLE IF NOT EXISTS orders (
  client_order_id VARCHAR PRIMARY KEY,   -- DETERMINISTA: f"st-{intent_id}" → idempotencia
  broker_order_id VARCHAR, intent_id VARCHAR, run_id VARCHAR,
  mode VARCHAR, broker VARCHAR,
  ticker VARCHAR, side VARCHAR, order_type VARCHAR, tif VARCHAR,
  qty DOUBLE, notional DOUBLE, limit_price DOUBLE, stop_price DOUBLE,
  status VARCHAR,                        -- new|accepted|partially_filled|filled|canceled|rejected|expired
  filled_qty DOUBLE DEFAULT 0, filled_avg_price DOUBLE,
  submitted_at TIMESTAMP, updated_at TIMESTAMP, filled_at TIMESTAMP,
  reject_reason VARCHAR, raw JSON
);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(mode, status);

CREATE TABLE IF NOT EXISTS fills (
  fill_id VARCHAR PRIMARY KEY, client_order_id VARCHAR, broker_order_id VARCHAR,
  ticker VARCHAR, side VARCHAR, qty DOUBLE, price DOUBLE,
  filled_at TIMESTAMP, commission DOUBLE DEFAULT 0,
  slippage_bps DOUBLE,                   -- vs ref_price del intent → mide coste real
  mode VARCHAR
);

-- ============ ESTADO DE LA CARTERA ============
CREATE TABLE IF NOT EXISTS portfolio_snapshots (
  snapshot_at TIMESTAMP, mode VARCHAR, strategy_id VARCHAR,
  cash DOUBLE, equity DOUBLE, long_market_value DOUBLE, buying_power DOUBLE,
  n_positions INTEGER, gross_exposure_pct DOUBLE, daytrade_count INTEGER,
  peak_equity DOUBLE, drawdown_pct DOUBLE,
  pnl_day DOUBLE, pnl_total DOUBLE, benchmark_equity DOUBLE,
  positions JSON,                        -- foto completa para auditoría
  PRIMARY KEY (snapshot_at, mode)
);

CREATE TABLE IF NOT EXISTS bot_positions (   -- estado gestionado por NOSOTROS (stops sintéticos)
  ticker VARCHAR, mode VARCHAR, strategy_id VARCHAR,
  qty DOUBLE, avg_entry_price DOUBLE, opened_at TIMESTAMP,
  stop_price DOUBLE, stop_type VARCHAR,      -- atr_fixed|atr_trailing
  highest_close_since_entry DOUBLE,          -- para el trailing
  target_weight DOUBLE, entry_intent_id VARCHAR,
  max_hold_until DATE, last_reviewed_at TIMESTAMP,
  PRIMARY KEY (ticker, mode)
);

-- ============ AUDITORÍA DE DECISIONES ============
CREATE TABLE IF NOT EXISTS decision_log (
  decision_id VARCHAR PRIMARY KEY, run_id VARCHAR, logged_at TIMESTAMP,
  mode VARCHAR, strategy_id VARCHAR, ticker VARCHAR,
  decision VARCHAR,     -- PROPOSED|VETOED|RESIZED|APPROVED|REJECTED|EXPIRED|SUBMITTED|FILLED
                        -- |SKIPPED_NO_SIGNAL|SKIPPED_ALREADY_HELD|SKIPPED_PDT|HALTED
  reason_code VARCHAR,  -- enum estable, apto para filtrar en SQL
  reason_text VARCHAR,  -- frase legible generada por core/narrative.py
  context JSON          -- todo lo necesario para reconstruir la decisión
);
CREATE INDEX IF NOT EXISTS idx_decision_ticker_date ON decision_log(ticker, logged_at);

CREATE TABLE IF NOT EXISTS risk_violations (
  id VARCHAR PRIMARY KEY, logged_at TIMESTAMP, run_id VARCHAR, mode VARCHAR,
  rule_id VARCHAR,      -- 'max_position_pct','daily_loss','max_drawdown','pdt_limit'…
  severity VARCHAR,     -- info|warn|block|kill
  ticker VARCHAR, observed DOUBLE, limit_value DOUBLE, headroom DOUBLE,
  action_taken VARCHAR, -- resize|veto|halt_new|flatten
  detail JSON
);

CREATE TABLE IF NOT EXISTS bot_state (       -- una fila por modo; estado del kill switch
  mode VARCHAR PRIMARY KEY,
  state VARCHAR,        -- RUNNING|HALT_NEW|FLATTEN_PENDING|HALTED
  autonomy VARCHAR,     -- semi|auto
  halted_at TIMESTAMP, halt_rule VARCHAR, halt_detail VARCHAR,
  rearmed_at TIMESTAMP, rearmed_by VARCHAR, rearm_note VARCHAR,
  peak_equity DOUBLE, day_start_equity DOUBLE, day_start_date DATE,
  updated_at TIMESTAMP
);
```

**Invariante de auditoría**: cualquier ticker candidato de cualquier ciclo genera
**al menos una fila** en `decision_log`, incluidas las descartadas. La consulta
canónica que debe funcionar siempre:

```sql
SELECT logged_at, decision, reason_code, reason_text
FROM decision_log
WHERE ticker = ? AND logged_at::DATE = ? AND mode = ?
ORDER BY logged_at;
```

> La columna se llama `logged_at` y no `at` como decia la primera version de
> esta adenda: `at` es palabra reservada en DuckDB y habria obligado a
> entrecomillarla en cada consulta escrita a mano.

---

## 4. Gestión de riesgo — `trading/risk.py`

### `config/trading.yaml` (valores por defecto conservadores)

```yaml
mode: simulated                 # simulated | paper | live  (live exige ALPACA_LIVE_CONFIRMED)
autonomy: semi                  # semi | auto
base_currency: USD              # la cuenta de Alpaca es en USD; ver riesgo FX en §10
capital_cap: 55.0               # tope duro de capital a desplegar (≈50 €); el bot nunca usa más

universe:
  source: universe_membership
  allowed: [SP500, NASDAQ100]   # SOLO EE.UU.
  require_fractionable: true
  min_price: 5.0                # evita chicharros
  min_dollar_volume_20d: 20_000_000
  exclude_asset_classes: [crypto, commodity, fx]

risk:
  # --- dimensionamiento ---
  risk_per_trade_pct: 1.5       # % de la equity arriesgado hasta el stop
  atr_stop_mult: 2.5            # stop = entrada - 2.5 x ATR14
  atr_trailing: true
  min_notional: 1.0             # mínimo de Alpaca
  max_position_pct: 22.0        # tope por activo (dentro del 20-25 % del mandato)
  target_position_pct: 15.0     # objetivo de reparto con 6-7 posiciones
  # --- concentración ---
  max_positions: 7              # mandato: 5-8
  min_positions_for_diversification: 4   # aviso si baja de aquí con exposición alta
  max_sector_pct: 35.0          # exposición máxima por sector GICS
  max_gross_exposure_pct: 90.0
  min_cash_pct: 10.0            # efectivo mínimo permanente
  # --- pérdidas ---
  max_daily_loss_pct: 3.0       # sobre la equity de apertura del día → HALT_NEW
  max_drawdown_pct: 15.0        # sobre el máximo histórico de equity → KILL SWITCH
  drawdown_warn_pct: 10.0       # aviso previo
  # --- actividad ---
  max_orders_per_day: 6         # anti-bucle
  max_orders_per_ticker_per_day: 1
  max_new_positions_per_day: 3
  max_day_trades_5d: 2          # margen sobre el límite PDT de 3; ver §10
  min_holding_days: 2           # evita day trades por construcción
  # --- prohibiciones absolutas (no configurables a true) ---
  allow_shorting: false
  allow_leverage: false
  allow_options: false
  allow_extended_hours: false
  # --- eventos ---
  block_days_before_earnings: 3
  block_days_after_earnings: 1
  block_if_evidence_not_validated: true   # solo señales con evidence='validada' (fase 3)
  min_data_coverage: 0.5
  max_data_staleness_hours: 30            # si los precios son más viejos, no se opera

execution:
  order_type: market
  time_in_force: day
  use_notional_for_buys: true
  no_trade_window_open_min: 20    # no operar los primeros 20 min tras la apertura
  no_trade_window_close_min: 15   # ni los últimos 15 min
  max_price_drift_pct: 2.0        # si el precio se ha movido más desde la propuesta → caduca
  slippage_bps_assumed: 15        # para el simulador y para el sizing conservador
  commission_bps: 0               # Alpaca sin comisiones

approval:
  intent_ttl_hours: 18            # una propuesta de anoche caduca antes de la apertura siguiente
  require_pin: true
  channels: [ui, telegram]

kill_switch:
  on_daily_loss: halt_new         # deja de abrir; mantiene stops vigentes
  on_max_drawdown: flatten        # liquida TODO y queda HALTED
  on_reconcile_mismatch: halt_new
  on_repeated_rejects: halt_new   # 3 rechazos del bróker en un mismo ciclo
  on_stale_data: halt_new
  rearm: manual_only              # NUNCA automático
```

### Reglas duras (`RiskManager`), en orden de evaluación

`evaluate(intents, ctx) -> list[RiskVerdict]`. **Las reglas de nivel cuenta se
evalúan antes que las de nivel intención**; un `kill` corta el ciclo entero.

| # | `rule_id` | Nivel | Acción |
|---|---|---|---|
| 1 | `kill_switch_active` | cuenta | `VETO` de todo si `bot_state.state != RUNNING` |
| 2 | `stale_data` | cuenta | `VETO` global si el último `prices_daily.date` supera `max_data_staleness_hours` |
| 3 | `max_drawdown` | cuenta | `equity < peak_equity × (1 − 15 %)` → **KILL SWITCH: `flatten`** |
| 4 | `daily_loss` | cuenta | `equity < day_start_equity × (1 − 3 %)` → `HALT_NEW` |
| 5 | `broker_blocked` | cuenta | `account.trading_blocked` o `account_blocked` → `VETO` global |
| 6 | `pdt_limit` | cuenta | `daytrade_count >= max_day_trades_5d` → `VETO` de todo cierre que crearía day trade |
| 7 | `max_orders_per_day` | cuenta | `VETO` de los excedentes (prioridad por `score_pctile`) |
| 8 | `min_cash` | cuenta | `RESIZE` hasta respetar el 10 % de efectivo; si no cabe, `VETO` |
| 9 | `max_gross_exposure` | cuenta | `RESIZE` / `VETO` |
| 10 | `max_positions` | cuenta | `VETO` de aperturas si ya hay 7 posiciones |
| 11 | `max_sector_pct` | cartera | `RESIZE` / `VETO` si el sector supera el 35 % tras la orden |
| 12 | `max_position_pct` | intención | `RESIZE` al 22 % |
| 13 | `position_sizing_atr` | intención | Recalcula el tamaño (fórmula abajo) y `RESIZE` |
| 14 | `min_notional` | intención | `VETO` si el tamaño resultante baja de 1 $ |
| 15 | `earnings_blackout` | intención | `VETO` si hay resultados en la ventana ±3/+1 días |
| 16 | `min_holding_days` | intención | `VETO` de cierres antes de 2 sesiones (salvo `stop_exit`, que siempre pasa) |
| 17 | `evidence_gate` | intención | `VETO` si la señal que la origina no está `validada` |
| 18 | `universe_gate` | intención | `VETO` si el ticker no es fraccionable, es barato o ilíquido |
| 19 | `no_short_no_leverage` | intención | `VETO` si `qty` resultante < 0 o si la compra excede `buying_power` sin margen |
| 20 | `duplicate_intent` | intención | `VETO` si ya hay una orden del día para ese ticker |

**Dimensionamiento por volatilidad** (`trading/sizing.py::size_by_atr`):

```
riesgo_€        = equity × risk_per_trade_pct / 100
distancia_stop  = atr_stop_mult × ATR14
qty_teórica     = riesgo_€ / distancia_stop
notional        = min(qty_teórica × precio,
                      equity × max_position_pct / 100,
                      equity × target_position_pct / 100 × factor_régimen,
                      cash_disponible − reserva_min_cash)
```

`factor_régimen` sale de `regime_daily.regime`: `risk_on` 1,0 · `neutral` 0,8 ·
`risk_off` 0,5. Con 50 € y un ATR% típico del 2,5 %, esto produce posiciones de
8-12 €, es decir 4-6 posiciones — coherente con el mandato.

**Excepción de escala**: con 50 € el sizing por ATR puede dar importes por debajo de
1 $. `size_by_atr` aplica entonces `notional = max(calculado, min_notional)` **solo
si** eso no rompe `max_position_pct`; si lo rompe, `VETO` con
`reason_code='POSITION_TOO_SMALL_FOR_RISK'`. Nunca se relaja un límite para que
quepa una orden.

### Kill switch — comportamiento exacto (`trading/killswitch.py`)

| Estado | Qué hace | Qué NO hace |
|---|---|---|
| `RUNNING` | Operativa normal | — |
| `HALT_NEW` | **Deja de abrir y de ampliar.** Sigue vigilando stops y **sí ejecuta cierres de protección**. Cancela las órdenes de compra abiertas y caduca todas las propuestas `PENDING` | No liquida |
| `HALTED` (tras `FLATTEN`) | **Liquida todo** (`close_all_positions(cancel_orders=True)`), cancela cuanto haya abierto y **no vuelve a operar en absoluto** | No se rearma solo |

Disparadores → acción, según `kill_switch:` del YAML: pérdida diaria → `HALT_NEW`
(se rearma **automáticamente** al día siguiente **solo** este caso, porque el límite
es por definición diario, y queda registrado); **caída máxima del 15 % → `FLATTEN` +
`HALTED`**; discrepancia de conciliación → `HALT_NEW`; 3 rechazos seguidos →
`HALT_NEW`; datos obsoletos → `HALT_NEW`.

**Rearme — intervención humana explícita, nunca automática:**

1. Solo por CLI:
   `python -m stocks_tracker.trading.killswitch rearm --mode paper --confirm "REARMAR BOT PAPER" --note "..."`.
   La frase de confirmación **incluye el modo** y debe teclearse íntegra.
2. El modo `live` exige además la variable `ALPACA_LIVE_CONFIRMED` presente en el
   entorno de esa invocación.
3. La UI **no puede rearmar**: la página 9 muestra el estado y las instrucciones, con
   un botón que solo copia el comando al portapapeles. Asimetría deliberada:
   **parar es fácil y accesible desde cualquier sitio; volver a arrancar es incómodo
   y requiere consola.**
4. `rearm` exige que hayan pasado ≥ `cooldown_hours: 12` desde el `halted_at` y
   escribe `rearmed_at/by/note` en `bot_state`. El scheduler **nunca** invoca
   `rearm` — test que verifica que `daily_update.sh` no lo contiene.
5. Al rearmar tras `FLATTEN`, `peak_equity` se **reinicia a la equity actual** (si no,
   el bot quedaría permanentemente en drawdown y se auto-mataría en el primer ciclo).

---

## 5. Flujo semiautomático

### Ciclo de propuesta (`trading/run_bot.py --phase propose`, tras el cierre US)

1. `reconcile.run()` — estado real vs BD; discrepancia → `HALT_NEW`.
2. `context.build_context(date)` — lee `factor_scores`, `factor_contributions`,
   `signals` (solo `evidence='validada'`), `indicators_daily`, `regime_daily`,
   `instruments`, `bot_positions`, `Account`, `Position[]`.
3. Cada estrategia activa devuelve `list[Intent]`.
4. `RiskManager.evaluate()` → `APPROVE` / `RESIZE` / `VETO`; todo a `decision_log` y
   los vetos a `risk_violations`.
5. Las supervivientes se guardan con `status='PENDING'` y `expires_at = now + 18 h`.
6. `approval.notify_pending()` → Telegram + registro para la UI.

### Tarjeta de propuesta

UI y Telegram comparten el texto, generado por
`trading/rationale.py::render_intent(intent) -> IntentCard`:

```
┌─ PROPUESTA · caduca en 14 h 20 min ──────────────────────┐
│ COMPRAR  NASDAQ:AMAT · Applied Materials                 │
│ 8,40 $ (notional) ≈ 0,0412 acciones a 203,88 $           │
│ Peso objetivo 16,8 % de la cartera                       │
│                                                          │
│ POR QUÉ                                                  │
│  · Percentil 94 de score en Information Technology       │
│  · Corrección en tendencia alcista (RSI 36, sobre MM200) │
│  · Momentum 12-1 +31 %, entre los mejores de su sector   │
│  · PER 18,2 frente a la mediana sectorial (24,1)         │
│  · Señal PULLBACK_IN_UPTREND — evidencia: validada       │
│ A VIGILAR                                                │
│  · Volatilidad 252d en el decil alto del sector          │
│                                                          │
│ RIESGO                                                   │
│  Stop 2,5 × ATR14 → 191,45 $ (−6,1 %)                    │
│  Pérdida máxima estimada: 0,51 $ (1,0 % de la equity)    │
│  Régimen actual: neutral (score +8)                      │
│                                                          │
│ CONSUME                                                  │
│  Posiciones      4/7   ▓▓▓▓▓▓░░░░                        │
│  Sector Tech     28%/35%  ▓▓▓▓▓▓▓▓░░                     │
│  Efectivo tras   21%  (mín. 10%)  ▓▓▓░░░░░░░             │
│  Órdenes hoy     2/6                                     │
│                                                          │
│  [ Aprobar ]  [ Rechazar ]  [ Ver ficha ]                │
└──────────────────────────────────────────────────────────┘
```

Los "POR QUÉ" **reutilizan `core/explain.py::build_reasons()` y
`config/explanations.yaml`** de la adenda 1 — sin duplicar lógica ni plantillas.

### Aprobación en la UI

`st.container(border=True)` por propuesta, botones `Aprobar`/`Rechazar`, campo PIN
(`TRADING_ACTION_PIN`) si `approval.require_pin`, y confirmación en dos pasos para el
modo `live` (`st.checkbox("Entiendo que es dinero real")` + botón). Escribe
`intents.status`, `decided_by='user:ui'`, `decided_at`.

### Aprobación por Telegram — `trading/telegram_approval.py`

- Worker de **long polling** (`getUpdates`), porque no hay URL pública para webhooks.
  Se lanza junto al bot o como servicio aparte.
- Mensaje con `reply_markup` de botones inline;
  `callback_data = f"{action}:{intent_id_short}:{hmac_sig[:10]}"` firmado con
  HMAC-SHA256 sobre `TELEGRAM_CALLBACK_SECRET`. **Firma obligatoria**: sin ella,
  cualquiera que adivine un `intent_id` podría aprobar órdenes.
- Verifica que `chat_id` coincide con `TELEGRAM_CHAT_ID` autorizado; ignora todo lo
  demás.
- Al pulsar: valida firma, comprueba `status='PENDING'` y `expires_at > now`, escribe
  la decisión, **edita el mensaje** para reflejar el resultado (evita doble
  pulsación) y responde con `answerCallbackQuery`.
- El modo `live` **no admite aprobación por Telegram**: exige la UI con PIN.
  Restricción deliberada.

### Caducidad y deriva de precio

- `expires_at = created_at + 18 h`. `approval.expire_stale()` corre al inicio de cada
  ciclo y marca `EXPIRED`.
- **Además**, en el momento de ejecutar, `execution.py` recomprueba el precio con
  `get_latest_price()`: si `|precio_actual/ref_price − 1| > max_price_drift_pct`
  (2 %), la propuesta pasa a `EXPIRED` con `reason_code='PRICE_DRIFT'` y se **vuelve
  a proponer** en el ciclo siguiente con precio y stop recalculados. Una propuesta de
  ayer nunca se ejecuta hoy a otro precio.
- Una propuesta `PENDING` para un ticker que reaparece en un ciclo nuevo se marca
  `SUPERSEDED` y se sustituye por la nueva.

### Promoción a modo autónomo (`trading/promotion.py::check_autonomy_gate()`)

Todos simultáneamente, evaluados sobre el histórico de `intents` y `decision_log` en
modo paper:

1. ≥ **60 sesiones** de mercado en modo semiautomático.
2. ≥ **40 propuestas** generadas.
3. **≥ 90 %** de las propuestas presentadas fueron **aprobadas** por el usuario
   (mide que el bot propone cosas sensatas).
4. **0** propuestas aprobadas que el riesgo debiera haber vetado (auditoría posterior).
5. **0** discrepancias de conciliación sin resolver.
6. Drawdown máximo en paper **≤ 10 %**.
7. **0** incidencias críticas (excepción no controlada en `run_bot.py`) en los
   últimos 30 días.

La UI muestra un panel de progreso con estos 7 criterios. **El paso a
`autonomy: auto` sigue requiriendo un comando CLI explícito con frase de
confirmación**; el gate solo lo habilita, no lo activa. En modo autónomo se mantienen
todos los límites y el kill switch, y las propuestas se siguen registrando en
`intents` con `decided_by='auto'` — la trazabilidad no se pierde.

### Paso a dinero real

Requiere: (a) superar los umbrales de §8, (b) editar `mode: live` en el YAML,
(c) exportar claves `ALPACA_LIVE_*`, (d) exportar
`ALPACA_LIVE_CONFIRMED=ENTIENDO_QUE_ES_DINERO_REAL`, (e) confirmación de dos pasos en
la UI la primera vez. **Faltando cualquiera de las cinco, el registry lanza
`ConfigError` y el bot no arranca.** Nunca hay un cambio silencioso de paper a real.

---

## 6. Estrategias

Todas implementan `trading/strategies/base.py::Strategy` y **solo leen del
`StrategyContext`**; ninguna toca la BD, la red ni el bróker.

```python
class Strategy(Protocol):
    strategy_id: str
    def propose(self, ctx: StrategyContext) -> list[Intent]: ...
    def should_run_today(self, ctx: StrategyContext) -> bool: ...
```

### 6.1 `momentum_multifactor_v1` — núcleo, rebalanceo semanal

- **Cuándo**: primer día hábil de la semana (`should_run_today`), o si hay hueco de
  posiciones.
- **Universo**: S&P 500 ∩ `universe_gate` del riesgo.
- **Ranking**: `factor_scores` con `weights_hash` del preset `momentum` (momentum
  0,35 · quality 0,25 · value 0,15 · lowvol 0,15 · dividend 0,10),
  `peer_group = gics_sector`.
- **Entrada**: los `N = max_positions` mejores por `composite_pctile` que además
  cumplan `above_sma200 = TRUE`, `rsi14 < 75`, `coverage ≥ 0,5`. Peso objetivo
  equiponderado ajustado por ATR (§4).
- **Salida**: el valor cae por debajo del **percentil 60** (banda de histéresis: se
  entra en el top N pero solo se sale por debajo de p60 → reduce la rotación y el
  consumo de `max_orders_per_day`), o pierde la MM200, o toca el stop.
- **Stop**: `entrada − 2,5 × ATR14`, **trailing** sobre `highest_close_since_entry`.
- **Rebalanceo**: solo si la desviación del peso objetivo supera 5 pp (banda muerta
  que evita microoperaciones caras en tiempo y en PDT).

### 6.2 `pullback_uptrend_v1` — oportunista, dirigida por eventos

- **Cuándo**: cualquier día con señal.
- **Entrada**: `signals.signal_id = 'PULLBACK_IN_UPTREND'` **y** `evidence='validada'`
  **y** `composite_pctile > 0,60` **y** `regime != 'risk_off'`.
- **Salida**: `rsi14 > 65` (objetivo alcanzado), o stop de `2,0 × ATR14` (más ceñido
  que la estrategia de momentum, porque la tesis es de corto plazo), o
  **`max_hold_until = entrada + 30 sesiones`** (si la tesis no funciona en 30
  sesiones, se cierra: evita "posiciones zombi").
- **Tamaño**: la mitad del objetivo estándar (`target_position_pct × 0,5`), por ser
  una apuesta táctica.
- Máximo **2 posiciones simultáneas** de esta estrategia.

### 6.3 `defensive_regime_v1` — overlay, no estrategia independiente

Se ejecuta **después** de las otras dos y modifica sus intenciones. Lee
`regime_daily`:

| `regime` | Efecto |
|---|---|
| `risk_on` (score > +30) | Sin cambios; `factor_régimen = 1,0` |
| `neutral` | `factor_régimen = 0,8`; exige `quality_z > −0,5` en toda entrada nueva |
| `risk_off` (score < −30) | `factor_régimen = 0,5`; **prohíbe abrir posiciones nuevas** salvo que `lowvol_z > 0,5` y `quality_z > 0,5`; sube `min_cash_pct` al 30 %; propone **recortar a la mitad** las posiciones con `lowvol_z < −1` |
| Transición a `risk_off` **dos días seguidos** | Propone `trim` del 50 % de la exposición total, repartido (una intención por posición, todas sujetas al riesgo y a la aprobación del usuario) |

**Nunca liquida por sí mismo**: reducir es una propuesta como cualquier otra. La
liquidación total es competencia exclusiva del kill switch.

**Conexión con lo existente**: `factor_scores` → ranking y filtros;
`factor_contributions` → justificación de la tarjeta; `signals` + `evidence` →
disparadores; `indicators_daily.atr14 / rsi14 / above_sma200` → stops y filtros;
`regime_daily` → overlay defensivo. **El bot no calcula ni un indicador propio.**

---

## 7. Página 9 del dashboard — "Bot de trading"

Fichero `app/pages/9_bot_trading.py`. Se registra en `st.navigation` en una sección
propia **"Bot"** (última, separada visualmente del resto).

**Franja superior fija, siempre visible** (`st.container` con fondo de color según
modo):

| Modo | Color | Texto |
|---|---|---|
| `simulated` | gris | `SIMULACIÓN — sin bróker, sin dinero` |
| `paper` | **azul** | `PAPER TRADING — dinero ficticio de Alpaca` |
| `live` | **rojo intenso, con icono** | `DINERO REAL — capital desplegado: 47,20 €` |

Junto a la franja, **botón KILL SWITCH grande, rojo, siempre en pantalla** (dentro de
un `st.container` fijo en la cabecera, no al final de la página): `⛔ PARAR TODO`, que
abre un `st.dialog` con dos opciones — `Dejar de abrir posiciones (HALT_NEW)` y
`Liquidar todo y parar (FLATTEN)` — cada una con confirmación por PIN. Debe funcionar
aunque el resto de la página falle: **se renderiza antes que cualquier consulta** y va
envuelto en su propio `try/except`.

**Secciones (de arriba abajo):**

1. **Estado de la cuenta** — `st.metric` × 5: equity, PnL del día, PnL total,
   efectivo, nº de posiciones. Más `daytrade_count` (con aviso si ≥2) y la hora del
   último ciclo.
2. **Curva de equity vs benchmark** — **nivel 2**
   (`lwc.equity_chart({"Bot": ..., "SPY": ..., "Equiponderado": ...})`) con
   **marcadores** de compras/ventas y **líneas de precio** en los umbrales de pérdida
   diaria y de drawdown máximo. Ningún widget de TradingView puede mostrar esto: son
   nuestros datos de cuenta.
3. **Propuestas pendientes** — tarjetas de §5, ordenadas por caducidad, con cuenta
   atrás. Si no hay, mensaje explícito: *"Sin propuestas. Último ciclo: hoy 22:41.
   3 candidatos descartados — ver registro de decisiones."*
4. **Posiciones abiertas** — tabla con ticker, peso, precio medio, actual, PnL %,
   **stop vigente y distancia al stop** (`ProgressColumn` inverso), riesgo vivo en €,
   días en cartera, estrategia de origen. Cada fila expandible con un **mini-gráfico
   de nivel 2** (velas 3 meses + marcador de entrada + `createPriceLine` del stop).
5. **Estado de los límites de riesgo** — **nivel 3 (Plotly)**: barras horizontales de
   consumo para posiciones, exposición bruta, exposición por sector, efectivo,
   pérdida diaria, drawdown, órdenes del día, day trades. Verde <60 %, ámbar 60-85 %,
   rojo >85 %. Es el panel que responde de un vistazo a "¿qué me está frenando?".
6. **Historial de operaciones** — `fills` unido a `intents`: fecha, ticker, lado,
   importe, precio, **slippage real en pb**, y la justificación original desplegable.
   Exportable a CSV.
7. **Rendimiento de la estrategia** — métricas frente al benchmark reutilizando
   `backtest/metrics.py` (mismas funciones, sin duplicar): retorno total, Sharpe, max
   DD, hit rate, nº operaciones, retorno medio por operación, exposición media. **Con
   `n` siempre visible** y el aviso de §10 sobre la insignificancia estadística de
   50 €.
8. **Registro de decisiones** — buscador por ticker y fecha sobre `decision_log`, con
   filtro por `reason_code`. Responde a "¿por qué no compraste NVDA el martes?".
9. **Progreso hacia el modo autónomo / hacia real** — los 7 criterios de §5 y los
   umbrales de §8 como checklist con su estado.

**Aplicación de la regla de gráficos de la adenda 1**: en esta página **no hay widgets
de TradingView** — todo lo que se muestra son datos de nuestra cuenta y de nuestras
decisiones, que ningún widget puede representar. Nivel 2 para equity y precios con
marcadores; nivel 3 para barras de consumo, atribución de PnL y donuts de exposición.
(El Ticker Tape global de la cabecera sigue estando, por ser global.)

---

## 8. Simulación y validación previa

Tres puertas encadenadas. **No se puede saltar ninguna.**

### Puerta 1 — Backtest con costes (reutiliza el motor de la fase 3)

`backtest/run_backtest.py --strategy momentum_multifactor_v1 --broker simulated`
ejecuta la estrategia **completa** (con `RiskManager` activo) contra
`SimulatedBroker`, aplicando `commission_bps` y `slippage_bps_assumed: 15`, con
walk-forward 3+1 años.

**Umbrales que debe superar, todos out-of-sample:**

| Métrica | Umbral |
|---|---|
| Periodo cubierto | ≥ 5 años, ≥ 3 pliegues walk-forward |
| Operaciones | ≥ 100 |
| Sharpe OOS | ≥ 0,50 |
| Max drawdown OOS | ≤ 20 % |
| Expectativa por operación | > 0 **después** de costes y slippage |
| Ventanas móviles de 12 meses positivas | ≥ 55 % |
| Retorno OOS frente al benchmark equiponderado | ≥ el del benchmark, o inferior **con menos de la mitad de su drawdown** |
| Estabilidad | Ningún año concentra > 60 % del resultado total |
| Robustez a parámetros | Sharpe ≥ 0,35 al variar `atr_stop_mult` y `max_positions` ±25 % |

Si falla, **la estrategia no se activa**; se ajusta o se descarta. El resultado se
guarda en `strategies.params` + un informe.

### Puerta 2 — Paper trading con seguimiento

`mode: paper`, `autonomy: semi`. **Duración mínima: 60 sesiones de mercado (≈3 meses
naturales)** y **≥ 30 operaciones cerradas**. Requisitos de salida:

- Los 7 criterios de autonomía de §5.
- **Desviación del backtest acotada**: el Sharpe realizado en paper no debe ser
  inferior en más de 0,4 al esperado, ni el drawdown superior en más de 1,5×. Una
  divergencia mayor indica un bug o sobreajuste, no mala suerte.
- **Slippage real medido** (`fills.slippage_bps`) ≤ 2 × el asumido en el backtest. Si
  es mayor, se recalibra el backtest y **se vuelve a la puerta 1**.
- 0 excepciones no controladas, 0 discrepancias de conciliación abiertas.

### Puerta 3 — Dinero real, con tope

Solo tras superar la puerta 2. `capital_cap: 55.0`, **sin ampliar durante los primeros
60 días**, en modo `semi` (no autónomo) durante al menos 30 sesiones adicionales
aunque el gate de autonomía esté superado en paper. El paso de paper a real
**reinicia** el contador de autonomía: son entornos distintos.

**Advertencia que la UI debe mostrar en la puerta 3, textualmente**: *"30 operaciones
sobre 50 € no permiten concluir nada estadísticamente. Superar estas puertas reduce la
probabilidad de un error de programación o de diseño evidente; no dice nada sobre la
rentabilidad futura."*

---

## 9. Operación

### Programación (`scripts/trading_cycles.sh`, invocado por cron)

| Ciclo | Hora (CET) | `--phase` | Qué hace |
|---|---|---|---|
| Conciliación de apertura | 15:00 | `reconcile` | Estado real vs BD antes de que abra el mercado (15:30 CET) |
| Ejecución | **15:55** | `execute` | ≥20 min tras la apertura US. Ejecuta las propuestas `APPROVED` no caducadas |
| Vigilancia de stops | 16:00-21:45, **cada 15 min** | `monitor` | `get_latest_price()` (feed IEX de Alpaca); si `precio ≤ stop_price`, emite intención `stop_exit` que **el riesgo aprueba siempre** y se ejecuta sin aprobación humana (un stop no se somete a votación) |
| Cierre | 21:45 | `eod` | Cancela órdenes DAY vivas, snapshot de cartera, actualiza `peak_equity` y trailing stops |
| Propuesta | 23:15 (tras ingest+compute) | `propose` | Genera las propuestas del día siguiente y notifica |

Todos los ciclos empiezan consultando **`broker.get_clock()`**: si `is_open` es falso
(festivo, cierre anticipado), el ciclo registra `SKIPPED_MARKET_CLOSED` y termina.
**Nunca se confía en el reloj local ni en un calendario propio para decidir si
operar.** Las ventanas `no_trade_window_open_min` / `close_min` se calculan sobre
`clock.next_open` / `clock.next_close`, lo que maneja automáticamente los cierres
anticipados.

`flock` sobre `/tmp/stocks_tracker_bot.lock` impide solapes entre ciclos.

### Idempotencia y caídas a medio camino

- **`client_order_id = f"st-{mode[0]}-{intent_id}"`**, determinista y único por
  intención. Si el proceso muere entre `submit_order` y el `INSERT` en `orders`, al
  reintentar se llama primero a `get_order_by_client_id()`: si existe, se adopta el
  estado real en lugar de reenviar. **Nunca se envía una orden sin esta comprobación
  previa.**
- `reconcile.run()` **al arrancar todo ciclo**: compara `broker.get_positions()` con
  `bot_positions` y `broker.get_orders()` con `orders`. Cualquier posición
  desconocida, cantidad divergente (> 0,001 acciones) u orden huérfana → fila en
  `risk_violations` con `severity='block'` y `HALT_NEW`. **Manda siempre el bróker**:
  la BD se corrige con la realidad, nunca al revés.
- Si al conciliar aparece una posición que el bot no abrió (operación manual del
  usuario en la misma cuenta), el bot **no la toca** (no le pone stop ni la vende)
  pero la cuenta para los límites de exposición, y avisa. Recomendación en el README:
  usar una cuenta dedicada.

### Órdenes parciales, rechazadas y caducadas

- **Parcial**: se registra `filled_qty` real y `bot_positions.qty` se ajusta a lo
  realmente ejecutado. Con `TIF=DAY` el resto se cancela al cierre; **el remanente no
  se reenvía automáticamente** — se propone de nuevo en el ciclo siguiente pasando
  otra vez por el riesgo. El stop se calcula sobre el `filled_avg_price` real.
- **Rechazada**: `orders.status='rejected'` + `reject_reason`, intención a `FAILED`,
  entrada en `decision_log` y alerta. Tres rechazos en un ciclo → `HALT_NEW`. Los
  códigos frecuentes (`insufficient buying power`, `asset not fractionable`,
  `42210000 fractional must be simple order`) se mapean a `reason_code` estables en
  `brokers/alpaca.py::REJECT_REASON_MAP` para poder filtrarlos en SQL.
- **Caducada sin ejecutar**: `EXPIRED`, sin efectos secundarios.

### Registro

Todo ciclo escribe `bot_runs`; toda decisión, `decision_log`; toda orden y fill, sus
tablas; log estructurado JSON en `data/logs/bot_{mode}_{date}.jsonl` con `run_id` en
cada línea. **Los logs no contienen nunca claves ni secretos** — test que verifica que
el formateador redacta cualquier valor cuyo nombre de variable contenga `key`,
`secret`, `token` o `pin`.

---

## 10. Seguridad y riesgos

### Gestión de credenciales

- **Solo variables de entorno.** `ALPACA_PAPER_KEY_ID`, `ALPACA_PAPER_SECRET_KEY`,
  `ALPACA_LIVE_KEY_ID`, `ALPACA_LIVE_SECRET_KEY`, `ALPACA_LIVE_CONFIRMED`,
  `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_CALLBACK_SECRET`,
  `TRADING_ACTION_PIN`.
- **Ninguna clave en YAML ni en el repo.** `.env` en `.gitignore` desde el commit 1;
  `.env.example` solo con nombres. **Test `test_no_secrets_in_repo.py`** con patrones
  (`PK[A-Z0-9]{18}`, `AK[A-Z0-9]{18}`, `bot\d{8,}:[A-Za-z0-9_-]{30,}`) sobre todo el
  árbol excepto `.env*`.
- **Separación tajante paper/live**: nombres de variable distintos, cargados por
  *rutas de código distintas* en `registry.py`. `AlpacaBroker.__init__` **verifica**
  que la URL base resultante coincide con el modo pedido y lanza si no (defensa contra
  un `paper=` mal pasado).
- Claves de Alpaca con **permisos de trading únicamente**. Nota honesta: la API de
  Alpaca **no permite retirar fondos**, así que una clave filtrada no puede vaciar la
  cuenta a un tercero — pero **sí puede operar hasta arruinarla**. Rotación de claves
  recomendada cada 90 días y tras cualquier sospecha.

### Exposición del dashboard

- Streamlit **no tiene autenticación**. Regla: `--server.address 127.0.0.1`
  **siempre**; jamás `0.0.0.0`. Documentado en el README y forzado en el `Makefile`
  (`make run` incluye la bandera).
- Para acceso remoto: Tailscale/WireGuard o proxy inverso con autenticación. **Nunca
  exponer el puerto directamente.**
- **Todas las acciones con efecto** (aprobar, rechazar, kill switch, cambio de modo)
  exigen `TRADING_ACTION_PIN`, con límite de 5 intentos por sesión y registro de cada
  intento fallido en `decision_log`. Si `TRADING_ACTION_PIN` no está definido y
  `mode != simulated`, la página 9 se renderiza en **solo lectura** con un aviso.
- El botón de kill switch está disponible **sin PIN para `HALT_NEW`** (parar debe ser
  lo más fácil posible) y **con PIN para `FLATTEN`** (liquidar es destructivo).
- Toda acción registra `source` (`ui`/`telegram`) y marca temporal.

### Tabla de riesgos específicos del bot

| Riesgo | Por qué duele | Mitigación |
|---|---|---|
| **Bug de signo** (comprar donde debía vender, stop por encima del precio) | Pérdida directa y silenciosa | `Intent` con `side` como `StrEnum`, nunca booleano ni entero. Invariantes verificadas en `RiskManager`: compra ⇒ `stop_price < ref_price`; venta ⇒ `qty > 0` y `qty ≤ posición`. Tests de propiedad con `hypothesis` sobre `sizing.py`. `SimulatedBroker` en el CI ejecuta un ciclo completo end-to-end en cada commit |
| **Datos con retardo → órdenes a precios obsoletos** | Las decisiones se toman con cierres de yfinance de la noche anterior | `max_data_staleness_hours: 30` con `HALT_NEW`; revalidación de precio con `max_price_drift_pct: 2 %` justo antes de ejecutar; el `monitor` de stops usa el feed en tiempo real del bróker, **no** yfinance |
| **Gaps de apertura que saltan los stops** | Un stop en 191 $ con apertura en 178 $ ejecuta en 178 $, no en 191 $ | Se asume y se documenta: **el stop no es un suelo, es una intención**. El sizing usa `slippage_bps_assumed: 15` y el `atr_stop_mult` deja holgura. El backtest debe simular el gap (fill al `open`, no al `stop_price`) — requisito explícito de `SimulatedBroker`. `block_days_before_earnings` evita el gap más previsible |
| **50 € es una muestra estadísticamente insignificante** | 30 operaciones no distinguen habilidad de suerte; el intervalo de confianza del Sharpe es enorme | Aviso permanente en la página 9 y en la puerta 3. El objetivo declarado del bot es **validar la mecánica y la disciplina**, no medir rentabilidad |
| **El paper trading no reproduce el slippage real** | Alpaca rellena en paper de forma optimista; en real hay spread e impacto | Se mide `fills.slippage_bps` desde el día 1 en paper y en real; la puerta 2 exige que el slippage medido no supere 2× el asumido; discrepancia ⇒ vuelta a la puerta 1 con parámetros recalibrados |
| **Regla PDT (FINRA): 3 day trades en 5 días hábiles** por debajo de 25 000 $ | Alpaca **bloquea** el cuarto y puede restringir la cuenta 90 días | `max_day_trades_5d: 2` (margen deliberado) + `min_holding_days: 2` que hace estructuralmente improbable el day trade + comprobación de `account.daytrade_count` antes de todo cierre. **Excepción**: un `stop_exit` puede crear un day trade; se permite (proteger capital prima) pero cuenta y avisa |
| **Órdenes fraccionadas no admiten bracket/OCO** (error `42210000`) | No se pueden delegar los stops en el bróker | **Stops sintéticos gestionados por nosotros**: `bot_positions.stop_price` + ciclo `monitor` cada 15 min. Consecuencia honesta a documentar: **si el proceso está caído, no hay stop**. Config `stop_mode: synthetic` (por defecto) / `native` (intento de orden stop simple, con degradación a sintético si el bróker la rechaza) |
| **Divisa**: cuenta en USD, capital del usuario en EUR | El resultado en € mezcla estrategia y EUR/USD | Se registra `EURUSD=X` en cada snapshot y la página 9 muestra el PnL **en USD (estrategia) y en EUR (real)** por separado. El bot no cubre divisa |
| **Elegibilidad y fiscalidad** | Alpaca es un bróker estadounidense; un residente en España debe verificar su elegibilidad y tiene obligaciones informativas (W-8BEN, declaración de rentas del extranjero, y según importes D-6 / modelo 720) | Prerrequisito documentado en el README **antes** de la puerta 3. El paper trading no requiere cuenta financiada. **Esto no es asesoramiento fiscal**; el usuario debe consultar con un profesional |
| **Acciones corporativas** (splits, dividendos, fusiones) durante una posición abierta | `bot_positions.qty` y el stop quedan desfasados | La conciliación manda siempre sobre la BD; ante cambio de `qty` no explicado por fills, `HALT_NEW` y aviso para revisión manual |
| **La máquina se apaga / pierde red** | Sin ciclo `monitor` no hay stops sintéticos | Aviso en la página 9 si el último `bot_runs` tiene más de 2 h en horario de mercado; alerta de Telegram por "latido perdido"; recomendación de ejecutar en una máquina siempre encendida antes de la puerta 3 |
| **Sobreajuste al pasar de backtest a real** | Los umbrales de la puerta 1 pueden alcanzarse probando muchas configuraciones | El contador de configuraciones probadas de la fase 3 se aplica también aquí; `strategies.params_hash` congela los parámetros al activar y **cualquier cambio posterior reinicia el contador de la puerta 2** |

---

## 11. Encaje en el plan de fases

**Regla dura**: **ninguna fase del bot puede comenzar antes de completar la fase 3
(validación de señales)**, porque `block_if_evidence_not_validated: true` es un límite
de riesgo y sin `signals.evidence` poblado el bot no tendría nada que operar. Operar
señales sin validar es exactamente el fallo que el proyecto trata de evitar.

| Fase | Contenido | Duración | Puerta de salida |
|---|---|---|---|
| **0-5** | Sin cambios respecto a la adenda 1 | — | — |
| **6 — Bot simulado** *(tras fases 3 y 4)* | `trading/brokers/base.py` + `SimulatedBroker` + `context.py` + `sizing.py` + **`risk.py` completo** + `momentum_multifactor_v1` + `decision_log` + esquema completo. **Sin red, sin Alpaca, sin UI.** CLI `run_bot.py --mode simulated` | 3-4 días | **Puerta 1**: backtest con costes supera todos los umbrales de §8 |
| **7 — Bot en paper, semiautomático** | `AlpacaBroker`, `execution.py`, `reconcile.py`, `killswitch.py`, `approval.py`, `client_order_id`, ciclos de cron, **página 9 completa**, aprobación por UI con PIN | 3-4 días | 30 sesiones sin excepciones ni discrepancias |
| **8 — Aprobación por Telegram + estrategias 2 y 3** | `telegram_approval.py` con HMAC, `pullback_uptrend_v1`, `defensive_regime_v1`, panel de progreso de autonomía, métricas de la estrategia | 2-3 días | **Puerta 2**: 60 sesiones, ≥30 operaciones, los 7 criterios de §5 |
| **9 — Modo autónomo en paper** | Activación de `autonomy: auto` (solo por CLI), vigilancia reforzada, informe semanal automático | 1 día + 30 sesiones de observación | Autonomía estable 30 sesiones, drawdown ≤10 % |
| **10 — Dinero real con tope de 50 €** | `mode: live` con las 5 condiciones de §5, vuelta a `autonomy: semi`, `capital_cap: 55` | 1 día + observación | **Puerta 3**. Sin ampliación de capital durante 60 días |

Las fases 6-10 **discurren en paralelo a la fase 5** (refinamiento del dashboard): son
ramas independientes del trabajo. El calendario realista, contando los periodos de
observación obligatorios, sitúa el dinero real **a no menos de 6-7 meses** del inicio
del proyecto. Ese plazo es una característica del diseño, no una demora.

---

## E. Nuevos ficheros, dependencias y configuración

```
config/trading.yaml                    ← NUEVO (§4)
src/stocks_tracker/trading/
├── __init__.py
├── context.py            build_context() → StrategyContext (única lectura del warehouse)
├── intents.py            Intent, RiskVerdict, ApprovedOrder (constructor privado)
├── sizing.py             size_by_atr(), apply_caps()
├── risk.py               RiskManager — 20 reglas, cobertura de ramas 100 %
├── killswitch.py         estados, disparo, rearme manual (CLI)
├── approval.py           pendientes, caducidad, deriva de precio, decisiones
├── telegram_approval.py  long polling + HMAC en callback_data
├── execution.py          ApprovedOrder → submit_order, idempotencia
├── reconcile.py          bróker vs BD, manda el bróker
├── rationale.py          render_intent() → IntentCard (usa core/explain.py)
├── promotion.py          check_autonomy_gate(), check_live_gate()
├── run_bot.py            CLI: --mode --phase {propose,execute,monitor,eod,reconcile}
├── brokers/{base,alpaca,simulated,registry}.py
└── strategies/{base,momentum_multifactor,pullback_uptrend,defensive_regime}.py
src/stocks_tracker/app/pages/9_bot_trading.py
src/stocks_tracker/app/components/trading_cards.py
scripts/trading_cycles.sh
```

**Dependencias añadidas:**

```
alpaca-py>=0.30          # SDK oficial; único punto de contacto con el bróker
ulid-py>=1.1             # IDs ordenables temporalmente para run_id/intent_id
```

`hypothesis` pasa de opcional a **obligatoria en dev** (tests de propiedad de
`sizing.py` y `risk.py`).

---

## I. Tests nuevos

13. **`test_arch_broker_isolation.py`** — `alpaca` no se importa fuera de
    `trading/brokers/alpaca.py` (AST sobre `src/`).
14. **`test_risk_manager.py`** — una prueba por cada una de las 20 reglas, con
    carteras sintéticas; **cobertura de ramas obligatoria del 100 %**; verificación de
    que una excepción interna produce `VETO` (fallo cerrado) y no `APPROVE`.
15. **`test_no_bypass.py`** — no existe ninguna ruta de código que construya
    `ApprovedOrder` fuera de `RiskManager` (AST + intento de construcción directa que
    debe fallar).
16. **`test_killswitch.py`** — `HALT_NEW` permite cierres de protección y bloquea
    aperturas; `FLATTEN` liquida y deja `HALTED`; el rearme sin frase exacta falla; el
    rearme antes del cooldown falla; `daily_update.sh` y `trading_cycles.sh` **no
    contienen** la palabra `rearm`.
17. **`test_idempotency.py`** — dos ejecuciones del mismo `intent_id` producen **una**
    orden; simulación de caída entre `submit` y `INSERT` (mock que lanza tras enviar)
    → al reintentar se adopta la orden existente y no se duplica.
18. **`test_sizing_properties.py`** (`hypothesis`) — para cualquier equity, precio y
    ATR positivos: el notional nunca supera `max_position_pct`, nunca es negativo, el
    stop de una compra siempre queda por debajo del precio, y el riesgo teórico nunca
    excede `risk_per_trade_pct`.
19. **`test_reconcile.py`** — posición huérfana, cantidad divergente y orden
    desconocida producen `HALT_NEW` y fila en `risk_violations`; la BD se corrige con
    el estado del bróker, nunca al revés.
20. **`test_simulated_broker.py`** — fills al `open` siguiente, gap por debajo del stop
    ejecuta al `open` real (no al `stop_price`), slippage y comisión aplicados,
    `daytrade_count` correcto.
21. **`test_telegram_hmac.py`** — un `callback_data` con firma inválida o de otro
    `chat_id` se ignora; una propuesta caducada no se puede aprobar; la doble pulsación
    no ejecuta dos veces.
22. **`test_no_secrets_in_repo.py`** — patrones de claves de Alpaca y de tokens de
    Telegram no aparecen en ningún fichero versionado; el formateador de logs redacta
    `key`/`secret`/`token`/`pin`.
23. **`test_e2e_simulated.py`** — ciclo completo
    `propose → risk → approve → execute → reconcile → eod` contra `SimulatedBroker` con
    datos sintéticos, en el CI, sin red.

---

## Ficheros críticos de esta adenda

- `src/stocks_tracker/trading/risk.py` — la capa que veta; el módulo con más consecuencias del proyecto
- `src/stocks_tracker/trading/brokers/base.py` — contrato que aísla Alpaca y permite el simulador
- `src/stocks_tracker/trading/brokers/simulated.py` — hace posible probar y testear todo sin red ni dinero
- `config/trading.yaml` — mandato conservador expresado como configuración auditable
- `src/stocks_tracker/trading/execution.py` — idempotencia y el único punto que habla con el bróker
- `src/stocks_tracker/app/pages/9_bot_trading.py` — control humano: aprobación, límites y kill switch

## Referencias

- [alpaca-py (SDK oficial)](https://github.com/alpacahq/alpaca-py) · [Trading docs](https://alpaca.markets/sdks/python/trading.html)
- [Fractional Trading](https://docs.alpaca.markets/us/docs/fractional-trading)
- [Working with /orders](https://docs.alpaca.markets/us/docs/working-with-orders) · [Placing Orders](https://docs.alpaca.markets/us/docs/orders-at-alpaca)
- [Limitaciones de fraccionadas con bracket/OCO](https://forum.alpaca.markets/t/frustration-with-fractional-notional-order-limitations-oco-bracket-orders/16277)
- [Errores comunes de la API de trading](https://alpaca.markets/learn/how-to-fix-common-trading-api-errors-at-alpaca)
