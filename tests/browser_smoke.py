import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory() as directory:
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        process = subprocess.Popen([sys.executable, '-m', 'fleet_reliability', '--port', str(port), '--state', str(Path(directory) / 'state.sqlite3')], cwd=ROOT)
        base = f'http://127.0.0.1:{port}'
        try:
            for attempt in range(100):
                try:
                    with urlopen(base + '/api/catalog', timeout=1) as response:
                        catalog = json.load(response)
                    break
                except OSError:
                    time.sleep(.05)
            else:
                raise RuntimeError('服务启动超时')
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={'width': 1280, 'height': 720})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(base)
                page.wait_for_function("document.querySelector('#status').textContent === '分析已更新'")
                assert page.locator('#results tbody tr').count() > 0
                # 风险区：状态卡片、借用证据、备件范围与设备明细都已渲染
                page.wait_for_selector('#machine-list tbody tr')
                assert page.locator('#risk-statuses .card').count() >= 5
                assert page.locator('#borrowing tbody tr').count() > 0
                assert page.locator('#spares tbody tr').count() == 2
                # 打开设备页：超龄设备必须显示明确状态而不是编造精确风险
                page.locator('#machine-list tbody button', has_text='m001').first.click()
                page.wait_for_selector('#machine-detail h3')
                detail_text = page.locator('#machine-detail').inner_text()
                assert 'm001' in detail_text
                assert '不提供点估计' in detail_text or '上界' in detail_text
                # 调整预测窗口后风险区刷新
                page.locator('#filters input[name="horizon_hours"]').fill('360')
                page.get_by_role('button', name='更新分析', exact=True).click()
                page.wait_for_function("document.querySelector('#status').textContent === '分析已更新'")
                assert page.locator('#machine-list tbody tr').count() > 0
                page.locator('#filters select').select_option(catalog['groups'][0])
                page.get_by_role('button', name='更新分析', exact=True).click()
                page.wait_for_function("document.querySelector('#status').textContent === '分析已更新'")
                page.locator('#save input').fill('浏览器业务核对')
                page.get_by_role('button', name='保存当前分析').click()
                page.wait_for_selector('#history button')
                page.reload()
                page.wait_for_selector('#history button')
                page.locator('#history button').click()
                page.wait_for_function("document.querySelector('#status').textContent.includes('浏览器业务核对')")
                assert page.locator('#filters select').input_value() == catalog['groups'][0]
                page.get_by_role('button', name='查看资料').click()
                page.wait_for_selector('#raw tbody tr')
                assert not errors, errors
                print(json.dumps({'结果': '通过', '页面错误': errors, '保存范围': page.locator('#filters select').input_value()}, ensure_ascii=False))
                browser.close()
        finally:
            process.terminate()
            process.wait(timeout=10)


if __name__ == '__main__':
    main()
