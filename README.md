# Stocks Tracker

Aplicación personal de análisis de acciones con fuentes gratuitas. El centro es
el **asesor**, la investigación de escenarios y tu cartera manual o sincronizada.
No ejecuta órdenes ni instala tareas periódicas o avisos externos. La conexión
opcional con eToro mediante SnapTrade Personal es exclusivamente de lectura.

## Windows

Abre `Stocks Tracker.bat`, el mismo lanzador de tu instalación. Conserva la
comprobación de versiones y datos al abrirlo. Cerrar el programa no deja un bot
operando: ya no existen ciclos de trading ni vigilancia en segundo plano.

La actualización elimina únicamente las tareas antiguas de Stocks Tracker
(actualización diaria, ciclo del bot y prueba de backups). Si Windows deniega
permisos, se muestra el error; no se afirma que se hayan retirado.
También puedes ejecutar `scripts/windows/stocks.ps1 autostart-off`.

## Flujo de trabajo

1. **Asesor de acciones**: actualiza los datos o recalcula las recomendaciones.
   Indica el efectivo disponible en euros para dimensionar propuestas; por
   defecto es cero. Con SnapTrade activo usa el efectivo consultado. Ninguna
   propuesta modifica tu cartera.
2. **Predicción y escenarios**: compara episodios de tendencia similares a 1, 3
   o 6 meses (21, 63 o 126 sesiones). Muestra procedencia, fecha y tamaño de muestra.
3. **Ficha de valor / Oportunidades**: revisa factores, riesgos y razones.
4. **Cartera y watchlist**: introduce tus compras reales y revisa concentración.
5. **Estado de los datos / Validación**: comprueba calidad y evidencia.

Para dejar de cargar CSV, abre **Mi cartera → Conexión SnapTrade** y sigue la
[configuración de SnapTrade Personal](docs/07-snaptrade-personal.md). Las claves
se introducen únicamente en la app y se cifran con el usuario de Windows.
Consulta posiciones y efectivo mientras la app está abierta; no reconstruye
el historial de operaciones ni garantiza información en tiempo real.

## Qué significan las estimaciones

Los escenarios son descriptivos y experimentales, no un modelo predictivo
validado. Usan precios ajustados de una sola fuente, episodios no solapados y
entrada en la sesión posterior a la señal. Rechazan datos sintéticos, inválidos,
mezclados o antiguos y se abstienen con menos de veinte episodios comparables.
Los percentiles históricos no son un intervalo de confianza del resultado futuro.
La frecuencia de subidas no es una probabilidad calibrada.

Las recomendaciones del asesor son reglas explicables, no asesoramiento
profesional ni garantía de beneficios. Su fuerza expresa coincidencia con reglas,
no probabilidad de acertar. La validación existente también es experimental:
el marcador no acredita rentabilidad neta, independencia de las observaciones
ni resultados futuros. El marcador separa modelos, perfiles e índices, evalúa
al vencimiento con retorno ajustado en EUR y muestra costes solo si se registraron.
Las recomendaciones repetidas y solapadas de la misma acción no suman nuevos episodios.

## La misma base en varios ordenadores (sin servicios de pago)

1. Actualiza **ambos PC a la misma versión** y usa la misma configuración de análisis.
2. En el equipo con el histórico completo: **Sistema → Base de mercado compartida →
   Preparar archivo → Descargar**. Se genera un archivo `.stmarket`.
3. Traslada el archivo por USB u otro medio privado. **No se transfiere automáticamente.**
4. Importa el mismo archivo en los dos equipos para congelarlos sobre la misma base.
   Una instalación nueva permite introducir su ruta antes de descargar el universo.
5. Comprueba que coincidan la huella y la fecha de la base común en la barra lateral.

Incluye precios, fundamentales acumulados, universo y resultados de mercado. No
incluye posiciones, efectivo, diario, watchlist, claves ni recomendaciones personales.
Por ello los consejos de cartera pueden ser distintos: dependen también de tus compras,
efectivo y perfil. Los widgets externos en directo no pertenecen a la copia.

Una copia importada detiene las descargas y recálculos de mercado, incluido el `.bat`.
Para obtener una base nueva: desactívala en el PC de origen, actualiza, exporta e
importa el nuevo archivo en ambos. Si cambia la versión del motor, genera una copia
con esa versión. No hay sincronización en segundo plano ni almacenamiento en GitHub.
Antes de reemplazar una base local, expórtala si quieres conservarla. La importación
es transaccional y valida integridad y compatibilidad antes de modificar tablas.
Las copias son para uso privado conforme a las condiciones de los proveedores.

## Evidencia y riesgo del asesor

- Configuración breve dentro del asesor: horizonte, costes supuestos, tolerancia
  al escenario de pérdida y límite de posiciones correlacionadas. Sin confirmación
  no se proponen compras; tampoco cuando faltan datos o ventaja neta suficiente.
- Comparación temporal de modelos de tendencia y volatilidad con referencias
  sencillas. No elige retrospectivamente el ganador ni promete probabilidades calibradas.
  Con históricos cortos puede abstenerse durante mucho tiempo: no se rebajan los
  requisitos para generar recomendaciones.
- Escenarios de caída y divisas, correlaciones y riesgo histórico en EUR. Un stop
  no garantiza la pérdida calculada: gaps y ejecución pueden empeorarla.
- Cuentas oficiales SEC/EDGAR sin API key, con periodo, fecha de publicación y
  enlace verificable. Actualizar datos desde el asesor consulta un lote limitado;
  en la ficha puede consultarse un emisor. Cobertura US-GAAP parcial, no toda la bolsa mundial.
  La SEC puede rechazar conexiones (por ejemplo, HTTP 403): se muestra que la fuente
  no está disponible y no se presenta la ausencia de cuentas como una validación oficial.
- Sensibilidad de valoración bajo varios supuestos, no un precio objetivo único.
  La confirmación estadística de rentabilidad real sigue pendiente; los tests de
  software no sustituyen esa evidencia.

## Datos y privacidad

Sin suscripciones obligatorias. Las claves de `.env.example` son opcionales.
Los proveedores gratuitos pueden fallar o limitar peticiones; una única fuente
no equivale a un precio contrastado. No se inventan datos de respaldo.
La aplicación escucha solo en `127.0.0.1`.

La actualización conserva `data/`, cartera, historial y `.env`. No borra tablas
antiguas ni cancela órdenes que pudieras haber colocado fuera de la aplicación.
El código y documentación del bot retirado están en
`archive/retired-automation/`: no forman parte del paquete instalado.

## Desarrollo

Python según `pyproject.toml`. Instala las dependencias bloqueadas de
`requirements-runtime.lock` y el proyecto editable. Comandos de Windows:

```powershell
.\scripts\windows\stocks.ps1 daily
.\scripts\windows\stocks.ps1 consejo -Caja 1500
.\scripts\windows\stocks.ps1 run
.\.venv\Scripts\python.exe -m pytest -q
```

En Linux: `make daily` actualiza manualmente datos, cálculo, asesor e informe.
Los límites del asesor viven en `config/settings.yaml`, sección `advisor.risk`.
