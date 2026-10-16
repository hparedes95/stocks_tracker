# Diseño claro y oscuro

La apariencia se define en `.streamlit/config.toml` con dos temas nativos.
El menú superior ⋮ permite elegir **Light**, **Dark** o **System**. El navegador
recuerda su elección; no se cambia el tema de otros equipos. El botón lateral
«Apariencia» explica dónde está el selector. No es un interruptor ficticio ni se
modifica el almacenamiento interno de Streamlit con JavaScript.

`components/design.py` y `design.css` contienen la presentación común de las
13 páginas. Los encabezados usan el mismo espaciado, los indicadores se agrupan
en tarjetas y la navegación sigue siendo enlaces nativos registrados. Los
formularios, tablas y diálogos mantienen el tema nativo. Los gráficos Plotly y
Lightweight Charts usan la paleta compartida y TradingView conserva su atribución
y adaptación claro/oscuro.

El Asesor es la pantalla piloto: controles desplegables, botones de actualización
visibles, resumen de posiciones/cobertura/horizonte y tarjetas en dos columnas,
que se apilan en pantallas estrechas. La cobertura es disponibilidad de datos,
no porcentaje de acierto. Las curvas pequeñas muestran los últimos 63 cierres
ajustados descargados, limitados a la fecha del informe; no representan la
rentabilidad personal. No se dibujan cuando el análisis rechaza los datos.

Cada tarjeta separa el símbolo de su conclusión completa. La fecha del último
cierre y la fuente quedan a la vista; la serie y el corte del análisis están en
«Motivos y datos», también cuando falta histórico. Las curvas muestran el inicio
y el final del período y permiten consultar la fecha y el precio ajustado de
cada punto. No se inventan sesiones intermedias ni se confunden con ganancias de
la cartera. Los enlaces laterales y desplegables tienen foco de teclado visible.

Los avisos de sincronización, cartera parcial, datos de prueba y mercado
congelado siguen visibles. Los motivos completos, los datos fundamentales y los
límites de cada posición se pueden desplegar. No cambian las reglas, los modelos,
las credenciales, las valoraciones ni el registro de recomendaciones.

## Comprobación

- Pruebas de las 13 páginas vacías y con datos, y regresiones del Asesor.
- Contraste mínimo 4,5:1 del texto normal/secundario sobre las dos superficies.
- Coincidencia de los colores nativos con la paleta de gráficos.
- Trabajo visual de CI con navegador real: temas claro/oscuro, persistencia al
  recargar, navegación y capturas a 1366, 1920 y 390 píxeles.
- Consulta de precios con el ratón, navegación por teclado y exclusión de fechas
  posteriores al cierre validado del informe.

`scripts/preview_design.py` solo prepara una base de datos ficticia y aislada en
`reports/design-preview.duckdb`, con sincronización y widgets externos desactivados
y un aviso explícito. No es el arranque normal ni consulta la cuenta del usuario.
Las capturas de CI se guardan como artefactos durante siete días. El navegador de
pruebas no es una dependencia de la instalación Windows.
