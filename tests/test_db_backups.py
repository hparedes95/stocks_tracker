"""La base local se puede recuperar si una migracion sale mal."""

from __future__ import annotations

import duckdb

from stocks_tracker.core import db


def _configurar(tmp_path, monkeypatch):
    class Stub:
        warehouse_path = tmp_path / "warehouse.duckdb"
        raw: dict = {}

    monkeypatch.setattr(db, "get_settings", lambda: Stub())
    return Stub.warehouse_path


def test_la_primera_creacion_no_copia_un_fichero_que_no_existe(tmp_path, monkeypatch):
    path = _configurar(tmp_path, monkeypatch)

    db.migrate()

    assert path.exists()
    assert not (tmp_path / "backups").exists()


def test_un_esquema_nuevo_hace_backup_antes_de_tocar_los_datos(tmp_path, monkeypatch):
    _configurar(tmp_path, monkeypatch)
    db.migrate()
    with db.connect() as conn:
        conn.execute("INSERT INTO instruments (ticker, name) VALUES ('TEST', 'Conservar')")

    nuevo = tmp_path / "schema-nuevo.sql"
    nuevo.write_text(
        db.schema_path().read_text("utf-8")
        + "\nCREATE TABLE IF NOT EXISTS tabla_nueva (id INTEGER);\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(db, "schema_path", lambda: nuevo)

    db.migrate()

    copias = list((tmp_path / "backups").glob("warehouse-*.duckdb"))
    assert len(copias) == 1
    with duckdb.connect(str(copias[0]), read_only=True) as copia:
        assert copia.execute(
            "SELECT name FROM instruments WHERE ticker = 'TEST'"
        ).fetchone()[0] == "Conservar"
        tablas = {fila[0] for fila in copia.execute("SHOW TABLES").fetchall()}
        assert "tabla_nueva" not in tablas, "la copia tiene que ser anterior a la migracion"


def test_repetir_el_mismo_esquema_no_duplica_backups(tmp_path, monkeypatch):
    _configurar(tmp_path, monkeypatch)
    db.migrate()
    nuevo = tmp_path / "schema-nuevo.sql"
    nuevo.write_text(
        db.schema_path().read_text("utf-8")
        + "\nCREATE TABLE IF NOT EXISTS tabla_nueva (id INTEGER);\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(db, "schema_path", lambda: nuevo)

    db.migrate()
    db.migrate()

    assert len(list((tmp_path / "backups").glob("warehouse-*.duckdb"))) == 1


def test_restaurar_valida_y_conserva_el_estado_anterior(tmp_path, monkeypatch):
    _configurar(tmp_path, monkeypatch)
    db.migrate()
    with db.connect() as conn:
        conn.execute("INSERT INTO instruments (ticker, name) VALUES ('TEST', 'Original')")

    nuevo = tmp_path / "schema-nuevo.sql"
    nuevo.write_text(
        db.schema_path().read_text("utf-8")
        + "\nCREATE TABLE IF NOT EXISTS tabla_nueva (id INTEGER);\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(db, "schema_path", lambda: nuevo)
    db.migrate()
    copia = db.backups_disponibles()[0]
    with db.connect() as conn:
        conn.execute("UPDATE instruments SET name = 'Cambiado' WHERE ticker = 'TEST'")

    seguridad = db.restaurar_backup(copia)

    assert seguridad is not None and seguridad.exists()
    with db.connect(read_only=True) as conn:
        assert conn.execute(
            "SELECT name FROM instruments WHERE ticker = 'TEST'"
        ).fetchone()[0] == "Original"


def test_no_se_puede_restaurar_un_fichero_de_fuera(tmp_path, monkeypatch):
    _configurar(tmp_path, monkeypatch)
    ajeno = tmp_path / "ajeno.duckdb"
    duckdb.connect(str(ajeno)).close()

    try:
        db.restaurar_backup(ajeno)
    except ValueError as exc:
        assert "carpeta de backups" in str(exc)
    else:
        raise AssertionError("se acepto una base ajena como copia propia")


def test_el_simulacro_clona_y_lee_todas_las_tablas(tmp_path, monkeypatch):
    _configurar(tmp_path, monkeypatch)
    db.migrate()
    with db.connect() as conn:
        conn.execute("INSERT INTO instruments (ticker, name) VALUES ('TEST', 'Original')")
    nuevo = tmp_path / "schema-nuevo.sql"
    nuevo.write_text(
        db.schema_path().read_text("utf-8")
        + "\nCREATE TABLE IF NOT EXISTS tabla_nueva (id INTEGER);\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(db, "schema_path", lambda: nuevo)
    db.migrate()

    resultado = db.verificar_backup(db.backups_disponibles()[0])

    assert resultado["tables"] > 0
    assert resultado["rows"] >= 1
    assert not list((tmp_path / "backups").glob("restore-drill-*.duckdb"))
