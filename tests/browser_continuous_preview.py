from pathlib import Path
import json, os, subprocess, sys
from playwright.sync_api import sync_playwright
base=Path(__file__).resolve().parents[1]
output=base/'evidence';output.mkdir(exist_ok=True)
env=dict(os.environ,PYTHONPATH=str(base),PATH='/home/hoang/.conda/envs/video/bin:/usr/bin:/bin')
fixture=subprocess.Popen(['/home/hoang/.conda/envs/video/bin/python','-m','tests.continuous_browser_fixture'],cwd=base,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
try:
    url=fixture.stdout.readline().strip();assert url.startswith('http://127.0.0.1:'),url
    os.environ['LD_LIBRARY_PATH']='/tmp/ai-video-ui-check/libs/extracted/usr/lib/x86_64-linux-gnu'
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,executable_path='/home/hoang/.cache/ms-playwright/chromium-1243/chrome-linux64/chrome',args=['--no-sandbox'])
        page=browser.new_page(viewport=dict(width=1440,height=1050));errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.goto(url+'/#preview')
        video=page.locator('#continuous-video');video.wait_for(timeout=45000)
        page.wait_for_function("() => document.querySelector('#continuous-video').videoWidth > 0")
        assert page.locator('#preview-captions').inner_text().startswith('Xin chào')
        assert page.locator('#preview-chapters button').count()==0  # Semantic nav uses a class.
        chapters=page.locator('.preview-chapters button');assert chapters.count()==2
        chapters.nth(1).click();page.wait_for_timeout(300)
        assert video.evaluate('(v)=>v.currentTime')>=.79
        assert 'Tiếp theo' in page.locator('#preview-captions').inner_text()
        page.get_by_role('button',name='Ẩn phụ đề',exact=True).click();assert page.locator('#preview-captions').is_hidden()
        page.get_by_role('button',name='Hiện phụ đề',exact=True).click();assert page.locator('#preview-captions').is_visible()
        page.get_by_role('button',name='Thêm tiếng',exact=True).click()
        page.wait_for_function("() => document.querySelector('.preview-heading p').textContent.includes('Có lời đọc')",timeout=45000)
        page.wait_for_function("() => document.querySelector('#continuous-video').videoWidth > 0")
        video.evaluate('(v)=>{v.currentTime=.3;}');page.wait_for_timeout(200)
        assert 'Xin chào' in page.locator('#preview-captions').inner_text()
        video.evaluate('(v)=>v.play()');page.wait_for_timeout(600)
        assert video.evaluate('(v)=>v.currentTime')>.7
        video.evaluate('(v)=>v.pause()')
        previous=video.evaluate('(v)=>v.currentTime')
        page.get_by_role('button',name='Duyệt từng cảnh',exact=True).click()
        page.wait_for_function("() => document.querySelector('#continuous-video').readyState >= 1")
        assert abs(video.evaluate('(v)=>v.currentTime')-previous)<.15
        assert page.locator('#review-layout').is_visible()
        page.get_by_role('button',name='Thu gọn duyệt từng cảnh',exact=True).click()
        assert page.locator('#review-layout').is_hidden()
        page.get_by_role('button',name='Xem bản không tiếng',exact=True).click()
        page.wait_for_function("() => document.querySelector('.preview-heading p').textContent.includes('Chưa có tiếng')")
        page.get_by_role('button',name='Xem bản có tiếng',exact=True).click()
        page.wait_for_function("() => document.querySelector('#continuous-video').readyState >= 2")
        video.evaluate('(v)=>{v.currentTime=.3;}');page.wait_for_timeout(300)
        page.screenshot(path=str(output/'preview-desktop.png'),full_page=True)
        for name in ('Tải video MP4','Tải phụ đề SRT'):
            link=page.get_by_role('link',name=name,exact=True);assert page.request.get(url+link.get_attribute('href')).ok
        page.set_viewport_size(dict(width=390,height=844));page.wait_for_timeout(200)
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),page.evaluate('[document.documentElement.scrollWidth,innerWidth]')
        page.screenshot(path=str(output/'preview-mobile.png'),full_page=True)
        page.get_by_role('button',name='Cấu hình giọng đọc',exact=True).click()
        assert page.locator('#voice-settings-panel').get_attribute('open') is not None
        assert not errors,errors
        empty=subprocess.Popen(['/home/hoang/.conda/envs/video/bin/python','-m','tests.continuous_browser_fixture'],cwd=base,env=dict(env,EMPTY_PREVIEW='1'),stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            empty_url=empty.stdout.readline().strip()
            page.goto(empty_url+'/#preview')
            page.get_by_role('heading',name='Chưa đủ cảnh để ghép',exact=True).wait_for()
            assert page.get_by_role('button',name='Thêm tiếng',exact=True).is_disabled()
            assert page.get_by_role('button',name='Ghép bản xem trước',exact=True).is_disabled()
            assert 'Duyệt kịch bản' in page.locator('#continuous-preview').inner_text()
            assert not errors,errors
        finally:
            empty.terminate();empty.communicate(timeout=10)
        browser.close()
    report=dict(passed=True,checks=['auto local assembly','chapter seeking','captions below video','caption toggle','add voice with fake provider','audio playback','playback position survives refresh','per-scene review toggle','silent/audio switch','downloads','mobile width','no JavaScript errors'])
    (output/'browser-verification.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report))
except Exception:
    if 'page' in locals():
        print('NOTICE:',page.locator('#notice').inner_text(),flush=True)
        print('ERRORS:',errors,flush=True)
        page.screenshot(path=str(output/'browser-failure.png'),full_page=True)
    raise
finally:
    fixture.terminate()
    try:fixture.communicate(timeout=10)
    except subprocess.TimeoutExpired:fixture.kill();fixture.communicate()
