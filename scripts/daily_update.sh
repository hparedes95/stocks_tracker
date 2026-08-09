#!/usr/bin/env bash
#
# Ciclo diario: ingesta -> calculo -> alertas.
#
# Pensado para cron. Ejemplo (23:15 CET de lunes a viernes, con el mercado
# estadounidense ya cerrado y los datos consolidados):
#
#   15 23 * * 1-5 /ruta/a/stocks_tracker/scripts/daily_update.sh
#
# Se usa `flock` para que dos ejecuciones no se solapen: la ingesta es el unico
# escritor del almacen, y dos procesos escribiendo a la vez lo bloquearian.
#
# La validacion de senales NO va aqui: cambia poco de un dia para otro y es
# cara. Ejecutala a mano o con un cron semanal (`make validate`).

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHON="${PYTHON:-$ROOT/.venv/bin/python}"
LOCK_FILE="${LOCK_FILE:-/tmp/stocks_tracker_daily.lock}"
LOG_DIR="$ROOT/data/logs"
LOG_FILE="$LOG_DIR/daily_$(date +%Y%m%d).log"

mkdir -p "$LOG_DIR"

# Carga el .env si existe, para que las claves esten disponibles bajo cron
# (cron no hereda el entorno de tu shell).
if [ -f "$ROOT/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    . "$ROOT/.env"
    set +a
fi

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

run_step() {
    local name="$1"
    shift
    log "--- $name ---"
    # Un paso que falla NO detiene el ciclo: es preferible tener el dashboard
    # con datos de ayer que dejarlo a medias sin alertas.
    if "$@" >>"$LOG_FILE" 2>&1; then
        log "$name: correcto"
    else
        log "$name: FALLIDO (codigo $?)"
        return 1
    fi
}

main() {
    log "===== Ciclo diario ====="

    local failures=0
    run_step "Ingesta" "$PYTHON" -m stocks_tracker.ingest.run_ingest --what all || failures=$((failures + 1))
    run_step "Calculo" "$PYTHON" -m stocks_tracker.compute.run_compute || failures=$((failures + 1))
    run_step "Alertas" "$PYTHON" -m stocks_tracker.alerts.run_alerts || failures=$((failures + 1))

    # Limpieza del historico, para que la tabla de alertas no crezca sin limite.
    "$PYTHON" -m stocks_tracker.alerts.run_alerts --purge-days 365 >>"$LOG_FILE" 2>&1 || true

    # Los logs antiguos tampoco hacen falta.
    find "$LOG_DIR" -name 'daily_*.log' -mtime +60 -delete 2>/dev/null || true

    if [ "$failures" -gt 0 ]; then
        log "===== Terminado con $failures pasos fallidos ====="
        exit 1
    fi
    log "===== Terminado correctamente ====="
}

# -n: si ya hay otra ejecucion, esta sale en lugar de esperar. Con un ciclo
# diario, encolarse no aporta nada.
exec flock -n "$LOCK_FILE" bash -c "$(declare -f log run_step main); \
    ROOT='$ROOT' PYTHON='$PYTHON' LOG_FILE='$LOG_FILE' LOG_DIR='$LOG_DIR' main"
