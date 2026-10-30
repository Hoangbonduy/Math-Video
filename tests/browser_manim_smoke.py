"""Browser evidence: actual Manim MP4, code/log downloads and exact approval promotion."""
from pathlib import Path
import json
import os
import subprocess
import sys
import time
from tempfile import TemporaryDirectory
from playwright.sync_api import sync_playwright

root=Path(__file__).resolve().parents[1]
fixture=subprocess.Popen([sys.argv[1],'-m','tests.manim_browser_fixture'],cwd=root,stdout=subprocess.PIPE,text=True)
try:
    url=fixture.stdout.readline().strip()
    assert url.startswith('http://127.0.0.1:'),url
    with sync_playwright() as pw,TemporaryDirectory() as downloads:
        browser=pw.chromium.launch(headless=True,args=['--no-sandbox'],executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'))
        page=browser.new_page(viewport={'width':1440,'height':1000},accept_downloads=True)
        errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(url+'/#design')
        page.get_by_role('heading',name='Thiết kế cảnh bằng Manim',exact=True).wait_for()
        page.wait_for_function("() => document.querySelector('#viewer video')?.readyState >= 2")
        assert 'Manim 0.19.1' in page.locator('#manim-status').inner_text()
        assert 'Video thiết kế · Manim Cairo' in page.locator('.viewer-kind').inner_text()
        video=page.locator('#viewer video')
        assert video.evaluate('(v)=>v.duration')>14
        video.evaluate('(v)=>{v.currentTime=6;}')
        page.wait_for_function("() => { const v=document.querySelector('#viewer video'); return v && !v.seeking && v.readyState>=2 && Math.abs(v.currentTime-6)<0.2; }")
        with page.expect_download() as result:
            page.get_by_role('link',name='Tải mã Manim .py',exact=True).click()
        path=Path(downloads)/'scene.py';result.value.save_as(path)
        assert 'GeneratedScene(Scene)' in path.read_text() and 'DESIGN =' in path.read_text()
        response=page.request.get(url+page.get_by_role('link',name='Xem log render',exact=True).get_attribute('href'))
        assert response.ok and 'SUCCESS' in response.text()
        before=page.request.get(url+'/api/state').json()['project']
        hashes={a['scene_id']:a['manim_render']['sha256'] for a in before['artifacts'] if a['stage']=='scene_design'}
        output=root/'data/manim_agent_validation';output.mkdir(exist_ok=True)
        for width,name in [(1440,'design_desktop.png'),(390,'design_mobile.png')]:
            page.set_viewport_size({'width':width,'height':1000})
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            page.screenshot(path=str(output/name),full_page=True)
        page.set_viewport_size({'width':1440,'height':1000})
        for index in range(2):
            page.locator('#scenes button').nth(index).click()
            page.get_by_role('button',name='Duyệt thiết kế',exact=True).click()
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            after=page.request.get(url+'/api/state').json()
            project=after['project']
            if project['stage']=='scene_clip' and project['status']=='awaiting_review' and not any(t['state'] in ('queued','working') for t in after['tasks'].values()):
                break
            time.sleep(.1)
        else:
            raise AssertionError('Manim promotion did not finish: '+json.dumps(after['tasks']))
        assert not any(t.get('error') for t in after['tasks'].values())
        clips=[a for a in after['project']['artifacts'] if a['stage']=='scene_clip']
        assert len(clips)==2, json.dumps(dict(stage=after['project']['stage'],status=after['project']['status'],scenes=after['project']['scene_ids'],artifacts=[(a['stage'],a['scene_id'],a['version']) for a in after['project']['artifacts']],tasks=after['tasks']),ensure_ascii=False)
        for clip in clips:
            assert clip['reused_design_render'] and clip['sha256']==hashes[clip['scene_id']]
        assert not errors,errors
        browser.close()
    print('PASS: actual Manim video decodes and seeks, correct runtime, source and successful log downloads, desktop/mobile, design approval reuses identical video without AI, no JS errors')
finally:
    fixture.terminate()
    try:fixture.communicate(timeout=10)
    except subprocess.TimeoutExpired:fixture.kill();fixture.communicate()
