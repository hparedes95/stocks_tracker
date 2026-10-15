import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pytest

from stocks_tracker.core.config import project_root

spec = importlib.util.spec_from_file_location('release_install', project_root() / 'installer/release_install.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


@pytest.fixture(autouse=True)
def disk_capacity(monkeypatch):
    monkeypatch.setattr(release.shutil, 'disk_usage', lambda _: SimpleNamespace(free=100 * release.GIB))


@pytest.fixture
def package(tmp_path):
    source, target = tmp_path / 'source', tmp_path / 'app'
    for root in (source, target):
        for name in ('pyproject.toml', 'requirements-runtime.lock', 'src/stocks_tracker/core/schema.sql'):
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('package', encoding='utf-8')
    (target / 'portfolio').write_text('original', encoding='utf-8')
    (target / '.version').write_text('old', encoding='ascii')
    return source, target


def test_failure_before_promotion_keeps_installation(package):
    source, target = package
    def fail(stage):
        raise OSError('disk full or dependency failure')
    with pytest.raises(OSError):
        release.install(source, target, 'a' * 40, prepare=fail)
    assert (target / 'portfolio').read_text() == 'original'
    assert (target / '.version').read_text() == 'old'
    assert not list(target.parent.glob('app.staging-*'))


def test_failure_after_swap_restores_code_and_data(package):
    source, target = package
    def fail(root):
        (root / 'partially-migrated').touch()
        raise RuntimeError('failed smoke check')
    with pytest.raises(RuntimeError):
        release.install(source, target, 'a' * 40, prepare=lambda _: None, check=fail)
    assert (target / '.version').read_text() == 'old'
    assert (target / 'portfolio').read_text() == 'original'
    assert not (target / 'partially-migrated').exists()


def test_success_and_restore_preserve_newer_data_separately(package):
    source, target = package
    state = release.install(source, target, 'a' * 40, prepare=lambda _: None, check=lambda _: None)
    assert (target / '.version').read_text().strip() == 'a' * 40
    assert (Path(state['previous']) / 'portfolio').read_text() == 'original'
    (target / 'new-purchase').write_text('new', encoding='utf-8')
    release.restore(target)
    assert (target / '.version').read_text() == 'old'
    assert list(target.parent.glob('app.before-restore-*/new-purchase'))


def test_changed_private_data_aborts_before_replacement(package):
    source, target = package
    def prepare(stage):
        (stage / '.source-data-hash').write_text(release.private_digest(target))
        (target / '.env').write_text('changed')
    with pytest.raises(ValueError, match='cambió'):
        release.install(source, target, 'a' * 40, prepare=prepare, check=lambda _: None)
    assert (target / '.version').read_text() == 'old'


def test_incomplete_package_never_touches_destination(package):
    source, target = package
    (source / 'requirements-runtime.lock').unlink()
    with pytest.raises(ValueError, match='incompleto'):
        release.install(source, target, 'a' * 40)
    assert (target / 'portfolio').read_text() == 'original'


def test_copy_private_checkpoints_and_keeps_configuration_separate(tmp_path):
    old, stage = tmp_path / 'old', tmp_path / 'stage'
    (old / 'data').mkdir(parents=True)
    (old / 'config').mkdir()
    stage.mkdir()
    (old / 'config/settings.yaml').write_text('paths:\n  warehouse: data/warehouse.duckdb\n')
    (old / '.env').write_text('private')
    with duckdb.connect(str(old / 'data/warehouse.duckdb')) as c:
        c.execute('CREATE TABLE portfolio AS SELECT 7 AS quantity')
    release.copy_private(old, stage)
    with duckdb.connect(str(stage / 'data/warehouse.duckdb')) as c:
        assert c.execute('SELECT quantity FROM portfolio').fetchone()[0] == 7
    assert (stage / '.env').read_text() == 'private'
    assert (stage / 'config.anterior/settings.yaml').exists()
    assert not (stage / 'config').exists()


def test_interruption_before_first_rename_can_be_recovered(package):
    source, target = package
    stage = release.sibling(target, 'staging')
    stage.mkdir()
    journal = target.with_name(target.name + '.update.json')
    journal.write_text(json.dumps({'target': str(target), 'stage': str(stage),
                                  'previous': str(release.sibling(target, 'previous'))}))
    release.restore(target)
    assert (target / 'portfolio').read_text() == 'original'
    assert stage.exists()
    assert not journal.exists()


def test_full_disk_aborts_before_dependencies_and_copy(package, monkeypatch):
    source, target = package
    monkeypatch.setattr(release.shutil, 'disk_usage', lambda _: SimpleNamespace(free=0))
    def unexpected(_):
        pytest.fail('No se debe instalar nada sin espacio')
    with pytest.raises(OSError, match='Espacio insuficiente'):
        release.install(source, target, 'a' * 40, prepare=unexpected)
    assert not list(target.parent.glob('app.staging-*'))
    assert (target / 'portfolio').read_text() == 'original'


def test_space_estimate_includes_backups_and_migration_copy(package, monkeypatch):
    source, target = package
    (target / 'data/backups').mkdir(parents=True)
    (target / 'data/warehouse.duckdb').write_bytes(b'x' * 100)
    (target / 'data/backups/old.duckdb').write_bytes(b'x' * 80)
    estimates = []
    monkeypatch.setattr(release, 'require_space', lambda _, needed: estimates.append(needed))
    release.install(source, target, 'a' * 40, prepare=lambda _: None, check=lambda _: None)
    assert estimates[0] == 3 * release.GIB + 180 + 100 + sum(p.stat().st_size for p in source.rglob('*') if p.is_file())


def test_cancel_preparation_removes_only_its_own_stage(package):
    source, target = package
    unrelated = target.with_name('app.staging-000000000000')
    unrelated.mkdir()
    (unrelated / 'keep').write_text('legacy')
    def cancel(stage):
        (stage / 'partial-data').write_bytes(b'partial')
        raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        release.install(source, target, 'a' * 40, prepare=cancel)
    assert list(target.parent.glob('app.staging-*')) == [unrelated]
    assert (unrelated / 'keep').read_text() == 'legacy'


def test_cleanup_refuses_paths_outside_exact_owned_sibling(package, tmp_path):
    _, target = package
    external = tmp_path / 'unrelated'
    external.mkdir()
    with pytest.raises(ValueError, match='no reconocido'):
        release.remove_owned_tree(external, target, 'staging')
    assert external.exists()


def test_two_managed_previous_copies_and_legacy_is_untouched(package):
    source, target = package
    legacy = target.with_name('app.previous-000000000000')
    legacy.mkdir()
    (legacy / 'important').write_text('private')
    versions = []
    for sha in 'abcd':
        state = release.install(source, target, sha * 40, prepare=lambda _: None, check=lambda _: None)
        versions.append(Path(state['previous']))
    assert not versions[0].exists() and not versions[1].exists()
    assert versions[2].exists() and versions[3].exists()
    assert (legacy / 'important').read_text() == 'private'
    release.restore(target)
    assert (target / '.version').read_text().strip() == 'c' * 40


def test_other_installer_is_rejected_without_creating_stage(package):
    source, target = package
    with release.update_lock(target):
        with pytest.raises(RuntimeError, match='otra actualizacion'):
            release.install(source, target, 'a' * 40)
    assert not list(target.parent.glob('app.staging-*'))


def test_copy_verification_detects_silent_corruption(tmp_path, monkeypatch):
    source, stage = tmp_path / 'old', tmp_path / 'stage'
    (source / 'data').mkdir(parents=True)
    (source / 'config').mkdir()
    stage.mkdir()
    (source / 'data/private').write_text('important')
    (source / 'config/settings.yaml').write_text('paths:\n  warehouse: data/warehouse.duckdb\n')
    original = release.shutil.copytree
    def corrupt(src, dst, *args, **kwargs):
        result = original(src, dst, *args, **kwargs)
        if Path(dst) == stage / 'data':
            (Path(dst) / 'private').write_text('damaged')
        return result
    monkeypatch.setattr(release.shutil, 'copytree', corrupt)
    with pytest.raises(ValueError, match='no coincide'):
        release.copy_private(source, stage)
    assert (source / 'data/private').read_text() == 'important'


def test_unreadable_directory_does_not_silently_skip_private_files(tmp_path, monkeypatch):
    def fail(path, **kwargs):
        kwargs['onerror'](PermissionError('denied'))
        return []
    monkeypatch.setattr(release.os, 'walk', fail)
    with pytest.raises(PermissionError):
        list(release.files_under(tmp_path))


def test_non_object_old_journal_does_not_break_update(package):
    source, target = package
    target.with_name(target.name + '.recovery.json').write_text('[]')
    release.install(source, target, 'e' * 40, prepare=lambda _: None, check=lambda _: None)
    assert (target / '.version').read_text().strip() == 'e' * 40
