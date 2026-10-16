"""Real-browser visual QA of native themes and responsive holding cards.

Playwright is a CI-only tool, not a runtime dependency for Windows users.
Run against scripts/preview_design.py: that fixture cannot access a live book.
"""
import json
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

from playwright.sync_api import TimeoutError as BrowserTimeout
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'reports/design-qa'
URL = 'http://127.0.0.1:8517'
ROUTES = (
    ('Asesor de acciones', 'advisor'), ('Predicción y escenarios', 'prediction'),
    ('Qué se mueve hoy', 'market'), ('Sectores y rotación', 'sectors'),
    ('Oportunidades', 'opportunities'), ('Ficha de valor', 'security'),
    ('Macro y riesgo', 'macro'), ('Validación de señales', 'validation'),
    ('Cartera y watchlist', 'portfolio'), ('Diario de decisiones', 'journal'),
    ('Conexión SnapTrade', 'snaptrade'), ('Estado de los datos', 'data-status'),
    ('Base de mercado compartida', 'shared-data'),
)


def set_sidebar(page, *, opened):
    expect(page.get_by_test_id('stApp')).to_have_attribute('data-test-script-state', 'notRunning', timeout=60000)
    sidebar = page.get_by_test_id('stSidebar')
    target_state = 'true' if opened else 'false'
    for attempt in range(3):
        if sidebar.get_attribute('aria-expanded') == target_state:
            return
        try:
            if opened:
                page.get_by_test_id('stExpandSidebarButton').click(timeout=3000)
            else:
                # On desktop the native collapse control is rendered on hover.
                # A responsive resize can briefly retain that desktop behavior.
                sidebar.hover(timeout=3000)
                page.get_by_test_id('stSidebarCollapseButton').get_by_role('button').click(timeout=3000)
            expect(sidebar).to_have_attribute('aria-expanded', target_state, timeout=3000)
            return
        except (BrowserTimeout, AssertionError):
            if attempt == 2:
                page.screenshot(path=OUTPUT / 'sidebar-state-failure.png')
                raise


