# Estrategia de implementación: fiabilidad del asesor

Fecha: 2026-10-13. Base publicada: `00db28f`.
Estado: implementación por entregables. Véase `06-entrega-fiabilidad.md` para los
cambios, pruebas y límites pendientes; este plan no equivale a una certificación
de rentabilidad ni a la finalización de las observaciones prospectivas.

## Objetivo y límites

Entregar un programa local que permita analizar acciones y evaluar recomendaciones
con datos trazables, resultados reproducibles y límites explícitos. El éxito del
desarrollo se mide por corrección, seguridad y capacidad de evaluación, no por
prometer rentabilidad. La evidencia favorable o desfavorable se publicará igual.

Se mantiene Python/Streamlit/DuckDB, la actualización mediante `.bat`, las fuentes
gratuitas y la cartera manual. No se incorporan trading automático, órdenes a un
bróker, Telegram, eToro, Delta, servicios de pago ni infraestructura de nube.
La transferencia de mercado sigue siendo manual y no transporta datos personales.
No se publican históricos de proveedores ni copias privadas en GitHub.

## Secuencia y dependencias

0. Línea base y contrato de aceptación.
1. Actualización y recuperación seguras.
2. Contrato temporal y calidad de los datos.
3. Simulación de la política completa del asesor.
4. Fundamentales y valoración verificables.
5. Validación estadística y reglas de activación.
6. Experiencia de uso y copias compartidas.
7. Ensayo de actualización, publicación y comprobación en dos PC.

La fase 1 se adelanta para proteger los datos antes de las migraciones siguientes.
Las fases 3 y 4 dependen de la 2; ambas deben estar cerradas antes de certificar la 5.
La interfaz de la 6 consume los contratos ya fijados: no calcula una lógica financiera
distinta. La observación prospectiva continúa después de la entrega, sin bloquear
la disponibilidad del programa como herramienta experimental.

## Fase 0. Congelar una línea base

Trabajo:
- Registrar versión, configuración, dependencias y resultados actuales de pruebas.
- Inventariar componentes existentes para extenderlos, no duplicarlos.
- Definir casos de instalación vacía, base antigua, cartera con varias monedas,
  datos incompletos y copia compartida congelada.
- Medir tiempos y memoria con conjuntos de prueba de tamaño fijo. Los datos
  sintéticos se usan únicamente en pruebas, nunca como respaldo visible al usuario.

Entrega: informe de referencia y lista de pruebas de aceptación por fase.
Salida: se puede reproducir el estado inicial y se conoce cómo volver a él.

## Fase 1. Actualización y recuperación seguras

Trabajo:
- Resolver una sola vez el SHA de la versión y descargar ese archivo concreto,
  evitando mezclar una descarga con la marca de otra versión de la rama.
- Preparar y verificar archivos y dependencias en una carpeta temporal antes de
  sustituir la instalación activa. No desactivar validaciones TLS.
- Comprobar rutas de destino y detener el proceso ante rutas ambiguas o bloqueos.
- Separar los datos privados del reemplazo de código; conservar una copia consistente
  y comprobable de base, configuración y `.env` antes de una migración.
- Escribir `.version` únicamente después de instalación y comprobaciones exitosas.
- Recuperar código y datos compatibles si falla una actualización; no abrir una base
  migrada con una versión anterior sin restaurar su copia correspondiente.
- Guardar una copia previa al reemplazo de mercado, con restauración explícita.

Pruebas: descarga truncada, ZIP inválido, disco lleno simulado, dependencia fallida,
base bloqueada, interrupción entre pasos y migración fallida.
Salida: nunca se marca una actualización fallida como instalada; el estado anterior
se conserva o se recupera sin perder la cartera.
Entrega: instalador actualizado, restauración y pruebas automatizadas de fallos.

## Fase 2. Unificar tiempo, procedencia y suficiencia del histórico

Trabajo:
- Adoptar horizontes de 21, 63 y 126 sesiones, mostrados como aproximadamente 1, 3
  y 6 meses. Conservar la regla original para recomendaciones antiguas.
- Definir sesiones según calendario del mercado, no como cualquier fila disponible.
  Un precio ausente no puede acortar artificialmente el horizonte. Precisar festivos,
  zonas horarias y comparación con un benchmark de otro mercado.
- Registrar fecha de observación, disponibilidad de la información, emisión,
  ejecución simulada y vencimiento. Establecer una fecha de corte común por análisis.
- Separar precios de ejecución, precios ajustados usados para retorno y eventos
  corporativos. Evitar contabilizar un dividendo a la vez en el ajuste y en caja.
- Versionar revisiones relevantes: una corrección del proveedor no debe cambiar
  silenciosamente un resultado cerrado. Conservar original y revisión etiquetada.
