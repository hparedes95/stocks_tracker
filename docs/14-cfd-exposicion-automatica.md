# CFD de CIBR e ITA: asignación automática — 29/10/2026

La integración ya consultaba cuentas y posiciones, pero descartaba `net_value`
y `instrument.underlying_instrument`. Ahora conserva estos campos en la copia
privada de SnapTrade, junto con `sync_status.positions`. La siguiente sincronización
normal los obtiene sin CSV, nuevas claves, servicios de pago ni formularios.

## Dos magnitudes distintas

- **Patrimonio neto**: `net_value.amount/currency`, comunicado por la cuenta de eToro.
- **Exposición bruta del CFD**: unidades por precio del contrato, convertidas a EUR.
  Las unidades ya representan exposición: no se vuelve a multiplicar por un
  apalancamiento supuesto. Se contrasta el precio con la referencia ETF y se usa
  el mayor para el escenario de riesgo.

No se presenta la exposición como valor de liquidación, margen aportado o dinero
disponible. No se reparte el patrimonio total entre los CFD para fabricar valores
individuales. El efectivo disponible sigue viniendo del saldo, nunca del poder
de compra. Los pesos de riesgo son exposición / patrimonio y pueden superar 100 %.

## Condiciones automáticas

Solo se admiten aquí CFD largos de CIBR e ITA cuyo subyacente se identifique
expresamente como el ETF USD en un mercado compatible con la ficha local. El
contrato se mantiene separado de las acciones al contado y no recibe propuestas
de aumentar el CFD. Se estudian las compras del resto de valores.

Se exige patrimonio positivo, saldos y posiciones frescos, fechas de sincronización
de cuenta y posiciones con una diferencia máxima de cinco minutos, divisas válidas,
cierre de referencia vigente sin cuarentena y precios de contrato y referencia a menos
del 5 %. Son tolerancias de la aplicación, no garantías del proveedor. El valor
neto es una cifra en caché de SnapTrade: se muestra la sincronización de saldos,
sin afirmar que sea un precio en tiempo real ni una fecha independiente del NAV.
Si efectivo + exposición no puede explicar el patrimonio comunicado (tolerancia
1 %, mínimo 1 EUR), se rechaza el dimensionamiento.

El cálculo aplica efectivo, reserva de caja, número de posiciones, exposición
por valor, sectores, correlación y escenario de caída sobre el patrimonio neto.
La exposición de sector desconocido se reserva contra cada sector candidato;
no se considera riesgo cero. Los retornos ETF sirven como aproximación al riesgo
de precio, no como rentabilidad histórica del contrato: no reconstruyen financiación,
llamadas de margen, liquidación forzosa, impuestos ni comisiones del CFD.

Si falta un campo o hay contradicción, la pantalla indica el motivo específico
y conserva las recomendaciones condicionadas para otros valores. La siguiente
sincronización vuelve a evaluarlo. No se promete que eToro proporcione todos los
campos en todas las cuentas; la disponibilidad real la determina SnapTrade.

## Fuentes contrastadas

- [Especificación oficial SnapTrade](https://docs.snaptrade.com/openapi.yaml):
  `InvestmentAccount`, `ConnectionAccountSyncStatus`, `AccountPosition`,
  `CfdInstrument`, `UnderlyingCfdInstrument` y `Balance`.
- [Posiciones de SnapTrade](https://docs.snaptrade.com/reference/Account%20Information/AccountInformation_getAllAccountPositions).
- [Datos de cuenta](https://docs.snaptrade.com/docs/account-data).
- [eToro: valor de contratos y de cuenta, glosario](https://www.etoro.com/wp-content/uploads/2026/03/Combined-PDS-eToro-AUS.pdf).
  Se utiliza la distinción contable entre exposición y patrimonio, no condiciones
  regulatorias australianas para una cuenta europea.

La API de saldos V2 tiene campos adicionales, pero es beta y requiere habilitación
por SnapTrade. Esta solución usa las consultas ya autorizadas; no depende de esa beta.

## Corrección del flujo de actualización — 30/10/2026

Actualizar datos y análisis consulta primero la cuenta activa, incluso tras un
fallo reciente, y después descarga las series de las posiciones recibidas.
Recalcular con datos guardados conserva su significado: no consulta al bróker.
Una respuesta idéntica de SnapTrade ya no invalida el cálculo vigente. Los cambios
de cartera y los errores de sincronización siguen invalidándolo.

La valoración de exposición no exige 200 cierres para una media de tendencia:
contrasta el precio comunicado con un cierre vigente de fuente identificada.
La autorización de compras sigue exigiendo histórico suficiente para correlación;
los precios en cuarentena no se usan para ese cálculo. Valorar un contrato no
equivale a disponer de toda la evidencia necesaria para ampliar la cartera.

La pantalla vuelve a leer patrimonio, posiciones y recomendaciones tras actualizar;
el panel de riesgo muestra el motivo específico. El perfil sin confirmar y la
ausencia de un cálculo vigente ya no quedan ocultos tras el aviso genérico.
