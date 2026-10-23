"""Acciones explícitas del usuario; sin tareas, brokers ni ejecución de órdenes."""
from __future__ import annotations

import math
import subprocess
import sys

from stocks_tracker.core.config import project_root


def _failure_detail(result):
    # Translate only known error classes; never expose raw provider/credential output.
    output = str(getattr(result, 'stderr', '') or '') + str(getattr(result, 'stdout', '') or '')
    if result.returncode == 75 or any(s in output for s in ('AlreadyRunning', 'AlmacenOcupado', 'Conflicting lock', 'conflicting lock')):
        return 'Hay otro proceso usando o actualizando la base. Espera a que termine; no abras otra instancia.'
    if any(s in output for s in ('No space left on device', 'WinError 112', 'database or disk is full')):
        return 'No queda espacio suficiente en el disco para actualizar la base.'
    if 'PermissionError' in output:
        return 'Windows ha denegado el acceso a un archivo de la instalación.'
    if 'ModuleNotFoundError' in output:
        return 'Falta una dependencia de Python. Cierra la aplicación y actualiza desde tu .bat.'
    return 'Fallo interno de actualización. Revisa Estado de los datos y el registro de ingesta; no se ha completado el proceso.'


def refresh_research(*, cash: float, download: bool = False) -> None:
    if not math.isfinite(cash) or cash < 0:
        raise ValueError("El efectivo debe ser finito y no negativo.")
    steps = []
    from stocks_tracker.core.market_bundle import frozen_market
    frozen = frozen_market()
    if download and not frozen:
        steps.append(["stocks_tracker.ingest.holding_refresh"])
        steps.append(["stocks_tracker.ingest.run_ingest", "--what", "all"])
    if not frozen:
        steps.append(["stocks_tracker.compute.run_compute"])
    if download and not frozen:
        steps.append(["stocks_tracker.ingest.sec_fundamentals"])
    steps.append(["stocks_tracker.compute.run_advice", "--caja", str(cash)])
    for step in steps:
        if download and not frozen and step[0] == 'stocks_tracker.compute.run_advice':
            from stocks_tracker.ingest.run_ingest import needs_update
            freshness = needs_update()
            if freshness.atrasado:
                raise RuntimeError('La descarga no ha completado los cierres pendientes. '
                                   + freshness.motivo + '. No se sustituyen las recomendaciones anteriores.')
        result = subprocess.run(
            [sys.executable, "-m", *step], cwd=project_root(),
            capture_output=True, text=True, timeout=900,
        )
        if result.returncode:
            # No exponemos salida que pudiera contener credenciales.
            raise RuntimeError(f"Falló {step[0]} (código {result.returncode}). {_failure_detail(result)}")


def extend_history(ticker):
    result = subprocess.run([sys.executable, '-m', 'stocks_tracker.ingest.history_extension', ticker],
                             cwd=project_root(), capture_output=True, text=True, timeout=900)
    if result.returncode:
        raise RuntimeError('La ampliación no se completó. Revisa el estado de fuentes; los intentos quedan registrados.')
