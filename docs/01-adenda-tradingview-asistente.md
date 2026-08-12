# Adenda 1 — Visualización con TradingView y reenfoque como asistente diario

Amplía y modifica [`00-plan-general.md`](00-plan-general.md).

---

## A. Índice de sustituciones

| Sección original | Estado | Qué la sustituye |
|---|---|---|
| §0 fila "stack de gráficos" (Plotly) | **Modificada** | §B: estrategia de visualización de tres niveles |
| §3 tabla `instruments` | **Ampliada** | §C.1: columnas `tv_symbol`, `tv_exchange`, `tv_verified`, `tv_source` |
| §5 completa (diseño del dashboard) | **Sustituida** | §B.4 (asignación de gráficos por página) + §D.1 (nueva página 1) + §D.3 (nueva página 3) |
| §5 "Página 1 — Visión general del mercado" | **Sustituida íntegramente** | §D.1 "¿Qué se mueve hoy?" |
| §5 "Página 3 — Screener / Oportunidades" | **Ampliada** | §D.3: vista de tarjetas + `config/explanations.yaml` |
| §2 estructura de directorios | **Ampliada** | §E: nuevos módulos y ficheros |
| §6 backtesting (encuadre) | **Reenmarcada** | §D.4: "filtro de ruido", no motor predictivo. Métricas y disciplina **sin cambios** |
| §8 plan por fases | **Sustituido** | §G: fases reordenadas |
| §9 dependencias | **Ampliada** | §F |
| §10 tabla de riesgos | **Ampliada** | §H: 6 riesgos nuevos |
| §9 tests mínimos | **Ampliada** | §I: 5 tests nuevos |
| §1, §3 (resto), §4, §6 (métricas), §7 | **Sin cambios** | — |

---

## B. Estrategia de visualización en tres niveles

### B.0 El trade-off, explícito

| | Nivel 1: widgets embebidos | Nivel 2: `lightweight-charts` | Nivel 3: Plotly |
|---|---|---|---|
| **Datos** | De TradingView (caja negra) | **Nuestros** (`prices_daily`) | Nuestros |
| **Nuestras señales encima** | Imposible | Total | Total |
| **Coste de construcción** | ~0 (pegar snippet) | Medio (componente JS) | Bajo |
| **Riqueza / familiaridad** | Muy alta (es TradingView) | Media (lo que programemos) | Baja para velas |
| **Funciona sin internet** | No | Sí (con JS vendorizado) | Sí |
| **Sirve en backtest / export** | No | Sí | Sí |
| **Dónde encaja** | Contexto, exploración, macro, noticias | Nuestra verdad analítica | Gráficos que no son de precio |

**Regla de decisión permanente del proyecto:**

> Si el gráfico muestra **el mercado**, va widget de TradingView.
> Si el gráfico muestra **lo que nosotros hemos calculado sobre el mercado**, va `lightweight-charts`.
> Si el gráfico **no es una serie de precio**, va Plotly.

Los niveles 1 y 2 se muestran juntos en la página 4 en pestañas
(`st.tabs(["Gráfico TradingView", "Gráfico con nuestras señales"])`), de modo que
el usuario tiene la herramienta familiar y, al lado, exactamente lo mismo con
nuestros marcadores. Esta redundancia es intencionada: valida visualmente que
nuestros cálculos coinciden con la referencia del mercado.

---

### B.1 Nivel 1 — Widgets embebidos de TradingView

**Implementación única y centralizada**: `src/stocks_tracker/app/components/tv_widgets.py`.
Ninguna página escribe HTML de widget directamente.

```python
# app/components/tv_widgets.py — firmas
def _render(widget: str, config: dict, height: int, key: str) -> None
    """Construye el <div class="tradingview-widget-container"> + <script src=
    'https://s3.tradingview.com/external-embedding/embed-widget-{widget}.js'>
    con json.dumps(config) inline, incluye SIEMPRE el div de atribución,
    y llama a st.components.v1.html(html, height=height+12, scrolling=False)."""

def advanced_chart(tv_symbol, interval="D", studies=None, height=720, key=...)
def ticker_tape(symbols: list[dict], height=78)
def technical_analysis(tv_symbol, interval="1D", height=450)
def symbol_info(tv_symbol, height=180)
def fundamental_data(tv_symbol, height=500)
def company_profile(tv_symbol, height=420)
def stock_heatmap(data_source="SPX500", grouping="sector", height=600)
def crypto_heatmap(height=550)
def economic_calendar(countries=("us","eu","es"), height=560)
def top_stories(tv_symbol=None, feed_mode="all_symbols", height=580)
def screener(market="america", default_column="overview", height=560)
def market_overview(tabs: list[dict], height=520)
def mini_symbol_overview(tv_symbol, height=220)
```

**Catálogo, alturas y ubicación:**

| Widget | `embed-widget-*` | Altura | Página | Rol |
|---|---|---|---|---|
| Ticker Tape | `ticker-tape` | **78 px** (`displayMode:"regular"`) | **`app/main.py`, cabecera global** (fuera de la navegación, antes de `page.run()`) | Pulso permanente: SPX, NDX, IBEX, SX5E, VIX, DXY, oro, petróleo, BTC, EURUSD |
| Advanced Real-Time Chart | `advanced-chart` | **620 px** (700 en pantalla ancha) | **Pág. 4**, pestaña 1 | Velas + indicadores + herramientas de dibujo |
| Technical Analysis gauge | `technical-analysis` | **450 px** | **Pág. 4**, columna derecha | Resumen multi-timeframe. **Etiquetado como "opinión técnica de TradingView, contraste externo — no es nuestra señal"** |
| Symbol Info | `symbol-info` | **180 px** | **Pág. 4**, cabecera | Precio, variación, capitalización |
| Fundamental Data | `financials` | **500 px** | **Pág. 4**, pestaña "Fundamentales" | Estados financieros completos — cubre el hueco de yfinance, especialmente en Europa |
| Company Profile | `symbol-profile` | **420 px** | **Pág. 4**, pestaña "Perfil" | Descripción, sector, sede, empleados |
| Stock Heatmap | `stock-heatmap` | **600 px** | **Pág. 2** (`dataSource`: `SPX500`, `NASDAQ100`, `IBC`, `SX5E`; `grouping:"sector"`, `blockSize:"market_cap_basic"`, `blockColor:"change"`) | Visión instantánea de qué sector tira hoy |
| Crypto Coins Heatmap | `crypto-coins-heatmap` | **550 px** | **Pág. 2**, pestaña "Cripto" | Apetito por riesgo |
| Economic Calendar | `events` | **560 px** | **Pág. 6** | Próximos datos macro US/EU/ES |
| Top Stories / Timeline | `timeline` | **580 px** | **Pág. 1** (`feedMode:"all_symbols"`) y **pág. 4** (`feedMode:"symbol"`) | Noticias. Complementa, no sustituye, a `news_items` |
| Screener | `screener` | **560 px** | **Pág. 3**, pestaña secundaria | **Complemento explícito, nunca sustituto** de nuestro screener. La primera pestaña es siempre la nuestra |
| Market Overview | `market-overview` | **520 px** | **Pág. 6** | Índices / Bonos / Divisas / Materias primas |
| Mini Symbol Overview | `mini-symbol-overview` | **220 px** | **Pág. 5** | Vistazo rápido sin salir de la watchlist |

