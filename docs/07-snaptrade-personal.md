# Cartera eToro mediante SnapTrade Personal

## Configuración única en Windows

1. Actualiza el programa y abre **Mi cartera → Conexión SnapTrade**.
2. Introduce localmente el **Client ID** y la **Consumer Key** de SnapTrade Personal,
   confirma el permiso de consulta y pulsa **Guardar claves y comprobar conexión**.
   No envíes claves por chat, ni las guardes en Git. No son las credenciales de eToro.
3. Selecciona tu cuenta eToro real y pulsa **Consultar cartera ahora**.
4. Revisa posiciones, divisas, efectivo y fechas. Confirma **Usarla EN LUGAR de mi
   cartera manual/CSV** y pulsa **Activar cartera SnapTrade**.
5. En Asesor puedes recalcular. Con la consulta automática activada, la app consulta
   al abrir (si no consultó en los últimos cinco minutos) y cada cinco minutos mientras
   permanece abierta. Tras sincronizar intenta recalcular; no ejecuta operaciones.

La cartera manual se conserva separada. Desactivar SnapTrade vuelve a usarla;
no mezcla ni duplica posiciones con los CSV. Solo se admite una cuenta activa.
No hay servicio cuando el programa está cerrado ni una sincronización entre PCs.
Cada PC necesita configurar sus claves y su cuenta; la base de mercado compartida
no contiene estas credenciales ni las posiciones del bróker.

## Qué consulta y qué no

- Solo peticiones GET a cuentas, conexiones, posiciones y saldos. Sin órdenes,
  sin refrescos de pago, sin activar servicios comerciales ni importar movimientos.
- Requiere conexión **Read only**, habilitada, real y de eToro. Una conexión caducada
  debe reconectarse en SnapTrade; una respuesta HTTP correcta no acredita su vigencia.
- Los cinco minutos son frecuencia de consulta, **no garantía de datos en tiempo real**.
  SnapTrade puede entregar caché diaria. Se muestra la fecha del proveedor, separada
  de la fecha en que se hizo la consulta. El asesor se bloquea ante errores, fechas
  ausentes, futuras o de más de 36 horas. Un fin de semana también puede bloquearlo.
- Se lee efectivo, nunca poder de compra. La valoración del asesor usa los precios
  y tipos de cambio de la base de mercado; la vista de conexión muestra el precio
  informado por el bróker. Por sus fechas pueden diferir de eToro.
- La correspondencia utiliza símbolo, mercado y divisa. Acciones, ETF, ADR largos,
  BTC y ETH al contado pueden alimentar la cartera. CFD, cortos y productos
  desconocidos requieren otro modelo y siguen identificados con su motivo.
- Una foto de posiciones **no es un libro de movimientos**. No inventa fechas de
  compra, ventas realizadas, dividendos, comisiones ni rentabilidad histórica.
  Las cantidades del bróker ya son actuales: no se reaplican splits históricos.

## Protección y recuperación

Las claves se cifran con Windows DPAPI para el usuario actual en
`data/private/snaptrade.dpapi`; quedan fuera de Git y de los archivos `.stmarket`.
Las copias locales completas de instalación sí pueden contener ese archivo cifrado
y datos privados: protégelas como tu propia base de datos. No permite que otro
usuario de Windows o PC use las claves sin volver a configurarlas.

Las respuestas se validan antes de sustituir la copia; una sincronización fallida
conserva la última válida y la identifica como antigua. La actualización es
transaccional; consultar varias veces no añade posiciones ni órdenes duplicadas.
El botón para borrar claves locales no revoca la autorización en SnapTrade y no
borra la última foto de cartera. La revocación se realiza en SnapTrade.

## Verificación y límites

Pruebas automatizadas de firma, destinos permitidos, errores sin secretos, respuestas
incompletas, duplicados, aislamiento de CSV, frescura, efectivo, splits, cifrado
DPAPI y pantalla de configuración. Se utilizan cuentas ficticias, sin acceder a
la cuenta personal. **La primera conexión real requiere las claves del usuario**.
Esto automatiza la lectura de la cartera; no demuestra rentabilidad ni aumenta por
sí solo la precisión predictiva. Las puertas de calidad del asesor siguen activas.

