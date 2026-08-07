# Adenda 3 — Multi-venue: cripto en Kraken, autonomía por modo y ejecución en el equipo del usuario

Amplía y modifica [`02-adenda-bot-trading.md`](02-adenda-bot-trading.md).

**Cuatro hallazgos verificados condicionan el diseño:**

1. **Kraken SÍ admite stops nativos en spot alojados en el exchange**
   (`ordertype=stop-loss` / `stop-loss-limit`, con `price` = disparo, `price2` = límite
   y `trigger` = `last` | `index`), además de cierre condicional (`close[ordertype]`).
   Esto cambia el diseño de stops respecto a Alpaca y es **la razón técnica por la que
   cripto puede llegar a dinero real antes** ejecutándose en un portátil.
2. **Kraken no ofrece sandbox spot autoservicio**: el demo público es de derivados y el
   entorno de pruebas spot es "bajo petición". El "papel" para cripto será
   `SimulatedBroker` alimentado con precios reales de Kraken.
3. **Autenticación con nonce estrictamente creciente** (uint64, timestamp en ms) y firma
   **HMAC-SHA512 de `URI_path + SHA256(nonce + POST_data)`** con el secreto decodificado
   de base64. El contador de rate limit es **por clave API** y decae con el tiempo;
   `AddOrder`/`CancelOrder` usan un limitador **distinto** del resto.
4. **Las comisiones de Kraken han cambiado dos veces en 2026.** Conclusión de diseño:
   **no se hardcodean — se leen de la API** (`TradeVolume`) y se persisten.

---

## A. Índice de sustituciones y ampliaciones

| Sección previa | Estado | Qué cambia |
|---|---|---|
| Adenda 2 §2 (abstracción de bróker) | **Ampliada** | §1: `KrakenBroker` + `KrakenPriceProvider` |
| Adenda 2 §4 (stops sintéticos) | **Sustituida para Kraken** | §2: stops en **dos capas**. Alpaca sigue con sintético puro |
| Adenda 2 §3 (modelo de datos) | **Ampliado** | §6: columna `venue` en 9 tablas + 3 tablas nuevas |
| Adenda 2 §4 `config/trading.yaml` | **Reestructurado** | §6: bloque `venues:` con límites independientes |
| Adenda 2 §5 (autonomía `semi` global) | **Sustituida** | §8: autonomía por (venue, modo) |
| Adenda 2 §6 (estrategias) | **Ampliada** | §4-5: motor de señales y estrategia de cripto |
| Adenda 2 §7 (página 9) | **Ampliada** | §6: selector y agregación multi-venue |
| Adenda 2 §8 (puertas de validación) | **Ajustada** | §11: puertas específicas de cripto |
| Adenda 2 §9 (ciclos atados a NY) | **Sustituida** | §7: despachador 24/7 |
| Adenda 2 §10 (seguridad) | **Ampliada** | §10, §12 |
| Adenda 2 §11 (fases 6-10) | **Sustituida** | §13: cripto primero |
| Adendas 1 y 2, resto | **Sin cambios** | — |

---

## 1. `KrakenBroker` — mismo Protocol, otro mundo

`trading/brokers/kraken.py`. Implementa **exactamente** el `BrokerAdapter` de la
adenda 2; ningún módulo fuera de `trading/brokers/` conoce Kraken (test de
arquitectura extendido).

### 1.1 Mapa de endpoints por método del Protocol

| Método | Endpoint Kraken | Notas |
|---|---|---|
| `get_account()` | `/0/private/BalanceEx` + `/0/private/TradeBalance` | No hay `buying_power`: se calcula como saldo EUR libre. `pattern_day_trader=False`, `daytrade_count=0` (no aplica PDT) |
| `get_positions()` | `/0/private/BalanceEx` | **No existe endpoint de "posiciones" en spot**: una posición es un saldo. `avg_entry_price` **no lo da Kraken** → se reconstruye desde `bot_positions` y `fills` |
| `get_orders()` | `/0/private/OpenOrders`, `/0/private/ClosedOrders` | `ClosedOrders` cuesta +2 en el contador |
| `get_order_by_client_id()` | `/0/private/QueryOrders` con `userref`, o filtrando `cl_ord_id` | Ver §1.4 |
| `submit_order()` | `/0/private/AddOrder` | `pair`, `type`, `ordertype`, `volume`, `price`, `price2`, `cl_ord_id`, `userref`, `validate`, `close[...]` |
| `cancel_order()` / `cancel_all_orders()` | `/0/private/CancelOrder`, `/0/private/CancelAll` | |
| `close_position()` | `AddOrder` type=sell ordertype=market | No hay "close position": se vende el saldo |
| `get_clock()` | `/0/public/SystemStatus` + `/0/public/Time` | **Siempre abierto**; `online` → `is_open=True`; `maintenance`/`cancel_only`/`post_only` → `is_open=False` con motivo. `next_open`/`next_close` = `None` |
| `get_latest_price()` | `/0/public/Ticker` (lote de pares en una llamada) | |
| `is_fractionable()` | Siempre `True` | Todo es fraccionable en cripto |
| `supports(f)` | `native_stop=True`, `trailing_stop_native=True`, `conditional_close=True`, `bracket=False`, `shorting=False`, `notional=False` | **Kraken pide `volume` en cantidad de cripto, no en importe** → hay que convertir (§1.5) |

Añadidos al Protocol (opcionales, con `NotSupportedError` por defecto en los demás
adaptadores):

```python
def place_native_stop(self, symbol, qty, stop_price, limit_price=None,
                      trigger="last", client_order_id=None) -> Order: ...
def get_native_stops(self, symbol: str | None = None) -> list[Order]: ...
def get_fee_schedule(self, pairs: list[str]) -> dict[str, FeeTier]: ...   # TradeVolume
def get_book_depth(self, symbol: str, within_pct: float = 1.0) -> BookDepth: ...  # Depth
def get_pair_spec(self, symbol: str) -> PairSpec: ...                     # AssetPairs
```

### 1.2 Autenticación y nonce — `trading/brokers/kraken_auth.py`

**Se implementa la firma a mano** (stdlib `hmac`, `hashlib`, `base64`,
`urllib.parse` + `requests`), ~25 líneas, en lugar de depender de una librería. El
algoritmo está completamente especificado y la parte delicada no es firmar sino
**gestionar el nonce**, que ninguna librería resuelve bien para nuestro caso. Test
obligatorio contra el vector de ejemplo publicado por Kraken.

```python
def sign(url_path: str, data: dict, secret_b64: str) -> str:
    postdata = urllib.parse.urlencode(data)
    encoded  = (str(data["nonce"]) + postdata).encode()
    message  = url_path.encode() + hashlib.sha256(encoded).digest()
    mac      = hmac.new(base64.b64decode(secret_b64), message, hashlib.sha512)
    return base64.b64encode(mac.digest()).decode()
```

**Gestión del nonce — `NonceGenerator`:**

- Fuente: `int(time.time() * 1000)`, pero **con guarda monótona persistida**:
  `nonce = max(now_ms, last_nonce + 1)`, con `last_nonce` en la tabla `venue_state`
  (§6) dentro de la misma transacción que la petición. Sobrevive a ajustes de reloj
  hacia atrás (NTP, cambio de hora, suspensión del portátil), que es exactamente el
  fallo que produce `EAPI:Invalid nonce` y deja la clave inutilizable hasta que se
  supera el nonce anterior.
- **Colisión entre procesos**: dos procesos con la misma clave pueden generar el
  mismo nonce o generarlo desordenado, y Kraken rechaza el menor. **Regla dura: una
  clave API por proceso.**
  - `ALPACA_*` y `KRAKEN_BOT_*` para el bot.
  - **`KRAKEN_SETUP_*`, clave distinta**, para el asistente de configuración y los
    scripts manuales (§10). Así el wizard nunca envenena el nonce del bot.
  - Cerrojo `flock` sobre `data/.kraken_{key_id_hash}.lock` adquirido por
    `KrakenBroker.__init__`; si otro proceso lo tiene, **falla al arrancar con
    mensaje claro**, no espera.
  - Se documenta que el usuario debe configurar la **"ventana de nonce"** de la clave
    en Kraken (p. ej. 5000 ms) como red de seguridad adicional.
- El nonce **no se reutiliza jamás**, ni siquiera si la petición falla por red.

### 1.3 Rate limit — `trading/brokers/kraken_ratelimit.py`

Kraken usa un **contador por clave que decae con el tiempo**, no un cupo por ventana:

- La mayoría de llamadas privadas suman **+1**; `TradesHistory` / `Ledgers` suman
  **+2**; el máximo y la velocidad de decaimiento dependen del tier de verificación
  (típicamente máx. 15-20, decaimiento de ~0,33-0,5 por segundo).
- **`AddOrder` y `CancelOrder` usan un limitador distinto**, penalizado por la vida de
  la orden (cancelar una orden recién puesta penaliza más que cancelar una antigua).

Implementación: `TokenBucket(capacity, decay_per_sec, cost_map)` **local y
conservador** (configurado al 70 % del límite nominal del tier), que **bloquea antes
de enviar** en lugar de reaccionar al `EAPI:Rate limit exceeded`. Endpoints públicos
llevan su propio bucket. Con nuestra cadencia (§7) el consumo real es de unas 300
llamadas/día: el limitador es una red de seguridad, no un cuello de botella.

### 1.4 Idempotencia — el `client_order_id` no encaja

La adenda 2 definió `client_order_id = f"st-{mode[0]}-{intent_id}"` (cadena libre, que
Alpaca acepta). **Kraken no acepta cadenas arbitrarias**: ofrece `userref` (entero de
32 bits con signo) y `cl_ord_id` (**UUID**).

```python
KRAKEN_NS = uuid.UUID("6f9b1e6a-0000-4000-8000-000000000001")  # namespace fijo del proyecto
def to_cl_ord_id(intent_id: str) -> str:      # UUIDv5 determinista
    return str(uuid.uuid5(KRAKEN_NS, intent_id))
def to_userref(intent_id: str) -> int:        # int32 determinista, para QueryOrders
    return int.from_bytes(hashlib.blake2s(intent_id.encode(), digest_size=4).digest(),
                          "big", signed=True)
```

