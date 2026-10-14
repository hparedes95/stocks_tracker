import importlib.util
import json
from pathlib import Path

import duckdb
import pytest

from stocks_tracker.core.config import project_root

spec = importlib.util.spec_from_file_location('release_install', project_root() / 'installer/release_install.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


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
