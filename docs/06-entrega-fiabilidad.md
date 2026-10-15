# Fiabilidad: implementación y límites

Base de partida: `00db28f`, 1.753 pruebas aprobadas y una omitida; cobertura de
ramas/líneas combinada 69,87 %. Python local de ensayo: 3.14, Windows.
Este informe distingue implementación de evidencia de inversión: ninguna prueba
de software demuestra que una estrategia vaya a ganar dinero.

## Cambios preparados

1. Instalador: descarga fijada al SHA, dependencias con hashes, preparación en
   carpeta hermana, comprobación de datos privados antes y después de copiar,
   migración y arranque comprobados antes de escribir `.version`. No borra la
   instalación anterior. Un fallo durante la promoción restaura código y datos.
2. Calendarios de bolsa para 21/63/126 sesiones. La regla de meses naturales se
   conserva para recomendaciones antiguas. No se desplaza una ejecución porque
   falte una cotización. FX y fundamentales respetan la fecha de corte.
3. Primer resultado cerrado conservado en `evaluation_ledger`; las correcciones
   del proveedor se registran por separado en `price_revisions`. Reingestar un
   precio igual no crea una revisión por el mero cambio de `ingested_at`.
4. Simulación de investigación que llama a `calculate_policy`, la misma política
   del asesor. Cartera separada, caja, posiciones, costes, FX, movimientos y NAV;
   referencia prefijada VT en EUR y efectivo al 0 %. Tres ensayos visibles: base,
   costes dobles y retraso de una sesión. Incluye intentos fallidos en el registro.
5. Cuentas anuales oficiales por periodo, unidad, publicación y documento.
   Sensibilidad sobre mediana de tres ejercicios comparables, con caja aproximada
   atribuible al accionista. Ausencias no equivalen a cero; bancos, aseguradoras,
   inmobiliarias y series incompletas quedan fuera de esta fórmula.
6. Protocolos predictivos preinscritos: candidato fijo, evaluación temporal,
   error estándar conservador con HAC, corrección por universo y tres horizontes,
   estabilidad temporal y coste doble. El periodo final empieza después del
   registro; la evidencia retrospectiva no activa una compra. Una refutación no
   se sustituye por un resultado posterior favorable del mismo protocolo.
7. Copia local automática antes de importar mercado; se conservan tres puntos y
   pueden restaurarse desde la app. Previsualización de fechas, motor y filas.
   Acción explícita de actualizar y preparar archivo, sin nube ni sincronización.
8. Ampliación de histórico de hasta treinta años para un valor, su referencia y
   divisa; pausa de siete días entre intentos, incluso si no hay datos. SEC aplica
   pausa de 24 horas ante indisponibilidad. Ninguna de estas acciones es programada.
9. Las tarjetas separan datos, evidencia y encaje personal y muestran versión y
   fechas. Los protocolos del modelo viajan en la copia de mercado: un PC nuevo
   puede conservar su seguimiento sin copiar cartera, efectivo ni perfil de riesgo.

## Uso

- Abre el `.bat` habitual después de publicar esta versión y cierra primero las
  ventanas de Stocks Tracker para que el actualizador pueda copiar la base.
- En **Predicción y escenarios**, solicita más histórico si hace falta y usa el
  apartado de simulación para revisar movimientos, abstenciones y costes.
- En **Asesor**, confirma los cuatro supuestos personales y recalcula. El estado
  de los protocolos explica por qué todavía no hay autorización predictiva para
  comprar. Las advertencias de deterioro/concentración siguen disponibles.
- En **Base de mercado compartida**, actualiza/prepara/exporta en el origen y
  previsualiza/importa en el destino. La copia previa se guarda automáticamente;
  transportar el archivo sigue siendo manual. Para igualdad estricta, importa
  también la misma copia en el origen y mantenla congelada.
- Actualizar código no transmite datos entre PC. Misma copia, motor, parámetros
  y corte reproducen el mercado; cartera, caja y riesgo personales pueden cambiar
  el consejo. Una versión de motor distinta exige una copia recién exportada con
  esa misma versión, no reutilizar silenciosamente una copia incompatible.

## Recuperación del programa