`orders.client_order_id` sigue siendo la clave primaria canónica; se añade
`orders.venue_order_ref` con el `cl_ord_id`/`userref` realmente enviado. La
comprobación previa a reenviar consulta `QueryOrders(userref=...)`. **Advertencia**:
`userref` de 32 bits puede colisionar teóricamente; por eso la comprobación **valida
además el `cl_ord_id` (UUIDv5, sin colisión práctica)** antes de dar por buena una
orden existente.

### 1.5 Precisión, mínimos y pares — `PairSpec`

`/0/public/AssetPairs` devuelve por par: `altname`, `wsname`, `pair_decimals`
(decimales de **precio**), `lot_decimals` (decimales de **cantidad**), `ordermin`
(volumen mínimo), `costmin` (coste mínimo), `fees` / `fees_maker`. Todo se cachea en
`venue_instruments` (§6) durante el `setup` y se refresca semanalmente.

| Diferencia con Alpaca | Resolución |
|---|---|
| Kraken pide **volumen**, no importe (`notional`) | `sizing.py` calcula el importe en EUR; `kraken.py::to_volume(notional_eur, price, spec)` divide y **trunca (nunca redondea al alza)** a `lot_decimals`; si queda bajo `ordermin` o `costmin` → `VETO` con `reason_code='BELOW_VENUE_MINIMUM'` |
| Precios con `pair_decimals` | Todo `price`/`price2` pasa por `round_price(p, spec.pair_decimals)`; un stop mal redondeado es rechazado con `EGeneral:Invalid arguments` |
| Nombres de par (`XXBTZEUR` vs `XBT/EUR` vs `BTC-EUR`) | Tabla de traducción en `venue_instruments`: `ticker` canónico (`BTC-EUR`) ↔ `venue_symbol` (`XXBTZEUR`) ↔ `altname` ↔ `wsname`. Misma filosofía que `tv_symbol` de la adenda 1. `to_kraken_pair(ticker)` con overrides YAML y **degradación: si no hay par, el ticker no entra en el universo** |
| Mínimos reales | ~0,0001 BTC, ~0,01 ETH, y **mínimo de coste de 5 EUR** por orden. Con un tope de 50 € esto es determinante: **una posición nunca puede ser menor de ~5-6 €**, lo que fija el número máximo real de posiciones cripto en 4-5, no 7 |
| Pares EUR vs USD | **Solo pares EUR.** Operar BTC/USD desde una cuenta en EUR añadiría conversión y riesgo divisa dentro del venue. `universe.quote_currency: EUR` obligatorio y validado en el arranque |
| Tipos de orden | `market`, `limit`, `stop-loss`, `stop-loss-limit`, `take-profit`, `take-profit-limit`, `trailing-stop`, `trailing-stop-limit`, y **cierre condicional** `close[ordertype]` adjunto a la orden de entrada |
| No hay `Clock` | `SystemStatus`; `maintenance`/`cancel_only`/`post_only` → "no operar" y `SKIPPED_VENUE_UNAVAILABLE` |

### 1.6 `KrakenPriceProvider` — el otro lado del muro

`providers/kraken_provider.py`, implementa el `PriceProvider` del plan original con
endpoints **públicos** (`/0/public/OHLC`, `/0/public/Ticker`), **sin claves**. Es un
módulo distinto de `trading/brokers/kraken.py` (endpoints privados y firma). Ventaja
decisiva: **los datos con los que decidimos vienen del mismo libro en el que
ejecutamos**, cosa que no ocurre con yfinance. Sustituye a yfinance como fuente
primaria de los pares del universo cripto; yfinance queda como fallback para el
histórico largo (el `OHLC` de Kraken devuelve ventana limitada: el backfill profundo
se hace una vez con yfinance `BTC-EUR` y se continúa incrementalmente con Kraken).

---

## 2. Stops en dos capas *(sustituye el diseño de stops de la adenda 2, solo para Kraken)*

**Consecuencia del hallazgo 1**: el stop sobrevive a que el ordenador del usuario esté
apagado. Esto invierte la jerarquía de protección respecto a Alpaca.

### 2.1 Diseño de las dos capas

| Capa | Quién la mantiene | Qué hace | Cuándo actúa |
|---|---|---|---|
| **L-A · Stop nativo (red de seguridad)** | Kraken, alojado en el exchange | `stop-loss-limit` con disparo en `stop_price_hard` y límite en `stop_price_hard × (1 − limit_slack_pct)`. **Se coloca inmediatamente después de confirmar el fill de entrada, en el mismo ciclo.** Cantidad = 100 % de la posición | Siempre, incluso con el bot apagado |
| **L-B · Stop sintético (lógica fina)** | Nuestro bot | Trailing sobre `highest_close_since_entry`, salidas por señal (`rsi14 > 65`), salida por tiempo (`max_hold_until`), recorte parcial por régimen | Solo cuando el bot está vivo |

**`stop_price_hard` (L-A) se sitúa deliberadamente por debajo del stop lógico (L-B)**:
`hard = entry − atr_stop_mult_hard × ATR14` con
`atr_stop_mult_hard = atr_stop_mult + 1.5`. La red de seguridad no debe dispararse
antes que la lógica fina; solo debe atrapar lo que la lógica fina no puede atrapar
porque el bot está caído.

```yaml
# config/trading.yaml → venues.kraken_eu.risk
stop:
  mode: dual                    # dual | native_only | synthetic_only
  atr_stop_mult: 4.0            # L-B, trailing, gestionado por el bot
  atr_stop_mult_hard: 5.5       # L-A, nativo en el exchange
  native_order_type: stop-loss-limit
  native_limit_slack_pct: 3.0   # límite un 3 % por debajo del disparo
  native_trigger: last          # last | index
  resync_tolerance_pct: 0.5     # redistancia mínima para molestarse en recolocar
  max_native_resyncs_per_day: 4 # cancelar+recolocar consume rate limit y penaliza
```

**Por qué `stop-loss-limit` y no `stop-loss` a mercado**: un stop a mercado en un
desplome de cripto puede ejecutarse decenas de puntos porcentuales por debajo. El
límite acota el destrozo. **Contrapartida honesta y documentada**: si el precio
atraviesa el límite sin llenarse, **la orden queda sin ejecutar y el usuario se queda
con la posición en caída**. Es un canje entre "vender muy abajo" y "no vender". Con
`native_limit_slack_pct: 3.0` se acepta hasta un 3 % de deslizamiento y se renuncia
por debajo. El parámetro se expone en la UI con esta explicación literal.

### 2.2 Sincronización entre capas — `trading/stops.py`

```python
def ensure_native_stop(broker, position: BotPosition, spec: PairSpec) -> StopSyncResult
def resync_trailing(broker, position, last_close) -> StopSyncResult
def detect_orphan_stops(broker, positions) -> list[Order]
def cancel_native_stop(broker, position) -> None
```

1. **Al abrir**: fill confirmado → `ensure_native_stop()` en el **mismo ciclo**. Si
   falla, 3 reintentos; si sigue fallando, se emite intención `close` inmediata (**una
   posición sin stop nativo no se mantiene**) y
   `risk_violations(rule_id='native_stop_failed', severity='block')`.
2. **Al subir el trailing (L-B)**: si
   `nuevo_hard − actual_hard > resync_tolerance_pct`, se **cancela y recoloca** el stop
   nativo. Kraken penaliza cancelar órdenes recientes, de ahí
   `max_native_resyncs_per_day: 4` — el trailing nativo se mueve **a saltos**, no
   continuamente. El trailing fino sigue en L-B; L-A solo persigue de lejos.
3. **Al cerrar por lógica (L-B)**: **cancelar primero el stop nativo, vender después.**
   El orden importa: si se vende primero, queda un stop huérfano que podría vender un
   saldo que ya no existe (o peor, uno que el usuario tenga por su cuenta).
   `execution.py` aplica este orden y hay test que lo verifica.
4. **Vigilancia de huérfanos**: cada conciliación llama a `detect_orphan_stops()` y
   cancela stops nativos sin `bot_positions` asociada.
5. **Invariante verificado en cada conciliación**:
   `∀ p ∈ bot_positions(venue='kraken_eu') : p.native_stop_order_id IS NOT NULL ∧ existe en OpenOrders ∧ su volumen ≥ p.qty`.
   Cualquier incumplimiento → `HALT_NEW` + aviso.

### 2.3 Cuando el exchange ejecuta un stop y no nos enteramos

Es el caso normal con un portátil apagado. `reconcile.py --deep`:

1. Consulta `ClosedOrders` y `TradesHistory` desde `venue_state.last_seen_trade_ts`
   (**no** desde "hace 24 h": desde la última marca conocida, así una ausencia de una
   semana se recupera igual).
2. Detecta órdenes `closed` cuyo `cl_ord_id` corresponde a un stop nativo nuestro.
3. Inserta los `fills` reales (precio y hora **del exchange**, no estimados), cierra
   `bot_positions`, marca la intención asociada, y registra
   `decision_log(decision='FILLED', reason_code='NATIVE_STOP_TRIGGERED_WHILE_OFFLINE')`.
4. **Recalcula la serie de equity hacia atrás** y comprueba si durante la ausencia se
   superó `max_daily_loss_pct` o `max_drawdown_pct`. Si se superó → aplica el kill
   switch **retroactivamente** (`HALT_NEW` o `HALTED`) y exige revisión humana. Un
   límite no deja de aplicarse porque nadie estuviera mirando.
5. Notifica por Telegram un resumen de todo lo ocurrido en ausencia.

### 2.4 Limitaciones honestas (a mostrar en la UI junto al stop)

- **Un stop no es un suelo.** En un desplome puede ejecutarse muy por debajo del
  precio de disparo; con `stop-loss-limit`, directamente puede no ejecutarse.