**Reglas transversales obligatorias:**

1. **Atribución**: el `<div class="tradingview-widget-copyright">` con el enlace a
   `tradingview.com` es **condición de la licencia de uso gratuito**. `_render()`
   lo inyecta siempre y **ninguna página puede desactivarlo**. Test
   (`test_tv_widgets.py::test_attribution_always_present`) que comprueba que el
   HTML generado contiene el enlace.
2. **Tema claro/oscuro**: `colorTheme` se deriva de `st.context.theme.type`
   (fallback: `st.get_option("theme.base")`) y se guarda en
   `st.session_state["ui_theme"]`. Como `components.html` cachea el iframe por
   posición, el `key` de cada widget debe **incluir el tema**
   (`key=f"tv_chart_{tv_symbol}_{theme}"`) para forzar el remontaje al cambiar; si
   no, el widget se queda con el tema antiguo. `"backgroundColor": "rgba(0,0,0,0)"`
   para heredar el fondo de Streamlit.
3. **`locale: "es"`** y `"timezone": "Europe/Madrid"` por defecto, configurable en
   `settings.yaml → ui.tradingview`.
4. **Altura**: `st.components.v1.html(height=H+12)`. El margen de 12 px evita la
   barra de scroll interna que aparece por el div de atribución.
5. **Carga diferida**: los widgets pesados (heatmap, screener, calendar) van dentro
   de `st.expander` o `st.tabs` para que no se instancien 6 iframes a la vez.
6. **Sin dependencia de datos**: TradingView **no** entra en `providers/`. Es capa
   de presentación. Ningún cálculo del sistema puede depender de un widget.

---

### B.2 Nivel 2 — `lightweight-charts` con nuestros datos y señales

**Evaluación de wrappers (agosto 2026):**

| Opción | Estado | Veredicto |
|---|---|---|
| `freyastreamlit/streamlit-lightweight-charts` | El original; poco activo, anclado a la API v3/v4 | Descartado |
| `streamlit-lightweight-charts-ntf` | Fork congelado en v3.8.0, sin desarrollo | Descartado |
| `streamlit-lightweight-charts-v5` | Publicado en PyPI el 9 de julio de 2026, integra la v5 con multi-panel | Vivo y actual, pero muy joven |
| `streamlit-lightweight-charts-pro` (nandkapadia) | Activo, API fluida, anotaciones y visualización de operaciones. **Un solo mantenedor** | Rico pero con riesgo de bus factor = 1 |

**Decisión: componente propio `app/components/lwc.py`.** Motivos:

- No necesitamos comunicación bidireccional (no hay click-to-Python en nuestros
  gráficos: la selección se hace en la tabla/tarjeta, no en el lienzo). Sin ese
  requisito, un componente bidireccional real es sobreingeniería y un wrapper de
  terceros solo añade una dependencia de un mantenedor.
- Son ~100 líneas de JS inline en `components.html`, con acceso **completo** a la
  API v5 (los wrappers siempre van por detrás).
- **El fichero JS se vendoriza** en
  `app/static/lightweight-charts.standalone.production.js` (Apache 2.0,
  redistribución permitida con aviso de licencia) en lugar de cargarlo del CDN →
  el nivel 2 **funciona sin internet**, que es justo lo que arregla la mayor
  limitación del nivel 1.

Se mantiene `streamlit-lightweight-charts-v5` documentado en el README como plan B
si en algún momento hiciera falta interactividad bidireccional.

**API v5 — puntos que el implementador debe conocer** (cambian respecto a todos los
tutoriales v4):

- Series: `chart.addSeries(LightweightCharts.CandlestickSeries, {...})`.
  **`addCandlestickSeries()` ya no existe.**
- Marcadores: `LightweightCharts.createSeriesMarkers(series, markersArray)`.
  **`series.setMarkers()` ya no existe** en `ISeriesApi`; los marcadores son ahora
  un *primitive* con ciclo de vida propio.
- **El `time` de cada marcador debe coincidir exactamente con el `time` de un punto
  existente de la serie; si no, el marcador se descarta en silencio.** Es el fallo
  más probable de esta parte. Mitigación obligatoria: `lwc.py::to_lwc_time(d) -> str`
  emite `"YYYY-MM-DD"` para datos **y** marcadores, y `snap_to_session(date, index)`
  desplaza toda fecha de señal al día hábil disponible más cercano hacia atrás
  antes de generar el marcador. Test dedicado (§I).
- Niveles de soporte/resistencia:
  `series.createPriceLine({price, color, lineWidth, lineStyle, axisLabelVisible, title})`.
- Bandas de Bollinger: dos `LineSeries` (superior/inferior) + `AreaSeries`
  translúcida, o un *primitive* de banda. Empezar por las dos líneas.
- Multi-panel (volumen, RSI, MACD): `chart.addPane()` en v5, o `priceScaleId: ''`
  con `scaleMargins` para el volumen superpuesto.

**Firma del componente:**

