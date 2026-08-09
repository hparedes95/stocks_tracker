# stocks_tracker

Dashboard de monitorización del mercado de valores, detección de oportunidades y
bot de trading experimental, para uso personal.

> ## ⚠️ Aviso
>
> Esta herramienta es un **sistema de apoyo a la decisión** y de **experimentación
> personal**. No es asesoramiento financiero ni fiscal.
>
> Ninguna señal predice el mercado. Todos los resultados son **probabilísticos** y
> se basan en datos con retardo y de calidad variable.
>
> Quien la usa opera **su propio dinero bajo su exclusiva responsabilidad**.
> Ninguna métrica pasada garantiza resultados futuros.

## Estado

**Fases 1 a 5 funcionando, más vigilancia en vivo.** El dashboard ingiere datos, calcula indicadores,
factores, señales, amplitud, rotación sectorial y régimen de riesgo, muestra el
ranking de candidatos explicado, **valida cada señal contra su histórico** para
distinguir las que aportan algo de las que son ruido, y **avisa** cuando pasa
algo relevante en tu cartera, tu watchlist o el mercado. Puedes ver el mismo
universo ordenado por **cinco estilos de inversión** distintos, y si Yahoo deja
de responder hay un **proveedor de respaldo**. Aparte del ciclo diario, un
**vigilante en vivo** avisa al móvil si el mercado se desploma. El bot de
trading todavía no está implementado (fases 6 en adelante).

## Instalacion en Windows

Descarga **[`Instalar Stocks Tracker.bat`](installer/Instalar%20Stocks%20Tracker.bat)**
(boton derecho → *Guardar enlace como…*) y haz doble clic.

Se encarga de todo: instala Python si no lo tienes, descarga el proyecto en tu
carpeta de usuario, prepara el entorno, genera datos de prueba y deja un icono
en el Escritorio. No hace falta ser administrador ni tener git.

Windows SmartScreen avisará de que el fichero no es habitual — es lo normal con
un `.bat` descargado y sin firmar. *Más información* → *Ejecutar de todas
formas*. Puedes leer el fichero antes con el Bloc de notas: son 40 líneas.

Después, para los comandos del día a día:

```powershell
cd $env:LOCALAPPDATA\StocksTracker
.\scripts\windows\stocks.ps1 ingest    # datos reales de Yahoo
.\scripts\windows\stocks.ps1 compute   # recalcular
.\scripts\windows\stocks.ps1 run       # abrir el dashboard
.\scripts\windows\stocks.ps1 watch     # vigilar el mercado en vivo
```

`scripts\windows\stocks.ps1` es el equivalente al Makefile, porque Windows no
trae `make`. El ciclo diario automatico (`daily_update.sh`) es bash: en Windows
usa `stocks.ps1 daily` desde el Programador de tareas.

## Puesta en marcha (macOS y Linux)

```bash
make setup        # crea el entorno e instala dependencias
make migrate      # crea el almacén DuckDB
make ingest       # descarga datos reales (Yahoo Finance + FRED si hay clave)
make compute      # indicadores, señales, factores, amplitud, rotación y régimen
make compute-presets  # puntúa además con los otros estilos de inversión
make validate     # valida las señales contra su histórico y las etiqueta
make alerts       # evalúa las reglas de aviso y las entrega
make run          # abre el dashboard en http://127.0.0.1:8501
make watch        # aparte: vigila el mercado en vivo y avisa si se desploma
```

Si no tienes acceso a Yahoo Finance (red restringida, o solo quieres probar la
interfaz), sustituye el tercer paso por `make ingest-demo`, que genera series
sintéticas realistas sin salir a internet. Los datos son inventados: sirven para
ver funcionar la aplicación, no para tomar ninguna decisión.

> `make run` fija `--server.address 127.0.0.1` de forma deliberada. Streamlit no
> tiene autenticación: exponerlo en `0.0.0.0` deja la aplicación abierta a
> cualquiera en la red. Para acceso remoto, túnel SSH o Tailscale.

La primera descarga baja diez años de histórico y tarda varios minutos: se hace
por lotes y sin hilos a propósito, porque la concurrencia es lo que dispara el
bloqueo de Yahoo. Las siguientes son incrementales y cuestan segundos.

## Qué hay ahora