- **"Stop hunting"**: en pares con libro poco profundo, mechas breves barren zonas de
  stops evidentes (números redondos, mínimos recientes). Mitigaciones: (a)
  `atr_stop_mult_hard: 5.5` deja el nivel lejos del ruido; (b) los niveles se calculan
  por ATR, no en números redondos; (c) `native_trigger: index` es opción configurable
  (el índice agregado es más difícil de manipular que el último precio de un único
  libro) — se documenta el canje: `index` protege de mechas locales pero puede no
  disparar ante un problema real solo en Kraken.
- **Riesgo de disparo por incidencia técnica del exchange** (impresión de precio
  errónea). No es hipotético; se acepta como coste de tener red de seguridad.
- **El stop nativo no protege del riesgo de contraparte** (§12): si el exchange está
  caído o en `cancel_only`, no se ejecuta nada.

### 2.5 Alpaca: sin cambios, y por qué importa

En Alpaca las órdenes fraccionadas **deben ser simples** (sin bracket ni OCO), así que
**no hay capa L-A** para acciones fraccionadas. `supports("native_stop")` devuelve
`False` y `stop.mode` cae forzosamente a `synthetic_only`. **Esta asimetría es la razón
técnica principal de la regla dura de §9**: en un portátil, Alpaca en real queda
bloqueado y Kraken no.

---

## 3. Comisiones y presupuesto de rotación

### 3.1 No hardcodear: leer de la API

```python
# trading/brokers/kraken.py
def get_fee_schedule(self, pairs) -> dict[str, FeeTier]   # /0/private/TradeVolume
                                                          # devuelve fees (taker) y fees_maker reales
```

- El **asistente de configuración** (§10) lo llama y guarda el resultado en
  `venue_config`.
- Un ciclo semanal lo refresca; si cambia más de 5 pb, **avisa y marca los backtests
  como "recalibrar"**.
- **Valor por defecto conservador mientras no se haya verificado: 0,40 % taker en
  ambas patas = 0,80 % ida y vuelta.** Es preferible sobreestimar el coste: una
  estrategia que sobrevive a 0,80 % sobrevive a 0,50 %; al revés no.

```yaml
venues.kraken_eu.execution:
  fee_source: api            # api | manual
  fee_taker_bps_fallback: 40
  fee_maker_bps_fallback: 25
  assumed_roundtrip_bps: 80  # se recalcula tras leer TradeVolume
  slippage_bps_assumed: 25   # cripto, mayor que los 15 pb de acciones
```

### 3.2 El cálculo del presupuesto de rotación

Coste total realista por ida y vuelta en cripto: **comisiones 0,50-0,80 % +
deslizamiento y horquilla ~0,40 % ≈ 1,0-1,2 %**. Frente a Alpaca: **0 % de comisión +
~0,15 % de deslizamiento ≈ 0,15 %**. La cripto es entre **7 y 8 veces más cara por
operación**.

Aritmética explícita que debe aparecer en la documentación de la estrategia:

> Sea `c` = coste por ida y vuelta sobre el importe operado (1,1 %), `w` = peso de la
> posición sobre la cartera (0,30 en cripto, con 3-4 posiciones), y `n` = número de
> idas y vueltas al año.
> **Coste anual sobre la cartera = n × c × w = n × 1,1 % × 0,30 = n × 0,33 %.**
> Si la expectativa bruta anual de la estrategia es del 12 % y se acepta que las
> comisiones no se coman más del **25 %** de esa expectativa (3 puntos):
> **n ≤ 3 / 0,33 ≈ 9 idas y vueltas al año**, es decir **menos de una al mes**.

Mismo cálculo para acciones con `c = 0,15 %` y `w = 0,15`: `n ≤ 3 / 0,0225 ≈ 133` idas
y vueltas al año. **Aunque nadie querría rotar tanto, el margen es 15 veces mayor.** De
ahí que la estrategia de acciones pueda rebalancear semanalmente y la de cripto no.

### 3.3 Parámetros ajustados en consecuencia

| Parámetro | Acciones (Alpaca) | **Cripto (Kraken)** | Justificación |
|---|---|---|---|
| Frecuencia de rebalanceo | Semanal | **Mensual** (primer día hábil del mes) | n ≤ 9/año |
| `min_holding_days` | 2 | **21** | Impide que el ruido genere rotación |
| Banda muerta de rebalanceo | 5 pp | **12 pp** | Solo se ajusta lo que de verdad se ha desviado |
| Umbral de señal para entrar | `composite_pctile > 0,60` | **`> 0,80`** | Con 1,1 % de coste, una señal mediocre pierde dinero por construcción |
| Histéresis de salida | p60 | **p40** | Banda ancha = menos vaivén |
| `max_orders_per_day` | 6 | **2** | Anti-bucle, muy estricto |
| `max_orders_per_month` | — | **8** (≈4 idas y vueltas, margen sobre 9/año) | **Límite duro nuevo** |
| `max_new_positions_per_day` | 3 | **1** | |
| Tipo de orden preferente | market | **limit post-only con `limit_offset_bps: 10`**, con degradación a market si no se llena en 30 min | Pasar de taker a maker ahorra ~15 pb por pata |

Reglas nuevas en `RiskManager`:

- **nº 21 · `max_orders_per_month`** — evaluada a nivel cuenta contra `orders` del mes
  natural.
- **nº 22 · `min_expected_edge`** — `VETO` si
  `(score_pctile − 0,5) × edge_scale < assumed_roundtrip_bps × 1,5`; formulación
  concreta de "esta operación no gana ni para pagar las comisiones".

En el simulador, `SimulatedBroker` recibe `commission_bps` y `slippage_bps` **por
venue**, y los umbrales de la **Puerta 1** (adenda 2 §8) se evalúan **después de costes
con los valores reales de Kraken**. Requisito añadido: **la estrategia debe seguir
superando la Puerta 1 con el coste multiplicado por 1,5** (resistencia a subidas de
tarifas y a peor ejecución).

---

## 4. Motor de señales para cripto

### 4.1 Qué se cae y qué se queda

| Bloque del plan original §4 | En cripto |
|---|---|
| **Técnicos** (SMA/EMA, MACD, RSI, ADX, ATR, Bollinger, OBV, ROC, mom 12-1, 52 semanas, drawdown, vol realizada, volumen relativo) | **Todos válidos** y son el núcleo |
| **Fundamentales / valoración** (PER, PEG, P/B, EV/EBITDA, FCF yield, márgenes, ROE, crecimiento, deuda, dividendo, sorpresas) | **No aplican en absoluto.** No hay estados financieros ni beneficios |
| Factores `value`, `growth`, `quality`, `dividend` | Se excluyen |
| Factor `size` | Existe capitalización, pero con una lista blanca de 5-7 activos el z-score no es informativo → **desactivado** |
| Factores `momentum`, `lowvol`, `technical` | Válidos |
| **Breadth / rotación sectorial** | No hay sectores GICS. Se sustituye por §4.3 |
| **Macro / régimen** | Parcial: VIX, DXY y tipos siguen siendo informativos como contexto risk-on/risk-off; el resto no |
| **Sentimiento** | Fuera de alcance inicial (las APIs de noticias cubren mal cripto en su capa gratuita) |

### 4.2 Composición del score cripto

`peer_group = 'CRYPTO'` (no sector GICS). Nuevo preset en `config/factors.yaml`:

```yaml
presets:
  crypto_conservative:
    momentum: 0.40      # mom 12-1, ROC 3m/6m, RS vs BTC
    technical: 0.25     # tendencia + señales activas
    lowvol:    0.20     # vol realizada 60/252, max DD 1a, beta vs BTC
    liquidity: 0.15     # volumen relativo, profundidad de libro, $ volumen 30d
peer_groups:
  CRYPTO: {min_group_size: 4, robust_zscore: true, winsorize: [0.05, 0.95]}
```

`winsorize` más agresivo (5/95 en vez de 2/98) porque las colas en cripto son extremas
y un solo día puede dominar un z-score.

**Nuevo factor `liquidity`** (`core/factors.py`), específico de cripto: z-score de
volumen medio 30d en EUR en Kraken, profundidad de libro al 1 % (`Depth`), y
estabilidad de la horquilla.

### 4.3 Señales específicas de cripto — `core/crypto_regime.py`

| Métrica | Datos | Qué mide |
|---|---|---|
| **Dominancia de BTC** | Capitalizaciones (CoinGecko API gratuita, `/global`, 1 llamada/día) | Dominancia subiendo = rotación defensiva dentro de cripto → penaliza las alts. Dominancia bajando con BTC alcista = apetito por riesgo |
| **Fuerza relativa vs BTC** | `prices_daily` | Análogo al "vs sector" de acciones: BTC **es** el índice de referencia del universo cripto |
| **Beta vs BTC (252d)** | retornos | Componente de `lowvol`: una alt con beta 2,5 arriesga el doble por unidad de exposición |
| **Correlación BTC ↔ Nasdaq (60d)** | `^NDX` + BTC | Correlación alta = la cripto cotiza como activo de riesgo largo → el semáforo macro de acciones **sí** aplica. Correlación baja = régimen desacoplado. **Modula el peso del overlay defensivo** |
| **`crypto_regime`** | BTC vs MM200, pendiente de dominancia, percentil de vol realizada, drawdown de BTC desde máximos | Semáforo **propio del venue**, distinto de `regime_daily`. Estados: `bull` / `neutral` / `bear` / `capitulation` |

Nueva tabla
`crypto_regime_daily(date, btc_dominance, btc_above_sma200, btc_dd_from_high, realized_vol_pctile, corr_ndx_60, regime, risk_score, components JSON)`.

### 4.4 Señales del catálogo §4.8: cuáles sobreviven

