"""Exercise the real graph UI; requires installed Playwright/Chromium and Pillow.

Run against a disposable API store with two existing skills and a valid edge:
python scripts/test_graph_browser.py --base-url URL --output EMPTY_DIR
    --skill-id ID --skill-name NAME
No requests are stubbed and no product graph internals are exposed.
"""
import argparse
import json
from io import BytesIO
from pathlib import Path
from PIL import Image
from playwright.sync_api import sync_playwright, expect

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--base-url', required=True)
parser.add_argument('--output', required=True)
parser.add_argument('--skill-id', required=True)
parser.add_argument('--skill-name', required=True)
args = parser.parse_args()
out = Path(args.output)
out.mkdir(parents=True, exist_ok=False)
evidence = {'page_errors': [], 'console_errors': [], 'steps': [], 'api_responses': []}

def node_centres(page):
    # Read only the rendered canvas pixels; do not hook G6 or inject test APIs.
    canvas = page.locator('canvas').first
    picture = Image.open(BytesIO(canvas.screenshot())).convert('RGB')
    pixels = picture.load()
    candidates = {(x, y) for y in range(picture.height) for x in range(picture.width)
                  if (lambda r, g, b: b > r + 45 and b > g + 20 and 50 < g < 230)(*pixels[x, y])}
    # Erode thin blue edges/labels so connected nodes remain separate regions.
    candidates = {(x, y) for x, y in candidates
                  if all((x+dx, y+dy) in candidates for dx,dy in [(-4,0),(4,0),(0,-4),(0,4)])}
    groups = []
    while candidates:
        seed = candidates.pop()
        queue = [seed]
        group = [seed]
        while queue:
            x, y = queue.pop()
            for adjacent in [(x-1,y),(x+1,y),(x,y-1),(x,y+1)]:
                if adjacent in candidates:
                    candidates.remove(adjacent);queue.append(adjacent);group.append(adjacent)
        if len(group) > 100:
            groups.append(group)
    assert len(groups) == 2, 'Expected two rendered skill nodes, got ' + str(len(groups))
    box = canvas.bounding_box()
    return [(box['x'] + sum(x for x,y in group)/len(group), box['y'] + sum(y for x,y in group)/len(group)) for group in groups]

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    page = browser.new_page(viewport={'width': 1440, 'height': 1000})
    page.on('pageerror', lambda error: evidence['page_errors'].append({'message': str(error), 'stack': error.stack}))
    page.on('console', lambda msg: evidence['console_errors'].append(msg.text) if msg.type == 'error' else None)
    page.on('response', lambda r: evidence['api_responses'].append({'url': r.url, 'status': r.status}) if '/api/v1/' in r.url else None)
    try:
        page.goto(args.base_url + '/graph?preselect=' + args.skill_id, wait_until='networkidle')
        expect(page.get_by_role('heading', name='Skill Knowledge Graph')).to_be_visible()
        expect(page.get_by_text(args.skill_name, exact=True).first).to_be_visible()
        expect(page.locator('canvas').first).to_be_visible()
        page.wait_for_timeout(750)
        evidence['identity'] = {'url': page.url, 'title': page.title()}
        evidence['steps'].append('initial graph and URL preselection rendered')
        page.screenshot(path=str(out/'01-initial.png'), full_page=True)
        assert not evidence['page_errors'], 'Initial graph errors: ' + json.dumps(evidence['page_errors'])
        for label in ['Provenance', 'Version impact', 'Skill-only']:
            page.get_by_text(label, exact=True).first.click()
            expect(page.locator('canvas').first).to_be_visible()
            page.wait_for_timeout(700)
            evidence['steps'].append('switch ' + label)
        for label in ['Zoom in', 'Zoom out', 'Fit view']:
            page.get_by_role('button', name=label, exact=True).click()
            page.wait_for_timeout(350)
            evidence['steps'].append(label)
        page.get_by_role('button', name='Layout settings', exact=True).click()
        page.get_by_role('button', name='Readable', exact=True).click()
        page.get_by_role('button', name='Apply', exact=True).click()
        expect(page.get_by_text('Readable preset', exact=True)).to_be_visible()
        page.wait_for_timeout(700)
        page.screenshot(path=str(out/'02-readable.png'), full_page=True)
        evidence['steps'].append('existing Readable preset applied')
        page.wait_for_timeout(600)
        centres = node_centres(page)
        page.mouse.move(*centres[0]);page.wait_for_timeout(150)
        page.mouse.click(*centres[0], delay=100)
        expect(page.get_by_text('Kind', exact=True)).to_be_visible()
        expect(page.get_by_text('Version', exact=True)).to_be_visible()
        page.screenshot(path=str(out/'02-node-details.png'), full_page=True)
        evidence['steps'].append('real canvas node click opens details')
        page.mouse.move(1100, 100)
        page.wait_for_timeout(1500)
        centres = node_centres(page)
        midpoint = tuple((a+b)/2 for a,b in zip(centres[0], centres[1]))
        canvas = page.locator('canvas').first
        box = canvas.bounding_box()
        picture = Image.open(BytesIO(canvas.screenshot())).convert('RGB')
        cx, cy = int(midpoint[0]-box['x']), int(midpoint[1]-box['y'])
        edge_pixels = [(x,y) for x in range(cx-2,cx+3) for y in range(cy-12,cy+13)
                       if (lambda r,g,b: (b > r+45 and b > g+20)
                           or (r > b+60 and g > b+25 and r > g+20))(*picture.getpixel((x,y)))]
        assert edge_pixels, 'No rendered edge at the midpoint'
        edge_y = min(edge_pixels, key=lambda point: abs(point[1]-cy))[1]
        midpoint = (box['x']+cx, box['y']+edge_y)
        evidence['edge_click_geometry'] = {'centres':centres, 'click':midpoint}
        page.screenshot(path=str(out/'02-before-edge-click.png'), full_page=True)
        page.mouse.move(*midpoint);page.wait_for_timeout(150)
        page.mouse.click(*midpoint, delay=100)
        expect(page.get_by_text('Edge Details', exact=True)).to_be_visible()
        expect(page.get_by_text('Source', exact=True)).to_be_visible()
        expect(page.get_by_text('Target', exact=True)).to_be_visible()
        page.screenshot(path=str(out/'02-edge-details.png'), full_page=True)
        evidence['steps'].append('real canvas edge click opens details')
        page.get_by_role('button', name='Reload Skill graph').click()
        expect(page.locator('canvas').first).to_be_visible()
        page.wait_for_timeout(700)
        evidence['steps'].append('reload graph')
        page.goto(args.base_url + '/graph?preselect=' + args.skill_id, wait_until='networkidle')
        expect(page.get_by_text(args.skill_name, exact=True).first).to_be_visible()
        evidence['steps'].append('preselection survives reload')
        page.set_viewport_size({'width': 1024, 'height': 800})
        page.wait_for_timeout(500)
        page.screenshot(path=str(out/'03-resize.png'), full_page=True)
        evidence['steps'].append('resize renders graph')
        page.goto(args.base_url + '/harness', wait_until='networkidle')
        expect(page.get_by_text('Current M2 BenchFlow runs', exact=True)).to_be_visible()
        refresh = page.get_by_role('button', name='Refresh current runs')
        expect(refresh).to_be_enabled()
        with page.expect_response(lambda r: '/harness/benchflow/current-runs' in r.url) as response:
            refresh.click()
        assert response.value.status == 200, 'Current evidence refresh request failed'
        expect(refresh).to_be_enabled()
        page.screenshot(path=str(out/'04-harness.png'), full_page=True)
        evidence['steps'].append('Harness first load and refresh succeeded')
        page.goto(args.base_url + '/graph', wait_until='networkidle')
        page.get_by_text('Provenance', exact=True).first.click()
        page.get_by_role('button', name='Zoom in', exact=True).click()
        page.goto(args.base_url + '/wiki', wait_until='networkidle')
        evidence['steps'].append('leave graph during transform')
        assert page.locator('vite-error-overlay').count() == 0, 'Vite error overlay present'
        assert not evidence['page_errors'], 'Page errors: ' + json.dumps(evidence['page_errors'])
        evidence['known_deprecation_warnings'] = [msg for msg in evidence['console_errors'] if msg.startswith('Warning: [antd:') and 'deprecated' in msg]
        unexpected_console = [msg for msg in evidence['console_errors'] if msg not in evidence['known_deprecation_warnings']]
        assert not unexpected_console, 'Console errors: ' + json.dumps(unexpected_console)
        evidence['passed'] = True
    except Exception as exc:
        evidence['passed'] = False
        evidence['failure'] = str(exc)
        page.screenshot(path=str(out/'failure.png'), full_page=True)
    finally:
        evidence['final_body'] = page.locator('body').inner_text()
        (out/'result.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
        browser.close()
print(json.dumps({'passed': evidence['passed'], 'page_errors': len(evidence['page_errors']), 'steps': evidence['steps'], 'failure': evidence.get('failure')}))
raise SystemExit(0 if evidence['passed'] else 1)
