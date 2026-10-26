"""Security floors, trusted TLS and image rendering, without external requests."""
import re
import ssl
import tomllib
from importlib.metadata import version
from io import BytesIO
from pathlib import Path

import certifi
import pytest
import requests
from packaging.requirements import Requirement
from packaging.version import Version
from PIL import Image
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
FLOORS = {'certifi': '2026.10.19', 'pillow': '13.0.0', 'streamlit': '1.66.1'}


@pytest.mark.parametrize('filename', ['requirements-runtime.lock', 'requirements-dev.lock'])
def test_windows_install_locks_include_security_floors(filename):
    text = (ROOT / filename).read_text(encoding='utf-8')
    for name, floor in FLOORS.items():
        match = re.search(rf'^{name}==([^\s]+) \\$', text, re.MULTILINE)
        assert match, f'{name} must be pinned in {filename}'
        assert Version(match[1]) >= Version(floor)


def test_uv_lock_and_project_prevent_vulnerable_downgrades():
    project = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))
    requirements = {r.name: r.specifier for r in map(Requirement, project['project']['dependencies'])}
    packages = tomllib.loads((ROOT / 'uv.lock').read_text(encoding='utf-8'))['package']
    for name, floor in FLOORS.items():
        assert Version(floor) in requirements[name]
        assert Version({'certifi': '2026.7.22', 'pillow': '12.3.0', 'streamlit': '1.64.0'}[name]) not in requirements[name]
        locked = [p['version'] for p in packages if p['name'] == name]
        assert locked and all(Version(v) >= Version(floor) for v in locked)


@pytest.mark.parametrize('name,floor', FLOORS.items())
def test_running_environment_uses_patched_dependencies(name, floor):
    assert Version(version(name)) >= Version(floor)


def test_auditor_filelock_is_patched_without_adding_it_to_runtime():
    project = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))
    dev = {r.name: r.specifier for r in map(Requirement, project['project']['optional-dependencies']['dev'])}
    assert Version('4.2.1') in dev['filelock']
    assert Version('4.0.1') not in dev['filelock']
    packages = tomllib.loads((ROOT / 'uv.lock').read_text(encoding='utf-8'))['package']
    locked = next(p['version'] for p in packages if p['name'] == 'filelock')
    assert Version(locked) >= Version('4.2.1')
    assert f'filelock=={locked} ' in (ROOT / 'requirements-dev.lock').read_text(encoding='utf-8')
    assert 'filelock==' not in (ROOT / 'requirements-runtime.lock').read_text(encoding='utf-8')
    assert Version(version('filelock')) >= Version('4.2.1')


def test_updated_ca_bundle_loads_without_disabling_tls_checks():
    context = ssl.create_default_context(cafile=certifi.where())
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname
    assert context.get_ca_certs()
    assert requests.Session().verify is True


@pytest.mark.parametrize('format_name', ['PNG', 'JPEG'])
def test_image_roundtrip_with_patched_pillow(format_name):
    source = Image.new('RGB', (24, 16), (20, 50, 90))
    buffer = BytesIO()
    source.save(buffer, format=format_name)
    buffer.seek(0)
    with Image.open(buffer, formats=[format_name]) as restored:
        restored.load()
        assert restored.size == source.size
        assert restored.mode == source.mode
        assert all(abs(a - b) <= 2 for a, b in zip(restored.getpixel((5, 5)), source.getpixel((5, 5)), strict=True))
    assert Image.MAX_IMAGE_PIXELS is not None


def test_streamlit_renders_pillow_images_after_upgrade():
    page = AppTest.from_file(str(ROOT / 'tests/fixtures/pillow_app.py')).run(timeout=20)
    assert not page.exception
