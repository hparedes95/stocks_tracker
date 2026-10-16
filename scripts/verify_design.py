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

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'reports/design-qa'
URL = 'http://127.0.0.1:8517'


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with (OUTPUT / 'server.log').open('w', encoding='utf-8') as log:
        server = subprocess.Popen([sys.executable, '-m', 'streamlit', 'run',
            'scripts/preview_design.py', '--server.port=8517', '--server.address=127.0.0.1',
            '--server.headless=true'], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        results = []
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
                for mode in ('Light', 'Dark'):
                    # Real native theme switch, not injected CSS/localStorage.
                    page.get_by_test_id('stMainMenuButton').click()
                    page.screenshot(path=OUTPUT / 'native-theme-menu.png')
                    page.get_by_test_id(f'stMainMenuItem-theme-{mode}').click()
                    page.get_by_role('heading', name='Asesor de acciones', exact=True).wait_for()
                    expected_color = 'rgb(16, 19, 30)' if mode == 'Dark' else 'rgb(245, 246, 251)'
                    expect(page.get_by_test_id('stAppViewContainer')).to_have_css('background-color', expected_color)
                    # The theme must survive a full browser reload.
                    page.reload()
                    expect(page.get_by_role('heading', name='Asesor de acciones', exact=True)).to_be_visible(timeout=60000)
                    expect(page.get_by_test_id('stAppViewContainer')).to_have_css('background-color', expected_color)
                    for width, height in ((1366,900), (1920,1080), (390,844)):
                        page.set_viewport_size({'width':width, 'height':height})
                        if width < 640 and page.get_by_test_id('stSidebarCollapseButton').is_visible():
                            page.get_by_test_id('stSidebarCollapseButton').get_by_role('button').click()
                        cards = page.locator('[class*="st-key-holding-card-"]')
                        expect(cards).to_have_count(3)
                        expect(page.get_by_test_id('stException')).to_have_count(0)
                        # No horizontal page overflow at any tested viewport.
                        overflow = page.evaluate('document.documentElement.scrollWidth > window.innerWidth')
                        assert not overflow, (mode, width, 'horizontal overflow')
                        boxes = [card.bounding_box() for card in cards.all()]
                        assert all(box and box['width'] >= 270 for box in boxes), boxes
                        page.screenshot(path=OUTPUT / f'advisor-{mode.lower()}-{width}.png', full_page=True)
                        results.append({'theme':mode, 'width':width, 'height':height, 'cards':len(boxes), 'overflow':overflow})
                    page.set_viewport_size({'width':1366, 'height':900})
                    if page.get_by_test_id('stExpandSidebarButton').is_visible():
                        page.get_by_test_id('stExpandSidebarButton').click()
                    # Charts and tables on another route must use the same theme.
                    page.get_by_role('link', name='Predicción y escenarios', exact=True).click()
                    expect(page.get_by_role('heading', name='Predicción y escenarios', exact=True)).to_be_visible()
                    expect(page.get_by_test_id('stException')).to_have_count(0)
                    page.screenshot(path=OUTPUT / f'prediction-{mode.lower()}.png', full_page=True)
                    page.get_by_role('link', name='Asesor de acciones', exact=True).click()
                    expect(page.get_by_role('heading', name='Asesor de acciones', exact=True)).to_be_visible()
                browser.close()
            (OUTPUT / 'checks.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
            print(json.dumps(results))
        finally:
            server.terminate()
            server.wait(timeout=20)


if __name__ == '__main__':
    main()
