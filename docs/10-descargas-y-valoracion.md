# Descargas pendientes y valoración de cartera

El lanzador no considera una descarga reciente como prueba de que los precios
están completos. Compara la última sesión completa con el calendario de la bolsa
que reúne la mayoría de acciones y ETF identificados. Usa la dependencia local
`exchange_calendars`, sin claves ni servicios nuevos. Si no puede identificar un
calendario mayoritario, lo indica y no deduce festivos de la ausencia de precios.

Los intentos identificados del universo completo, incluso si después fallan o
agotan el límite de peticiones, tienen una pausa de 30 minutos entre
comprobaciones automáticas para proteger las fuentes
gratuitas. La pausa no significa que los datos estén al día ni programa una tarea:
el siguiente arranque puede reintentarlo al vencer. Las tareas de fundamentales
no reinician esa pausa, ni tampoco descargar solo índices, divisas o posiciones
individuales. La actualización explícita del Asesor sigue disponible.
Las copias de mercado congeladas conservan su comportamiento: no se descargan
datos hasta que el usuario decida abandonar la copia.
Si tras descargar y calcular continúa faltando una sesión completa, la acción
del Asesor informa de descarga incompleta y no sustituye las recomendaciones
guardadas por un supuesto análisis actualizado. Los datos disponibles y las
lecturas por posición siguen siendo consultables.

La actualización prioritaria de posiciones solicita también sus pares de divisa
admitidos cuando faltan o están caducados. No se inventa paridad ni se emplean
tipos de cambio antiguos para completar una valoración.

El Asesor, la conexión SnapTrade y la consola identifican las posiciones que no
pueden valorarse, con motivo y datos pendientes: producto/identidad, cantidad,
precio o divisa. No muestran los detalles de la cuenta ni credenciales. Los CFD
no se convierten en ETF al contado: estudiar su subyacente no permite deducir
margen, apalancamiento ni valor del contrato. Una cartera parcialmente valorada
sigue impidiendo las compras que necesitan comprobar su riesgo conjunto.

Los errores de proceso conocidos se traducen a mensajes seguros: base ocupada,
disco lleno, acceso denegado o dependencia ausente. Las respuestas y trazas de
proveedores no se copian a la interfaz porque podrían contener credenciales.
El número de descargas fallidas mostrado en la barra lateral es histórico,
no el número de fallos del último intento.
