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
    html, titles, captions = [], [], []
    monkeypatch.setattr(design.st, 'html', html.append)
    monkeypatch.setattr(design.st, 'title', titles.append)
    monkeypatch.setattr(design.st, 'caption', captions.append)
    design.page_header('Asesor de acciones', section='<script>bad</script>')
    assert '<script>' not in html[-1] and '&lt;script&gt;' in html[-1]
    assert titles == ['Asesor de acciones']
    assert captions == [design.PAGE_DESCRIPTIONS['Asesor de acciones']]


def test_every_registered_route_has_a_shared_orientation():
    import re

    main = (project_root() / 'src/stocks_tracker/app/main.py').read_text(encoding='utf-8')
    titles = set(re.findall(r'title="([^"]+)"', main)) - {'Stocks Tracker'}
    assert titles == set(design.PAGE_DESCRIPTIONS)
    assert len(titles) == 13


def test_native_panels_preserve_keys_and_controls(monkeypatch):
    calls = []
    monkeypatch.setattr(design.st, 'container', lambda **kwargs: calls.append(kwargs))
    design.panel('forecast-controls')
    assert calls == [{'border':True, 'key':'panel-forecast-controls'}]


def test_visual_fixture_only_populates_the_supplied_disposable_database():
    import duckdb

    from scripts.design_fixture import populate
    from stocks_tracker.core import db
    from tests.test_holding_analysis import seed

    with duckdb.connect(':memory:') as conn:
        conn.execute(db.schema_path().read_text(encoding='utf-8'))
        seed(conn)
        populate(conn)
        assert conn.execute('SELECT count(*) FROM instruments').fetchone()[0] == 8
        assert conn.execute('SELECT count(*) FROM regime_daily').fetchone()[0] == 90
        assert conn.execute('SELECT count(*) FROM recommendations').fetchone()[0] == 0


def test_small_price_chart_does_not_flatten_movements_against_an_invisible_zero():
    import pandas as pd

    from stocks_tracker.app.components.charts import sparkline

    chart = sparkline(pd.Series([100., 101., 102.]))
    assert chart.data[0].fill != 'tozeroy'
    assert chart.layout.yaxis.rangemode == 'normal'
    assert chart.data[0].hoverinfo == 'skip'


def test_price_tooltip_uses_only_supplied_trading_dates():
    import pandas as pd

    from stocks_tracker.app.components.charts import sparkline

    dates = pd.Series(['2026-10-09', '2026-10-13'], index=[4, 8])
    values = pd.Series([101.25, 102.75], index=[20, 21])
    chart = sparkline(values, dates=dates)
    assert list(pd.to_datetime(chart.data[0].x)) == list(pd.to_datetime(dates))
    assert list(chart.data[0].y) == [101.25, 102.75]
    assert 'Precio ajustado' in chart.data[0].hovertemplate
    assert chart.layout.dragmode is False
    with pytest.raises(ValueError, match='Cada precio necesita su fecha'):
        sparkline(values, dates=dates.iloc[:1])


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
    assert any('Corte del análisis: 2026-10-16' in c.value for c in page.caption)
    assert any('Fuente: no disponible' in c.value for c in page.caption)
    assert not page.get('plotly_chart')


def test_holding_chart_respects_report_cut_and_preserves_all_explanations(tmp_path, monkeypatch):
    import json

    import pandas as pd
    from streamlit.testing.v1 import AppTest, app_test

    monkeypatch.setattr(app_test, 'TMP_DIR', SimpleNamespace(name=str(tmp_path)))

    def script():
        import pandas as pd

        from stocks_tracker.app.components.holding_card import render_holding_card
        prices = pd.DataFrame({'date':pd.date_range('2026-07-01', periods=70, freq='B'),
                               'adj_close':range(100, 170)})
        render_holding_card({'id':'cut-test', 'kind':'cfd', 'symbol':'CIBR', 'ticker':'CIBR',
            'stance':'Subyacente favorable; vigilar el CFD', 'price_date':'2026-09-30',
            'source':'test-source', 'cutoff':'2026-10-01',
            'metrics':{'momentum_3m_pct':1., 'drawdown_pct':-2., 'daily_vol_pct':3.},
            'reasons':['Tendencia de prueba.', 'RSI de prueba.'],
            'review_if':['Revisar la exposición.'], 'fundamentals':{}, 'scenario':None,
            'limitations':['No es rentabilidad neta del contrato.']}, prices=prices)

    page = AppTest.from_function(script, default_timeout=120).run()
    assert not page.exception
    chart = json.loads(page.get('plotly_chart')[0].proto.spec)
    dates = pd.to_datetime(chart['data'][0]['x'])
    assert len(dates) == 63 and dates.max() == pd.Timestamp('2026-09-30')
    text = ' '.join(m.value for m in page.markdown)
    for phrase in ('Tendencia de prueba.', 'RSI de prueba.', 'Revisar la exposición.',
                   'No es rentabilidad neta del contrato.', 'Subyacente favorable; vigilar el CFD'):
        assert phrase in text
    assert any('Fuente: test-source' in c.value for c in page.caption)
    assert any('Corte del análisis: 2026-10-01' in c.value for c in page.caption)