| Página | Qué responde |
|---|---|
| **Qué se mueve hoy** | Resumen del día en lenguaje natural, mayores movimientos, rupturas anuales, volumen inusual, cambios de tendencia, sectores líderes, amplitud y semáforo de riesgo |
| **Sectores y rotación** | Mapa de rotación (qué sectores lideran y cuáles se debilitan), rendimiento por sector y horizonte, mapa de superficie, amplitud interna |
| **Macro y riesgo** | Semáforo risk-on/risk-off con su desglose, tipos y curva, crédito, actividad, termómetros de mercado y correlación media |
| **Oportunidades** | Ranking de candidatos en tarjetas, cada uno con sus motivos en castellano y sus banderas rojas; selector de estilo de inversión |
| **Ficha de valor** | Gráfico de velas con nuestras señales, medias y niveles dibujados encima; gráfico de TradingView; fundamentales frente a la mediana del sector; perfil factorial y riesgo |
| **Cartera y watchlist** | Tus posiciones con resultado y peso, importación desde eToro y Trade Republic, diagnóstico de concentración (sector, perfil factorial, correlación media entre posiciones) y los valores que sigues |
| **Alertas** | Histórico de avisos (de cierre y en vivo), estado del vigilante de desplomes, canales de entrega y reglas configuradas |
| **Validación de señales** | Qué señales aportan algo y cuáles son ruido: eventos, acierto, exceso sobre el universo, estabilidad entre ventanas y distribución de retornos |
| **Estado de los datos** | Qué se descargó, cuándo, qué falló, calidad de datos por universo, procedencia de los precios y qué tickers no tienen equivalencia en TradingView |

## Desarrollo

```bash
make test    # 376 tests, sin red
make lint    # estilo
```

Los tests no tocan la red ni el almacén real: usan el proveedor sintético y una
base de datos temporal. Los que más valen:

- `test_no_lookahead.py` altera el futuro de cada serie y comprueba que ningún
  indicador cambia en el pasado. Incluye una contraprueba con un indicador que
  sí mira al futuro, para confirmar que el test detecta el fallo.
- `test_backtest.py` verifica que la entrada está retardada un día: una señal
  detectada al cierre no se puede comprar a ese cierre. Sin ese retardo los
  resultados salen preciosos y el dinero real se pierde.
- `test_alerts.py` comprueba que el período de espera suprime el aviso repetido
  al día siguiente, que ninguna expresión peligrosa del YAML llega a ejecutarse
  y que el estado de los canales no filtra credenciales.
- `test_presets.py` comprueba que las consultas devuelven una fila por valor y
  no una por estilo. Verificado quitando el filtro a propósito: cuatro tests
  fallan.
- `test_providers_chain.py` simula la forma real en que Yahoo se rompe —
  responder a medias sin avisar — y comprueba que al respaldo solo se le piden
  los tickers que faltaban.
- `test_brokers.py` comprueba que «1.234,56» y «1,234.56» se leen igual, que
  los lotes de un mismo valor se agrupan con coste medio ponderado, y que un
  ISIN desconocido se reporta en vez de desaparecer sin más.
- `test_watch.py` comprueba que el vigilante no te satura: el mismo nivel no
  avisa dos veces, uno peor avisa al momento aunque hayan pasado dos minutos, y
  la recuperación se anuncia una sola vez. Verificado desactivando el control
  de escalado a propósito: dos tests fallan.

## Documentación

| Documento | Contenido |
|---|---|
| [`docs/00-plan-general.md`](docs/00-plan-general.md) | Arquitectura, modelo de datos, catálogo de factores y señales, scoring, backtesting, alertas, fases, riesgos |
| [`docs/01-adenda-tradingview-asistente.md`](docs/01-adenda-tradingview-asistente.md) | Estrategia de visualización con TradingView, mapeo de símbolos, reenfoque de la interfaz como asistente diario |
| [`docs/02-adenda-bot-trading.md`](docs/02-adenda-bot-trading.md) | Capa de trading automatizado con Alpaca: gestión de riesgo, flujo de aprobación, estrategias, validación previa, seguridad |
| [`docs/03-adenda-cripto-kraken-multivenue.md`](docs/03-adenda-cripto-kraken-multivenue.md) | Cripto en Kraken como segundo venue: stops nativos en el exchange, presupuesto de comisiones, autonomía por modo, ejecución en un equipo no permanente |

Los documentos son acumulativos y describen el proyecto completo; el código
implementa por ahora las fases 1 a 5. Cada adenda empieza con un índice de qué
secciones del plan base sustituye.

