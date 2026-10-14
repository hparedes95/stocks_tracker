"""Acciones explícitas del usuario; sin tareas, brokers ni ejecución de órdenes."""
from __future__ import annotations

import math
import subprocess
import sys

from stocks_tracker.core.config import project_root


def refresh_research(*, cash: float, download: bool = False) -> None:
    if not math.isfinite(cash) or cash < 0:
        raise ValueError("El efectivo debe ser finito y no negativo.")
    steps = []
    from stocks_tracker.core.market_bundle import frozen_market
    frozen = frozen_market()
    if download and not frozen:
        steps.append(["stocks_tracker.ingest.run_ingest", "--what", "all"])
    if not frozen:
        steps.append(["stocks_tracker.compute.run_compute"])
    if download and not frozen:
        steps.append(["stocks_tracker.ingest.sec_fundamentals"])
    steps.append(["stocks_tracker.compute.run_advice", "--caja", str(cash)])
    for step in steps:
        result = subprocess.run(
            [sys.executable, "-m", *step], cwd=project_root(),
            capture_output=True, text=True, timeout=900,
        )
        if result.returncode:
            # No exponemos salida que pudiera contener credenciales.
            raise RuntimeError(f"Falló {step[0]} (código {result.returncode})")


def extend_history(ticker):
    result = subprocess.run([sys.executable, '-m', 'stocks_tracker.ingest.history_extension', ticker],
                             cwd=project_root(), capture_output=True, text=True, timeout=900)
    if result.returncode:
        raise RuntimeError('La ampliación no se completó. Revisa el estado de fuentes; los intentos quedan registrados.')