```python
# app/components/lwc.py
def price_chart(
    ohlcv: pd.DataFrame,                 # date, open, high, low, close, volume
    overlays: dict[str, pd.Series] = None,   # {"SMA200": s, "BB sup": s, "BB inf": s}
    markers: list[Marker] = None,        # Marker(time, position, color, shape, text)
    price_lines: list[PriceLine] = None, # soportes/resistencias calculados por nosotros
    panes: dict[str, pd.DataFrame] = None,   # {"RSI": df, "MACD": df}
    height: int = 520, theme: str = "dark", key: str = "",
) -> None

def markers_from_signals(signals: pd.DataFrame, index: pd.DatetimeIndex) -> list[Marker]
    """bullish → arrowUp verde belowBar; bearish → arrowDown rojo aboveBar;
       text = signal_id legible. Aplica snap_to_session()."""

def equity_chart(curves: dict[str, pd.Series], height=380, key="") -> None
    """Curvas de equity del backtest (baseline vs señal vs deciles)."""
```

**Dónde va el nivel 2 (y solo aquí):**

- **Pág. 4**, pestaña "Nuestras señales": velas + SMA20/50/200 + Bollinger propias
  + marcadores de todas las señales históricas + líneas de soporte/resistencia
  calculadas por `core/indicators.py` + paneles RSI/MACD/volumen.
- **Pág. 3**, mini-gráfico de cada tarjeta (`height=140`, sin ejes, solo línea de
  cierre 6 meses + SMA200 + marcador de la señal más reciente).
- **Pág. 7 (backtest)**, íntegramente: curvas de equity, gráfico de la señal con
  los eventos marcados. Los widgets aquí son **inservibles** por definición.
- **Pág. 5**, gráfico de valor de la cartera con marcadores de compras/ventas de
  `positions`.

---

### B.3 Nivel 3 — Plotly, ámbito reducido

Plotly **deja de ser el motor de gráficos de precio** y queda restringido a:

| Gráfico | Página |
|---|---|
| RRG de rotación sectorial (dispersión con estelas + cuadrantes) | 2 |
| Radar de los 7 factores de estilo | 4, 5 |
| Barras divergentes de contribución por factor | 3, 4 |
| Treemap propio (agrupado por *tipo de inversión*, dimensión que el heatmap de TradingView no ofrece) | 2 |
| Histogramas de distribución de retornos forward, deciles | 7 |
| Gauge del semáforo risk-on/risk-off y desglose de componentes | 1, 6 |
| Series de amplitud (% > MM200, línea AD, máx/mín 52s) | 1, 2 |
| Curva de tipos y heatmap de correlaciones | 6, 2 |
| Donuts de exposición de cartera | 5 |

---

### B.4 Asignación consolidada por página

| Pág. | Nivel 1 (widgets TV) | Nivel 2 (lightweight-charts) | Nivel 3 (Plotly) |
|---|---|---|---|
| **main.py** (global) | Ticker Tape | — | — |
| **1 ¿Qué se mueve hoy?** | Top Stories | Sparklines de los movers | Gauge de régimen, amplitud, barras sector |
| **2 Sectores/rotación** | Stock Heatmap, Crypto Heatmap | — | RRG, treemap por tipo, RS relativa, heatmap sector×horizonte |
| **3 Oportunidades** | Screener TV (pestaña 2ª) | Mini-gráfico de cada tarjeta | Barras de contribución |
| **4 Ficha de valor** | Advanced Chart, Technical Analysis, Symbol Info, Fundamental Data, Company Profile, Top Stories | Gráfico con nuestras señales + S/R + paneles | Radar de factores, ratios vs mediana sectorial |
| **5 Cartera/watchlist** | Mini Symbol Overview por fila | Equity de cartera con marcadores de operaciones | Donuts, radar factorial agregado |
| **6 Macro y riesgo** | Economic Calendar, Market Overview | — | Curva de tipos, VIX, spreads, semáforo histórico |
| **7 Backtest** | **Ninguno** (imposible por diseño) | Todos los gráficos de precio y equity | Distribuciones, deciles, IC por año |
| **8 Alertas/config** | — | — | Estado de ingesta (barras) |

---

## C. Mapeo de símbolos yfinance ↔ TradingView

Es el punto de fricción real. Se resuelve en la ingesta, **nunca en la UI**.

### C.1 Cambios en `instruments`

```sql
ALTER TABLE instruments ADD COLUMN tv_symbol   VARCHAR;  -- 'NASDAQ:AAPL'
ALTER TABLE instruments ADD COLUMN tv_exchange VARCHAR;  -- 'NASDAQ'
ALTER TABLE instruments ADD COLUMN tv_verified BOOLEAN DEFAULT FALSE;
ALTER TABLE instruments ADD COLUMN tv_source   VARCHAR;  -- 'rule'|'override'|'manual'
```

### C.2 `core/symbols.py` (módulo nuevo)

```python
def to_tv_symbol(ticker: str, exchange: str | None, asset_class: str,
                 overrides: dict[str, str]) -> str | None:
    """Orden de resolución: 1) overrides YAML  2) reglas por clase de activo
       3) reglas por sufijo de mercado  4) reglas por código de bolsa yfinance
       5) None (→ degradación elegante)."""
def from_tv_symbol(tv: str) -> str | None      # inversa, para depurar
def tv_exchange_of(tv: str) -> str
```

**Reglas por sufijo / bolsa (tabla de verdad del módulo):**

