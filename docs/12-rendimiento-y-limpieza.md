# Rendimiento y limpieza — 26/10/2026

## Cambios

- Caída máxima móvil: operaciones NumPy por bloques, sin una llamada Python
  por sesión. El bloque de trabajo se limita a aproximadamente 8 MiB (más la
  serie y la salida), sin materializar todas las ventanas a la vez. Se conserva
  la ventana completa, el orden temporal y el tratamiento de datos ausentes.
- Asesor: un histórico, comprobación técnica y escenario por subyacente y
  lectura, aunque haya varios lotes. No se agregan cantidades ni se confunden
  posiciones largas, cortas o CFD; cada una conserva sus limitaciones.
- Pantallas: reutilización temporal de la integridad y el análisis de cartera.
  Caché solo en memoria, hasta 16 entradas y 60 segundos. La clave incluye la
  identidad/modificación de la base y su WAL; el análisis incluye fecha y
  horizonte. Cambiar/restaurar la base invalida la lectura. No se mantienen
  conexiones abiertas ni se usa esta caché para autorizar recomendaciones.
- Arranque: los fallos transitorios de migración ya no se memorizan como un
  resultado definitivo. Se reintenta en la siguiente interacción.
- Perfil: guardar un horizonte nuevo se aplica al análisis de esa misma pantalla.
- Si no se puede comprobar la integridad, aparece un aviso en lugar de silencio.

## Medición reproducible, no una promesa para todos los equipos

Windows, Python 3.14, dependencias bloqueadas. Datos ficticios deterministas:
4.228 sesiones por símbolo, 20 series para indicadores y 20 lotes de un mismo
subyacente para cartera. Medianas de 3 ejecuciones de indicadores y 5 del resto.
Antes: funciones del commit `f36723a`; después: mismas entradas y bibliotecas.
Procesos separados, ejecutados consecutivamente sin la batería de tests en paralelo.

| Trabajo medido | Antes | Después | Reducción del tiempo |
| --- | ---: | ---: | ---: |
| Indicadores de 20 series | 1,305 s | 0,551 s | 58 % |
| Análisis de 20 lotes del mismo valor | 1,184 s | 0,083 s | 93 % |
| Caída máxima de una serie | 0,0151 s | 0,0050 s | 67 % |

Estos porcentajes no describen toda la app ni las descargas: los proveedores,
la conexión, el número de valores distintos y el equipo cambian el resultado.
La migración de una base ya creada no recibió optimizaciones en esta entrega.

Para medir la versión actual sin red ni datos personales:

```powershell
.\.venv\Scripts\python.exe scripts/benchmark_runtime.py
```

El script crea y elimina exclusivamente su carpeta temporal dentro de `reports/`;
no abre ni modifica la base del usuario. No contiene precios reales.

## Limpieza y seguridad de los datos

Retirados del paquete activo el evaluador, reglas, configuración y lecturas UI
de las alertas automáticas antiguas, sin llamantes en el programa actual. Se
conservan los avisos de calidad, riesgo, cuarentena y el evaluador seguro usado
por el asesor. El código retirado sigue recuperable en el historial Git.

No se eliminan tablas antiguas, cartera, credenciales, CSV, históricos ni copias
de seguridad. Tampoco se borran instalaciones del usuario para simular ahorro
de espacio. Los temporales de pytest y las coberturas auxiliares quedan fuera
del repositorio. No se añaden dependencias de ejecución; se corrige `filelock`
del entorno de desarrollo según la auditoría, sin modificar el paquete del `.bat`.

## Verificación y actualización

Pruebas añadidas: equivalencia exacta del drawdown con la implementación previa
(ventanas, huecos, infinitos, ceros, bloques y datos futuros), separación de
lotes/contratos, invalidación de caché por WAL/restauración/fecha/horizonte,
reintento de arranque y aplicación inmediata del perfil en la pantalla real.

Se mantiene la batería financiera, de seguridad, SnapTrade, páginas y del
instalador. CI ejecuta Python 3.11/3.12/3.14 en Linux y 3.14 en Windows; la
comprobación visual cubre 13 rutas en claro/oscuro y 3 resoluciones.

La actualización usa el `.bat` habitual, cerrando antes la app. Conserva el
procedimiento transaccional y la posibilidad de volver a la versión anterior.
Al cambiar el código del motor cambia su huella: una copia de mercado congelada
de una versión anterior debe regenerarse desde el origen actualizado, como ya
exige el control de compatibilidad. No se salta ese control.