| `signal_id` | Cripto | Nota |
|---|---|---|
| `GOLDEN_CROSS` / `DEATH_CROSS` | Sí | |
| `PULLBACK_IN_UPTREND` | Sí | RSI<45, no <40: en cripto las correcciones son más someras en tiempo y más profundas en precio |
| `MACD_BULL_CROSS` | Sí | |
| `RSI_OVERSOLD_REVERSAL` | Con reservas | En cripto la "sobreventa" puede prolongarse semanas. **Solo con `btc_above_sma200`** |
| `52W_HIGH_BREAKOUT` | Sí | Históricamente el patrón más rentable en cripto — y el que más falsos positivos da |
| `BB_SQUEEZE` | Sí | |
| `VOLUME_SPIKE` | Sí | |
| `NEW_DOWNTREND` | Sí | |
| `EARNINGS_SURPRISE_DRIFT` | No | No existe |
| `VALUE_QUALITY_COMBO` | No | No existe |
| `SECTOR_LEADER` | Sustituida por **`BTC_RELATIVE_LEADER`** | RS vs BTC en decil superior + `crypto_regime != bear` |

### 4.5 La validación hay que rehacerla — cambio de esquema

**Una señal validada en acciones NO está validada en cripto.** Los regímenes, la
volatilidad, la microestructura y la base de inversores no tienen nada que ver.
`signals.evidence` (columna de la adenda 2) es insuficiente porque es global.

```sql
-- se elimina la columna signals.evidence
CREATE TABLE IF NOT EXISTS signal_evidence (
  signal_id VARCHAR, scope VARCHAR,       -- 'equity_us' | 'crypto'
  evidence VARCHAR,                       -- validada | debil | no_validada | sin_datos
  ic_ir DOUBLE, hit_rate DOUBLE, avg_excess_ret DOUBLE, n_obs INTEGER,
  horizon_days INTEGER, oos_from DATE, oos_to DATE,
  costs_bps_assumed DOUBLE, updated_at TIMESTAMP,
  PRIMARY KEY (signal_id, scope, horizon_days)
);
```

La regla de riesgo `evidence_gate` consulta `(signal_id, scope)` donde `scope` sale del
venue de la intención. **Hasta que exista una fila `validada` con `scope='crypto'`,
ninguna señal cripto puede operar.**

Advertencia adicional para la validación cripto: el histórico útil es **corto y
dominado por dos o tres ciclos** (2017, 2021, 2022, 2024-25). El requisito de "≥3
pliegues walk-forward" se mantiene, pero se añade: **la señal debe ser positiva en al
menos 2 de los 3 pliegues**, y el informe muestra el resultado por ciclo, no solo
agregado. Un resultado que depende íntegramente del ciclo alcista de 2021 no es una
señal, es una fotografía.

---

## 5. Universo cripto y parámetros de riesgo específicos

### 5.1 Por qué los números de acciones no sirven

En renta variable de gran capitalización el **ATR% diario típico es del 1,5-2 %** y una
caída del 20 % en un día es un acontecimiento de portada. En BTC el **ATR% diario
típico es del 3-5 %**, y en alts del 5-8 %; una caída del 20 % en un día ocurre varias
veces al año y del 50 % en un mes varias veces por ciclo.

**Consecuencia mecánica**: aplicar `atr_stop_mult: 2.5` en cripto sitúa el stop a un
10-12 % — dentro del rango de ruido de dos días. Aplicar `max_daily_loss_pct: 3`
produce un kill switch que se dispara casi cada semana. **Un límite que salta con el
ruido normal no es un límite: es un generador de falsas alarmas que enseña al usuario a
ignorarlo.**

### 5.2 Lista blanca

```yaml
venues.kraken_eu.universe:
  quote_currency: EUR
  whitelist: [BTC-EUR, ETH-EUR, SOL-EUR, XRP-EUR, LINK-EUR, ADA-EUR]
  core_assets: [BTC-EUR, ETH-EUR]        # tratamiento diferenciado en los topes
  max_non_core_positions: 2
  admission_criteria:                     # se comprueban en el setup y semanalmente
    min_market_cap_rank: 20
    min_history_years: 2
    min_kraken_eur_volume_24h: 1_000_000
    min_book_depth_1pct_eur: 20_000       # /0/public/Depth
    max_spread_bps: 20
  forbidden:
    - margin
    - futures
    - leveraged_tokens
    - staking            # inmoviliza fondos y complica la contabilidad
    - meme_and_microcaps
    - anything_not_in_whitelist           # lista blanca cerrada: no hay descubrimiento automático
```

**La lista blanca es cerrada por diseño.** El bot **no puede** comprar un activo que el
usuario no haya aprobado antes en el YAML. Es la protección más simple y eficaz contra
el escenario "el bot compró una moneda que nadie conocía porque el momentum estaba
altísimo".

### 5.3 Parámetros de riesgo cripto vs acciones

```yaml
venues.kraken_eu.risk:
  risk_per_trade_pct: 2.0        # vs 1.5 en acciones: menos operaciones, cada una pesa más
  atr_stop_mult: 4.0             # vs 2.5 → stop al ~15-20 %, fuera del ruido de 2-3 días
  atr_stop_mult_hard: 5.5        # red nativa al ~22-28 %
  max_position_pct_core: 40.0    # BTC/ETH: con 4-5 posiciones y mínimo de 5 EUR, no cabe menos
  max_position_pct_non_core: 18.0
  max_positions: 4               # el mínimo de coste de 5 EUR limita físicamente el reparto
  min_positions_hint: 2
  min_cash_pct: 20.0             # vs 10: reserva mayor para comprar caídas y absorber golpes
  max_gross_exposure_pct: 80.0
  max_daily_loss_pct: 8.0        # vs 3: un -3 % diario en BTC es una tarde cualquiera
  max_drawdown_pct: 20.0         # vs 15: ver nota
  drawdown_warn_pct: 12.0
  max_orders_per_day: 2
  max_orders_per_month: 8
  min_holding_days: 21
  allow_shorting: false
  allow_leverage: false
  allow_margin: false            # explícito: Kraken ofrece margin en spot y hay que apagarlo
  allow_derivatives: false
```

**Nota sobre `max_drawdown_pct: 20` — decisión que el usuario debe confirmar.** El
mandato original decía 15 %. En cripto, un 15 % de caída desde máximos ocurre en
semanas normales y produciría un `FLATTEN` (liquidación total) recurrente, vendiendo
sistemáticamente en el peor momento y consumiendo comisiones. Alternativas, con el
canje explícito:

- `20 %` (propuesto): menos falsas liquidaciones, se acepta perder hasta ~10 € de los 50.
- `15 %` (mandato literal): más protección aparente, alta probabilidad de liquidar en un
  retroceso normal y quedar fuera de la recuperación.
- **La protección real del capital no es el drawdown, es `capital_cap`**: con 25 €
  asignados a cripto, la pérdida máxima concebible está acotada por construcción, sin
  depender de que un stop funcione.

Se deja como parámetro visible y explicado en la página 9, no como decisión enterrada.

### 5.4 Filtro de liquidez por profundidad de libro

Regla de riesgo **nº 23**, específica de cripto: antes de cada orden,
`get_book_depth(symbol, within_pct=1.0)`; si `importe_orden > book_depth_eur × 0.05` →
`RESIZE` (máximo el 5 % de la profundidad al 1 %) y, si tras el recorte queda bajo
`costmin`, `VETO`. Con 50 € esto casi nunca morderá en BTC/ETH; es la salvaguarda que
impide operar un par que se ha quedado seco.

---

## 6. Arquitectura multi-venue

### 6.1 Principio: dos carteras, nunca un bote común

| Motivo | Explicación |
|---|---|
| **Dominios de fallo independientes** | Un bug en la lógica de cripto, o una caída de Kraken, no debe poder tocar la cartera de acciones |
| **Perfiles de riesgo incompatibles** | `max_daily_loss_pct` de 8 % en cripto y de 3 % en acciones no pueden convivir en un único contador |
| **Divisas distintas** | Sumar USD y EUR en un límite de drawdown mezcla el resultado de la estrategia con el del EUR/USD |
| **Realidad operativa** | Son dos cuentas en dos entidades: el efectivo no es fungible entre ellas sin una transferencia manual de días |
| **Trazabilidad** | "¿Por qué se paró el bot?" debe tener una respuesta por venue |

**Pero sí existe un "parar todo" global**: `killswitch.py halt --venue ALL`, que recorre
los venues y aplica la acción a cada uno. La página 9 lo expone como botón principal. La
asimetría se mantiene: parar todo es un clic; rearmar es por venue y por CLI.

### 6.2 Cambios de esquema

**Columna `venue VARCHAR` añadida a**: `strategies`, `bot_runs`, `intents`, `orders`,
`fills`, `portfolio_snapshots`, `bot_positions`, `decision_log`, `risk_violations`.
Claves primarias que cambian:

```sql
-- bot_positions: PK (ticker, mode)            → PK (venue, mode, ticker)
-- portfolio_snapshots: PK (snapshot_at, mode) → PK (venue, mode, snapshot_at)
-- bot_state: PK (mode)                        → PK (venue, mode)
```

**Tablas nuevas:**

```sql
CREATE TABLE IF NOT EXISTS venue_config (
  venue VARCHAR PRIMARY KEY,           -- 'alpaca_us' | 'kraken_eu'
  broker VARCHAR, base_currency VARCHAR, asset_class VARCHAR,  -- equity | crypto
  capital_cap DOUBLE, is_24_7 BOOLEAN,
  fee_taker_bps DOUBLE, fee_maker_bps DOUBLE, fee_checked_at TIMESTAMP,
  supports_native_stop BOOLEAN, supports_notional BOOLEAN,
  tz_accounting VARCHAR,               -- 'America/New_York' | 'UTC'
  enabled BOOLEAN, updated_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS venue_instruments (
  venue VARCHAR, ticker VARCHAR,       -- canónico del proyecto: 'BTC-EUR'
  venue_symbol VARCHAR,                -- 'XXBTZEUR'
  altname VARCHAR, wsname VARCHAR,
  pair_decimals INTEGER, lot_decimals INTEGER,
  ordermin DOUBLE, costmin DOUBLE,
  tradable BOOLEAN, status VARCHAR,
  fee_taker_bps DOUBLE, fee_maker_bps DOUBLE,
  refreshed_at TIMESTAMP,
  PRIMARY KEY (venue, ticker)
);

CREATE TABLE IF NOT EXISTS venue_state (       -- estado operativo por venue
  venue VARCHAR PRIMARY KEY,
  last_nonce BIGINT,                   -- guarda monótona del nonce (§1.2)
  last_seen_trade_ts TIMESTAMP,        -- marca de agua para reconcile --deep
  last_heartbeat_at TIMESTAMP,
  api_status VARCHAR,                  -- online|maintenance|cancel_only|degraded|unreachable
  clock_drift_ms INTEGER,
  updated_at TIMESTAMP
);
```

