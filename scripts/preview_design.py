"""Isolated visual fixture. No credentials, no requests to a broker, no live book.

Only used by visual QA, never by the Windows launcher. Every screenshot carries
an explicit fictional-data warning. The database lives under ignored reports/.
"""
import runpy
import sys
from pathlib import Path
from types import SimpleNamespace

import streamlit as st

from stocks_tracker.core import db, holding_analysis, investor_profile
from stocks_tracker.core import snaptrade_portfolio as snap
from stocks_tracker.core.config import get_settings, project_root

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.test_holding_analysis import DAY, seed  # noqa: E402

root = project_root()
settings = get_settings()
settings.raw['paths']['warehouse'] = str(root / 'reports/design-preview.duckdb')
settings.raw.setdefault('ui', {}).setdefault('tradingview', {})['enabled'] = False
holding_analysis.date = SimpleNamespace(today=lambda: DAY.date())
db.migrate()
with db.connect() as conn:
    if not conn.execute('SELECT count(*) FROM instruments').fetchone()[0]:
        seed(conn)
    snap.enable(conn, True, automatic=False)
    investor_profile.save(conn, {**investor_profile.DEFAULTS, 'confirmed':True})

original_page = st.Page


def preview_page(page, **kwargs):
    if isinstance(page, str) and page.startswith('pages/'):
        page = root / 'src/stocks_tracker/app' / page
    return original_page(page, **kwargs)


st.Page = preview_page
st.set_page_config(layout='wide')
st.warning('VISTA DE DISEÑO · Datos ficticios de prueba, no cotizaciones reales ni tu cartera. No sirven para invertir.')
try:
    runpy.run_path(str(root / 'src/stocks_tracker/app/main.py'), run_name='__main__')
finally:
    st.Page = original_page