El instalador conserva carpetas `StocksTracker.previous-*` junto a la instalación,
con código y datos de antes de actualizar. No deben publicarse: contienen datos
privados. El manifiesto hermano `StocksTracker.recovery.json` identifica la última.
Con la app cerrada, el módulo `installer/release_install.py --restore RUTA` restaura
ese conjunto. Antes guarda el estado actual en `StocksTracker.before-restore-*`:
los movimientos introducidos después de actualizar no desaparecen, pero tampoco
se fusionan automáticamente con la cartera anterior. Las carpetas de instalación
retenidas requieren espacio en disco. El instalador conserva las dos últimas copias
anteriores que él mismo identifica como administradas; no borra carpetas heredadas
sin esa marca. Limpia su propia carpeta staging si la preparación falla y comprueba
espacio libre antes de preparar y de copiar los datos privados.

## Límites que permanecen visibles

- **Replay parcial:** las fuentes disponibles no acreditan todos los metadatos,
  anuncios de resultados, bajas del universo ni fundamentales históricos. Solo
  se admiten rankings registrados en su fecha; no se reconstruyen con balances
  actuales. Un informe parcial nunca habilita el asesor completo.
- **Splits:** el almacén heredado no declara uniformemente si cada fuente ajustó
  `close`. El libro contable tiene la operación de split comprobada, pero el
  replay real se detiene si un split afecta a una posición sin ese contrato.
  Resolver la normalización por proveedor sigue pendiente; no se adivinan ratios.
- **Dividendos:** se reconocen como derechos por cobrar, sin reinvertirlos; falta
  fecha de pago verificable. Los impuestos personales no se simulan.
- **Valoración:** es sensibilidad hipotética, no precio objetivo. No modela
  adquisiciones ni dilución futura; su deuda neta informativa no es deuda total
  acreditada. No mezcla valor de empresa y capitalización ni resta deuda dos veces.
- **Validación final:** necesita observaciones futuras vencidas, potencialmente
  años para acumular suficientes episodios. No se puede completar con más pruebas
  de programación ni con una descarga. Durante ese tiempo es normal no recibir
  propuestas de compra validadas; análisis y avisos de riesgo siguen funcionando.
- **SEC:** se comprobó un bloqueo HTTP 403 en este entorno. Se conserva como fuente
  opcional con caché y explicación, sin rodear restricciones ni inventar cuentas.
- Las copias locales de recuperación no sustituyen un respaldo fuera del disco.
  No hay Telegram ni órdenes automáticas. La conexión opcional de solo lectura
  con eToro a través de SnapTrade se documenta en [esta guía](07-snaptrade-personal.md).

## Criterios y comprobaciones

- Casos contables manuales: compra consume caja, venta no duplica saldo, comisión
  única, split conserva NAV y dividendo no se suma a un retorno ya ajustado.
- Fugas temporales: snapshot aislado, cotizaciones y FX anteriores al corte,
  publicación de cuentas, ejecución posterior y no desplazada ante huecos.
- Recuperación: fallo de preparación, fallo después de renombrar, modificación
  concurrente, corte antes del primer rename, copia consistente DuckDB y restauración.
- Intercambio: base antigua/incompleta y recién creada, igualdad de todas las tablas
  autorizadas, protocolos comunes, cartera de destino intacta, corrupción y rollback.
- Windows real: instalación limpia desde dependencias bloqueadas y actualización/
  restauración sobre cartera ficticia aislada; sin tocar la instalación personal.
- Fuentes oficiales de diseño: [API de SEC](https://www.sec.gov/search-filings/edgar-application-programming-interfaces),
  [flujos de caja al accionista, NYU](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/littlebook/cashflows.htm),
  [precios ajustados, Yahoo](https://in.help.yahoo.com/kb/adjusted-close-sln28256.html).

La certificación de rentabilidad y la normalización completa de eventos corporativos
no se dan por terminadas. Los resultados finales de la suite y CI se registran en
la entrega de esta revisión; no se sustituyen por estimaciones.

## Medición acotada de rendimiento

Fixture reproducible, semilla 123, 7.745 sesiones XNYS entre 1996 y 2026; diez
repeticiones después de calentar la caché. Comparación con `00db28f` en el mismo
Python 3.14 local: escenario anterior 72,1 ms de mediana, nuevo 85,3 ms.
El control de calendario añade unos 13 ms en este ensayo. Ambos devuelven
99 episodios y exactamente la misma mediana de retorno. Pico de memoria Python
medido con `tracemalloc`: 1,39 MB en ambos; no mide toda la RAM de librerías nativas.
Es una microprueba del cálculo de escenarios, no un SLA del proveedor ni un
benchmark del replay de diez años sobre todos los mercados.
