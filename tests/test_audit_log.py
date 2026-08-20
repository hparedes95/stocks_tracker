"""El registro que contesta a "¿como se llego a este numero?".

El proyecto ya tenia cuatro registros y cada uno contesta bien a lo suyo:
`ingest_log` que se descargo, `data_quality` que se comprobo, `bot_decisions`
que decidio el bot, `price_consensus` que dijo cada proveedor.

Ninguno contestaba a esta: *el score de AAPL salio 82,4 el martes, ¿como?*.
Hacen falta tres cosas a la vez —que datos habia, que version del codigo, que
configuracion— y estaban en tres sitios o en ninguno.
"""

from __future__ import annotations

import json

import pytest

from stocks_tracker.core import audit, db, lineage


@pytest.fixture
def warehouse(tmp_path, monkeypatch):
    class Stub:
        warehouse_path = tmp_path / "test.duckdb"
        compute: dict = {"weights_preset": "balanced"}
        raw: dict = {}

    monkeypatch.setattr(db, "get_settings", lambda: Stub())
    db.migrate()
    return Stub


def filas():
    return db.query("SELECT * FROM audit_log ORDER BY empezado")


# ---------------------------------------------------------------------------
# Lo que se anota
# ---------------------------------------------------------------------------

def test_un_paso_deja_su_rastro(warehouse):
    with audit.paso("scores", config={"preset": "balanced"}) as r:
        r.leido(filas=1200, desde="2020-01-01")
        r.escrito(filas=214)

    fila = filas().iloc[0]
    assert fila["paso"] == "scores"
    assert fila["estado"] == audit.OK
    assert json.loads(fila["entrada"])["filas"] == 1200
    assert json.loads(fila["salida"])["filas"] == 214


def test_se_guarda_la_version_del_codigo_y_la_configuracion(warehouse):
    """Sin las dos, "que datos habia" no basta para reproducir nada."""
    with audit.paso("scores", config={"preset": "value"}):
        pass

    fila = filas().iloc[0]
    assert fila["git_commit"]
    assert fila["config_hash"] == lineage.config_hash({"preset": "value"})


def test_dos_configuraciones_distintas_dan_hashes_distintos(warehouse):
    with audit.paso("scores", config={"preset": "value"}):
        pass
    with audit.paso("scores", config={"preset": "growth"}):
        pass

    hashes = set(filas()["config_hash"])
    assert len(hashes) == 2, "la configuracion no distingue las dos ejecuciones"


def test_los_pasos_de_una_misma_ejecucion_comparten_run_id(warehouse):
    """Es lo que permite reconstruir que el ranking del martes salio de ESTOS
    indicadores. Con un id por paso, las filas quedan sueltas."""
    run = "mismo-run"
    for paso in ("indicators", "scores"):
        with audit.paso(paso, run_id=run):
            pass

    assert set(filas()["run_id"]) == {run}


# ---------------------------------------------------------------------------
# El fallo no se traga
# ---------------------------------------------------------------------------

def test_un_paso_que_revienta_deja_su_fila(warehouse):
    """Un registro que solo guarda los exitos convierte un fallo en un hueco, y
    un hueco se lee igual que "ese dia no se ejecuto"."""
    with pytest.raises(ValueError), audit.paso("indicators"):
        raise ValueError("se ha roto algo")

    fila = filas().iloc[0]
    assert fila["estado"] == audit.ERROR
    assert "se ha roto algo" in fila["detalle"]


def test_la_excepcion_sigue_su_camino(warehouse):
    """Tragarsela aqui convertiria el registro en la causa de que un fallo pase
    inadvertido, que es lo contrario de para lo que existe."""
    with pytest.raises(KeyError), audit.paso("x"):
        raise KeyError("no")


def test_rechazado_no_es_lo_mismo_que_error(warehouse):
    """La puerta de calidad negandose a calcular es el sistema FUNCIONANDO.
    Mezclarlo con las averias haria que en un mes nadie distinguiera un dia con
    datos malos de un dia con el disco lleno."""
    with audit.paso("compute") as r:
        r.rechazado("4 barras imposibles en valores que usan el rango")

    fila = filas().iloc[0]
    assert fila["estado"] == audit.RECHAZADO
    assert fila["estado"] != audit.ERROR