- Mostrar histórico disponible, huecos, fuentes y número de episodios utilizables.
- Solicitar ampliaciones de histórico acotadas y reanudables cuando haya datos;
  no repetir descargas imposibles ni rebajar mínimos para forzar recomendaciones.
- Identificar cobertura de componentes históricos del universo y valores desaparecidos.
  No atribuir ausencia de sesgo de supervivencia a datos que no lo permiten comprobar.

Pruebas: festivos, sesión ausente, cambio de moneda, split, dividendo, fuente mezclada,
revisión de datos, recomendación emitida hoy sobre copia antigua y corte temporal.
Salida: todos los módulos interpretan igual un horizonte; los huecos provocan un
diagnóstico explícito y no una fecha de salida inventada.
Entrega: contrato de datos/tiempo, migración versionada y diagnóstico de cobertura.

## Fase 3. Simular el asesor completo

Trabajo:
- Reutilizar la política real de selección, riesgo y dimensionamiento del asesor
  mediante una interfaz común; no crear un segundo asesor simplificado para las pruebas.
- Crear una cartera exclusivamente simulada, con capital inicial fijo y posiciones,
  caja, costes, eventos corporativos y conversión a EUR registrados en un libro de movimientos.
- Usar solo información disponible al decidir; ejecutar después de la señal según
  una convención explícita y compatible con los precios disponibles.
- Evitar reutilizar un fundamental actual como si se conociera en una fecha pasada.
  Si faltan variables históricas, abstenerse o identificar la prueba como parcial.
- Comparar con una referencia prefijada y con mantener efectivo, sin elegir a posteriori
  el benchmark más fácil. Separar comparación de selección y exposición al mercado.
- Probar costes base y duplicados, deslizamiento y retrasos de ejecución. Los supuestos
  no se presentan como tarifas reales de ningún bróker.
- Medir rentabilidad neta, exceso, caída máxima, rotación, exposición, pérdidas y abstenciones;
  no reducir la evaluación a porcentaje de operaciones acertadas.

Pruebas: una compra consume caja, una venta no duplica saldo, costes se descuentan una
sola vez, no existen posiciones imposibles y el balance coincide con movimientos y valoración.
Salida: ejemplo manual sencillo reproducido exactamente, pruebas contra información futura
y resultados iguales al repetir la misma copia/configuración/versión.
Entrega: informe de la estrategia completa con operaciones simuladas auditables.

## Fase 4. Fundamentales y valoración verificables

Trabajo:
- Conservar publicación, periodo, unidad y documento de origen de cada cifra oficial.
- Añadir métricas plurianuales y distinguir flujos trimestrales, acumulados y anuales.
- Tratar separadamente deuda, caja, dilución y caja atribuible al accionista; evitar
  mezclar valor de empresa con capitalización sin un puente explícito.
- Mostrar escenarios y sensibilidad a supuestos, no un único precio objetivo.
- Identificar sectores no compatibles con cada fórmula y datos extraordinarios o insuficientes.
- Gestionar indisponibilidad de SEC y otras fuentes con caché, límites y mensajes claros,
  respetando restricciones y sin rodear bloqueos ni fabricar cifras.
- Toda regla nueva permanece experimental hasta superar la fase 5; no cambia compras
  solo por incorporarse a la ficha informativa.

Pruebas: casos numéricos manuales, fechas de publicación y revisiones, monedas/unidades,
deuda/caja, periodos incompatibles y proveedor inaccesible.
Salida: cada métrica puede recalcularse desde sus entradas; lo desconocido no equivale a cero.
Entrega: ficha fundamental trazable y valoración con cobertura explícita.

## Fase 5. Validar y decidir qué puede recomendar

Trabajo:
- Fijar candidatos, reglas, costes y referencias antes de evaluar el periodo reservado.
- Separar desarrollo, validación temporal y evaluación final; impedir contaminación por
  resultados futuros y controlar solapamientos de horizontes entre particiones.
- Registrar todos los experimentos y cambios, incluidos los desfavorables. Si se usa el
  conjunto final para ajustar, deja de ser una evaluación final independiente.
- Evaluar estabilidad por periodos y sensibilidad a costes; tratar dependencia temporal
  y comparaciones múltiples sin llamar independientes a activos correlacionados.
- Mantener modelos nuevos en investigación. Un promedio favorable o un test de software
  aprobado no autoriza por sí solo recomendaciones de compra.
- Distinguir estados: datos no utilizables, evidencia insuficiente, evidencia histórica
  favorable bajo condiciones, evidencia desfavorable y seguimiento prospectivo pendiente.