def check_all_routes(page, mode):
    checks = []
    for title, slug in ROUTES:
        page.set_viewport_size({'width':1366, 'height':900})
        set_sidebar(page, opened=True)
        page.get_by_test_id('stPageLink-NavLink').filter(has_text=title).click()
        heading = page.get_by_role('heading', name=title, exact=True)
        expect(heading).to_be_visible(timeout=30000)
        expect(page.get_by_test_id('stApp')).to_have_attribute('data-test-script-state', 'notRunning', timeout=60000)
        expect(page.get_by_test_id('stException')).to_have_count(0)
        expect(page.get_by_text('VISTA DE DISEÑO', exact=False)).to_be_visible()
        if slug in ('market', 'sectors', 'macro'):
            expect(page.get_by_test_id('stPlotlyChart').first).to_be_visible()
        if slug == 'opportunities':
            try:
                expect(page.locator('[class*="st-key-panel-opportunity-"]').first).to_be_visible(timeout=30000)
            except AssertionError:
                page.screenshot(path=OUTPUT / f'failure-{slug}-{mode.lower()}.png')
                print(page.locator('.stMain').inner_text()[:10000])
                raise
        if slug == 'snaptrade':
            expect(page.get_by_label('SnapTrade Personal Client ID', exact=True)).to_have_attribute('type', 'password')
            expect(page.get_by_label('SnapTrade Personal Consumer Key', exact=True)).to_have_attribute('type', 'password')
        for width, height in ((1366,900), (1920,1080), (390,844)):
            page.set_viewport_size({'width':width, 'height':height})
            if width < 640:
                set_sidebar(page, opened=False)
            heading.scroll_into_view_if_needed()
            assert not page.evaluate('document.documentElement.scrollWidth > window.innerWidth'), (title, mode, width)
            expect(page.get_by_test_id('stException')).to_have_count(0)
            page.screenshot(path=OUTPUT / f'all-{slug}-{mode.lower()}-{width}.png', animations='disabled')
            checks.append({'page':title, 'theme':mode, 'width':width, 'overflow':False})
    page.set_viewport_size({'width':1366, 'height':900})
    set_sidebar(page, opened=True)
    page.get_by_test_id('stPageLink-NavLink').filter(has_text='Asesor de acciones').click()
    expect(page.get_by_role('heading', name='Asesor de acciones', exact=True)).to_be_visible()
    return checks


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with (OUTPUT / 'server.log').open('w', encoding='utf-8') as log:
        server = subprocess.Popen([sys.executable, '-m', 'streamlit', 'run',
            'scripts/preview_design.py', '--server.port=8517', '--server.address=127.0.0.1',
            '--server.headless=true'], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        results = []
        route_results = []
        try:
            for _ in range(60):
                try:
                    with urlopen(URL + '/_stcore/health', timeout=1) as response:
                        if response.status == 200:
                            break
                except OSError:
                    time.sleep(.5)
            else:
                raise RuntimeError('Preview server unavailable')
            with sync_playwright() as pw:
                browser = pw.chromium.launch()
                context = browser.new_context(viewport={'width':1366, 'height':900}, reduced_motion='reduce')
                page = context.new_page()
                page.goto(URL)
                expect(page.get_by_role('heading', name='Asesor de acciones', exact=True)).to_be_visible(timeout=60000)
                expect(page.get_by_text('VISTA DE DISEÑO', exact=False)).to_be_visible()
                page.screenshot(path=OUTPUT / 'initial.png', full_page=True)
                for mode in ('Dark', 'Light'):
                    # Real native theme switch, not injected CSS/localStorage.
                    page.get_by_test_id('stMainMenuButton').click()
                    page.screenshot(path=OUTPUT / 'native-theme-menu.png')
                    page.get_by_test_id(f'stMainMenuItem-theme-{mode}').click()
                    page.get_by_role('heading', name='Asesor de acciones', exact=True).wait_for()
                    expected_color = 'rgb(16, 19, 30)' if mode == 'Dark' else 'rgb(245, 246, 251)'
                    expect(page.get_by_test_id('stApp')).to_have_css('background-color', expected_color)
                    # The theme must survive a full browser reload.
                    page.reload()
                    expect(page.get_by_role('heading', name='Asesor de acciones', exact=True)).to_be_visible(timeout=60000)
                    expect(page.get_by_test_id('stApp')).to_have_css('background-color', expected_color)
                    for width, height in ((1366,900), (1920,1080), (390,844)):
                        page.set_viewport_size({'width':width, 'height':height})
                        if width < 640 and page.get_by_test_id('stSidebarCollapseButton').is_visible():
                            try:
                                page.get_by_test_id('stSidebarCollapseButton').get_by_role('button').click(timeout=2000)
                            except BrowserTimeout:
                                # Streamlit can collapse automatically during
                                # resize while the old control still exists.
                                expect(page.get_by_test_id('stExpandSidebarButton')).to_be_visible()
                        cards = page.locator('[class*="st-key-holding-card-"]')
                        expect(cards).to_have_count(3)
                        expect(page.get_by_test_id('stException')).to_have_count(0)
                        expect(cards.first.get_by_role('heading', name='AAPL', exact=True)).to_be_visible()
                        expect(cards.first.get_by_text('Mantener en vigilancia', exact=True)).to_be_visible()
                        # No horizontal page overflow at any tested viewport.
                        overflow = page.evaluate('document.documentElement.scrollWidth > window.innerWidth')
                        assert not overflow, (mode, width, 'horizontal overflow')
                        boxes = [card.bounding_box() for card in cards.all()]
                        assert all(box and box['width'] >= 270 for box in boxes), boxes
                        page.screenshot(path=OUTPUT / f'advisor-{mode.lower()}-{width}.png', full_page=True)
                        cards.first.scroll_into_view_if_needed()
                        if width == 1366:
                            # Native Plotly hover reads the supplied date/price;
                            # no synthetic sessions or portfolio P&L labels.
                            chart = cards.first.get_by_test_id('stPlotlyChart')
                            chart.scroll_into_view_if_needed()
                            box = chart.bounding_box()
                            assert box is not None
                            page.mouse.move(box['x'] + box['width'] / 2, box['y'] + box['height'] / 2)
                            expect(chart.locator('.hoverlayer')).to_contain_text('Precio ajustado')
                            page.screenshot(path=OUTPUT / f'advisor-{mode.lower()}-tooltip.png')
                            page.mouse.move(0, 0)
                            cards.first.scroll_into_view_if_needed()
                        page.screenshot(path=OUTPUT / f'advisor-{mode.lower()}-{width}-cards.png')
                        page.get_by_role('heading', name='Asesor de acciones', exact=True).scroll_into_view_if_needed()
                        results.append({'theme':mode, 'width':width, 'height':height, 'cards':len(boxes), 'overflow':overflow})
                    page.set_viewport_size({'width':1366, 'height':900})
                    if page.get_by_test_id('stExpandSidebarButton').is_visible():
                        try:
                            page.get_by_test_id('stExpandSidebarButton').click(timeout=2000)
                        except BrowserTimeout:
                            # Conversely, desktop resize can reopen the sidebar
                            # before the old expand button receives the click.
                            expect(page.get_by_test_id('stPageLink-NavLink').filter(has_text='Predicción y escenarios')).to_be_visible()
                    # Charts and tables on another route must use the same theme.
                    page.get_by_test_id('stPageLink-NavLink').filter(has_text='Asesor de acciones').focus()
                    page.keyboard.press('Tab')
                    prediction_link = page.get_by_test_id('stPageLink-NavLink').filter(has_text='Predicción y escenarios')
                    expect(prediction_link).to_be_focused()
                    expect(prediction_link).to_have_css('outline-width', '2px')
                    page.get_by_test_id('stPageLink-NavLink').filter(has_text='Predicción y escenarios').click()
                    expect(page.get_by_role('heading', name='Predicción y escenarios', exact=True)).to_be_visible()
                    expect(page.get_by_test_id('stException')).to_have_count(0)
                    page.screenshot(path=OUTPUT / f'prediction-{mode.lower()}.png', full_page=True)
                    page.get_by_test_id('stPageLink-NavLink').filter(has_text='Cartera y watchlist').click()
                    expect(page.get_by_role('heading', name='Cartera y watchlist', exact=True)).to_be_visible()
                    expect(page.get_by_test_id('stException')).to_have_count(0)
                    expect(page.get_by_test_id('stDataFrame').first).to_be_visible()
                    page.screenshot(path=OUTPUT / f'portfolio-{mode.lower()}.png', full_page=True)
                    page.get_by_test_id('stPageLink-NavLink').filter(has_text='Asesor de acciones').click()
                    expect(page.get_by_role('heading', name='Asesor de acciones', exact=True)).to_be_visible()
                    route_results.extend(check_all_routes(page, mode))
                browser.close()
            (OUTPUT / 'checks.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
            (OUTPUT / 'routes.json').write_text(json.dumps(route_results, indent=2), encoding='utf-8')
            print(json.dumps(results))
            print(f'{len(route_results)} route/theme/viewport checks passed')
        finally:
            server.terminate()
            server.wait(timeout=20)


if __name__ == '__main__':
    main()
