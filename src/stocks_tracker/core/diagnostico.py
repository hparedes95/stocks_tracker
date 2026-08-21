"""Vuelca de una vez los hechos que hacen falta para saber por que no avanza.

POR QUE EXISTE

Un usuario reporto "no se cargan los datos nuevos". Se diagnostico dos veces a
partir de capturas de pantalla, se encontraron dos fallos reales —y ninguno de
los dos era el que le tenia parado—. Deducir el estado de una maquina desde una
foto de la interfaz es adivinar con pasos intermedios.

Esto no arregla nada ni escribe nada. Imprime los hechos:

  - hasta cuando hay PRECIOS y cuantos valores por dia
  - hasta cuando hay INDICADORES y cuantos por dia
  - que sesion esta considerando vigente el dashboard, y por que
  - que responderian las dos preguntas del lanzador
  - las ultimas ejecuciones de descarga y de calculo, con sus errores
  - los hallazgos de calidad que estan BLOQUEANDO el calculo

Con eso, "por que sigo viendo el martes" tiene una respuesta y no una hipotesis.

SOLO LECTURA, A PROPOSITO

Ni `migrate()` ni ninguna escritura. Se ejecuta con el dashboard abierto —que es
justo cuando alguien quiere saber por que no avanza— y DuckDB no admite un
escritor y un lector a la vez sobre el mismo fichero.

SIN ACENTOS

La consola de Windows usa cp1252 y este texto sale por ahi.
"""

from __future__ import annotations

import sys

import pandas as pd

from .config import get_settings
from .db import connect

SESIONES = 8


def _linea(titulo: str) -> None:
    print()
    print(f"--- {titulo} " + "-" * max(0, 68 - len(titulo)))


def _tabla(filas, cabecera: str) -> None:
    if not filas:
        print("  (nada)")
        return
    print(f"  {cabecera}")
    for fila in filas:
        print("  " + "  ".join("-" if v is None else str(v) for v in fila))


def informe() -> int:
    ajustes = get_settings()
    print()
    print("=" * 74)
    print(" DIAGNOSTICO DE STOCKS TRACKER")
    print("=" * 74)
    print(f"  Almacen: {ajustes.warehouse_path}")
    if not ajustes.warehouse_path.exists():
        print("  NO EXISTE. No se ha llegado a crear el almacen.")
        return 1

    with connect(read_only=True) as conn:
        _precios(conn)
        _indicadores(conn)
        _sesion_vigente(conn)
        _lo_que_decide_el_lanzador()
        _descargas(conn)
        _calculos(conn)
        _calidad(conn)

    print()
    print("=" * 74)
    print(" Copia TODO lo de arriba tal cual. Sin recortar: lo que parece")
    print(" irrelevante suele ser la fila que lo explica.")
    print("=" * 74)
    return 0


def _precios(conn) -> None:
    _linea("PRECIOS: hasta cuando hay, y de cuantos valores")
    filas = conn.execute(
        f"""
        SELECT p.date, COUNT(*) AS valores
        FROM prices_daily p JOIN instruments i USING (ticker)
        WHERE i.asset_class IN ('equity', 'etf')
        GROUP BY p.date ORDER BY p.date DESC LIMIT {SESIONES}
        """
    ).fetchall()
    _tabla(filas, "fecha        valores")
    if filas:
        print()
        print("  Si el numero de valores cae de golpe en el ultimo dia, la sesion")
        print("  esta a medias: se descargo antes de que cerraran todos los mercados.")


def _indicadores(conn) -> None:
    _linea("INDICADORES: hasta cuando esta CALCULADO")
    filas = conn.execute(
        f"""
        SELECT d.date, COUNT(*) AS valores
        FROM indicators_daily d JOIN instruments i USING (ticker)
        WHERE i.asset_class IN ('equity', 'etf')
        GROUP BY d.date ORDER BY d.date DESC LIMIT {SESIONES}
        """
    ).fetchall()
    _tabla(filas, "fecha        valores")