| Origen yfinance | Regla | Ejemplo |
|---|---|---|
| Sin sufijo, `exchange` ∈ {`NMS`,`NGM`,`NCM`} | `NASDAQ:{t}` | `AAPL` → `NASDAQ:AAPL` |
| Sin sufijo, `exchange` = `NYQ` | `NYSE:{t}` | `JPM` → `NYSE:JPM` |
| Sin sufijo, `exchange` ∈ {`PCX`,`ASE`} (NYSE Arca/AMEX) | `AMEX:{t}` | `SPY`, `XLK` → `AMEX:SPY`, `AMEX:XLK` |
| `.MC` | `BME:{t}` | `SAN.MC` → `BME:SAN` |
| `.DE` | `XETR:{t}` | `SAP.DE` → `XETR:SAP` |
| `.PA` / `.AS` / `.BR` / `.LS` | `EURONEXT:{t}` | `ASML.AS` → `EURONEXT:ASML` |
| `.MI` | `MIL:{t}` | `ENI.MI` → `MIL:ENI` |
| `.L` | `LSE:{t}` | `SHEL.L` → `LSE:SHEL` |
| `.SW` | `SIX:{t}` | `NESN.SW` → `SIX:NESN` |
| `.CO`/`.ST`/`.HE` | `OMXCOP:`/`OMXSTO:`/`OMXHEX:` | `NOVO-B.CO` → `OMXCOP:NOVO_B` (guion → guion bajo) |
| `-USD` (cripto) | `CRYPTO:{base}USD` por defecto | `BTC-USD` → `CRYPTO:BTCUSD` |
| `=X` (divisa) | `FX:{par}` | `EURUSD=X` → `FX:EURUSD` |
| `=F` (futuro) | vía `overrides` (sin regla fiable) | `GC=F` → `COMEX:GC1!` |
| `^...` (índice) | vía `overrides` (sin regla fiable) | `^GSPC` → `SP:SPX` |

**Normalizaciones previas a la regla**: `.` interno → `_` (`BRK.B` → `NYSE:BRK.B`,
que TradingView acepta con punto; pero `NOVO-B` requiere `_`) y mayúsculas. Estas
excepciones son exactamente el motivo de tener overrides.

### C.3 `config/symbol_overrides.yaml` (fichero nuevo)

```yaml
# Casos sin regla fiable o con excepción conocida.
# Preferimos alias TVC:* para macro porque son estables y no dependen de bolsa.
indices:
  "^GSPC":     SP:SPX
  "^NDX":      NASDAQ:NDX
  "^DJI":      DJ:DJI
  "^IBEX":     BME:IBC
  "^STOXX50E": INDEX:SX5E
  "^FTSE":     INDEX:UKX
  "^VIX":      TVC:VIX
commodities:
  "GC=F": COMEX:GC1!      # alternativa de visualización: TVC:GOLD
  "SI=F": COMEX:SI1!
  "HG=F": COMEX:HG1!
  "CL=F": NYMEX:CL1!      # alternativa: TVC:USOIL
  "NG=F": NYMEX:NG1!
fx_macro:
  "DX-Y.NYB": TVC:DXY
  "EURUSD=X": FX:EURUSD
rates:
  "DGS10": TVC:US10Y
  "DGS02": TVC:US02Y
crypto:
  "BTC-USD": CRYPTO:BTCUSD   # usar BINANCE:BTCUSDT solo si se quiere libro concreto
  "ETH-USD": CRYPTO:ETHUSD
equities:
  "NOVO-B.CO": OMXCOP:NOVO_B
  "BRK-B":     NYSE:BRK.B    # yfinance usa guion, TradingView punto
  "RDSA.AS":   LSE:SHEL      # reubicaciones/fusiones
blacklist:                   # tickers que NO existen en TradingView → forzar nivel 2
  - "ALGUN.TICKER.RARO"
```

### C.4 Resolución en la ingesta

`ingest/universe.py::resolve_tv_symbols()` se ejecuta al final de cada refresco de
universo: aplica `to_tv_symbol()`, escribe `tv_symbol`/`tv_exchange`/`tv_source`, y
deja `tv_verified = FALSE`.

**Verificación opcional y manual**: `scripts/validate_tv_symbols.py` consulta el
endpoint de búsqueda de símbolos de TradingView
(`https://symbol-search.tradingview.com/symbol_search/?text=...&exchange=...`) para
marcar `tv_verified`. Es un endpoint **no documentado**: se usa **solo** en este
script, ejecutado a mano, con pausa entre peticiones y resultado persistido en BD.
**Prohibido llamarlo en tiempo de render.**

### C.5 Degradación elegante (regla no negociable)

`app/components/tv_widgets.py::advanced_chart()` y todas las funciones de nivel 1
hacen:

```
if not tv_symbol:  →  lwc.price_chart(...)  +  st.caption("Sin equivalencia en TradingView; se muestra nuestro gráfico.")
```

**Nunca** se renderiza un iframe con símbolo vacío o inválido: el widget mostraría
"Invalid symbol" y parecería una app rota. El helper
`app/data_access.py::get_tv_symbol(ticker) -> str | None` es la única vía de
acceso, y devuelve `None` también si el ticker está en `blacklist`.

Además, `settings.yaml → ui.tradingview.enabled: true|false` permite un **modo
offline global** que fuerza el nivel 2 en toda la app.

---

## D. Reenfoque: asistente de seguimiento, no predictor

Cambio de encuadre que atraviesa toda la UI: la app responde a **"¿qué está pasando
y qué merece que lo mire?"**, no a "¿qué va a pasar?". Consecuencias de lenguaje,
obligatorias en toda la interfaz: *"destaca hoy"*, *"cumple N criterios"*,
*"históricamente"*, *"merece revisión"*. Prohibido: *"predice"*, *"va a subir"*,
*"señal de compra"*, *"objetivo"*.

### D.1 Página 1 — "¿Qué se mueve hoy?"

Fichero: `app/pages/1_que_se_mueve_hoy.py`. Estructura de arriba abajo:

**Bloque 0 — Resumen en lenguaje natural** (destacado, `st.info`, 3-5 frases).
Generado por `core/narrative.py`, **plantillas deterministas, sin LLM**.

**Bloque 1 — Mayores movimientos del día** (dos columnas, tablas de 10)

```sql
-- data_access.get_movers(universe, date, n=10, min_dollar_vol=1_000_000)
SELECT i.ticker, i.name, i.gics_sector, p.close, d.ret_1d, d.rel_volume_20,
       f.composite_pctile
FROM indicators_daily d
JOIN instruments i USING (ticker)
JOIN prices_daily p USING (ticker, date)
LEFT JOIN factor_scores f ON f.ticker = d.ticker AND f.date = d.date
WHERE d.date = ?  AND i.ticker IN (SELECT ticker FROM universe_membership
                                   WHERE universe = ? AND (valid_to IS NULL OR valid_to >= ?))
  AND p.close * p.volume > ?          -- filtro de liquidez, evita chicharros
ORDER BY d.ret_1d DESC LIMIT ?;       -- y la variante ASC para las bajadas
```

