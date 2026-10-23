# Actualización de seguridad de dependencias

Versiones mínimas: `certifi 2026.10.19`, `Pillow 13.0.0` y `Streamlit 1.66.1`.
Streamlit también se actualiza porque la versión 1.64 exigía `Pillow<13`.
No se fuerzan combinaciones incompatibles ni se desactiva la validación HTTPS.

Se regeneran `uv.lock`, `requirements-runtime.lock` y `requirements-dev.lock`
con hashes de los paquetes. El instalador del `.bat` ya instala el archivo de
ejecución con `pip --require-hashes`; no hay que instalar paquetes a mano.
La instalación anterior se conserva con el mecanismo de recuperación existente.
No se cambia el formato de cartera, las credenciales ni las fórmulas financieras.

Las comprobaciones incluyen mínimos en manifiesto, archivos bloqueados y entorno;
carga del almacén de certificados con verificación de servidor activa; lectura y
escritura de imágenes y renderizado con Streamlit. Se mantienen las suites de
regresión financiera, instalación, páginas, diseño y auditoría de dependencias.
La auditoría detecta vulnerabilidades conocidas, no garantiza seguridad absoluta.

Referencias de los mantenedores:

- https://github.com/certifi/python-certifi/security/advisories/GHSA-6q48-6c7w-9gw2
- https://pillow.readthedocs.io/en/stable/releasenotes/13.0.0.html