def _sesion_vigente(conn) -> None:
    """La regla del 60 %, con los numeros delante.

    Es la explicacion menos evidente de "he calculado y sigo viendo el martes":
    la sesion vigente no es la mas reciente, es la mas reciente que reune al
    menos al 60 % de los valores del dia mas poblado. Una sesion calculada a
    medias existe en la tabla y no llega a vigente, asi que el dashboard no la
    ensena y desde fuera parece que el calculo no se ha ejecutado.
    """
    _linea("SESION VIGENTE: la que ensena el dashboard, y por que")
    fila = conn.execute("SELECT date, n FROM current_session").fetchone()
    if fila is None:
        print("  No hay ninguna. Sin indicadores no hay sesion vigente.")
        return

    conteos = conn.execute(
        """
        SELECT d.date, COUNT(*) AS n
        FROM indicators_daily d JOIN instruments i USING (ticker)
        WHERE i.asset_class IN ('equity', 'etf')
        GROUP BY d.date ORDER BY d.date DESC LIMIT 30
        """
    ).fetchall()
    if not conteos:
        return
    maximo = max(n for _, n in conteos)
    umbral = maximo * 0.6
    print(f"  Vigente: {fila[0]} con {fila[1]} valores")
    print(f"  Umbral para ser vigente: {umbral:.0f} valores (60 % de {maximo})")
    print()
    print("  fecha        valores  llega al umbral")
    for fecha, n in conteos[:SESIONES]:
        print(f"  {fecha}   {n:>7}  {'si' if n >= umbral else 'NO -> por eso no se ensena'}")


def _lo_que_decide_el_lanzador() -> None:
    _linea("LO QUE DECIDE EL LANZADOR EN CADA ARRANQUE")
    from ..compute.run_compute import sesiones_sin_calcular
    from ..ingest.run_ingest import needs_update, ultima_sesion_cerrada

    print(f"  Ultima sesion de mercado ya cerrada: {ultima_sesion_cerrada()}")
    hace_falta, motivo = needs_update()
    print(f"  Hay que DESCARGAR: {'si' if hace_falta else 'NO'} -> {motivo}")
    pendientes, por_que = sesiones_sin_calcular()
    print(f"  Hay que CALCULAR:  {'si' if pendientes else 'NO'} -> {por_que}")


def _descargas(conn) -> None:
    _linea("ULTIMAS DESCARGAS (ingest_log)")
    filas = conn.execute(
        """
        SELECT started_at, task, target, status, rows_written, error
        FROM ingest_log ORDER BY started_at DESC LIMIT 12
        """
    ).fetchall()
    _tabla([(pd.Timestamp(f[0]).strftime("%d/%m %H:%M"), f[1], f[2], f[3], f[4],
             (f[5] or "")[:60]) for f in filas],
           "cuando (UTC)  tarea  objetivo  estado  filas  error")


def _calculos(conn) -> None:
    _linea("ULTIMOS CALCULOS (audit_log)")
    filas = conn.execute(
        """
        SELECT empezado, paso, estado, salida, detalle
        FROM audit_log ORDER BY empezado DESC LIMIT 12
        """
    ).fetchall()
    _tabla([(pd.Timestamp(f[0]).strftime("%d/%m %H:%M"), f[1], f[2],
             (f[3] or "")[:40], (f[4] or "")[:60]) for f in filas],
           "cuando (UTC)  paso  estado  salida  detalle")
    if not filas:
        print("  Ninguna. El calculo NO se ha llegado a ejecutar nunca desde")
        print("  que existe este registro.")


def _calidad(conn) -> None:
    """Lo que puede estar impidiendo calcular.

    Se separan los BLOQUEANTES del resto porque son cosas distintas: un aviso
    ensucia y deja pasar; un bloqueante para el calculo entero, y desde la
    interfaz eso se ve igual que "no se ha ejecutado".
    """
    _linea("CALIDAD: lo que puede estar PARANDO el calculo")
    filas = conn.execute(
        """
        SELECT check_name, severity, ticker, detail, checked_at
        FROM data_quality
        WHERE checked_at = (SELECT MAX(checked_at) FROM data_quality)
          AND NOT passed
        ORDER BY severity, check_name
        """
    ).fetchall()
    if not filas:
        print("  Ningun hallazgo en la ultima revision: la calidad NO es lo que")
        print("  esta parando el calculo.")
        return
    bloqueantes = [f for f in filas if str(f[1]).lower() == "bloquea"]
    print(f"  {len(filas)} hallazgos, de los cuales {len(bloqueantes)} BLOQUEAN.")
    for f in filas[:12]:
        print(f"  [{f[1]}] {f[0]} {f[2] or ''}: {(f[3] or '')[:110]}")
    if bloqueantes:
        print()
        print("  CON UN SOLO BLOQUEANTE, EL CALCULO NO SE EJECUTA. Eso explica")
        print("  que la portada siga ensenando la ultima sesion calculada.")


def main() -> int:
    try:
        return informe()
    except Exception as exc:  # noqa: BLE001
        # Que reviente el diagnostico no puede dejar al usuario sin nada: el
        # motivo del fallo es en si mismo informacion util.
        print()
        print(f"  EL DIAGNOSTICO HA FALLADO: {type(exc).__name__}: {exc}")
        print("  Copia esta linea tambien.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