Columnas con `column_config.LineChartColumn` (sparkline 20 sesiones) y
`ProgressColumn` para el percentil de score.

**Bloque 2 — Rupturas de máximos / mínimos de 52 semanas**

```sql
-- data_access.get_breakouts_52w(universe, date)
SELECT ... FROM indicators_daily d
JOIN indicators_daily prev ON prev.ticker = d.ticker AND prev.date = ?  -- sesión anterior
WHERE d.date = ? AND d.dist_52w_high >= -0.002 AND prev.dist_52w_high < -0.002
```

(la condición sobre la sesión previa evita repetir el mismo valor 15 días
seguidos). Variante simétrica para mínimos.

**Bloque 3 — Volumen inusual**:
`WHERE d.date = ? AND d.rel_volume_20 > 2.0 ORDER BY rel_volume_20 DESC LIMIT 15`,
con la columna `ret_1d` al lado para distinguir acumulación de capitulación, y
enlace a la noticia más reciente de `news_items` si existe.

**Bloque 4 — Cambios de tendencia detectados hoy**

```sql
-- data_access.get_trend_changes(universe, date)
SELECT s.ticker, i.name, i.gics_sector, s.signal_id, s.direction, s.strength, s.detail
FROM signals s JOIN instruments i USING (ticker)
WHERE s.date = ? AND s.signal_id IN
      ('GOLDEN_CROSS','DEATH_CROSS','MACD_BULL_CROSS','RSI_OVERSOLD_REVERSAL',
       '52W_HIGH_BREAKOUT','PULLBACK_IN_UPTREND','BB_SQUEEZE','NEW_DOWNTREND')
ORDER BY s.direction, s.strength DESC;
```

Agrupado en dos columnas (alcistas / bajistas) con chips de color.

**Bloque 5 — Sectores líderes y rezagados** (día / semana / mes).
`data_access.get_sector_performance()` agrega **mediana** de `ret_1d`, `ret_5d`,
`roc_1m` por `gics_sector` (mediana, no media: es robusta a un valor que se dispara
por una OPA). Barras horizontales divergentes en Plotly, ordenadas por el horizonte
elegido, con el mismo cálculo replicado por **tipo de inversión** en una segunda
pestaña. Debajo, el **Stock Heatmap de TradingView** como confirmación visual.

**Bloque 6 — Pulso del mercado**: gauge del `risk_score`, `% > MM200` con su
variación semanal, nuevos máx vs mín, y correlación media. Todo desde
`breadth_daily` y `regime_daily` del día.

**Columna lateral derecha**: widget **Top Stories** de TradingView + últimas alertas
disparadas.

### D.2 `core/narrative.py` — resumen determinista

```python
@dataclass
class MarketContext:
    date: date; universe: str
    sector_leaders: list[tuple[str, float]]; sector_laggards: list[tuple[str, float]]
    n_breakouts_high: int; n_breakouts_low: int
    pct_above_sma200: float; pct_above_sma200_prev_week: float
    advances: int; declines: int
    regime: str; risk_score: float; risk_score_prev: float
    vix: float; vix_pctile: float
    n_volume_spikes: int; index_ret_1d: float
    top_signal_counts: dict[str, int]

def render_market_summary(ctx: MarketContext) -> list[str]
def render_sector_summary(ctx, sector: str) -> str
```

Reglas de generación (cada una emite una frase solo si se cumple su condición, en
este orden de prioridad; máximo 5 frases):

| Regla | Condición | Plantilla |
|---|---|---|
| `LEAD` | siempre | "Hoy lidera **{sector_leader}** ({ret:+.1%}) y se queda atrás **{sector_laggard}** ({ret:+.1%})." |
| `BREADTH_TREND` | \|Δ semanal %>MM200\| > 4 pp | "La amplitud **{mejora\|se deteriora}**: {pct:.0f} % de los valores está sobre su MM200 ({delta:+.0f} pp en una semana)." |
| `BREADTH_EXTREME` | pct < 30 o > 80 | "Amplitud en zona **{extrema baja\|de euforia}** ({pct:.0f} %); históricamente estas lecturas coinciden con movimientos amplios en ambos sentidos." |
| `BREAKOUTS` | n ≥ 3 | "{n} valores rompen máximos anuales frente a {m} en mínimos." |
| `DIVERGENCE` | índice +, declines > advances | "El índice sube ({r:+.1%}) pero **caen más valores de los que suben** ({d} vs {a}): la subida está concentrada en pocos nombres." |
| `REGIME_FLIP` | cambia `regime` | "El semáforo de riesgo pasa a **{regime}** (score {s:+.0f}, antes {p:+.0f})." |
| `VIX` | percentil > 0.85 o < 0.15 | "El VIX está en {v:.1f}, percentil {p:.0%} del último año." |
| `SIGNALS` | cualquier `signal_id` con ≥5 ocurrencias | "Destacan {n} casos de *{señal legible}*." |
| `VOLUME` | n ≥ 5 | "{n} valores negocian más del doble de su volumen habitual." |
| `QUIET` | ninguna otra dispara | "Sesión sin movimientos destacables: nada relevante que revisar hoy." |

Cada frase se testea por separado (`test_narrative.py`), y `render_market_summary`
**nunca** puede emitir una frase con verbo en futuro — test de guardarraíl que busca
`["subirá","bajará","va a","predice","recomendamos"]` en la salida.

### D.3 Página 3 — Vista de tarjetas explicables

Dos modos conmutables: `st.segmented_control(["Tarjetas", "Tabla"])`, **tarjetas por
defecto**.

`app/components/cards.py::render_candidate_card(row, contributions, signals, flags)`
pinta, en `st.container(border=True)` dentro de una rejilla de 2-3 columnas:

```
┌──────────────────────────────────────────────────────┐
│ IBE.MC · Iberdrola          Utilities · Dividendo    │
│ 12,84 €  +0,8 %             Score 78  ▓▓▓▓▓▓▓░░ p92  │
│ ┌── mini-gráfico lightweight-charts, 140 px ───────┐ │
│ │ cierre 6m + MM200 + marcador de la última señal  │ │
│ └──────────────────────────────────────────────────┘ │
│ A FAVOR                                              │
│  ✓ Cotiza por encima de su MM200 desde hace 84 ses.  │
│  ✓ PER 11,2 frente a la mediana de su sector (14,8)  │
│  ✓ RSI en 34: sobreventa dentro de tendencia alcista │
│  ✓ Rentabilidad por dividendo 5,2 % con payout 68 %  │
│  ✓ Volumen 2,3× su media de 20 sesiones              │
│ A VIGILAR                                            │
│  ⚠ Deuda neta/EBITDA 4,1× (alta para su sector)      │
│  ⚠ Ingresos +1,2 % interanual: crecimiento plano     │
│  ⚠ Presenta resultados en 4 días                     │
│ Cobertura de datos 82 %   [Ficha]  [+ Watchlist]     │
└──────────────────────────────────────────────────────┘
```

**Selección de motivos** (`core/explain.py::build_reasons()`):

1. Tomar `factor_contributions` del ticker/fecha/`weights_hash`, ordenar por
   `|contribution|`.
2. Para cada sub-métrica, buscar su plantilla en `config/explanations.yaml` y
   evaluar la condición.
3. Devolver hasta **5 pros** (contribución > 0 y condición cumplida) y hasta
   **3 contras**, sin repetir dos veces el mismo factor.
4. Añadir siempre las señales activas de hoy (`signals`) traducidas por
   `SIGNAL_LABELS`.
5. Si no hay ninguna frase disponible (cobertura baja), mostrar *"Aparece por su
   puntuación técnica agregada; datos fundamentales insuficientes para justificarlo
   mejor."* — honestidad antes que relleno.

**`config/explanations.yaml` (fichero nuevo)** — plantillas por sub-métrica:

```yaml
sector_median_source: fundamentals_snapshot   # se calcula al vuelo por peer_group
submetrics:
  trailing_pe:
    pro: {when: "z <= -0.75", text: "PER {x:.1f} frente a la mediana de su sector ({median:.1f})"}
    con: {when: "z >= 1.0",   text: "PER {x:.1f}, caro frente a su sector ({median:.1f})"}
  fcf_yield:
    pro: {when: "x >= 0.06",  text: "Genera caja: FCF yield del {x:.1%}"}
  roe:
    pro: {when: "z >= 0.75",  text: "ROE del {x:.1%}, por encima de sus comparables"}
  net_debt_to_ebitda:
    con: {when: "x >= 3.0",   text: "Deuda neta/EBITDA {x:.1f}×, elevada para su sector"}
  revenue_growth_yoy:
    pro: {when: "x >= 0.10",  text: "Ingresos +{x:.0%} interanual"}
    con: {when: "x <= 0.02",  text: "Ingresos {x:+.0%} interanual: crecimiento plano"}
  dividend_yield:
    pro: {when: "x >= 0.03 and payout_ratio < 0.8",
          text: "Rentabilidad por dividendo {x:.1%} con payout del {payout_ratio:.0%}"}
    con: {when: "payout_ratio > 1.0", text: "Paga más en dividendos que lo que gana (payout {payout_ratio:.0%})"}
  rsi14:
    pro: {when: "14 <= x < 40 and above_sma200", text: "RSI en {x:.0f}: sobreventa dentro de tendencia alcista"}
    con: {when: "x > 75", text: "RSI en {x:.0f}: zona de sobrecompra"}
  above_sma200:
    pro: {when: "x == True", text: "Cotiza por encima de su MM200 desde hace {days_above} sesiones"}
    con: {when: "x == False", text: "Cotiza por debajo de su MM200: tendencia de fondo bajista"}
  mom_12_1:
    pro: {when: "z >= 1.0", text: "Momentum 12-1 de {x:+.0%}, entre los mejores de su sector"}
  rel_volume_20:
    pro: {when: "x >= 1.8", text: "Volumen {x:.1f}× su media de 20 sesiones"}
  dist_52w_high:
    pro: {when: "x >= -0.03", text: "A un {x:.1%} de sus máximos anuales"}
    con: {when: "x <= -0.40", text: "Un {x:.0%} por debajo de máximos anuales"}
  drawdown:
    con: {when: "x <= -0.35", text: "En caída del {x:.0%} desde su máximo: puede seguir bajando"}
signal_labels:
  PULLBACK_IN_UPTREND: "Corrección dentro de tendencia alcista"
  GOLDEN_CROSS: "Cruce dorado (MM50 sobre MM200)"
  MACD_BULL_CROSS: "MACD cruza al alza"
  52W_HIGH_BREAKOUT: "Ruptura de máximos anuales con volumen"
  EARNINGS_SURPRISE_DRIFT: "Sorpresa positiva en resultados reciente"
```

**Banderas rojas** — `core/flags.py::red_flags(row) -> list[str]`, independientes
del score y **siempre visibles** aunque el score sea alto: payout > 100 %, deuda
neta/EBITDA > 4, drawdown < −35 %, por debajo de MM200, cobertura < 50 %,
resultados en menos de 5 días (`earnings_events`), volatilidad 252d en decil
superior, `$` de volumen medio < umbral (iliquidez), divisa distinta a la de la
cartera.

**Filtros** (`app/components/filters.py`, barra lateral compartida): universo,
sector GICS, tipo de inversión, capitalización (micro/small/mid/large/mega), rango
de precio, **perfil de riesgo** (`conservador` / `equilibrado` / `agresivo`) que
aplica un preset de pesos + guardas: conservador fuerza `above_sma200`,
`lowvol_z > 0`, `dividend_yield > 0`, excluye cripto y micro-caps; agresivo relaja
las guardas y permite momentum puro.

### D.4 Backtesting reenmarcado

La página 7 se retitula **"Validación de señales"** y su texto de cabecera fija el
propósito:

> Esta sección **no predice** nada. Sirve para **descartar** señales que se
> comportan igual que el azar. Una señal solo permanece en el dashboard si, fuera
> de muestra, bate a la referencia con muestra suficiente. Que haya funcionado no
> garantiza que funcione.