- Integrar el seguimiento real de recomendaciones emitidas mediante el registro existente,
  actualizado al usar la app, sin operaciones ni procesos programados.

Salida: informe reproducible con todos los experimentos, límites y razones de activación
o abstención. Los umbrales se fijan antes de mirar resultados, no para obtener un ganador.
Entrega: reglas de activación documentadas y panel de desempeño por versión/horizonte.
Límite: puede cerrarse correctamente sin encontrar ningún modelo apto para proponer compras.

## Fase 6. Interfaz sencilla y copia compartida

Trabajo:
- Una ficha por propuesta: acción sugerida, motivo, datos faltantes, riesgo añadido,
  condiciones que la invalidan y fecha/versión de la evidencia.
- Separar calidad del dato, evidencia predictiva y encaje personal; no fusionarlos en
  un porcentaje que parezca probabilidad de ganar.
- Mantener configuración breve dentro de la app; sin claves obligatorias ni ajustes
  técnicos del modelo para el usuario. Supuestos por defecto siempre identificados.
- Previsualizar la importación: fecha, versión, valores y cambios en el histórico.
- Ofrecer una acción guiada de actualizar y preparar copia en el equipo de origen,
  respetando la confirmación necesaria para abandonar una copia congelada.
- Conservar exportación/importación manual. Mostrar siempre si se usa mercado local o
  compartido, su identificador y su antigüedad. No decir que hay sincronización automática.
- Informar de que cartera, caja y perfil pueden cambiar recomendaciones aunque la copia
  de mercado coincida. También influye la fecha de consulta en avisos de caducidad.

Pruebas: pantallas vacías, copia antigua, fallo de fuente, perfil incompleto, importación
inválida y comparación de dos bases con mercados iguales y carteras distintas.
Salida: con misma copia, motor, configuración y fecha de corte, el análisis de mercado
es reproducible. Las diferencias personales o por caducidad están explicadas.
Entrega: flujo de uso e intercambio sin necesidad de consola.

## Fase 7. Ensayo y publicación

Trabajo:
- Ensayar instalación limpia y actualización desde la versión base con datos de prueba
  privados y aislados. Probar recuperación antes de publicar.
- Ejecutar pruebas de lógica financiera, interfaces, corrupción, secretos y dependencias
  en Windows y en las versiones de Python soportadas por el proyecto.
- Realizar comprobaciones acotadas con fuentes reales; distinguir bloqueo externo de
  fallo interno y no considerar una respuesta HTTP correcta suficiente para validar datos.
- Ensayar que exportar en un PC con histórico e importar en una instalación nueva conserva
  todas las tablas de mercado autorizadas y no reemplaza la cartera del destino.
- Comprobar tiempos y memoria frente a la línea base. Investigar regresiones importantes
  antes de publicar; no añadir servicios pagados para compensar ineficiencias.
- Publicar una versión identificable, comprobar las pruebas remotas y el enlace del `.bat`.

Salida: matriz de aceptación aprobada, versión recuperable, notas de cambios y límites
conocidos. Una fuente externa bloqueada se declara; no se disfraza como verificada.
Entrega: versión disponible para el lanzador habitual, sin pasos Git para el usuario.

## Gestión de cada entrega

1. Escribir primero casos de aceptación y medir el comportamiento inicial.
2. Implementar únicamente el alcance de la fase; migraciones aditivas y versionadas.
3. Ejecutar pruebas focalizadas y suite completa antes de publicación.
4. Publicar cambios financieros experimentales desactivados si aún dependen de fases posteriores.
5. Documentar versión, cambios visibles, evidencia, límites y procedimiento de recuperación.
6. No incorporar una nueva idea al alcance sin valorar su dependencia e impacto.

El diseño no fija fechas ficticias: tras la fase 0 se estima por entregables y se
revisa el esfuerzo al cerrar cada fase. El seguimiento prospectivo necesita tiempo
de mercado real y no puede acelerarse ejecutando más pruebas.

## Definición final de terminado

- Ninguna actualización o importación aceptada pierde la cartera en las pruebas de fallo.
- Una decisión se reconstruye con su versión, configuración e información disponible.
- La evaluación incluye caja y costes y no usa información futura.
- La evidencia insuficiente produce abstención comprensible, no seguridad aparente.
- La misma copia permite reproducir el análisis en una instalación nueva bajo el mismo corte.
- El programa se actualiza mediante el `.bat` y tiene recuperación comprobada.
- Las limitaciones del histórico gratuito y de la validación están visibles.
- No se requiere suscripción ni se ejecutan operaciones automáticamente.

La sincronización automática en nube y la rentabilidad futura garantizada no forman
parte de esta definición de terminado.
