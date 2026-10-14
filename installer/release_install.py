"""Instalación transaccional local. No descarga ni desactiva controles TLS."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path


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
        (stage / '.source-data-hash').write_text(before, encoding='ascii')
    finally:
        if connection:
            connection.close()


def install(source: Path, target: Path, sha: str, *, prepare=None, check=None):
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
    stage, previous = sibling(target, 'staging'), sibling(target, 'previous')
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, stage)
    py = stage / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if prepare:
        prepare(stage)
    else:
        run([sys.executable, '-m', 'venv', stage / '.venv'], cwd=stage)
        run([py, '-m', 'pip', 'install', '--require-hashes', '-r', 'requirements-runtime.lock'], cwd=stage)
        # La app usa siempre python -m, no lanzadores con rutas del staging.
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
        run([py, '-m', 'stocks_tracker.core.db', '--migrate'], cwd=stage, env=env)
        run([py, '-c', 'import streamlit; from stocks_tracker.core.db import connect; c=connect(); c.__enter__().execute("SELECT COUNT(*) FROM instruments"); c.__exit__(None,None,None)'], cwd=stage, env=env)
    state = {'target': str(target), 'previous': str(previous), 'stage': str(stage), 'sha': sha}
    journal.write_text(json.dumps(state), encoding='utf-8')
    moved = False
    try:
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
    except Exception:  # noqa: BLE001 — restaurar el conjunto código/datos ante cualquier fallo
        if moved:
            target.rename(sibling(target, 'failed'))
        if previous.exists() and not target.exists():
            previous.rename(target)
        journal.unlink()
        raise
    # Retener el manifiesto de recuperación; no borrar la instalación anterior.
    journal.replace(target.with_name(target.name + '.recovery.json'))
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
