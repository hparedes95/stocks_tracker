"""Tests de la exclusion entre procesos que escriben.

Existe por un escenario concreto: al encender el ordenador por la manana, la
tarea programada que se perdio anoche arranca por `StartWhenAvailable` justo
mientras el usuario abre el dashboard, que tambien se pone al dia. DuckDB
admite un solo escritor, asi que uno de los dos moriria con un error de bloqueo
que el lanzador interpreta como "hacen falta datos" y relanza la descarga.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from stocks_tracker.core import locking
from stocks_tracker.core.locking import AlreadyRunning, single_writer


@pytest.fixture
def lock_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(locking, "lock_path", lambda: tmp_path / "writer.lock")
    return tmp_path / "writer.lock"


def test_second_process_is_turned_away(lock_dir):
    with single_writer("primero"):
        with pytest.raises(AlreadyRunning):
            with single_writer("segundo"):
                pytest.fail("el segundo no deberia haber entrado")


def test_lock_is_released_on_exit(lock_dir):
    with single_writer("uno"):
        assert lock_dir.exists()
    assert not lock_dir.exists()

    # Y el siguiente puede entrar sin problema.
    with single_writer("dos"):
        assert lock_dir.exists()


def test_lock_is_released_even_if_the_work_fails(lock_dir):
    """Un fallo dentro del bloqueo no puede dejarlo tomado para siempre."""
    with pytest.raises(ValueError), single_writer("revienta"):
        raise ValueError("algo ha ido mal")

    assert not lock_dir.exists()
    with single_writer("siguiente"):
        pass


def test_a_stale_lock_is_reclaimed(lock_dir, monkeypatch):
    """Un proceso que muere sin limpiar dejaria el sistema sin actualizarse
    nunca. Pasadas unas horas, el bloqueo se considera abandonado."""
    lock_dir.parent.mkdir(parents=True, exist_ok=True)
    lock_dir.write_text("pid=99999 tarea=zombi", encoding="utf-8")

    old = lock_dir.stat().st_mtime - (locking.STALE_AFTER_HOURS + 1) * 3600
    os.utime(lock_dir, (old, old))

    with single_writer("nuevo"):
        assert lock_dir.exists()


def test_a_fresh_lock_is_respected(lock_dir):
    """Lo contrario tambien seria un fallo: reclamar un bloqueo recien tomado
    permitiria dos escritores a la vez, que es justo lo que se evita."""
    lock_dir.parent.mkdir(parents=True, exist_ok=True)
    lock_dir.write_text("pid=1234 tarea=en curso", encoding="utf-8")

    with pytest.raises(AlreadyRunning):
        with single_writer("intruso"):
            pass


def test_the_message_says_who_holds_it(lock_dir):
    """Para poder diagnosticar por que no se actualizo algo."""
    with single_writer("ingesta nocturna"):
        with pytest.raises(AlreadyRunning, match="ingesta nocturna"):
            with single_writer("otro"):
                pass


def test_lock_lives_next_to_the_logs(monkeypatch, tmp_path):
    """No en el directorio de datos: ahi esta el almacen, y un fichero suelto
    entre los datos invita a borrarlo por error."""
    assert locking.lock_path().name == "writer.lock"
    assert locking.lock_path().parent.name == "logs"


def test_ingest_takes_the_lock_before_writing():
    """Guardarrail: si alguien anade un camino de escritura fuera del bloqueo,
    vuelve el problema."""
    from stocks_tracker.core.config import project_root

    src = Path(project_root() / "src/stocks_tracker/ingest/run_ingest.py").read_text("utf-8")
    main = src[src.index("def main()"):src.index("def _run(")]

    assert "single_writer" in main, "la ingesta escribe sin tomar el bloqueo"
    # Las operaciones destructivas tienen que quedar DENTRO.
    lock_at = main.index("single_writer")
    for destructive in ("drop_synthetic()", "repair_mixed_sources("):
        assert main.index(destructive) > lock_at, (
            f"{destructive} se ejecuta antes de tomar el bloqueo"
        )
