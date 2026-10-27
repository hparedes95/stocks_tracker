# Asesor: corrección de bloqueos — 27/10/2026

## Causas reproducidas en código

- Todas las compras se condicionaban a 30 episodios prospectivos no solapados
  del mismo estado de mercado. A seis meses, el mínimo teórico ronda 15 años;
  puede ser mayor. No era un plazo de unos meses. Cambiar el motor crea otro
  protocolo y tampoco debe presentarse como continuidad del mismo experimento.
- SnapTrade no aporta fechas de compra fiables al análisis. Se omitía la
  comparación histórica, pero la lectura técnica actual no se conectaba a las
  recomendaciones guardadas. Sin pesos completos, tampoco había concentración.
- Un candidato rechazado por la evaluación final ya había consumido caja,
  capacidad sectorial y plazas en el generador de candidatos.
- El límite de 25 candidatos también podía excluir valores ya presentes en
  cartera. Las recomendaciones y sus motivos quedaban debajo del marcador.

## Política corregida

El modo predeterminado es **recomendación manual por reglas**, marcado en la
pantalla y en cada propuesta. No afirma probabilidad de acierto, ventaja
predictiva ni rentabilidad. El modo prospectivo sigue disponible con sus
condiciones originales; no se reducen sus pruebas ni se inventan resultados.

Una compra por reglas conserva ranking, cobertura, banderas fundamentales,
precios recientes con procedencia y calendario, cuarentena, divisas, efectivo,
coste estimado, plazas, exposición y correlación, perfil confirmado y eventos.
Solo una propuesta aceptada reserva presupuesto. Se estudian primero todos
los valores propios presentes en el ranking, además de hasta 25 nuevos.

Sin comparación desde la compra, una posición larga identificada obtiene
orientación actual. Si precio y media de 50 están bajo la media de 200 y el
retorno a 63 sesiones es negativo, propone **reducir por debilidad técnica**.
No declara rota la tesis empresarial, no propone vender todo ni inventa
cuántos títulos vender. En otro caso, mantener en vigilancia. Las salidas ya
justificadas por las reglas anteriores tienen prioridad.

Los CFD y contratos no identificados no se convierten en acciones al contado.
Su análisis de subyacente permanece visible. Una valoración incompleta impide
dimensionar nuevas compras, pero no oculta avisos independientes de posiciones
identificadas. Los rechazos y mantener se explican; las propuestas accionables
se registran con metodología y versión, sin enviar órdenes.

## Comprobación y límites

Pruebas de compras por reglas y modo estricto, datos inválidos/cuarentena,
SnapTrade sin fechas y con CFD, sesión caducada, rechazo sin consumir caja,
valores propios fuera del top 25, persistencia y pantalla.

La base local de desarrollo no contiene posiciones ni una sesión de mercado:
estos casos se reproducen con datos de prueba, no se presentan como una
inspección de la cartera instalada en otro ordenador.