**`bot_positions`** gana: `native_stop_order_id VARCHAR`, `native_stop_price DOUBLE`,
`native_stop_synced_at TIMESTAMP`, `native_stop_resyncs_today INTEGER`.
**`intents`** gana: `venue VARCHAR`, `user_retro_flag BOOLEAN` (§8).

### 6.3 `config/trading.yaml` reestructurado

```yaml
global:
  hosting: laptop                 # laptop | vps   → ver §9
  global_capital_cap_eur: 60.0    # techo agregado, convertido a EUR
  kill_all_on_reconcile_disaster: true

venues:
  kraken_eu:
    enabled: true
    broker: kraken
    asset_class: crypto
    base_currency: EUR
    capital_cap: 30.0             # EUR — INDEPENDIENTE
    mode: simulated
    tz_accounting: UTC
    accounting_day_cut: "00:00"   # corte del día contable
    universe: {...}               # §5.2
    risk:    {...}                # §5.3
    stop:    {...}                # §2.1
    execution: {...}              # §3.3
    schedule: {...}               # §7
    strategies: [crypto_trend_v1]

  alpaca_us:
    enabled: true
    broker: alpaca
    asset_class: equity
    base_currency: USD
    capital_cap: 30.0             # USD — INDEPENDIENTE
    mode: simulated
    tz_accounting: America/New_York
    accounting_day_cut: market_close
    universe: {...}               # adenda 2
    risk:    {...}                # adenda 2
    stop:    {mode: synthetic_only}
    strategies: [momentum_multifactor_v1, pullback_uptrend_v1, defensive_regime_v1]
```

**Reparto de capital: dos topes independientes.** `global_capital_cap_eur` es un techo
agregado de seguridad (regla de riesgo **nº 24**, evaluada convirtiendo a EUR), no un
bote compartido: el capital no migra automáticamente de un venue a otro. Si el usuario
quiere mover dinero, lo hace él, con una transferencia real.

### 6.4 Agregación en la página 9 sin mezclar divisas

- **Selector de venue** en la cabecera: `Todos | Kraken (cripto, EUR) | Alpaca
  (acciones, USD)`. Por defecto `Todos`.
- Con un venue seleccionado: todo en su divisa nativa, sin conversión. Es la vista "de
  verdad".
- Con `Todos`: **dos curvas de equity separadas en sus divisas** (nivel 2, dos gráficos
  apilados) y **una tercera vista agregada en EUR**, con estas reglas innegociables:
  - Conversión con `EURUSD=X` **al cierre de cada día** (no al tipo actual aplicado
    retroactivamente, que reescribiría la historia).
  - La tabla de resultados agregados desglosa **tres líneas**: `PnL estrategia cripto
    (EUR)`, `PnL estrategia acciones (USD→EUR a tipo constante)`, `Efecto divisa
    EUR/USD`. **El efecto divisa nunca se mezcla con el resultado de la estrategia.**
  - Etiqueta literal bajo el gráfico: *"Agregado en EUR al tipo de cierre de cada día.
    Incluye efecto divisa, que no es resultado de la estrategia."*
- Las **barras de consumo de límites** se muestran **siempre por venue**, nunca
  agregadas: un límite agregado no existe y mostrarlo induciría a error.
- Estado de cada venue con su propio semáforo: `RUNNING` / `HALT_NEW` / `HALTED` /
  `SIN LATIDO`.

---

## 7. Ciclos 24/7 *(sustituye a la adenda 2 §9, "Programación")*

### 7.1 Despachador único

Se abandonan las 5 entradas de cron atadas a horarios de Nueva York. Una sola entrada:

```
*/15 * * * *  flock -n /tmp/st_bot.lock python -m stocks_tracker.trading.run_bot --dispatch
```

`run_bot.py --dispatch` consulta `venue_config` y `bot_runs`, y decide **qué ciclos toca
ejecutar ahora**, con lógica de recuperación (`catch-up`): si el último ciclo `propose`
de un venue es de hace 30 h porque el portátil estuvo apagado, lo ejecuta ya. Ventajas
frente a cron por horas: sobrevive a apagados, a cambios de horario de verano y a que el
usuario encienda el equipo a mediodía.

### 7.2 Cadencia por venue

| Ciclo | `alpaca_us` (equity) | `kraken_eu` (cripto 24/7) |
|---|---|---|
| `reconcile` | Al arrancar + 15:00 CET | **Al arrancar (siempre `--deep`) + cada 6 h** |
| `propose` | 23:15 CET (tras ingest+compute) | **00:15 UTC**, tras cerrar la vela diaria |
| `execute` | 15:55 CET (≥20 min tras apertura) | **00:30 UTC** (auto) o al aprobar (semi), con `SystemStatus == online` |
| `monitor` (stops) | Cada 15 min, solo mercado abierto | **Cada 15 min, 24/7** |
| `eod` (cierre contable) | 21:45 CET | **23:55 UTC** |
| Refresco de `AssetPairs`/fees | — | Semanal, domingo 03:00 UTC |

**Corte del día contable en cripto: 00:00 UTC**, configurable
(`accounting_day_cut`). Es la convención del sector, coincide con el cierre de la vela
diaria que usa la estrategia, y evita que el "día" del bot dependa del huso del usuario o
del horario de verano. `bot_state.day_start_equity` y `day_start_date` se refijan en ese
instante; el `max_daily_loss_pct` se mide sobre esa referencia.

**Conviven en el mismo despachador** porque la puerta de "¿puedo operar?" está
abstraída: `get_clock()`. Para Alpaca devuelve el reloj real del mercado; para Kraken
devuelve siempre abierto salvo `maintenance`/`cancel_only`. **`run_bot.py` no contiene ni
un solo `if venue == 'kraken'` relativo a horarios** — la diferencia vive dentro del
adaptador. Test que lo verifica por AST.

**Ventanas de no-operación en cripto**: no hay apertura ni cierre, pero sí momentos
malos. Se define `no_trade_windows: ["23:50-00:10 UTC"]` (rollover diario, libros más
finos) y una guarda dinámica: si la horquilla actual supera `max_spread_bps`, se pospone
la orden al siguiente ciclo `monitor` con `reason_code='SPREAD_TOO_WIDE'`.

---

## 8. Nuevo modelo de autonomía *(sustituye a la adenda 2 §5, "Promoción a modo autónomo")*

### 8.1 La política

`autonomy` deja de ser un campo global y pasa a resolverse por **(venue, modo)**:

```yaml
global:
  autonomy_policy:
    simulated: auto     # sin dinero: la fricción no aporta nada
    paper: auto         # sin dinero: idem
    live: semi          # OBLIGATORIO al entrar en live. No configurable a 'auto' de inicio.
```

`bot_state(venue, mode)` gana: `autonomy VARCHAR`, `autonomy_since TIMESTAMP`,
`autonomy_stats_reset_at TIMESTAMP`, `mode_entered_at TIMESTAMP`.

`registry.get_broker()` y `run_bot.py` leen la autonomía **de `bot_state`, no del
YAML**: el YAML define la política por defecto al entrar en un modo; el estado real vive
en la BD y solo se cambia por el gate + CLI.

### 8.2 Por qué la aprobación humana vuelve justo con el dinero real

Corrección deliberada del diseño de la adenda 2:

1. **La fricción tiene valor donde hay consecuencias.** Aprobar 40 propuestas de papel no
   enseña nada y **produce fatiga de alertas**: a la décima el usuario pulsa "Aprobar"
   sin leer. **Una aprobación que se sella sin mirar no es un control, es teatro** — y
   además contamina la métrica de "% aprobado" que el gate pretendía medir.
2. **En papel el objetivo es medir la estrategia**, y la aprobación humana la contamina
   (introduce el criterio del usuario, y su latencia, en los resultados). En automático,
   el papel mide lo que se quiere medir: el bot solo.
3. **En real el objetivo es no perder dinero por un fallo.** El primer día en real es
   cuando aparecen los errores que el papel no reveló: mínimos del venue, redondeos,
   permisos, precios reales. Un humano mirando cada orden en ese momento es el control
   más barato que existe.
4. **Cambiar de entorno invalida la evidencia acumulada.** El paso paper→live no es una
   promoción, es una migración: **reinicia los contadores**.

### 8.3 Transiciones y reinicio de contadores

```
simulated(auto) ──[Puerta 1]──► paper(auto) ──[Puerta 2]──► live(semi) ──[Gate]──► live(auto)
                                                    ▲                                  │
                                                    └────[cualquier HALTED por drawdown]┘
```

- Todo cambio de `mode` fija `autonomy` según la política, escribe `mode_entered_at` y
  **reinicia `autonomy_stats_reset_at`**. Ninguna estadística cruza la frontera de modo.
- Un `HALTED` por `max_drawdown` en `live(auto)` **degrada automáticamente a
  `live(semi)`** al rearmar. Volver a `auto` exige repetir el gate completo. La
  degradación es automática; la promoción, nunca.
- El cambio de estrategia o de `params_hash` también reinicia los contadores del venue
  afectado.

### 8.4 `promotion.py` reescrito

```python
def check_paper_gate(venue) -> GateResult         # Puerta 2, ajustada
def check_live_entry_gate(venue) -> GateResult    # requisitos para pisar live
def check_live_autonomy_gate(venue) -> GateResult # live(semi) → live(auto)
```

**Criterios de `live(semi) → live(auto)`, por venue** (sustituyen a los 7 de la
adenda 2):

