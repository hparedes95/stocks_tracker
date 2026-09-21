#!/usr/bin/env bash
# Simulacro semanal recomendado: no toca el almacen activo.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-$ROOT/.venv/bin/python}"
cd "$ROOT"
exec "$PYTHON" -m stocks_tracker.core.db --verify-backups
