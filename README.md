# Stocks Tracker

Aplicación personal de análisis de acciones con fuentes gratuitas. El centro es
el **asesor**, la investigación de escenarios y tu cartera registrada manualmente.
No ejecuta órdenes, no conecta con brokers y no instala tareas periódicas ni avisos externos.

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
   defecto es cero. Ninguna propuesta modifica tu cartera.
2. **Predicción y escenarios**: compara episodios de tendencia similares a 1, 3
   o 6 meses (21, 63 o 126 sesiones). Muestra procedencia, fecha y tamaño de muestra.
3. **Ficha de valor / Oportunidades**: revisa factores, riesgos y razones.
4. **Cartera y watchlist**: introduce tus compras reales y revisa concentración.
5. **Estado de los datos / Validación**: comprueba calidad y evidencia.

## Qué significan las estimaciones

Los escenarios son descriptivos y experimentales, no un modelo predictivo
validado. Usan precios ajustados de una sola fuente, episodios no solapados y
entrada en la sesión posterior a la señal. Rechazan datos sintéticos, inválidos,
mezclados o antiguos y se abstienen con menos de ocho episodios comparables.
Los percentiles históricos no son un intervalo de confianza del resultado futuro.
La frecuencia de subidas no es una probabilidad calibrada.

Las recomendaciones del asesor son reglas explicables, no asesoramiento
profesional ni garantía de beneficios. Su fuerza expresa coincidencia con reglas,
no probabilidad de acertar. La validación existente también es experimental:
el marcador no acredita rentabilidad neta, independencia de las observaciones
ni resultados futuros. La validación prospectiva y de costes sigue siendo necesaria.

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
