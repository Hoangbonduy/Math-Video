"""Offline browser verification of stop -> notes -> fresh scene request."""
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch
from playwright.sync_api import sync_playwright
from src.shared import workflow as wf
from src.shared import _workflow_store as store
from src.shared.review_server import ReviewServer
from src.shared.task_control import checkpoint
from src.scene_design import scene_generation as sg
from tests.test_scene_v2_workflow import approved_project
from tests.scene_v2_support import DesignRouter

with tempfile.TemporaryDirectory() as folder:
    root=approved_project(Path(folder))
    def context(root,p,reviews):return wf._latest(p,'script',None),store.read_json(root/'input.json'),{'scene_1':3}
    def design(*args):
        while True:checkpoint();time.sleep(.01)
    with patch('src.shared.review_server.load_local_env'),patch.object(sg,'_context',side_effect=context),patch.object(sg,'CLIENT_FACTORY',return_value=DesignRouter()),patch.object(sg,'_design',side_effect=design),patch('src.scene_design.manim_runtime.info',return_value=dict(available=True,renderer='cairo',version='test')):
        server=ReviewServer(root,0);threading.Thread(target=server.serve_forever,daemon=True).start()
        try:
            with sync_playwright() as pw:
                browser=pw.chromium.launch(headless=True,args=['--no-sandbox'],executable_path='/home/hoang/.cache/ms-playwright/chromium-1243/chrome-linux64/chrome')
                page=browser.new_page(viewport={'width':1440,'height':1000});errors=[]
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.goto(server.origin+'/#design')
                page.get_by_role('button',name='Tạo cảnh này',exact=True).click()
                page.get_by_role('button',name='Dừng',exact=True).wait_for()
                page.get_by_role('button',name='Dừng',exact=True).click()
                page.get_by_text('Đã tạm dừng · Chờ ghi chú duyệt',exact=True).wait_for()
                field=page.locator('#feedback-text');field.fill('Giữ nhãn rõ hơn và giảm tốc chuyển động')
                page.wait_for_timeout(2500)
                assert field.input_value()=='Giữ nhãn rõ hơn và giảm tốc chuyển động'
                assert not page.locator('#scene-progress .spinner').count()
                assert page.get_by_role('button',name='Gửi góp ý và tạo cảnh này',exact=True).is_enabled()
                evidence=Path('artifacts/scene-pause.png');evidence.parent.mkdir(exist_ok=True)
                page.screenshot(path=str(evidence),full_page=True)
                page.get_by_role('button',name='Gửi góp ý và tạo cảnh này',exact=True).click()
                page.get_by_role('button',name='Dừng',exact=True).wait_for()
                assert store.read_json(root/'project.json')['failed_scene_feedback']['scene_1']['feedback']=='Giữ nhãn rõ hơn và giảm tốc chuyển động'
                page.get_by_role('button',name='Dừng',exact=True).click()
                page.get_by_text('Đã tạm dừng · Chờ ghi chú duyệt',exact=True).wait_for()
                page.set_viewport_size({'width':390,'height':900})
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                assert not errors,errors
                browser.close()
            print('PASS: Dừng stops generation, opens review notes, preserves typed notes across polling, and submits a fresh scene; mobile layout fits.')
        finally:server.shutdown();server.server_close()
