#!/usr/bin/env bash
# Actualizacion manual: datos -> calculo -> asesor -> informe. Sin cron ni avisos.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PYTHON="${PYTHON:-$ROOT/.venv/bin/python}"
# Un solo escritor. Ante un fallo se interrumpe, no se calculan consejos nuevos.
mkdir -p "$ROOT/data"
exec 9>"$ROOT/data/manual-update.lock"
flock -n 9 || { echo "Hay otra actualizacion en curso."; exit 1; }
"$PYTHON" -m stocks_tracker.ingest.run_ingest --what all
"$PYTHON" -m stocks_tracker.compute.run_compute
"$PYTHON" -m stocks_tracker.compute.run_advice
"$PYTHON" -m stocks_tracker.core.daily_report