## Universo

Los universos `SP500` y `NASDAQ100` descargan la lista real de constituyentes de
Wikipedia (unos 600 valores). Si la descarga falla, el sistema cae a las listas
manuales de `config/universe.yaml` y sigue funcionando con un universo reducido:
preferimos eso a una página en blanco porque una web cambió una cabecera.

La composición se guarda con fecha en `universe_membership`. Hoy los backtests
sufren sesgo de supervivencia —solo se conocen los constituyentes actuales—,
pero ese registro diario hace que el sesgo desaparezca hacia adelante.

## Datos macro (opcional)

Las series de tipos, crédito y actividad vienen de FRED y necesitan una clave
gratuita en `FRED_API_KEY`. Sin ella todo lo demás funciona igual: la página de
macro muestra los termómetros que salen de los precios (VIX, oro, cobre, dólar,
cobre/oro) y avisa de lo que falta. **Ningún cálculo del núcleo depende de esa
clave.**

## Validación de señales

`make validate` mide qué ocurrió después de cada disparo de cada señal y le pone
una etiqueta: **validada**, **débil**, **no validada** o **sin datos
suficientes**. En el dashboard, las señales sin validar se muestran apagadas y
con un aviso: una observación no puede presentarse con la misma autoridad que
algo que ha demostrado aportar valor.

Cuatro decisiones que hacen honesto el resultado:

- **Entrada retardada un día.** Una señal detectada al cierre no se puede
  comprar a ese cierre.
- **Referencia = universo equiponderado**, no un índice. Comparar contra un
  índice mezcla el aporte de la señal con la diferencia estructural entre esas
  acciones y el índice. El error tiene una firma reconocible: señales opuestas
  saliendo ambas ganadoras.
- **Costes incluidos** en la ida y en la vuelta.
- **Solo señales técnicas.** Los fundamentales que guardamos son una foto
  actual, no una serie histórica; validarlos con los datos de hoy sería hacer
  trampa.

Y el sesgo que queda, dicho en la propia página: el universo son los
constituyentes de **hoy**, así que los resultados están sesgados al alza por
supervivencia. Trátalos como una cota superior optimista.

## Estilos de inversión

El mismo universo, ordenado según lo que a ti te importe. Los cinco estilos de
[`config/factors.yaml`](config/factors.yaml) —equilibrado, valor, crecimiento,
dividendo y momentum— reparten los pesos de los siete factores de otra manera y
producen rankings distintos. Se eligen en la barra lateral de **Oportunidades**.

Un estilo **no filtra: reordena**. Las guardas de sensatez (tendencia, RSI,
cobertura, caída máxima) son otra cosa y se controlan aparte.

```bash
make compute-presets   # puntúa el universo con los cinco estilos
```

Los scores de todos los estilos conviven en la misma tabla, distinguidos por un
hash de los pesos. Es un detalle interno con una consecuencia práctica: **toda
consulta a `factor_scores` debe filtrar por ese hash**, o cada valor aparece una
vez por estilo. Hay cuatro tests que lo vigilan porque es un fallo que no da
error, solo multiplica filas en silencio.

## Si Yahoo deja de responder

yfinance es una API **no oficial** que ya se ha roto antes y se volverá a
romper. La cadena de `settings.yaml` es `[yfinance, stooq]`, y el relevo ocurre
**por ticker y después de intentarlo**: lo que el primero no consigue traer se
le pide al segundo. El relevo anterior solo actuaba si el proveedor no se podía
ni construir, que no es como Yahoo se rompe en la práctica —el import funciona,
la llamada funciona, y lo que vuelve está vacío—.

Stooq no pide clave, pero tiene dos limitaciones que conviene conocer:

- **Una petición por ticker**, no por lote. Es el camino de emergencia.
- **No ajusta el cierre por dividendos**, y Yahoo sí. Una serie servida a medias
  por cada fuente tendría un escalón artificial el día del relevo, y ese escalón
  no es un movimiento del mercado pero los indicadores no saben distinguirlo:
  aparece como un retorno enorme y puede disparar una señal.

Por eso la página de **Estado** detecta las series con fuentes mezcladas y
`make repair` las reconstruye enteras desde una sola fuente. Solo reemplaza lo
que consigue volver a descargar: borrar una serie que luego no se puede reponer
sería destruir datos por una mejora.