| # | Criterio | Cripto | Acciones |
|---|---|---|---|
| 1 | Días naturales en `live(semi)` | ≥ 45 | ≥ 30 sesiones |
| 2 | Propuestas presentadas | ≥ 12 (la estrategia rota poco) | ≥ 15 |
| 3 | **% aprobadas sin modificar** | ≥ 90 % | ≥ 90 % |
| 4 | Propuestas aprobadas que el riesgo debía haber vetado | 0 | 0 |
| 5 | Discrepancias de conciliación sin resolver | 0 | 0 |
| 6 | Drawdown realizado | ≤ ½ del límite (10 %) | ≤ ½ del límite (7,5 %) |
| 7 | Excepciones no controladas en 30 días | 0 | 0 |
| 8 | **Uptime del latido** (§9) | ≥ 95 % | ≥ 98 % en horario de mercado |
| 9 | **Stops nativos verificados** en toda posición abierta durante todo el periodo | obligatorio | n/a (no existen) |
| 10 | Deslizamiento real medido ≤ 2× el asumido | obligatorio | obligatorio |

**El criterio 3 en modo `paper(auto)` no es medible** (no hay aprobaciones). Se sustituye
por una **auditoría retrospectiva**: la página 9 muestra cada operación ejecutada en
papel con un botón `👎 Yo no habría hecho esto`, que escribe
`intents.user_retro_flag = TRUE`. La **Puerta 2** exige `≤ 10 % de operaciones
marcadas`. Se conserva así exactamente la señal que medía el criterio original —¿el bot
propone cosas sensatas a juicio del usuario?— sin la fatiga de aprobar en un entorno sin
consecuencias.

---

## 9. Ejecución en el ordenador del usuario

### 9.1 Latido — `trading/heartbeat.py`

```python
def beat(venue: str, cycle: str) -> None            # escribe venue_state.last_heartbeat_at
def check_stale(venue) -> StaleResult               # compara con el umbral del venue
def uptime_pct(venue, days: int = 30) -> float      # % de ciclos esperados realmente ejecutados
```

Umbrales: cripto **2 h** (debería haber un `monitor` cada 15 min); acciones **2 h pero
solo en horario de mercado**. Al superarse: banner ámbar en la página 9 y alerta de
Telegram: *"El bot lleva 7 h sin ejecutar ciclos en kraken_eu. Los stops nativos siguen
activos en el exchange; el trailing y las salidas por señal están pausados."* — el
mensaje **distingue qué protección sigue viva y cuál no**, que es lo único que el usuario
necesita saber.

`uptime_pct` alimenta el criterio 8 del gate y se muestra como métrica permanente.

### 9.2 Qué se pierde exactamente con el equipo apagado

| Función | ¿Sobrevive? | Consecuencia |
|---|---|---|
| **Stop nativo en Kraken (L-A)** | **Sí** | La protección de última instancia sigue en pie |
| Trailing del stop (L-B) | No | El stop se queda donde estaba; se pierde beneficio no consolidado, no capital |
| Salidas por señal (`rsi14 > 65`, pérdida de MM200) | No | La posición se mantiene más de lo previsto |
| Salida por tiempo (`max_hold_until`) | No | Se ejecuta con retraso al arrancar |
| Nuevas propuestas y entradas | No | Oportunidades perdidas — **no es un riesgo, es un coste de oportunidad** |
| Kill switch por pérdida diaria / drawdown | No en tiempo real | Se aplica **retroactivamente** en la conciliación (§2.3) |
| Conciliación | No | Se ejecuta al arrancar |
| **Stop en Alpaca (sintético)** | **No, y no hay red** | **Posición desprotegida durante todo el apagado** |

La última fila es la razón de la regla dura de §9.4.

### 9.3 Conciliación agresiva al arrancar

`reconcile.py --deep` es **obligatoria en el primer ciclo de cada arranque** (no
opcional, no muestreada). Además de lo descrito en §2.3:

- Comprueba `clock_drift_ms` contra `/0/public/Time`; si supera 5 000 ms, **no opera** y
  pide sincronizar el reloj (un reloj desviado rompe el nonce y las marcas temporales).
- Compara `venue_state.last_seen_trade_ts` con `TradesHistory` completo del intervalo, no
  con una ventana fija.
- Detecta **posiciones cerradas por el exchange** (stop ejecutado, o par retirado de
  cotización) y **posiciones abiertas sin stop nativo**.
- Si el intervalo de ausencia supera `deep_review_hours: 48`, **no reanuda
  automáticamente**: entra en `HALT_NEW` y exige que el usuario revise el resumen y
  rearme. Tras dos días fuera, el contexto de mercado puede haber cambiado por completo y
  las posiciones merecen una mirada humana.

### 9.4 Regla dura de alojamiento

```yaml
global:
  hosting: laptop        # laptop | vps
```

`registry.get_broker(mode=LIVE)` y `run_bot.py` aplican, **antes que cualquier otra
comprobación**:

| Condición | `hosting: laptop` | `hosting: vps` |
|---|---|---|
| **`alpaca_us` en `live`** | **BLOQUEADO SIN EXCEPCIÓN.** Motivo registrado: *"Las acciones fraccionadas de Alpaca no admiten stop nativo; sin proceso permanente no hay ninguna protección de las posiciones."* | Permitido tras las puertas |
| **`kraken_eu` en `live`** | Permitido **solo** con las 4 restricciones de abajo | Permitido tras las puertas |

Restricciones adicionales para `kraken_eu` en `live` con `hosting: laptop` (verificadas
en cada ciclo, no solo al arrancar):

1. **`stop.mode` forzado a `dual`** y `require_native_stop_for_all_positions: true`. Una
   posición sin `native_stop_order_id` confirmado en `OpenOrders` provoca cierre inmediato
   de esa posición.
2. **Estrategias con `requires_intraday_supervision = True` deshabilitadas.** Atributo
   declarado en la clase de la estrategia; `crypto_trend_v1` lo tiene a `False` por diseño
   (decide sobre velas diarias). Cualquier estrategia intradía queda bloqueada por
   configuración, no por confianza.
3. **`uptime_pct(30d) ≥ 95 %`** medido antes de permitir aperturas nuevas; por debajo,
   `HALT_NEW` (mantiene stops, no abre).
4. **`max_position_pct` reducido un 25 %** respecto a §5.3 y `capital_cap` limitado a
   **15 €** hasta migrar a alojamiento permanente.

### 9.5 Migración a VPS — camino corto

Documentado en `docs/migracion_vps.md`, 6 pasos, sin cambios de código:

1. VPS Linux mínimo (1 vCPU / 1 GB, ~4-6 €/mes) en la UE, con zona horaria UTC y NTP
   activo.
2. `git clone` + `uv sync`. El repo es el mismo.
3. Copiar `.env` por `scp` con permisos `0600`. **Crear claves API nuevas y revocar las
   del portátil** (nunca compartir clave entre dos máquinas: colisión de nonce, §1.2).
4. Copiar `data/warehouse.duckdb` una vez con el bot parado en ambos lados.
5. `systemd`: un servicio `stocks-tracker-bot.service` (`Type=oneshot`) + un
   `stocks-tracker-bot.timer` cada 15 min, sustituyendo al cron. Streamlit como segundo
   servicio escuchando en `127.0.0.1` + acceso por túnel SSH o Tailscale.
6. `hosting: vps` en `trading.yaml` → desbloquea Alpaca en live y levanta las 4
   restricciones. `uptime_pct` empieza a contar de cero.

---

## 10. Facilidad de puesta en marcha

### 10.1 Asistente — `python -m stocks_tracker.trading.setup --venue kraken_eu`

También como `make setup-broker VENUE=kraken_eu`. Interactivo, idempotente, ejecutable
tantas veces como haga falta, con salida clara en cada paso (`rich`).

| Paso | Qué hace | Si falla |
|---|---|---|
| 1 | Muestra la lista de comprobación previa (§10.2) y pide confirmación de que está hecha | Sale con instrucciones |
| 2 | Pide `API Key` y `Private Key` con `getpass` (**sin eco en pantalla, sin quedar en el historial del shell**) | — |
| 3 | Escribe `.env` con `os.open(path, O_CREAT\|O_WRONLY, 0o600)`; comprueba que `.env` está en `.gitignore` y **aborta si no lo está**. Nunca imprime las claves, ni truncadas | Aborta |
| 4 | **Conectividad y reloj**: `/0/public/SystemStatus` y `/0/public/Time`; calcula `clock_drift_ms` | Deriva > 5 s → aborta con el comando de sincronización NTP |
| 5 | **Verifica que la clave funciona para trading**: `AddOrder` con **`validate=true`** (valida sin colocar nada) sobre BTC/EUR con el volumen mínimo | Falta permiso de trading → aborta con instrucciones |
| 6 | **VERIFICA QUE LA CLAVE NO PUEDE RETIRAR FONDOS**: llama a `/0/private/WithdrawMethods` y `/0/private/WithdrawAddresses`. **Si devuelven datos en lugar de `EGeneral:Permission denied`, la clave TIENE permiso de retirada** → **aborta con error rojo**, borra la clave del `.env` recién escrito e indica cómo crear una clave correcta. **Es el único paso que no se puede omitir con `--force`** | Aborta |
| 7 | `BalanceEx`: muestra el saldo EUR y lo compara con `capital_cap`; avisa si el saldo es menor | Aviso, continúa |
| 8 | `AssetPairs`: resuelve la lista blanca a pares reales, guarda `venue_instruments` (decimales, `ordermin`, `costmin`) y **avisa de los pares del YAML que no existen o no son negociables** | Aviso, continúa |
| 9 | `Depth` sobre cada par: comprueba los criterios de admisión de liquidez (§5.2) | Aviso por par |
| 10 | `TradeVolume`: lee las **comisiones reales** de la cuenta, las guarda en `venue_config` y recalcula `assumed_roundtrip_bps` | Usa el fallback conservador |
| 11 | **Prueba de humo**: ejecuta un ciclo completo `propose → risk → execute → reconcile` contra `SimulatedBroker` con los datos y comisiones reales recién obtenidos | Aborta |
| 12 | Resumen: venue configurado, modo `simulated`, próximos pasos y comando exacto para arrancar | — |

