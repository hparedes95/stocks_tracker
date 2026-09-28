"""Requisitos de investigación histórica, independientes de la ejecución."""

from ..core.advice_calib import factores_de_precio
from ..core.db import connect
from ..core.scoring import preset_hash


def find_blockers(preset: str = "bot_core") -> list[str]:
    blockers = []
    if not factores_de_precio(preset):
        blockers.append("Este estudio requiere un perfil basado solo en precios; "
                        "no valida los fundamentales del asesor.")
    with connect(read_only=True) as conn:
        synthetic = conn.execute(
            "SELECT COUNT(*) FROM prices_daily WHERE source = 'synthetic'"
        ).fetchone()[0]
        sessions = conn.execute(
            "SELECT COUNT(DISTINCT date) FROM factor_scores WHERE weights_hash = ?",
            [preset_hash(preset)],
        ).fetchone()[0]
    if synthetic:
        blockers.append("Hay precios sintéticos: no sirven como evidencia de mercado.")
    if sessions < 100:
        blockers.append(f"Solo hay {sessions} sesiones de ranking histórico; se necesitan 100. "
                        "Genera el historial con run_compute --history.")
    return blockers
