"""V3 browser acceptance: real HTTP/workflow/Manim; deterministic external providers.

Run with Playwright on PYTHONPATH and Chromium dependencies installed.
"""
import copy,json,os,tempfile,threading,time
from pathlib import Path
from unittest.mock import patch
from playwright.sync_api import sync_playwright
from src.shared import workflow as wf
from src.shared import _workflow_store as store
from src.shared.review_server import ReviewServer
from src.scene_design import scene_generation as sg
from tests.test_scene_v2_workflow import approved_project
from tests.test_scene_code_pipeline import QualifiedPlanRouter,SOURCE,visual_plan
from tests.scene_v2_support import VisionRouter


class BrowserRouter(QualifiedPlanRouter):
    fixed=False
    def complete(self,payload):
        time.sleep(.15)
        response=super().complete(payload)
        context=json.loads(payload['messages'][1]['content'])
        sid=context.get('scene',{}).get('scene_id')
        value=json.loads(response['choices'][0]['message']['content'])
        if 'beats' in value:value['scene_id']=sid
        if 'source' in value:
            value['source']=SOURCE
            if sid=='scene_2' and not self.fixed:value['source']=SOURCE.replace("'Chuyển động', 28","'Chuyển động', 12")
        response['choices'][0]['message']['content']=json.dumps(value,ensure_ascii=False)
        return response


def main():
    output=Path('artifacts/scene-validation');output.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='scene-browser-') as folder:
        root=approved_project(Path(folder),count=2,narration=visual_plan()["narration"]);router=BrowserRouter();vision=VisionRouter()
        def context(root,p,r):return wf._latest(p,'script',None),store.read_json(root/'input.json'),{'scene_1':3,'scene_2':3}
        with patch('src.shared.review_server.load_local_env'),patch.object(sg,'CLIENT_FACTORY',return_value=router), \
             patch.object(sg,'_context',side_effect=context),patch.object(sg,'DESIGN_PIPELINE_VERSION',3), \
             patch('src.scene_design.scene_vision.CLIENT_FACTORY',return_value=vision), \
             patch('src.scene_design.scene_vision.review_model',return_value=vision.model), \
             patch('src.shared.openrouter_client.OpenRouterClient._send',side_effect=AssertionError('No live provider calls')):
            server=ReviewServer(root,0);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            errors=[]
            try:
                with sync_playwright() as pw:
                    browser=pw.chromium.launch(headless=True,args=['--no-sandbox'],executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE','/home/hoang/.cache/ms-playwright/chromium-1243/chrome-linux64/chrome'))
                    page=browser.new_page(viewport={'width':1440,'height':1000})
                    page.on('pageerror',lambda error:errors.append(str(error)))
                    page.goto(server.origin+'/#design')
                    page.get_by_role('button',name='Tạo cảnh này',exact=True).click()
                    page.wait_for_function("()=>document.querySelector('#viewer video')?.readyState>=2",timeout=90000)
                    video=page.locator('#viewer video')
                    assert video.evaluate('(v)=>[v.videoWidth,v.videoHeight]')==[360,640]
                    page.locator('.scene-agent-activity summary').first.click()
                    page.get_by_text('Đã trả kết quả',exact=False).first.wait_for()
                    assert not page.locator('.agent-source').count()
                    assert 'Use at most 16' not in page.locator('#scene-progress').inner_text()
                    page.screenshot(path=str(output/'v3-success.png'),full_page=True)
                    page.locator('#scenes button').nth(1).click()
                    page.get_by_role('button',name='Tạo cảnh này',exact=True).click()
                    page.get_by_text('Đã render · Chưa đạt kiểm tra',exact=True).wait_for(timeout=90000)
                    assert 'Rendered font is below' in page.locator('#scene-progress').inner_text()
                    assert 'Measured font_size=12' in page.locator('#scene-progress').inner_text()
                    page.screenshot(path=str(output/'v3-layout-failure.png'),full_page=True)
                    # A fresh user attempt must use the new provider output, not the cached failure.
                    router.fixed=True
                    page.get_by_role('button',name='Tạo cảnh này',exact=True).click()
                    page.wait_for_function("()=>document.querySelector('#viewer video')?.readyState>=2",timeout=90000)
                    page.wait_for_function("()=>!document.querySelector('#scene-progress.error')",timeout=30000)
                    assert 'Rendered font is below' not in page.locator('#scene-progress').inner_text()
                    jobs=sg._jobs(root)
                    assert len(jobs)==3 and jobs[1]['status']=='failed' and jobs[2]['status']=='awaiting_review'
                    page.reload()
                    page.wait_for_function("()=>document.querySelector('#viewer video')?.readyState>=2",timeout=30000)
                    page.set_viewport_size({'width':390,'height':900})
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                    page.screenshot(path=str(output/'v3-retry-mobile.png'),full_page=True)
                    state=wf.get_status(root)
                    assert all(r['status']=='pending' for r in state['review_history'] if r['stage']=='scene_design')
                    assert not errors,errors
                    browser.close()
                    (output/'browser.json').write_text(json.dumps(dict(passed=True,scenarios=['qualified APIs → real portrait playback','layout failure after successful render','fresh attempt bypasses failed cache','reload preserves latest preview','mobile width','no auto approval','no JS errors'],jobs=[dict(scene=j['input']['scene_id'],status=j['status']) for j in jobs]),ensure_ascii=False,indent=2))
                    print('PASS: V3 browser workflow, real playback, layout failure, retry, reload and mobile; no live AI')
            except BaseException:
                print([(j.get('status'),j.get('last_error')) for j in sg._jobs(root)],flush=True)
                raise
            finally:
                for control in server.task_controls.values():control.pause()
                server.shutdown();server.server_close();thread.join()


if __name__=='__main__':main()