Para Alpaca, `--venue alpaca_us` sigue los mismos pasos, con dos diferencias: el paso 6
comprueba en su lugar que se trata de una clave de **Trading API** (que por diseño no
puede retirar fondos) y lo documenta explícitamente, y el paso 5 usa una orden de 1 $ en
el entorno paper.

### 10.2 Lista de comprobación previa (README, sección "Empezar con cripto")

> **Antes de ejecutar `make setup-broker VENUE=kraken_eu`, haz esto en Kraken (unos 20
> minutos + espera de verificación):**
>
> 1. **Crea la cuenta** en kraken.com con tu correo.
> 2. **Activa el 2FA** (app de autenticación) en el acceso **y también en la sección de
>    API**.
> 3. **Verifica tu identidad** (DNI + selfie). Suele tardar de minutos a un par de días.
> 4. **Ingresa fondos por SEPA** en euros. Empieza con el importe que hayas fijado en
>    `capital_cap` (por defecto 30 €). Comprueba antes el mínimo de ingreso.
> 5. **Crea la clave API** en *Ajustes → API → Añadir clave*, con **exactamente** estos
>    permisos:
>    - Consultar saldo · Consultar órdenes y operaciones · Crear y modificar órdenes ·
>      Cancelar órdenes
>    - **Retirar fondos — DESMARCADO** (el asistente lo comprobará y se negará a seguir
>      si está marcado)
>    - Margen, futuros, staking — desmarcados
> 6. Pon la **ventana de nonce en 5000** en las opciones de la clave.
> 7. Copia la **API Key** y la **Private Key**. La privada **solo se muestra una vez**.
>
> **No pegues las claves en ningún fichero ni se las mandes a nadie.** El asistente te
> las pedirá y las guardará él.
>
> *Si más adelante quieres acciones, necesitarás además una cuenta en Alpaca y el
> formulario W-8BEN, que lleva más papeleo. Puedes dejarlo para después: el sistema
> funciona solo con cripto.*

---

## 11. "Paper trading" en cripto: qué es y qué no es

**Verificado**: Kraken **no ofrece un sandbox spot autoservicio**. El demo público es de
derivados; el entorno de pruebas spot está disponible **solo bajo petición**. No se puede
planificar sobre algo que hay que negociar.

**Definición operativa**: para `kraken_eu`, `mode: paper` se implementa como
**`SimulatedBroker` alimentado con precios reales en vivo de Kraken** (vía
`KrakenPriceProvider`, endpoints públicos, sin claves):

```python
BrokerMode.PAPER + venue.asset_class == "crypto"
    → SimulatedBroker(price_source=KrakenPriceProvider(), live=True,
                      fee_bps=venue_config.fee_taker_bps, slippage_bps=..., fill_model="next_close")
```

Se registra en `venue_config.broker = 'simulated_live'` para que **la página 9 no diga
"PAPER" a secas**, sino **`SIMULACIÓN CON PRECIOS REALES — no hay órdenes en Kraken`**.
El usuario no debe creer que hay algo suyo en el exchange.

**Qué NO reproduce (a mostrar en la UI, literalmente):**

- **Profundidad de libro**: los fills se asumen completos al precio de referencia más el
  deslizamiento supuesto. En real, una orden puede barrer varios niveles.
- **Deslizamiento real ni horquilla real** en el momento exacto.
- **Fills parciales** por libro fino.
- **Latencia** entre decisión y ejecución.
- **Estados degradados del exchange** (`cancel_only`, `post_only`, mantenimiento) ni
  rechazos por mínimos o precisión.
- **Comportamiento real de los stops nativos**, que es precisamente la pieza en la que
  más confiamos.

**Ajuste de las puertas de validación (modifica la adenda 2 §8) para cripto:**

| Puerta | Ajuste para `kraken_eu` |
|---|---|
| **Puerta 1 (backtest)** | Costes reales de Kraken leídos de la API. **Debe superarse también con coste × 1,5.** Rendimiento positivo en ≥2 de 3 pliegues walk-forward, y desglose por ciclo de mercado visible |
| **Puerta 2 (simulación con precios reales)** | ≥ **90 días naturales** (no 60 sesiones: cripto opera 24/7 pero la estrategia rota poco, hacen falta más días para acumular operaciones) y ≥ **12 operaciones cerradas**. ≤10 % de operaciones marcadas con `user_retro_flag` |
| **Puerta 2-bis · NUEVA: micro-live** | Antes del capital completo: **15 días naturales con el 20 % del `capital_cap` (≈6 €)** en `live(semi)`. Objetivo declarado: **no medir rentabilidad, sino medir la ejecución** — deslizamiento real, comportamiento de los stops nativos, redondeos, mínimos, permisos. Requisitos de salida: ≥3 operaciones reales, deslizamiento real ≤ 2× el asumido, **al menos un stop nativo colocado, verificado y (si es posible) cancelado correctamente**, 0 rechazos por precisión o mínimos, 0 discrepancias de conciliación |
| **Puerta 3 (capital completo)** | Tras la 2-bis, `capital_cap` completo en `live(semi)`. Autonomía solo tras el gate de §8.4 |

La Puerta 2-bis es la respuesta directa a que el papel no reproduce la ejecución real:
**si no se puede simular, se prueba con dinero de verdad pero en cantidad irrelevante.**
Perder 6 € aprendiendo que un stop no se colocaba bien es la inversión más rentable del
proyecto.

---

## 12. Riesgos nuevos

| Riesgo | Por qué importa aquí | Mitigación / postura |
|---|---|---|
| **Riesgo de contraparte del exchange** | Los fondos en Kraken **no son autocustodia**: son un saldo frente a una empresa. Si quiebra, es intervenida o suspende retiradas, el dinero puede quedar bloqueado o perderse, con independencia de lo bien que opere el bot. La historia del sector tiene ejemplos abundantes | Se acepta explícitamente como condición de usar un exchange centralizado (decisión tomada, y correcta frente a la alternativa de la clave privada). Mitigación real: **`capital_cap` bajo** — solo está expuesto lo que se está operando. Regla operativa: **no acumular saldo ocioso en el exchange**; si el bot queda parado mucho tiempo, retirar. Clave sin permiso de retirada protege de un atacante, **no** del riesgo del propio exchange |
| **Volatilidad extrema sin cierre de mercado** | En cripto no hay gaps de fin de semana porque no cierra... pero sí hay desplomes súbitos del 20-30 % en horas, típicamente de madrugada y en fin de semana, cuando el libro es más fino y el usuario duerme | Stop nativo alojado en el exchange (§2), `atr_stop_mult` de 4,0-5,5, `max_daily_loss_pct` de 8 %, `min_cash_pct` de 20 %. **Y honestidad: el stop puede ejecutarse muy por debajo** |
| **Las comisiones erosionan cuentas pequeñas** | Con 30 € y ~1,1 % por ida y vuelta, cada operación cuesta ~0,33 €. Diez operaciones al mes son 3,30 €: **el 11 % del capital al mes en comisiones** | Presupuesto de rotación de §3 con regla dura `max_orders_per_month: 8`, permanencia mínima de 21 días, umbral de señal p80, órdenes limit post-only. La página 9 muestra **"comisiones pagadas este mes / % del capital"** como métrica de primer nivel, junto al PnL |
| **Fiscalidad de cripto en España** | **Cada permuta y cada venta es un hecho imponible** y se declara operación a operación, con cálculo FIFO de la ganancia o pérdida patrimonial. Un bot que opera solo multiplica el número de líneas a declarar. Además existen obligaciones informativas específicas para saldos en el extranjero según importes. **Es un coste oculto real** en tiempo o en asesoría, que puede superar con holgura la ganancia esperada de 30 € | Se documenta como coste antes de habilitar `live`. **Diseño que ayuda**: la tabla `fills` guarda fecha, activo, cantidad, precio, comisión y contravalor en EUR de **cada** ejecución, y se añade `scripts/export_fiscal.py` que genera un CSV con lo necesario para un asesor o una herramienta fiscal. **Esto no es asesoramiento fiscal**: consultar con un profesional antes de operar en real |
| **Dos venues duplican la superficie de fallo** | Dos adaptadores, dos formatos de orden, dos relojes, dos modelos de comisión, dos esquemas de precisión, dos conjuntos de credenciales y dos de límites. Más código que puede fallar tocando dinero | (a) `venue` obligatorio en toda tabla y en toda firma — un olvido es un error de tipo, no un bug silencioso; (b) `RiskManager` **común**, parametrizado, no duplicado; (c) `SimulatedBroker` con test de contrato **idéntico** para ambos adaptadores; (d) **puesta en marcha escalonada**: un venue en real cada vez, nunca dos a la vez (§13) |
| **Nonce inutilizado / clave bloqueada** | Un nonce desordenado deja la clave inservible hasta superar el valor anterior; con timestamps en ms, un error puede bloquearla durante horas | Guarda monótona persistida, una clave por proceso, `flock`, ventana de nonce en Kraken, comprobación de deriva de reloj en el arranque |
| **Deriva del reloj del portátil** (suspensión, cambio de hora) | Rompe el nonce y desalinea las marcas temporales de la contabilidad diaria | Comprobación contra `/0/public/Time` en cada arranque; > 5 s → no opera |
| **Falsa sensación de seguridad por el stop nativo** | Saber que hay un stop en el exchange invita a dejar el bot desatendido más tiempo del prudente | El stop nativo se presenta en la UI como *"red de seguridad, no garantía"*, con el aviso de ejecución por debajo del nivel siempre visible junto al número |

---

## 13. Fases reencajadas *(sustituye a la adenda 2 §11)*

**Principio de ordenación**: cripto llega antes a dinero real **no porque sea menos
arriesgada en términos de mercado —es más volátil—, sino porque el riesgo operativo es
menor y controlable**: sin regla PDT, sin W-8BEN ni papeleo transfronterizo, mínimos de
5 €, mercado siempre abierto, **y sobre todo stops nativos que sobreviven al portátil
apagado**. El riesgo de mercado se acota con `capital_cap`, que es el instrumento que sí
controlamos.

