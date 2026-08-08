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

**Fases 1, 2 y 3 funcionando.** El dashboard ingiere datos, calcula indicadores,
factores, señales, amplitud, rotación sectorial y régimen de riesgo, muestra el
ranking de candidatos explicado, y **valida cada señal contra su histórico** para
distinguir las que aportan algo de las que son ruido. El bot de trading todavía
no está implementado (fases 6 en adelante).

## Puesta en marcha

```bash
make setup        # crea el entorno e instala dependencias
make migrate      # crea el almacén DuckDB
make ingest       # descarga datos reales (Yahoo Finance + FRED si hay clave)
make compute      # indicadores, señales, factores, amplitud, rotación y régimen
make validate     # valida las señales contra su histórico y las etiqueta
make run          # abre el dashboard en http://127.0.0.1:8501
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
| **Oportunidades** | Ranking de candidatos en tarjetas, cada uno con sus motivos en castellano y sus banderas rojas |
| **Ficha de valor** | Gráfico de velas con nuestras señales, medias y niveles dibujados encima; gráfico de TradingView; fundamentales frente a la mediana del sector; perfil factorial y riesgo |
| **Watchlist** | Valores seguidos y su evolución desde que se añadieron |
| **Validación de señales** | Qué señales aportan algo y cuáles son ruido: eventos, acierto, exceso sobre el universo, estabilidad entre ventanas y distribución de retornos |
| **Estado de los datos** | Qué se descargó, cuándo, qué falló y qué tickers no tienen equivalencia en TradingView |

## Desarrollo

```bash
make test    # 168 tests, sin red
make lint    # estilo
```

Los tests no tocan la red ni el almacén real: usan el proveedor sintético y una
base de datos temporal. Los dos que más valen:

- `test_no_lookahead.py` altera el futuro de cada serie y comprueba que ningún
  indicador cambia en el pasado. Incluye una contraprueba con un indicador que
  sí mira al futuro, para confirmar que el test detecta el fallo.
- `test_backtest.py` verifica que la entrada está retardada un día: una señal
  detectada al cierre no se puede comprar a ese cierre. Sin ese retardo los
  resultados salen preciosos y el dinero real se pierde.

## Documentación

| Documento | Contenido |
|---|---|
| [`docs/00-plan-general.md`](docs/00-plan-general.md) | Arquitectura, modelo de datos, catálogo de factores y señales, scoring, backtesting, alertas, fases, riesgos |
| [`docs/01-adenda-tradingview-asistente.md`](docs/01-adenda-tradingview-asistente.md) | Estrategia de visualización con TradingView, mapeo de símbolos, reenfoque de la interfaz como asistente diario |
| [`docs/02-adenda-bot-trading.md`](docs/02-adenda-bot-trading.md) | Capa de trading automatizado con Alpaca: gestión de riesgo, flujo de aprobación, estrategias, validación previa, seguridad |
| [`docs/03-adenda-cripto-kraken-multivenue.md`](docs/03-adenda-cripto-kraken-multivenue.md) | Cripto en Kraken como segundo venue: stops nativos en el exchange, presupuesto de comisiones, autonomía por modo, ejecución en un equipo no permanente |

Los documentos son acumulativos y describen el proyecto completo; el código
implementa por ahora las fases 1, 2 y 3. Cada adenda empieza con un índice de qué
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

## Decisiones de partida

- **Stack**: Python 3.11+ y Streamlit multipágina.
- **Datos**: fuentes gratuitas (yfinance para precios y fundamentales, FRED para
  macro), aisladas tras una capa de proveedores intercambiables.
- **Universo**: EE.UU. (S&P 500, Nasdaq 100), Europa (IBEX 35, Euro Stoxx 50),
  ETFs e índices, más cripto y materias primas como termómetro de riesgo.
- **Gráficos**: widgets de TradingView para el mercado, `lightweight-charts` para
  nuestros datos y señales, Plotly para todo lo que no es una serie de precio.
- **Bot**: dos carteras independientes — cripto en Kraken (EUR) y acciones en Alpaca
  (USD) —, mandato conservador diversificado, y aprobación humana obligatoria en el
  momento en que entra dinero real.

## Alcance excluido explícitamente

Modelos predictivos y machine learning. El proyecto ordena, filtra y explica lo
que ocurre en el mercado; no intenta anticiparlo.