## Miedo y codicia

El semáforo de riesgo, releído en la escala 0-100 del índice de CNN. **No es un
indicador nuevo**: es la misma cifra. La tentación era construir un Fear & Greed
propio, pero cinco de sus siete componentes ya estaban en el semáforo, así que
habrían salido dos números midiendo casi lo mismo en escalas distintas — y dos
termómetros que no coinciden del todo no dan más información, dan la duda de a
cuál hacer caso.

Lo que sí faltaba eran los dos componentes que el semáforo no tenía, y esos se
han añadido al propio semáforo: **máximos frente a mínimos anuales** (en un
techo, la amplitud aún aguanta mientras los nuevos máximos ya se secan) y
**momentum del índice frente a su media de medio año**.

## Importar tu cartera (eToro, Trade Republic)

**Ninguno de los dos se puede conectar automáticamente**, y conviene saber por
qué antes de buscar alternativas raras:

- **eToro** no ofrece API de lectura de cartera a clientes particulares.
- **Trade Republic** no tiene API pública de ningún tipo. Existen clientes no
  oficiales que inician sesión con tu teléfono y tu PIN; eso significa entregar
  tus credenciales a un script de terceros e incumplir sus condiciones de uso,
  así que **no está implementado aquí** y no pienso implementarlo por defecto.

El camino soportado es exportar del broker e importar el fichero, en **Cartera
y watchlist → Importar desde eToro o Trade Republic**. Es manual y periódico,
pero no le entregas tus credenciales a nadie.

- **eToro**: cuenta → *Historial* → descargar *Extracto de cuenta* (XLSX). Se
  sube tal cual; se detecta sola la hoja de posiciones abiertas.
- **Trade Republic**: perfil → *Documentos / Informes*. Si solo consigues PDF,
  la pestaña *Escribir a mano* trae una tabla editable que para quince valores
  va más rápido que pelearse con el PDF.

Tres cosas que hace el importador y que son la diferencia entre que funcione y
que parezca que funciona:

- **Traduce ISIN a ticker.** Trade Republic identifica todo por ISIN: trae
  `US0378331005` donde nosotros esperamos `AAPL`. La tabla está en
  [`config/isin_map.yaml`](config/isin_map.yaml) con los valores y ETF más
  habituales; lo que no reconozca te lo dice para que lo añadas.
- **Agrupa lotes con coste medio ponderado.** Los brokers listan cada compra
  por separado. Diez acciones a 100 y treinta a 200 dan 175, no 150.
- **Reemplaza, no añade.** Un extracto es una foto completa de tu cartera. Si
  se añadiera, reimportar el mismo fichero duplicaría cada posición y lo que
  hubieras vendido contaría para siempre.

Y una que no puede hacer: `1.234` significa mil doscientos treinta y cuatro
para un europeo y uno coma dos tres cuatro para un anglosajón, y **no hay forma
de saberlo mirando el valor**. Se lee como decimal, se avisa, y por eso el
importador siempre enseña una vista previa con el total invertido: compáralo
con lo que dice tu broker antes de confirmar.

## Vigilancia en vivo (avisos de desplome)

El dashboard analiza **al cierre**. Aparte hay un proceso que mira el precio
cada minuto y avisa al móvil si el mercado se está cayendo:

```bash
make watch          # se queda vigilando
make watch-test     # simula un -8% y manda los avisos de verdad
make watch-status   # qué vigila, cuándo, y si los canales están listos
```

Son dos cosas distintas a propósito. Recalcular el ranking cada minuto daría
señales que cambian toda la mañana y solo significan algo al cierre; enterarse
de que el mercado se desploma es otra pregunta, y esa sí es urgente.

### Qué vigila

Pocos símbolos, en [`config/watch.yaml`](config/watch.yaml): cuatro índices, el
VIX, dos criptos y **tus posiciones abiertas**, que se leen solas del almacén.
Cada símbolo es cuota de peticiones, y un desplome se ve en los índices — no
hace falta sondear 600 valores.

Los umbrales de índice **no son cifras elegidas a ojo**: −7%, −13% y −20% son
los cortacircuitos del mercado estadounidense, los porcentajes de caída del
S&P 500 a los que la SEC detiene la negociación. La cripto lleva umbrales más
anchos porque con los del índice avisaría casi a diario.

### Por qué no te va a saturar

