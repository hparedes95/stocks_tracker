"""Shared appearance must stay legible and must not change research semantics."""
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

from stocks_tracker.app.components import design, theme
from stocks_tracker.core.config import project_root


def _luminance(color):
    channels = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4 for c in channels]
    return sum(a * b for a, b in zip(linear, (.2126, .7152, .0722), strict=True))


def _contrast(a, b):
    low, high = sorted((_luminance(a), _luminance(b)))
    return (high + .05) / (low + .05)


@pytest.mark.parametrize('mode', ['light', 'dark'])
def test_native_theme_and_chart_palette_match_and_have_readable_text(monkeypatch, mode):
    cfg = tomllib.loads((project_root() / '.streamlit/config.toml').read_text(encoding='utf-8'))
    monkeypatch.setattr(theme.st, 'context', SimpleNamespace(theme=SimpleNamespace(type=mode)))
    p = theme.palette()
    assert p['plane'] == cfg['theme'][mode]['backgroundColor']
    assert p['surface'] == cfg['theme'][mode]['secondaryBackgroundColor']
    assert p['text_primary'] == cfg['theme'][mode]['textColor']
    for foreground in ('text_primary', 'text_secondary', 'muted', 'accent'):
        for background in ('plane', 'surface'):
            assert _contrast(p[foreground], p[background]) >= 4.5, (mode, foreground, background)
    assert _contrast('#ffffff', cfg['theme']['primaryColor']) >= 4.5
    css = design.stylesheet()
    assert f"--stx-surface:{p['surface']}" in css
    assert 'max-width: 640px' in css
    # Appearance must never remove the native theme menu, focus or data warnings.
    assert 'display: none' not in css and 'outline: none' not in css
    assert 'base' not in cfg['theme']


def test_shared_header_escapes_untrusted_section_and_preserves_title(monkeypatch):
    html, titles = [], []
    monkeypatch.setattr(design.st, 'html', html.append)
    monkeypatch.setattr(design.st, 'title', titles.append)
    design.page_header('Asesor de acciones', section='<script>bad</script>')
    assert '<script>' not in html[-1] and '&lt;script&gt;' in html[-1]
    assert titles == ['Asesor de acciones']


def test_small_price_chart_does_not_flatten_movements_against_an_invisible_zero():
    import pandas as pd

    from stocks_tracker.app.components.charts import sparkline

    chart = sparkline(pd.Series([100., 101., 102.]))
    assert chart.data[0].fill != 'tozeroy'
    assert chart.layout.yaxis.rangemode == 'normal'


def test_all_pages_use_shared_header_or_themed_connection_panel():
    pages = Path(project_root() / 'src/stocks_tracker/app/pages')
    for path in pages.glob('*.py'):
        if path.name == '__init__.py':
            continue
        source = path.read_text(encoding='utf-8')
        assert 'page_header(' in source or 'render_configuration()' in source, path.name


def test_empty_research_summary_and_missing_position_stay_explicit(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest, app_test

    monkeypatch.setattr(app_test, 'TMP_DIR', SimpleNamespace(name=str(tmp_path)))

    def script():
        from stocks_tracker.app.components.design import page_header
        from stocks_tracker.app.components.holding_card import render_holding_card, research_summary
        page_header('Asesor de acciones', section='Análisis')
        research_summary([], horizon=6)
        render_holding_card({'id':'missing', 'kind':'cfd', 'symbol':'CIBR', 'ticker':'CIBR',
            'stance':'Pendiente de datos', 'price_date':None, 'source':None, 'cutoff':'2026-10-16',
            'metrics':{}, 'reasons':[], 'review_if':[], 'fundamentals':{}, 'scenario':None,
            'limitations':['Sin histórico; no se inventa una recomendación.']})
    page = AppTest.from_function(script, default_timeout=120).run()
    assert not page.exception
    assert next(m for m in page.metric if m.label == 'Con lectura de mercado').value == '0 / 0'
    assert any('Pendiente de datos' in m.value for m in page.markdown)
    assert any('Sin histórico' in m.value for m in page.markdown)
    assert not page.get('plotly_chart')