- Cada señal lleva una etiqueta persistente en `signals` (columna nueva
  `evidence VARCHAR`: `validada` / `débil` / `no_validada` / `sin_datos`), asignada
  por `backtest/run_backtest.py --tag-signals` según: IC-IR out-of-sample > 0,3
  **y** n ≥ 100 **y** exceso sobre benchmark > 0.
- En las tarjetas y en la ficha, las señales `no_validada` se muestran en gris con
  tooltip *"sin evidencia histórica suficiente; se muestra a título informativo"*.
- **Prioridad explícitamente baja** (fase 5 o nunca) para cualquier componente de
  machine learning, modelo predictivo o "probabilidad de subida". El proyecto no
  incorpora `scikit-learn` ni equivalentes.

---

## E. Cambios en la estructura de directorios

```
config/
├── symbol_overrides.yaml        ← NUEVO (§C.3)
└── explanations.yaml            ← NUEVO (§D.3)
src/stocks_tracker/
├── core/
│   ├── symbols.py               ← NUEVO  to_tv_symbol / from_tv_symbol
│   ├── narrative.py             ← NUEVO  resumen determinista
│   ├── explain.py               ← NUEVO  build_reasons() (extraído de scoring.py)
│   └── flags.py                 ← NUEVO  red_flags()
└── app/
    ├── static/
    │   └── lightweight-charts.standalone.production.js   ← NUEVO (vendorizado, Apache 2.0)
    ├── components/
    │   ├── tv_widgets.py        ← NUEVO  nivel 1
    │   ├── lwc.py               ← NUEVO  nivel 2
    │   └── cards.py             ← NUEVO  tarjetas de candidato
    └── pages/
        └── 1_que_se_mueve_hoy.py   ← RENOMBRA a 1_vision_general.py
scripts/
└── validate_tv_symbols.py       ← NUEVO (manual, no en el cron)
```

`app/components/charts.py` (Plotly) se **conserva** con alcance reducido al nivel 3.

---

## F. Dependencias

**Añadir**: nada obligatorio. El nivel 1 son iframes (HTML puro) y el nivel 2 usa JS
vendorizado — ambos se sirven con `streamlit` a secas.

**Opcional**, documentado en `pyproject.toml` bajo
`[project.optional-dependencies] charts_alt`:

```
streamlit-lightweight-charts-v5>=0.1   # plan B si se necesita interactividad bidireccional
```

**No añadir** `streamlit-lightweight-charts` (original, anclado a v3/v4) ni `-ntf`
(congelado en 3.8.0).

**Fichero de licencia**: `app/static/LICENSE-lightweight-charts.txt` (Apache 2.0)
junto al JS vendorizado, y mención en el README. Requisito legal de la
redistribución.

---

## G. Fases reordenadas

### Fase 0 — Esqueleto (0,5 día)

Sin cambios. Se añade `core/symbols.py` y `config/symbol_overrides.yaml` desde el
principio (el DDL ya incluye `tv_symbol`).

### Fase 1 — MVP "asistente diario" (3-4 días) ★

**Entra ahora (antes estaba más tarde o no estaba):**

- Ingesta de precios + fundamentales snapshot para el universo reducido (~250
  tickers: S&P 100 + IBEX 35 + Euro Stoxx 50 + 20 ETFs + índices + macro tickers).
  Los fundamentales son **una sola pasada de 250 peticiones cacheada 7 días** —
  asumible y necesaria para que las tarjetas puedan decir "PER por debajo de la
  mediana de su sector".
- Indicadores P0 + amplitud **mínima** (`pct_above_sma200`, `pct_above_sma50`,
  nuevos máx/mín, advances/declines) — es barato y alimenta el resumen narrativo.
- Señales P0: `GOLDEN_CROSS`, `DEATH_CROSS`, `PULLBACK_IN_UPTREND`,
  `RSI_OVERSOLD_REVERSAL`, `MACD_BULL_CROSS`, `52W_HIGH_BREAKOUT`, `VOLUME_SPIKE`.
- **Página 1 "¿Qué se mueve hoy?" completa** + `core/narrative.py`.
- **Nivel 1 de TradingView**: Ticker Tape global, Advanced Chart, Technical
  Analysis, Symbol Info, Top Stories → la ficha de valor queda rica **el primer día
  y con esfuerzo casi nulo**.
- **Mapeo de símbolos** completo + degradación elegante + test de cobertura del
  universo.
- **Página 3 con tarjetas explicables** + `core/explain.py` +
  `config/explanations.yaml` + `core/flags.py`.
- Página 4 (ficha) con widgets; scoring con `momentum`, `technical`, `value` básico
  y `dividend`.

**Se desplaza para compensar:**

- **Nivel 2 (`lwc.py`) → fase 2.** En fase 1 el gráfico principal es el widget de
  TradingView y los mini-gráficos de las tarjetas son `st.line_chart` provisionales.
  Es el mejor canje del plan: gráficos ricos desde el día 1 sin construir nada.
- Línea AD acumulada, correlación media por pares, treemap, RRG → fase 2.
- Soportes/resistencias, ADX, ATR, Bollinger, OBV (indicadores P1) → fase 2.
- Factores `growth`, `quality`, `lowvol`, `size` → fase 2.
- Página 6 (macro/FRED) → fase 2.

**Entregable**: `make run` abre un asistente que responde, con datos propios y
gráficos de TradingView, a "qué se mueve hoy", "qué sectores tiran" y "qué valores
merecen que los mire y por qué", con justificación en frases legibles.

### Fase 2 — Profundidad de mercado y gráficos propios (3-4 días)

Universo completo (~750 tickers) · fundamentales escalonados · factores restantes ·
`breadth.py` y `relative.py` completos · **`app/components/lwc.py` + JS vendorizado
+ marcadores de señales + S/R + Bollinger propias** (pestaña "Nuestras señales" en
pág. 4, mini-gráficos reales en las tarjetas) · páginas 2 (con Stock/Crypto Heatmap)
y 6 (con Economic Calendar y Market Overview).

### Fase 3 — Validación de señales (2 días)

Motor de backtest, métricas, walk-forward, página 7 **construida íntegramente sobre
`lwc.py` y Plotly** (los widgets no sirven aquí). Etiquetado `evidence` de cada
señal y **poda efectiva** del catálogo de §4.8.

