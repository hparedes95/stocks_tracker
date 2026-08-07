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

Proyecto en fase de diseño. Todavía no hay código: este repositorio contiene por
ahora únicamente el plan de implementación.

## Documentación

| Documento | Contenido |
|---|---|
| [`docs/00-plan-general.md`](docs/00-plan-general.md) | Arquitectura, modelo de datos, catálogo de factores y señales, scoring, backtesting, alertas, fases, riesgos |
| [`docs/01-adenda-tradingview-asistente.md`](docs/01-adenda-tradingview-asistente.md) | Estrategia de visualización con TradingView, mapeo de símbolos, reenfoque de la interfaz como asistente diario |
| [`docs/02-adenda-bot-trading.md`](docs/02-adenda-bot-trading.md) | Capa de trading automatizado con Alpaca: gestión de riesgo, flujo de aprobación, estrategias, validación previa, seguridad |

Los documentos son acumulativos: las adendas modifican y amplían secciones
concretas del plan general, y cada una empieza con un índice de qué sustituye.

## Decisiones de partida

- **Stack**: Python 3.11+ y Streamlit multipágina.
- **Datos**: fuentes gratuitas (yfinance para precios y fundamentales, FRED para
  macro), aisladas tras una capa de proveedores intercambiables.
- **Universo**: EE.UU. (S&P 500, Nasdaq 100), Europa (IBEX 35, Euro Stoxx 50),
  ETFs e índices, más cripto y materias primas como termómetro de riesgo.
- **Gráficos**: widgets de TradingView para el mercado, `lightweight-charts` para
  nuestros datos y señales, Plotly para todo lo que no es una serie de precio.
- **Bot**: Alpaca (simulado → papel → real), mandato conservador diversificado,
  aprobación humana de cada orden antes de cualquier automatismo.

## Alcance excluido explícitamente

Modelos predictivos y machine learning. El proyecto ordena, filtra y explica lo
que ocurre en el mercado; no intenta anticiparlo.