| Fase | Contenido | Duración | Puerta de salida |
|---|---|---|---|
| **0-4** | Sin cambios (dashboard, datos, señales) | — | — |
| **5** | Sin cambios, en paralelo | — | — |
| **3-bis — Validación cripto** *(extiende la fase 3)* | `KrakenPriceProvider`, backfill de los 6 pares, `crypto_regime.py`, preset `crypto_conservative`, tabla `signal_evidence` con `scope`, pasada de backtest **específica de cripto** con costes reales | 2 días | ≥1 señal cripto con `evidence='validada'`. **Sin esto, la fase 6 no arranca** |
| **6 — Núcleo multi-venue en simulado** | Columna `venue` en 9 tablas, `venue_config`/`venue_instruments`/`venue_state`, `trading.yaml` reestructurado, `RiskManager` parametrizado (reglas 21-24), `SimulatedBroker` por venue con comisiones reales, estrategia `crypto_trend_v1`, `stops.py`. **Sin red, sin claves** | 4-5 días | **Puerta 1** (cripto y acciones, cada una la suya, incl. coste × 1,5) |
| **7 — Kraken: conexión y simulación con precios reales** | `KrakenBroker` completo, `kraken_auth`, nonce, rate limit, **asistente `setup`**, `reconcile --deep`, `heartbeat`, **página 9 multi-venue**, autonomía `paper=auto` | 4-5 días | **Puerta 2 cripto**: 90 días, ≥12 operaciones, ≤10 % marcadas |
| **8 — Kraken micro-live** | `mode: live`, `autonomy: semi` forzada, `capital_cap: 6 €`, stops nativos en producción, aprobación por UI y Telegram, restricciones de `hosting: laptop` | 1 día + **15 días** | **Puerta 2-bis**: ejecución verificada, stop nativo probado, deslizamiento medido |
| **9 — Kraken live con capital completo** | `capital_cap: 30 €` (o 15 € si sigue en portátil), `live(semi)` | **45 días** | Gate `live(semi) → live(auto)` de §8.4 |
| **10 — Kraken live autónomo** | `autonomy: auto` por CLI, informe semanal, vigilancia reforzada | 1 día + observación | Estabilidad 30 días |
| **11 — Alpaca paper** *(en paralelo desde la fase 7)* | `AlpacaBroker` contra el entorno paper real, `autonomy: auto`, estrategias de acciones | 3 días | **Puerta 2 acciones** |
| **12 — Alpaca live** | **BLOQUEADA hasta `hosting: vps`** + cuenta Alpaca verificada + W-8BEN. Migración a VPS (§9.5), luego `live(semi)` con 30 $ | 1 día + 30 sesiones | Gate de autonomía de acciones |

**Reglas de secuenciación innegociables:**

- Ninguna fase de bot antes de la 3 y la 3-bis: **operar señales sin validar en su propia
  clase de activo es el fallo que todo este plan intenta evitar**.
- **Nunca dos venues entrando en `live` a la vez.** Cuando Kraken llegue a la fase 9,
  Alpaca puede estar como mucho en la 11 (paper).
- Cada puerta se supera con **datos en la BD**, no con una impresión. `promotion.py` la
  evalúa y la página 9 muestra el checklist.

**Calendario realista**: la primera orden con dinero real (6 € en Kraken) queda a **unos
4-5 meses** del inicio del proyecto; el capital completo en cripto a **6-7 meses**; las
acciones en real dependen del VPS y del papeleo, previsiblemente **más allá de los 9
meses**. Esos plazos son el diseño funcionando, no un retraso.

---

## E. Ficheros, dependencias y tests nuevos

```
config/
├── trading.yaml                       ← REESTRUCTURADO (bloque venues:)
├── crypto_universe.yaml               ← NUEVO (lista blanca + criterios de admisión)
└── venue_symbol_overrides.yaml        ← NUEVO (ticker canónico ↔ par de Kraken)
src/stocks_tracker/
├── providers/kraken_provider.py       ← NUEVO (público, OHLC/Ticker; implementa PriceProvider)
├── core/crypto_regime.py              ← NUEVO (dominancia BTC, corr. Nasdaq, régimen cripto)
├── trading/
│   ├── brokers/kraken.py              ← NUEVO (privado; único fichero que firma peticiones)
│   ├── brokers/kraken_auth.py         ← NUEVO (firma HMAC-SHA512 + NonceGenerator)
│   ├── brokers/kraken_ratelimit.py    ← NUEVO (token bucket con decaimiento)
│   ├── stops.py                       ← NUEVO (dos capas, sincronización, huérfanos)
│   ├── heartbeat.py                   ← NUEVO
│   ├── setup.py                       ← NUEVO (asistente de 12 pasos)
│   ├── venues.py                      ← NUEVO (resolución de venue, capital, divisa)
│   └── strategies/crypto_trend.py     ← NUEVO (crypto_trend_v1)
scripts/export_fiscal.py               ← NUEVO
docs/migracion_vps.md                  ← NUEVO
```

**Dependencias añadidas**: ninguna obligatoria. La firma de Kraken usa
`hmac`/`hashlib`/`base64` de la biblioteca estándar + `requests` (ya presente). Se
**descartan** `krakenex` (mínimo y desactualizado) y `python-kraken-sdk` (activo, pero
añade dependencia en el módulo que toca dinero, donde el algoritmo cabe en 25 líneas
verificables contra el vector de referencia publicado). Opcional para `crypto_regime`:
ninguna — CoinGecko se consulta con `requests`.

**Tests nuevos** (continúan la numeración de la adenda 2):

24. `test_kraken_auth.py` — la firma coincide con el vector de referencia publicado por
    Kraken; el nonce es estrictamente creciente ante reloj retrasado, suspensión y
    reinicio; dos instancias con la misma clave no arrancan (cerrojo).
25. `test_kraken_precision.py` — `to_volume()` **trunca** y nunca redondea al alza; los
    precios respetan `pair_decimals`; por debajo de `ordermin`/`costmin` se produce
    `VETO`, nunca una orden redondeada al alza.
26. `test_broker_contract.py` — **parametrizado por adaptador** (`SimulatedBroker`,
    `AlpacaBroker` mockeado, `KrakenBroker` mockeado): los tres cumplen el mismo contrato
    y devuelven los mismos DTOs.
27. `test_stops_dual.py` — el stop nativo se coloca tras el fill; el fallo repetido cierra
    la posición; al cerrar se cancela **antes** de vender; los huérfanos se detectan; el
    resync respeta `max_native_resyncs_per_day`; `stop_price_hard` siempre queda por
    debajo del stop lógico.
28. `test_reconcile_offline.py` — con un stop ejecutado durante una ausencia de 72 h: se
    detecta el fill, se cierra la posición, se recalcula la equity y **se aplica el kill
    switch retroactivamente** si se superó un límite.
29. `test_venue_isolation.py` — el `HALT_NEW` de un venue no afecta al otro; `--venue ALL`
    los para todos; no hay ninguna consulta que sume importes de divisas distintas.
30. `test_autonomy_policy.py` — `simulated`/`paper` → `auto`; entrar en `live` fuerza
    `semi` aunque el YAML diga otra cosa; el cambio de modo reinicia los contadores; un
    `HALTED` por drawdown degrada `auto` → `semi`.
31. `test_hosting_rule.py` — con `hosting: laptop`, `alpaca_us` en `live` lanza
    `ConfigError`; `kraken_eu` en `live` exige stop nativo en toda posición y bloquea
    estrategias con `requires_intraday_supervision`.
32. `test_setup_wizard.py` — con una clave mockeada **con** permiso de retirada, el
    asistente **aborta y no deja la clave escrita**; con deriva de reloj > 5 s, aborta;
    `.env` se crea con permisos `0600`; las claves no aparecen en `stdout` ni en los logs.
33. `test_fee_budget.py` — `max_orders_per_month` se aplica; la regla `min_expected_edge`
    veta operaciones cuya ventaja esperada no cubre 1,5× el coste de ida y vuelta.
34. `test_arch_broker_isolation.py` — **ampliado**: ni `alpaca` ni el código de firma de
    Kraken se importan fuera de `trading/brokers/`; `run_bot.py` no contiene condicionales
    por nombre de venue relativos a horarios.

---

## Ficheros críticos de esta adenda

- `src/stocks_tracker/trading/brokers/kraken.py` — adaptador completo; concentra todas las diferencias con Alpaca
- `src/stocks_tracker/trading/brokers/kraken_auth.py` — firma y nonce; un fallo aquí bloquea la clave o, peor, duplica una orden
- `src/stocks_tracker/trading/stops.py` — las dos capas de stop; es la protección que sobrevive al ordenador apagado
- `config/trading.yaml` — límites, capital y políticas independientes por venue
- `src/stocks_tracker/trading/setup.py` — el asistente; incluye la verificación innegociable de que la clave no puede retirar fondos
- `src/stocks_tracker/trading/reconcile.py` — conciliación profunda; sin ella, el alojamiento en portátil no es defendible

## Referencias

- [Kraken · Add Order (REST)](https://docs.kraken.com/api/docs/rest-api/add-order/)
- [Kraken · Ejemplos de órdenes con distintos parámetros](https://support.kraken.com/articles/360000920786-examples-of-placing-orders-with-different-parameters)
- [Kraken · Órdenes stop loss](https://support.kraken.com/articles/7699391647892-stop-loss-orders)
- [Kraken · Autenticación REST spot](https://docs.kraken.com/api/docs/guides/spot-rest-auth/)
- [Kraken · Algoritmo de autenticación para endpoints privados](https://support.kraken.com/articles/360029054811-what-is-the-authentication-algorithm-for-private-endpoints-)
- [Kraken · Rate limits REST spot](https://docs.kraken.com/api/docs/guides/spot-rest-ratelimits/)
- [Kraken · Mínimos de criptomonedas](https://support.kraken.com/articles/360001389303-overview-of-cryptocurrency-minimums)
- [Kraken · Mínimo de coste para operar](https://support.kraken.com/articles/12425041458708-cost-minimum-for-trading)
- [Kraken · Cambios de tramos de comisiones (julio 2026)](https://support.kraken.com/articles/cross-platform-fee-tier-changes)
