"""Instalación transaccional local. No descarga ni desactiva controles TLS."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path

GIB = 1024 ** 3
OWNED_BACKUP = '.stocks-tracker-recovery.json'


def is_link(path):
    return path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction())


def files_under(path):
    """No seguir enlaces, tampoco dentro de carpetas de seguridad."""
    if is_link(path):
        raise ValueError(f'No se admiten enlaces: {path}')
    if path.is_file():
        yield path
    elif path.is_dir():
        def fail(error):
            raise error
        for directory, folders, files in os.walk(path, followlinks=False, onerror=fail):
            for name in folders + files:
                item = Path(directory) / name
                if is_link(item):
                    raise ValueError(f'No se admiten enlaces: {item}')
            yield from (Path(directory) / name for name in files)


def private_size(root):
    files = [p for name in ('data', '.env', 'config') for p in files_under(root / name)]
    size = sum(p.stat().st_size for p in files)
    # Una migración puede crear otra copia completa del almacén.
    database = max((p.stat().st_size for p in files if p.suffix == '.duckdb'), default=0)
    return size, database


def require_space(directory, needed):
    free = shutil.disk_usage(directory).free
    print(f'Espacio libre: {free / GIB:.2f} GiB; reserva estimada necesaria: {needed / GIB:.2f} GiB.', flush=True)
    if free < needed:
        raise OSError(f'Espacio insuficiente: faltan {(needed - free) / GIB:.2f} GiB '
                      f'en {directory}. No se ha reemplazado la instalacion.')


def remove_owned_tree(path, target, kind):
    """Solo el hermano exacto validado; nunca barrer con comodines."""
    if (is_link(path) or path.parent.resolve() != target.parent.resolve() or
            not re.fullmatch(re.escape(target.name) + r'\.' + kind + r'-[0-9a-f]{12}', path.name)):
        raise ValueError('Destino de limpieza no reconocido')
    if not path.exists():
        return
    if path.resolve() != target.parent.resolve() / path.name:
        raise ValueError('La limpieza apunta fuera de la instalacion')
    # Validar antes de iniciar cualquier borrado recursivo.
    for _ in files_under(path):
        pass
    shutil.rmtree(path)
    print(f'Eliminada copia administrada: {path.name}', flush=True)


def owned_backup(path, target):
    if (is_link(path) or path.parent != target.parent or
            not re.fullmatch(re.escape(target.name) + r'\.previous-[0-9a-f]{12}', path.name)):
        return False
    marker = path / OWNED_BACKUP
    try:
        return not is_link(marker) and json.loads(marker.read_text(encoding='utf-8')) == {
            'target': str(target), 'backup': path.name}
    except (OSError, ValueError):
        return False


def retain_backups(target, previous, old_state, state):
    """Dos versiones anteriores gestionadas; las copias heredadas no se borran."""
    if previous.exists():
        (previous / OWNED_BACKUP).write_text(json.dumps({'target': str(target), 'backup': previous.name}), encoding='utf-8')
    retained = [str(previous)] if previous.exists() else []
    if old_state.get('target') == str(target):
        for raw in old_state.get('retained_previous', []):
            path = Path(raw)
            if raw not in retained and owned_backup(path, target):
                retained.append(raw)
    # Publicar la recuperación vigente antes de retirar ninguna copia antigua.
    state['retained_previous'] = retained
    recovery = target.with_name(target.name + '.recovery.json')
    temporary = recovery.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(state), encoding='utf-8')
    temporary.replace(recovery)
    for raw in retained[2:]:
        if owned_backup(Path(raw), target):
            remove_owned_tree(Path(raw), target, 'previous')


@contextmanager
def update_lock(target):
    """Evitar dos instaladores simultáneos, sin depender de paquetes externos."""
    lock_path = target.with_name(target.name + '.install.lock')
    if is_link(lock_path):
        raise ValueError('El bloqueo de instalacion no puede ser un enlace')
    with lock_path.open('a+b') as stream:
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError('Ya hay otra actualizacion en curso; no abras otro instalador.') from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def sibling(target: Path, kind: str) -> Path:
    return target.with_name(f'{target.name}.{kind}-{uuid.uuid4().hex[:12]}')


def run(args, *, cwd, env=None):
    subprocess.run([str(a) for a in args], cwd=cwd, env=env, check=True)


def private_digest(root: Path):
    digest = hashlib.sha256()
    paths = [root / '.env', *(root / 'data').rglob('*')]
    for path in sorted(paths):
        if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
            raise ValueError('Los datos contienen enlaces; no se copian automáticamente')
        if path.is_file():
            digest.update(path.relative_to(root).as_posix().encode())
            with path.open('rb') as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(chunk)
    return digest.hexdigest()


def copy_private(source: Path, stage: Path):
    """Checkpoint y huellas antes/después; cancelar ante escritura concurrente.

    Windows no permite copiar el fichero con la conexión escritora abierta.
    Se exige cerrar la app; los cambios concurrentes detectados abortan la promoción.
    """
    import duckdb
    import yaml

    settings = yaml.safe_load((source / 'config/settings.yaml').read_text(encoding='utf-8'))
    database = (source / settings['paths']['warehouse']).resolve()
    if not database.is_relative_to((source / 'data').resolve()):
        raise ValueError('La base personalizada está fuera de data; no se actualiza automáticamente.')
    new_settings_path = stage / 'config/settings.yaml'
    if new_settings_path.exists():
        new_settings = yaml.safe_load(new_settings_path.read_text(encoding='utf-8'))
        if Path(settings['paths']['warehouse']) != Path(new_settings['paths']['warehouse']):
            raise ValueError('La ruta personalizada de la base difiere de la nueva versión; no se reemplaza la instalación.')
    connection = duckdb.connect(str(database)) if database.exists() else None
    try:
        if connection:
            connection.execute('CHECKPOINT')
            connection.close()
            connection = None
        size, database_size = private_size(source)
        require_space(stage, size + database_size + max(GIB, database_size // 5))
        print('Copiando datos privados y copias de seguridad; puede tardar.', flush=True)
        before = private_digest(source)
        for name in ('data', '.env'):
            path = source / name
            if path.is_dir():
                shutil.copytree(path, stage / name)
            elif path.is_file():
                shutil.copy2(path, stage / name)
        shutil.copytree(source / 'config', stage / 'config.anterior')
        if private_digest(source) != before:
            raise ValueError('Los datos cambiaron durante la copia; cierra la aplicación')
        if private_digest(stage) != before:
            raise ValueError('La copia de datos no coincide con el original; se cancela la actualizacion')
        (stage / '.source-data-hash').write_text(before, encoding='ascii')
    finally:
        if connection:
            connection.close()


def install(source: Path, target: Path, sha: str, *, prepare=None, check=None):
    # Validar la ruta antes de crear incluso el fichero de bloqueo.
    for path in (target, *target.absolute().parents):
        if is_link(path):
            raise ValueError('No se admiten enlaces en el destino')
    target = target.resolve()
    if target == Path(target.anchor) or target == Path.home() or len(target.parts) < 3:
        raise ValueError('Destino demasiado amplio')
    target.parent.mkdir(parents=True, exist_ok=True)
    with update_lock(target):
        return _install(source, target, sha, prepare=prepare, check=check)


def prepare_release(stage, target):
    py = stage / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    run([sys.executable, '-m', 'venv', stage / '.venv'], cwd=stage)
    run([py, '-m', 'pip', 'install', '--require-hashes', '-r', 'requirements-runtime.lock'], cwd=stage)
    site = subprocess.check_output([str(py), '-c', 'import sysconfig; print(sysconfig.get_path("purelib"))'], text=True).strip()
    (Path(site) / 'stocks_tracker.pth').write_text(str(target / 'src') + '\n', encoding='utf-8')
    if target.exists():
        run([py, __file__, '--copy-private', target, stage], cwd=stage)
    elif target.with_name('StocksTracker.backup').is_dir():
        legacy = target.with_name('StocksTracker.backup')
        for name in ('data', '.env'):
            old = legacy / name
            if old.is_dir():
                shutil.copytree(old, stage / name)
            elif old.is_file():
                shutil.copy2(old, stage / name)
    if not (stage / '.env').exists():
        shutil.copy2(stage / '.env.example', stage / '.env')
    env = {**os.environ, 'PYTHONPATH': str(stage / 'src'), 'STOCKS_TRACKER_ROOT': str(stage)}
    print('Comprobando migracion y arranque de la copia preparada.', flush=True)
    run([py, '-m', 'stocks_tracker.core.db', '--migrate'], cwd=stage, env=env)
    run([py, '-c', 'import streamlit; from stocks_tracker.core.db import connect; c=connect(); c.__enter__().execute("SELECT COUNT(*) FROM instruments"); c.__exit__(None,None,None)'], cwd=stage, env=env)


def _install(source: Path, target: Path, sha: str, *, prepare=None, check=None):
    for path in (source, target, *target.absolute().parents):
        if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
            raise ValueError('No se admiten enlaces en el destino')
    source, target = source.resolve(), target.resolve()
    if len(sha) != 40 or any(c not in '0123456789abcdef' for c in sha):
        raise ValueError('SHA inválido')
    if target == Path(target.anchor) or target == Path.home() or len(target.parts) < 3:
        raise ValueError('Destino demasiado amplio')
    if source == target or source.is_relative_to(target) or target.is_relative_to(source):
        raise ValueError('Origen y destino deben estar separados')
    for path in (source, target, *target.parents):
        if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
            raise ValueError('No se admiten enlaces en el destino')
    if target.exists() and not all((target / name).is_file() for name in
                                   ('pyproject.toml', 'src/stocks_tracker/core/schema.sql')):
        raise ValueError('El destino no es una instalación reconocida')
    for required in ('pyproject.toml', 'requirements-runtime.lock', 'src/stocks_tracker/core/schema.sql'):
        if not (source / required).is_file():
            raise ValueError(f'Paquete incompleto: {required}')
    journal = target.with_name(target.name + '.update.json')
    if journal.exists():
        raise ValueError(f'Actualización interrumpida: recupera primero con --restore {target}')
    recovery = target.with_name(target.name + '.recovery.json')
    try:
        old_state = json.loads(recovery.read_text(encoding='utf-8'))
        if not isinstance(old_state, dict):
            old_state = {}
    except (OSError, ValueError):
        old_state = {}
    private_root = target if target.exists() else target.with_name('StocksTracker.backup')
    size, database_size = private_size(private_root)
    runtime_size = sum(p.stat().st_size for p in files_under(target / '.venv'))
    package_size = sum(p.stat().st_size for p in files_under(source))
    required = package_size + size + max(2 * GIB, runtime_size) + database_size + max(GIB, database_size // 5)
    require_space(target.parent, required)
    stage, previous = sibling(target, 'staging'), sibling(target, 'previous')
    stage.mkdir()  # A partir de aquí esta carpeta exacta pertenece a este intento.
    try:
        shutil.copytree(source, stage, dirs_exist_ok=True)
        if prepare:
            prepare(stage)
        else:
            prepare_release(stage, target)
    except BaseException:
        # Fallo/cancelación antes de promover: nunca contiene la única copia de datos.
        try:
            remove_owned_tree(stage, target, 'staging')
        except (OSError, ValueError) as exc:
            print(f'No se pudo limpiar {stage}: {exc}', file=sys.stderr)
        raise
    state = {'target': str(target), 'previous': str(previous), 'stage': str(stage), 'sha': sha}
    moved = False
    try:
        journal.write_text(json.dumps(state), encoding='utf-8')
        if target.exists():
            marker = stage / '.source-data-hash'
            if marker.exists() and private_digest(target) != marker.read_text(encoding='ascii'):
                raise ValueError('La cartera cambió durante la preparación; se cancela la actualización')
            target.rename(previous)
            if marker.exists() and private_digest(previous) != marker.read_text(encoding='ascii'):
                raise ValueError('La cartera cambió; se restaura la instalación anterior')
        stage.rename(target)
        moved = True
        if check:
            check(target)
        else:
            installed_py = target / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
            env = {**os.environ, 'STOCKS_TRACKER_ROOT': str(target), 'PYTHONPATH': str(target / 'src')}
            run([installed_py, '-c', 'from stocks_tracker.core.db import connect; c=connect(); c.__enter__().execute("SELECT 1"); c.__exit__(None,None,None)'], cwd=target, env=env)
        (target / '.version').write_text(sha + '\n', encoding='ascii')
    except BaseException:
        if moved:
            target.rename(sibling(target, 'failed'))
        if previous.exists() and not target.exists():
            previous.rename(target)
        if stage.exists():
            try:
                remove_owned_tree(stage, target, 'staging')
            except (OSError, ValueError) as exc:
                print(f'No se pudo limpiar {stage}: {exc}', file=sys.stderr)
        journal.unlink(missing_ok=True)
        raise
    # Retener la instalación inmediatamente anterior; limpiar solo copias gestionadas.
    journal.replace(target.with_name(target.name + '.recovery.json'))
    try:
        retain_backups(target, previous, old_state, state)
    except (OSError, ValueError, TypeError) as exc:
        print(f'Actualizacion correcta; no se pudieron depurar las copias antiguas: {exc}', file=sys.stderr)
    return state


def restore(target: Path):
    if target.is_symlink() or (hasattr(target, 'is_junction') and target.is_junction()):
        raise ValueError('No se admiten enlaces en el destino')
    target = target.resolve()
    pending = target.with_name(target.name + '.update.json')
    recovery = pending if pending.exists() else target.with_name(target.name + '.recovery.json')
    state = json.loads(recovery.read_text(encoding='utf-8'))
    previous = Path(state['previous']).resolve()
    if (state['target'] != str(target) or previous.parent != target.parent or
            not previous.name.startswith(target.name + '.previous-') or previous.is_symlink() or
            (hasattr(previous, 'is_junction') and previous.is_junction())):
        raise ValueError('No hay una instalación anterior válida; no se modifica nada')
    if not previous.is_dir():
        # Corte antes del primer rename: la instalación anterior sigue en su sitio.
        stage = Path(state['stage']).resolve()
        if (recovery == pending and stage.parent == target.parent and
                stage.name.startswith(target.name + '.staging-') and stage.is_dir() and
                not stage.is_symlink() and (target / 'pyproject.toml').is_file()):
            recovery.unlink()
            return target
        raise ValueError('No hay una instalación anterior válida; no se modifica nada')
    # El estado actual se conserva, incluidos movimientos posteriores a la actualización.
    if Path.cwd().is_relative_to(target):
        os.chdir(target.parent)
    if target.exists():
        target.rename(sibling(target, 'before-restore'))
    previous.rename(target)
    recovery.unlink()
    return target


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path)
    parser.add_argument('--target', type=Path)
    parser.add_argument('--sha')
    parser.add_argument('--copy-private', nargs=2, type=Path)
    parser.add_argument('--restore', type=Path)
    args = parser.parse_args()
    if args.copy_private:
        copy_private(*args.copy_private)
    elif args.restore:
        print(restore(args.restore))
    else:
        print(json.dumps(install(args.source, args.target, args.sha)))


if __name__ == '__main__':
    main()
