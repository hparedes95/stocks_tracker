.PHONY: help setup migrate ingest ingest-demo compute validate alerts alerts-dry \
	daily run test lint fmt clean

PY := .venv/bin/python
UV := uv

help:
	@echo "make setup        Crea el entorno e instala dependencias"
	@echo "make migrate      Crea o actualiza el almacen DuckDB"
	@echo "make ingest       Descarga datos reales (yfinance) del universo configurado"
	@echo "make ingest-demo  Genera datos sinteticos para probar sin red"
	@echo "make compute      Calcula indicadores, factores, scores y senales"
	@echo "make validate     Valida las senales contra su historico y las etiqueta"
	@echo "make alerts       Evalua las reglas y envia los avisos"
	@echo "make alerts-dry   Igual, pero sin guardar ni enviar nada"
	@echo "make daily        Ciclo completo: ingesta + calculo + alertas"
	@echo "make run          Arranca el dashboard (solo 127.0.0.1)"
	@echo "make test         Ejecuta los tests"
	@echo "make lint         Comprueba estilo"

setup:
	$(UV) venv
	$(UV) pip install -e ".[data,dev]"

# Sin extras de datos: util en entornos sin acceso a Yahoo.
setup-min:
	$(UV) venv
	$(UV) pip install -e ".[dev]"

migrate:
	$(PY) -m stocks_tracker.core.db --migrate

ingest:
	$(PY) -m stocks_tracker.ingest.run_ingest --what all

ingest-demo:
	$(PY) -m stocks_tracker.ingest.run_ingest --what all --provider synthetic

compute:
	$(PY) -m stocks_tracker.compute.run_compute

# Decide que senales se quedan en el dashboard. Ejecutar tras `make compute`.
validate:
	$(PY) -m stocks_tracker.backtest.run_backtest --tag-signals

alerts:
	$(PY) -m stocks_tracker.alerts.run_alerts

# Para afinar reglas nuevas sin llenar el historico de pruebas.
alerts-dry:
	$(PY) -m stocks_tracker.alerts.run_alerts --dry-run

# Lo que conviene poner en cron. Ver la cabecera del script.
daily:
	./scripts/daily_update.sh

# 127.0.0.1 de forma deliberada: Streamlit no tiene autenticacion.
# Nunca exponer en 0.0.0.0. Para acceso remoto, tunel SSH o Tailscale.
run:
	$(PY) -m streamlit run src/stocks_tracker/app/main.py \
		--server.address 127.0.0.1 --server.port 8501 --server.headless true

test:
	$(PY) -m pytest -q

lint:
	.venv/bin/ruff check src tests

fmt:
	.venv/bin/ruff format src tests

clean:
	rm -rf data/warehouse.duckdb data/http_cache.sqlite .pytest_cache .ruff_cache