Un mercado parado en −3,2% cumple el umbral de −3% cada minuto durante seis
horas. Sin control eso son 360 mensajes, y a la tercera silencias el canal —
justo lo contrario de lo que quieres el día que haga falta. Así que:

- Solo se avisa **al cruzar un nivel peor** que el ya avisado hoy.
- Un nivel peor sale **al momento**, sin esperar. Pasar de −7% a −13% es pasar
  de cortacircuitos nivel 1 a nivel 2, y hacerte esperar diez minutos para
  contártelo sería tapar el caso para el que existe todo esto.
- Al recuperarse, **un solo** aviso de vuelta a la normalidad, y el sistema se
  rearma.
- Cada sesión empieza de cero: los niveles se miden contra el cierre anterior.

### Pruébalo antes de necesitarlo

Un aviso de desplome que nunca has probado no sirve: te enteras de que estaba
mal configurado el día que importa. `make watch-test` fuerza una caída del 8%
con datos sintéticos y recorre el camino entero —detección, mensaje, envío—.
Debería llegarte al móvil en segundos.

### Dejarlo siempre encendido

En **macOS**, con launchd (`~/Library/LaunchAgents/com.stockstracker.watch.plist`):

```xml
<plist version="1.0"><dict>
  <key>Label</key><string>com.stockstracker.watch</string>
  <key>ProgramArguments</key>
  <array>
    <string>/ruta/a/stocks_tracker/.venv/bin/python</string>
    <string>-m</string><string>stocks_tracker.watch.run_watch</string>
  </array>
  <key>WorkingDirectory</key><string>/ruta/a/stocks_tracker</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
</dict></plist>
```

`launchctl load ~/Library/LaunchAgents/com.stockstracker.watch.plist`.

En **Linux**, con un servicio de usuario en
`~/.config/systemd/user/stocks-watch.service`:

```ini
[Unit]
Description=Vigilante de mercado
[Service]
WorkingDirectory=/ruta/a/stocks_tracker
ExecStart=/ruta/a/stocks_tracker/.venv/bin/python -m stocks_tracker.watch.run_watch
Restart=always
[Install]
WantedBy=default.target
```

`systemctl --user enable --now stocks-watch`.

### Por qué los gráficos de TradingView no valen para esto

Los widgets de TradingView que lleva el dashboard **sí van en vivo** —el ticker
de la cabecera, los mapas de calor, el gráfico de la ficha, el screener, el
calendario—. Pero no pueden avisarte, por dos razones:

- **No se puede leer su contenido.** Son un `iframe` servido desde
  `tradingview.com`, y la política de mismo origen del navegador impide que
  nuestro código lea los precios de dentro. Sacarlos por detrás atacando sus
  endpoints internos incumpliría sus condiciones de uso.
- **Solo existen mientras miras.** Un iframe vive en una pestaña abierta; no
  evalúa umbrales ni manda notificaciones con el portátil cerrado.

**La recomendación honesta**: TradingView tiene alertas propias que corren en
sus servidores, llegan al móvil con el navegador cerrado y usan su feed, sin
los ~15 minutos de retraso de Yahoo. Para "avísame si el S&P se desploma" son
mejores que este vigilante. Botón derecho sobre el gráfico → Añadir alerta →
variación diaria por debajo de −3%. La cuenta gratuita permite un número
limitado de alertas activas.

Lo que ellas no pueden hacer y este vigilante sí: **tu cartera entera ponderada
por posición**, porque TradingView no sabe qué tienes. Usa los dos.

### Lo que este vigilante no puede hacer

Tres limitaciones que conviene tener claras antes de confiar en él:

- **Yahoo sirve la renta variable con unos 15 minutos de retraso.** No es un
  fallo que se pueda arreglar en el código: es lo que hay gratis. Si estás
  mirando la pantalla, te enteras antes por el propio mercado. Su valor está en
  los días en que **no** estás mirando. La cripto sí va al momento.
- **Si tu ordenador está apagado o suspendido, no vigila.** Para cobertura real
  de 24 horas hace falta algo siempre encendido: una Raspberry Pi o un VPS de
  tres euros al mes valen de sobra.
- **Avisa, no actúa.** No vende nada. Eso llegará —con aprobación tuya— con el
  bot de las fases 6 en adelante.

Necesitas **Telegram configurado** para que el aviso te llegue al móvil; con el
canal de fichero solo se escribe en `data/alerts.jsonl`, que no te despierta.
Ver la sección de canales más abajo.