### Fase 4 — Operativa diaria (1-2 días)

Alertas, cron, página 8, watchlist y cartera (página 5, con Mini Symbol Overview por
fila).

### Fase 5 — Refinamiento (continuo)

Sentimiento y noticias propias, Fear&Greed proxy, `StooqPriceProvider`, calidad de
datos europeos, presets, modo offline global, tema visual. **Explícitamente fuera de
alcance**: modelos predictivos y machine learning.

---

## H. Riesgos añadidos

| Riesgo | Impacto | Mitigación |
|---|---|---|
| **Los widgets requieren internet en el navegador del usuario** | Sin conexión, la app aparece medio vacía | El nivel 2 con JS vendorizado funciona offline; `ui.tradingview.enabled: false` fuerza el modo local en toda la app; cada widget tiene su equivalente de nivel 2 o 3 |
| **Los widgets no se pueden exportar** a PDF/imagen desde el servidor | No hay informes ni capturas automatizadas | Cualquier función de exportación usa **exclusivamente** niveles 2 y 3. Restricción de diseño, no de implementación |
| **Los widgets son inservibles en backtest** | La página 7 no puede apoyarse en ellos | Ya contemplado: pág. 7 = 100 % nivel 2 + Plotly |
| **Dependencia de que TradingView mantenga los widgets gratuitos** y de su atribución obligatoria | Podrían cambiar términos, limitar o retirar el servicio | El nivel 1 es **puramente decorativo/contextual**: ningún cálculo, alerta ni score depende de él. Si desapareciera, se pierde comodidad visual, no funcionalidad. La atribución se inyecta siempre y es intocable |
| **Símbolos sin equivalencia o mal mapeados** (renombres, fusiones, small caps europeas) | Iframes rotos con "Invalid symbol" | `tv_symbol` nulo → nivel 2; `blacklist` en YAML; `scripts/validate_tv_symbols.py` manual; test de cobertura del universo (§I) |
| **Marcadores descartados en silencio** en `lightweight-charts` v5 si el `time` no coincide con un punto de la serie | Señales que no aparecen y nadie se entera | `to_lwc_time()` único para datos y marcadores + `snap_to_session()` + test dedicado (§I) |
| **Ruptura de API entre v4 y v5** (la mayoría de tutoriales y wrappers siguen en v4) | Código copiado que no funciona | JS vendorizado con versión fijada; notas de API en §B.2 dentro del docstring de `lwc.py`; el JS no se actualiza sin revisar la guía de migración |
| **Riesgo de encuadre**: un gauge de TradingView que dice "STRONG BUY" puede leerse como recomendación nuestra | Mal uso, decisiones por delegación | El widget Technical Analysis lleva rótulo obligatorio *"Opinión técnica de TradingView. Contraste externo, no es nuestra señal ni una recomendación."* Test de que el rótulo se renderiza |

---

## I. Tests añadidos

8. **`test_symbols.py`** — (a) tabla de casos de `to_tv_symbol()` para las 12
   reglas de §C.2 incluyendo `BRK-B`, `NOVO-B.CO`, `SAN.MC`, `SPY`, `^GSPC`,
   `GC=F`, `BTC-USD`; (b) **`test_universe_fully_mapped`**: carga `universe.yaml` +
   `symbol_overrides.yaml` y verifica que **todo** ticker del universo configurado
   resuelve a un `tv_symbol` no nulo o está en `blacklist` — falla el CI si alguien
   añade un mercado sin regla; (c) `from_tv_symbol(to_tv_symbol(t)) == t` para el
   subconjunto reversible.
9. **`test_tv_widgets.py`** — la atribución aparece en el HTML de los 13 widgets; el
   `key` incluye el tema; con `tv_symbol=None` la función delega en
   `lwc.price_chart` y **no** emite iframe; el rótulo de aviso del gauge está
   presente.
10. **`test_lwc.py`** — `markers_from_signals()` sobre un índice con festivos: todo
    marcador devuelto tiene un `time` que existe en el índice de datos (test que
    blinda el fallo silencioso de la v5); `to_lwc_time()` produce el mismo formato
    para series y marcadores.
11. **`test_narrative.py`** — cada regla dispara con su contexto y calla sin él; el
    resumen nunca supera 5 frases; guardarraíl de lenguaje (sin futuros ni verbos de
    recomendación); con contexto vacío devuelve la frase `QUIET`.
12. **`test_explain.py`** — `build_reasons()` devuelve ≤5 pros y ≤3 contras, no
    repite factor, respeta las condiciones del YAML, y con `coverage < 0.3` devuelve
    el mensaje honesto de datos insuficientes; todas las plantillas de
    `explanations.yaml` formatean sin `KeyError` con una fila sintética completa.

---

## Ficheros críticos de esta adenda

- `src/stocks_tracker/core/symbols.py` — resuelve el punto de fricción yfinance ↔ TradingView; sin él, nada del nivel 1 funciona
- `src/stocks_tracker/app/components/tv_widgets.py` — única puerta a los widgets, atribución, tema y degradación elegante
- `src/stocks_tracker/app/components/lwc.py` — nuestros datos con nuestras señales; la parte que ningún widget puede dar
- `src/stocks_tracker/app/pages/1_que_se_mueve_hoy.py` — la página que materializa el reenfoque de asistente
- `config/explanations.yaml` — convierte los z-scores en frases; es lo que hace explicable el ranking
- `src/stocks_tracker/core/narrative.py` — resumen diario determinista, sin LLM

## Referencias

- [lightweight-charts (TradingView, Apache 2.0)](https://github.com/tradingview/lightweight-charts)
- [Migración v4 → v5](https://tradingview.github.io/lightweight-charts/docs/migrations/from-v4-to-v5)
- [Series markers v5](https://tradingview.github.io/lightweight-charts/tutorials/how_to/series-markers)
- [streamlit-lightweight-charts-v5 (PyPI)](https://pypi.org/project/streamlit-lightweight-charts-v5/)
- [streamlit-lightweight-charts-pro](https://github.com/nandkapadia/streamlit-lightweight-charts-pro)