def test_si_falla_el_registro_no_se_tumba_el_paso(warehouse, monkeypatch):
    """Un audit log que rompe la ingesta es peor que no tener audit log."""
    monkeypatch.setattr(audit, "guardar",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disco")))

    with audit.paso("indicators") as r:
        r.escrito(filas=10)      # no debe lanzar nada


# ---------------------------------------------------------------------------
# Reproducible
# ---------------------------------------------------------------------------

def test_un_calculo_con_el_repositorio_sucio_no_es_reproducible(warehouse, monkeypatch):
    """`lineage` marca el commit con `-sucio` cuando hay cambios sin commitear.
    Ese codigo no existe en ningun sitio: el numero no se puede volver a
    obtener, y decir que si seria mentir."""
    monkeypatch.setattr(lineage, "git_commit", lambda: "abc123456789-sucio")

    with audit.paso("scores"):
        pass

    with db.connect(read_only=True) as conn:
        puede, motivo = audit.reproducible(conn, "scores")

    assert not puede
    assert "sin commitear" in motivo


def test_un_calculo_con_el_repositorio_limpio_si_lo_es(warehouse, monkeypatch):
    monkeypatch.setattr(lineage, "git_commit", lambda: "abc123456789")

    with audit.paso("scores"):
        pass

    with db.connect(read_only=True) as conn:
        puede, motivo = audit.reproducible(conn, "scores")

    assert puede
    assert "abc123456789" in motivo


def test_sin_commit_conocido_no_es_reproducible(warehouse, monkeypatch):
    monkeypatch.setattr(lineage, "git_commit", lambda: "desconocido")

    with audit.paso("scores"):
        pass

    with db.connect(read_only=True) as conn:
        puede, _ = audit.reproducible(conn, "scores")

    assert not puede


def test_un_paso_que_nunca_se_ejecuto_no_se_declara_reproducible(warehouse):
    with db.connect(read_only=True) as conn:
        puede, motivo = audit.reproducible(conn, "backtest")

    assert not puede
    assert "ninguna ejecucion" in motivo


def test_una_ejecucion_fallida_no_es_reproducible(warehouse, monkeypatch):
    monkeypatch.setattr(lineage, "git_commit", lambda: "abc123456789")
    with pytest.raises(ValueError), audit.paso("scores"):
        raise ValueError("x")

    with db.connect(read_only=True) as conn:
        puede, _ = audit.reproducible(conn, "scores")

    assert not puede


# ---------------------------------------------------------------------------
# Y el calculo lo usa
# ---------------------------------------------------------------------------

def test_el_calculo_registra_sus_pasos():
    """Guardarrail. El modulo puede existir perfecto y no llamarlo nadie."""
    from stocks_tracker.core.config import project_root

    src = (project_root()
           / "src/stocks_tracker/compute/run_compute.py").read_text("utf-8")

    assert "audit.paso(" in src
    assert "run_id=run_id" in src, (
        "cada paso usa su propio run_id y las filas quedan sueltas"
    )


# ---------------------------------------------------------------------------
# Y la ingesta y el bot tambien
# ---------------------------------------------------------------------------

class _Args:
    """Lo minimo que `_run` mira de la linea de ordenes."""

    what = "all"
    provider = "yfinance"
    full = False
    years = None
    universes = None


def test_la_ingesta_registra_sus_cuatro_pasos(warehouse, monkeypatch):
    """Se ejecuta de verdad, no se lee el codigo.

    Comprobar que el fichero contiene la cadena "audit.paso(" solo demuestra
    que alguien la escribio. Aqui se sustituyen las cuatro descargas por
    contadores y se mira el registro que queda: es lo unico que distingue una
    llamada colocada de una llamada que funciona.
    """
    from stocks_tracker.ingest import run_ingest

    for nombre, filas in (("ingest_universe", 600), ("ingest_prices", 12000),
                          ("ingest_fundamentals", 90), ("ingest_macro", 40)):
        monkeypatch.setattr(run_ingest, nombre,
                            lambda *a, _n=filas, **k: _n)

    run_ingest._run(_Args())

    registro = filas_de("audit_log")
    pasos = set(registro["paso"])
    assert pasos == {"ingest_universe", "ingest_prices", "ingest_fundamentals",
                     "ingest_macro"}, pasos
    assert set(registro["estado"]) == {audit.OK}
    escrito = {r["paso"]: json.loads(r["salida"])["filas"]
               for _, r in registro.iterrows()}
    assert escrito["ingest_prices"] == 12000


def test_los_pasos_de_una_ingesta_comparten_run_id(warehouse, monkeypatch):
    """Con un id por paso las filas quedan sueltas y "¿de donde salio este
    numero?" vuelve a no tener respuesta."""
    from stocks_tracker.ingest import run_ingest

    for nombre in ("ingest_universe", "ingest_prices", "ingest_fundamentals",
                   "ingest_macro"):
        monkeypatch.setattr(run_ingest, nombre, lambda *a, **k: 1)

    run_ingest._run(_Args())

    assert len(set(filas_de("audit_log")["run_id"])) == 1


def test_la_ingesta_pasa_su_run_id_a_las_descargas(warehouse, monkeypatch):
    """El MISMO identificador en `audit_log` y en `ingest_log`.

    Es lo que permite perseguir un numero raro hacia atras sin cortes: del
    score al calculo, del calculo a la ingesta, y de ahi al lote concreto que
    fallo esa noche. Con identificadores distintos las dos mitades del rastro
    no se pueden unir, y un rastro que no se puede seguir entero no lo sigue
    nadie.
    """
    from stocks_tracker.ingest import run_ingest

    recibidos: list[str | None] = []

    def espia(*a, run_id=None, **k):
        recibidos.append(run_id)
        return 1

    for nombre in ("ingest_universe", "ingest_prices", "ingest_fundamentals",
                   "ingest_macro"):
        monkeypatch.setattr(run_ingest, nombre, espia)

    run_ingest._run(_Args())

    del_registro = set(filas_de("audit_log")["run_id"])
    assert len(del_registro) == 1
    assert set(recibidos) == del_registro, (
        f"la ingesta se registra con {del_registro} y descarga con {set(recibidos)}: "
        "las dos mitades del rastro no se pueden unir"
    )


def test_una_descarga_que_revienta_deja_su_fila_de_error(warehouse, monkeypatch):
    """Un registro que solo guarda los exitos convierte un fallo en un hueco, y
    un hueco se lee igual que "ese dia no se ejecuto"."""
    from stocks_tracker.ingest import run_ingest

    monkeypatch.setattr(run_ingest, "ingest_universe", lambda *a, **k: 1)

    def revienta(*a, **k):
        raise RuntimeError("Yahoo ha dejado de responder")

    monkeypatch.setattr(run_ingest, "ingest_prices", revienta)

    with pytest.raises(RuntimeError):
        run_ingest._run(_Args())

    registro = filas_de("audit_log")
    fallo = registro[registro["paso"] == "ingest_prices"].iloc[0]
    assert fallo["estado"] == audit.ERROR
    assert "Yahoo ha dejado de responder" in fallo["detalle"]


def filas_de(tabla: str):
    return db.query(f"SELECT * FROM {tabla} ORDER BY empezado")