## Alertas y ciclo diario

Las reglas viven en [`config/alerts.yaml`](config/alerts.yaml) y se editan a
mano: cada una tiene un ámbito (`watchlist`, `portfolio`, `market`,
`universe:SP500`, `sector:...`), una condición y un mensaje.

```bash
make alerts-dry   # evalúa sin guardar ni enviar: para afinar una regla nueva
make alerts       # evalúa, guarda y entrega por los canales activos
```

Tres decisiones sostienen que el sistema se siga leyendo dentro de seis meses:

- **Período de espera.** Una vez avisado un valor por una regla, esa regla no
  vuelve a dispararse para él durante `cooldown_days`. Sin eso la misma alerta
  se repetiría cada jornada mientras la condición siguiera siendo cierta, y en
  una semana dejarías de leerlas.
- **Solo señales con evidencia.** Con `require_validated_signals: true`, una
  señal que no ha demostrado nada en `make validate` no interrumpe.
- **Un resumen, no veinte mensajes.** Telegram y correo reciben un único
  mensaje agrupado por gravedad.

Las condiciones se interpretan con un evaluador que recorre el AST y solo
admite comparaciones, booleanos y aritmética. **Nunca se llama a `eval()`**
sobre el texto del YAML.

### Canales

El canal de **fichero** (`data/alerts.jsonl`) viene activado por defecto porque
no depende de nada externo. Telegram y correo se activan en el YAML y leen sus
credenciales **solo del entorno**: el YAML guarda el *nombre* de la variable,
nunca su valor. Ponlas en `.env` (que está en `.gitignore`). Si un canal falla,
los demás siguen entregando: perder un aviso porque Telegram estaba caído sería
el peor resultado posible.

La pestaña **Canales** de la página de alertas muestra qué falta configurar y
permite mandar un mensaje de prueba, sin revelar ningún secreto.

### Automatizar

[`scripts/daily_update.sh`](scripts/daily_update.sh) encadena ingesta, cálculo y
alertas, con `flock` para que dos ejecuciones no se solapen (la ingesta es el
único escritor del almacén) y tolerancia a fallos por paso: es preferible tener
el dashboard con datos de ayer que dejarlo a medias sin alertas.

```cron
# 23:15 de lunes a viernes, con el mercado estadounidense ya cerrado.
15 23 * * 1-5 /ruta/a/stocks_tracker/scripts/daily_update.sh
```

La validación no va en el ciclo diario: cambia poco de un día para otro y es
cara. Ejecútala a mano o con un cron semanal.

## Decisiones de partida

- **Stack**: Python 3.11+ y Streamlit multipágina.
- **Datos**: fuentes gratuitas (yfinance para precios y fundamentales, Stooq de
  respaldo para precios, FRED para macro), aisladas tras una capa de proveedores
  intercambiables.
- **Universo**: EE.UU. (S&P 500, Nasdaq 100), Europa (IBEX 35, Euro Stoxx 50),
  ETFs e índices, más cripto y materias primas como termómetro de riesgo.
- **Gráficos**: widgets de TradingView para el mercado, `lightweight-charts` para
  nuestros datos y señales, Plotly para todo lo que no es una serie de precio.
- **Bot**: dos carteras independientes — cripto en Kraken (EUR) y acciones en Alpaca
  (USD) —, mandato conservador diversificado, y aprobación humana obligatoria en el
  momento en que entra dinero real.

## Pendiente de la fase 5

La fase 5 es refinamiento continuo. Queda sin hacer, a propósito:

- **Noticias y sentimiento** (Finnhub / Marketaux). Requieren clave y no se
  pueden probar de verdad sin ella; las variables ya están reservadas en
  `.env.example`.
- **Exportación de un informe diario** en Markdown o PDF. Hoy solo hay descarga
  a CSV en Oportunidades y en Alertas.
- **Verificar el mapeo de Stooq mercado por mercado.** Los sufijos siguen la
  convención pública de Stooq, pero solo está comprobado el caso estadounidense;
  un ticker europeo que Stooq no reconozca queda registrado como fallido, que es
  el comportamiento correcto de un respaldo pero no una cobertura garantizada.

## Alcance excluido explícitamente

Modelos predictivos y machine learning. El proyecto ordena, filtra y explica lo
que ocurre en el mercado; no intenta anticiparlo.
