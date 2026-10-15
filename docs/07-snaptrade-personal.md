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
- La correspondencia exige símbolo, mercado y divisa. Acciones, ETF y ADR largos
  identificados con seguridad pueden alimentar el asesor. CFD, cortos, criptomonedas,
  productos desconocidos o símbolos ambiguos bloquean el cálculo; no se ocultan.
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
