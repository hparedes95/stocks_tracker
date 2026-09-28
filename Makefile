.PHONY: help setup migrate ingest ingest-demo compute compute-presets repair \
	real validate daily \
	run report backup-check test lint fmt clean

PY := .venv/bin/python
UV := uv

help:
	@echo "make setup        Crea el entorno e instala dependencias"
	@echo "make migrate      Crea o actualiza el almacen DuckDB"
	@echo "make real         Cambia los datos de prueba por precios reales (rapido)"
	@echo "make ingest       Descarga el universo completo (yfinance)"
	@echo "make ingest-demo  Genera datos sinteticos para probar sin red"
	@echo "make compute      Calcula indicadores, factores, scores y senales"
	@echo "make compute-presets  Puntua el universo con todos los estilos de inversion"
	@echo "make repair       Reconstruye series con fuentes de precios mezcladas"
	@echo "make validate     Valida las senales contra su historico y las etiqueta"
	@echo "make daily        Actualizacion manual: datos + calculo + asesor"
	@echo "make run          Arranca el dashboard (solo 127.0.0.1)"
	@echo "make report       Exporta el informe diario en Markdown"
	@echo "make backup-check Ensaya la restauracion de las copias"
	@echo "make test         Ejecuta los tests"
	@echo "make lint         Comprueba estilo"

setup:
	$(UV) venv
	$(UV) pip install --require-hashes -r requirements-runtime.lock
	$(UV) pip install -e . --no-deps
	git config core.hooksPath scripts/git-hooks || true

# Sin extras de datos: util en entornos sin acceso a Yahoo.
setup-min:
	$(UV) venv
	$(UV) pip install -e ".[dev]"

migrate:
	$(PY) -m stocks_tracker.core.db --migrate

report:
	$(PY) -m stocks_tracker.core.daily_report

backup-check:
	$(PY) -m stocks_tracker.core.db --verify-backups

ingest:
	$(PY) -m stocks_tracker.ingest.run_ingest --what all

# Camino corto de datos de prueba a precios reales: borra lo sintetico y baja
# solo los indices, que es lo que hace falta para que la portada cuadre.
real:
	$(PY) -m stocks_tracker.ingest.run_ingest --drop-synthetic --what prices \
		--universes INDICES,MACRO --years 3
	$(PY) -m stocks_tracker.compute.run_compute

ingest-demo:
	$(PY) -m stocks_tracker.ingest.run_ingest --what all --provider synthetic

compute:
	$(PY) -m stocks_tracker.compute.run_compute

# Puntua el universo con todos los estilos de factors.yaml, para poder
# comparar rankings desde el dashboard.
compute-presets:
	$(PY) -m stocks_tracker.compute.run_compute --only scores --all-presets

# Reconstruye las series cuyo historico mezcla varias fuentes de precios.
repair:
	$(PY) -m stocks_tracker.ingest.run_ingest --repair-mixed

# Decide que senales se quedan en el dashboard. Ejecutar tras `make compute`.
#
# Los tres pasos van en este orden y no se pueden saltar. El tramo posterior a
# `backtest.confirmation_from` NO se toca durante el descubrimiento: es lo unico
# que permite distinguir una senal de una casualidad bien contada.
validate:
	$(PY) -m stocks_tracker.backtest.run_backtest --tag-signals

# Congela lo que llego a `estable`. A partir de aqui, cambiar la senal, el
# horizonte, el universo, la referencia o el coste es OTRO experimento.
validate-freeze:
	$(PY) -m stocks_tracker.backtest.run_backtest --congelar

# Gasta el tramo reservado. Solo se puede una vez por especificacion: si falla,
# queda refutada y no se puede reintentar sin cambiar algo (y eso se anota).
validate-confirm:
	$(PY) -m stocks_tracker.backtest.run_backtest --fase confirmacion --tag-signals

# Cruza el precio de la cartera, las senales y una muestra rotatoria contra un
# segundo proveedor. No audita el universo entero a proposito: 600 valores por
# tres fuentes al dia son 1.800 peticiones contra APIs gratuitas, y eso no acaba
# en datos verificados sino en un bloqueo por abuso.
#
# Regeneracion manual de la referencia financiera.
oro:
	$(PY) scripts/regenerar_oro.py

auditar:
	$(PY) -m stocks_tracker.ingest.run_audit


# Para afinar reglas nuevas sin llenar el historico de pruebas.

# Actualizacion manual, sin registro de tareas.
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