Contrato consultado el 15/10/2026:
[Personal y Commercial](https://docs.snaptrade.com/docs/personal-vs-commercial),
[autenticación y claves](https://docs.snaptrade.com/docs/getting-started),
[firma de peticiones](https://docs.snaptrade.com/docs/request-signatures),
[especificación oficial de la API](https://docs.snaptrade.com/openapi.yaml).

Corrección de autenticación: la firma utiliza la ruta exacta que se envía,
igual que el [SDK oficial de Python](https://github.com/passiv/snaptrade-sdks/blob/master/sdks/python/snaptrade_client/request_after_hook.py).
La guía de firmas aún muestra el prefijo histórico `/api/v1`; añadirlo solo a la
firma, cuando la URL actual no lo contiene, produce firmas diferentes. Las
pruebas verifican esta correspondencia en las cuatro consultas permitidas.

## TSM, ASML, CIBR, ITA, BTC y ETH

El universo incluye TSM y ASML de EE.UU., además de los ETF CIBR e ITA. BTC y ETH
se asocian con BTC-USD y ETH-USD, que se descargan también cuando el proveedor
los clasifica como criptomonedas. No se utilizan los pares Kraken con barra.

ASML en Nasdaq/USD se mantiene separado de ASML.AS en Amsterdam/EUR. Las
cantidades del bróker no se multiplican por ratios de ADR ni por tipos de cambio.
Si falta el histórico, una cotización válida de SnapTrade permite valorar la
posición; se muestra el origen y se convierte su divisa sin inventar precios
históricos. El coste medio se deja vacío si corresponde a otra divisa.

Si uno de estos valores llega sin mercado o con una identidad contradictoria,
puede incluirse su valoración del bróker, pero queda separado de cualquier serie
bursátil hasta identificarlo. El asesor conserva los avisos de concentración;
las compras nuevas exigen poder contrastar también el riesgo con esa posición.
Una posición sin valorar no se interpreta como valor cero.
Los sectores desconocidos no se agrupan como si fueran un único sector económico.

BTC y ETH cuentan en el valor total, los pesos, la exposición y la concentración.
Su modelo de compras no es el de fundamentales de empresas: aceptar la posición
no acredita una predicción de compra/venta. Para el riesgo histórico mixto, los
retornos de cripto y bolsa se miden entre los mismos días de mercado (incluyendo
el movimiento cripto del fin de semana entre viernes y lunes). Los huecos no se
rellenan. Una cartera exclusivamente cripto usa 365 días para anualizar.

Tras actualizar la app, pulsa **Actualizar datos y análisis** en el Asesor para
descargar las nuevas series. Una base de mercado congelada conserva su corte:
actualízala en el equipo de origen y vuelve a compartirla cuando corresponda.

Identidades contrastadas en las fuentes de los emisores:
[TSM en NYSE](https://investor.tsmc.com/english/dividends/1q26),
[cotizaciones de ASML](https://investor.asml.com/share-information),
[CIBR en Nasdaq](https://www.ftportfolios.com/Retail/Etf/EtfSummary.aspx?Ticker=CIBR),
[ITA en Cboe BZX](https://www.ishares.com/us/products/239502/ishares-us).

## Análisis parcial cuando una posición está pendiente

Una posición sin correspondencia o sin modelo (por ejemplo, si CIBR llega como
CFD) ya no interrumpe el cálculo del asesor entero. Se conserva en la cartera y
recibe su explicación individual como **Sin opinión**; el resto del análisis
continúa y los diagnósticos se guardan. La pantalla permite recalcular y abre los
motivos pendientes para que se puedan revisar.

No se sustituye ese producto por el ETF al contado ni se interpreta su importe
desconocido como cero. Si impide valorar toda la cartera, tampoco se calculan
pesos sobre el subtotal conocido. Las propuestas de compra personalizadas siguen
pendientes mientras falte información necesaria para evaluar el conjunto de la
cartera. Los avisos independientes de otras posiciones no se eliminan por ello.
Una conexión completa caducada o fallida conserva su control de antigüedad.

Desde el 29/10/2026, los CFD largos de CIBR e ITA pueden incorporarse al
dimensionamiento del resto de acciones mediante exposición bruta y patrimonio
neto del bróker, obtenidos automáticamente de SnapTrade. No se usa la exposición
como valor de liquidación. Consulta el [modelo y sus controles](14-cfd-exposicion-automatica.md).
La siguiente sincronización normal añade los campos; no hay que introducir datos
ni cambiar claves. Si el proveedor no los entrega, la app identifica el campo
pendiente y conserva las recomendaciones condicionadas.
