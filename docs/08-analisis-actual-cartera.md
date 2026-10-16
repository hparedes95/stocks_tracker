# Análisis actual de posiciones (16/10/2026)

El asesor tiene dos salidas distintas:

- Análisis actual por posición: se lee al abrir la página, incluso sin fecha de
  compra, sin efectivo para comprar y sin propuestas operables guardadas. No se
  añade al marcador de recomendaciones. Muestra siempre serie, fecha y fuente.
- Propuestas operables: conservan los controles de evidencia, perfil, riesgo del
  libro completo y registro de evaluación hacia delante. No se habilitan compras
  solo para conseguir que aparezcan tarjetas.

La lectura técnica utiliza hasta 253 cierres ajustados, con un mínimo de 200.
Precio y media de 50 por debajo de la media de 200 y retorno a 63 sesiones negativo
producen «Revisar reducción; no ampliar». Precio > media 50 > media 200 y retorno
positivo producen «Mantener en vigilancia». El resto es mixto. Son heurísticas
explícitas, no modelos calibrados ni una demostración de rentabilidad. Se muestran
momentum, caída desde el máximo, volatilidad diaria y niveles móviles para revisar.
Las banderas fundamentales vigentes tienen prioridad sobre la lectura favorable.
Los fundamentales de empresa no se aplican a ETF ni criptomonedas.

Se rechazan para emitir una lectura precios inválidos, fuentes mezcladas/ausentes/
sintéticas, barras en cuarentena, huecos de más de siete días y cierres con más de
siete días de antigüedad. Los fundamentales necesitan fuente no sintética y una
fecha no posterior al corte y de antigüedad máxima de 90 días. No se reconstruyen
fundamentales de compra. Los escenarios mantienen el mínimo de 20 episodios
independientes; su ausencia no impide describir los datos actuales.
Cuando se conoce el calendario, también se comprueban las sesiones esperadas
(incluidos festivos); las criptomonedas requieren continuidad de siete días.

## CFD de CIBR e ITA

Se estudia explícitamente la referencia USD del ETF, no el valor económico del
contrato. El mercado OTC del contrato no se interpreta como la bolsa del ETF. Una
divisa distinta de USD o metadatos locales contradictorios impiden la asociación.
La referencia no habilita la valoración de la posición, su peso, margen,
apalancamiento, financiación, stop monetario ni una compra personalizada. Los CFD
siguen incluidos como exposición no valorable en el control conjunto; nunca se
borran para repartir artificialmente el resto de la cartera al 100 %.
La dirección del CFD no se da por verificada: las etiquetas describen el
subyacente, ya que una caída afecta de manera opuesta a un contrato largo o corto.

Referencias verificadas de los ETF:

- [CIBR, First Trust, Nasdaq](https://www.ftportfolios.com/Retail/Etf/EtfSummary.aspx?Ticker=CIBR)
- [ITA, iShares](https://www.ishares.com/us/products/239502/ishares-us)

## Actualización sin configuración adicional

«Actualizar datos y análisis» descarga primero los históricos de las posiciones
identificadas y de estas referencias, antes de gastar el presupuesto gratuito en
el universo completo. Los fundamentales de acciones en cartera no esperan al turno
semanal de su símbolo. «Recalcular» solo usa los datos guardados. Una copia de
mercado congelada no se modifica con ninguno de los dos botones.

No se envían órdenes, no se introducen servicios de pago ni se cambian permisos
de SnapTrade. Una sincronización fallida permite ver una lectura de mercado
fechada, pero no la presenta como una orientación actualizada para la cartera.
