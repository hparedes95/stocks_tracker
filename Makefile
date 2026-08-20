.PHONY: help setup migrate ingest ingest-demo compute compute-presets repair \
	real validate alerts alerts-dry daily watch watch-test watch-status \
	run test lint fmt clean

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
	@echo "make alerts       Evalua las reglas y envia los avisos"
	@echo "make alerts-dry   Igual, pero sin guardar ni enviar nada"
	@echo "make daily        Ciclo completo: ingesta + calculo + alertas"
	@echo "make watch        Vigila el mercado en vivo y avisa si se desploma"
	@echo "make watch-test   Simula un desplome del 8% para probar los avisos"
	@echo "make run          Arranca el dashboard (solo 127.0.0.1)"
	@echo "make test         Ejecuta los tests"
	@echo "make lint         Comprueba estilo"

setup:
	$(UV) venv
	$(UV) pip install -e ".[data,dev]"
	git config core.hooksPath scripts/git-hooks || true

# Sin extras de datos: util en entornos sin acceso a Yahoo.
setup-min:
	$(UV) venv
	$(UV) pip install -e ".[dev]"

migrate:
	$(PY) -m stocks_tracker.core.db --migrate

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
# Lo que encuentra tiene consecuencias: un precio sin consenso veta la orden en
# la regla 21 del gestor de riesgo.
auditar:
	$(PY) -m stocks_tracker.ingest.run_audit

alerts:
	$(PY) -m stocks_tracker.alerts.run_alerts

# Para afinar reglas nuevas sin llenar el historico de pruebas.
alerts-dry:
	$(PY) -m stocks_tracker.alerts.run_alerts --dry-run

# Lo que conviene poner en cron. Ver la cabecera del script.
daily:
	./scripts/daily_update.sh

# Vigilancia en vivo. Se queda corriendo: dejalo en una terminal abierta, o
# instalalo como servicio (ver README).
watch:
	$(PY) -m stocks_tracker.watch.run_watch

# Simulacro. Fuerza una caida del 8% con datos sinteticos y manda los avisos
# de verdad, para comprobar que llegan ANTES de necesitarlo.
watch-test:
	$(PY) -m stocks_tracker.watch.run_watch --test-crash 8

watch-status:
	$(PY) -m stocks_tracker.watch.run_watch --status

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
