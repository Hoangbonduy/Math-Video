"""UI regression from starting a scene to real Manim playback and a failed job."""
import os,subprocess,sys
from pathlib import Path
from playwright.sync_api import sync_playwright
root=Path(__file__).resolve().parents[1]
out=root.parent/'evidence';out.mkdir(exist_ok=True)
fixture=subprocess.Popen([sys.argv[1],'-m','tests.progress_browser_fixture'],cwd=root,stdout=subprocess.PIPE,text=True)
try:
    url=fixture.stdout.readline().strip();assert url.startswith('http://127.0.0.1:'),url
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,args=['--no-sandbox'],executable_path='/home/hoang/.cache/ms-playwright/chromium-1243/chrome-linux64/chrome')
        page=browser.new_page(viewport={'width':1440,'height':1000})
        errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(url+'/#design')
        page.get_by_text('Chưa chạy tạo cảnh Manim',exact=True).wait_for()
        page.get_by_role('button',name='Tạo / tiếp tục cảnh bằng Manim',exact=True).click()
        page.wait_for_function("()=>document.querySelector('#scene-progress[aria-busy=true] .spinner')")
        page.wait_for_function("()=>document.querySelector('#project-status').textContent==='Đang thực hiện'")
        assert page.locator('#project-status').inner_text()=='Đang thực hiện'
        assert 'AI:' in page.locator('#scene-progress').inner_text()
        assert page.locator('#controls button').first.is_disabled()
        page.screenshot(path=str(out/'progress-working.png'),full_page=True)
        page.get_by_text('Đang render Manim',exact=True).first.wait_for(timeout=45000)
        assert page.locator('#scene-progress[aria-busy=true] .spinner').count()==1
        page.screenshot(path=str(out/'progress-rendering.png'),full_page=True)
        page.wait_for_function("()=>document.querySelector('#viewer video')?.readyState>=2",timeout=45000)
        assert page.locator('#scene-progress .spinner').count()==0
        assert 'Video Manim đã sẵn sàng' in page.locator('#scene-progress').inner_text()
        assert page.locator('#viewer video').evaluate('(v)=>v.duration')>10
        page.screenshot(path=str(out/'progress-success.png'),full_page=True)
        page.locator('#scenes button').nth(1).click()
        page.get_by_role('button',name='Tạo / tiếp tục cảnh bằng Manim',exact=True).click()
        page.wait_for_function("()=>document.querySelector('#scene-progress.error')?.textContent.includes('BUDGET_EXCEEDED')",timeout=20000)
        assert page.locator('#scene-progress .spinner').count()==0
        assert page.locator('#scene-progress').get_attribute('aria-busy')=='false'
        assert page.locator('#controls button').first.is_enabled()
        page.set_viewport_size({'width':390,'height':1000})
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        page.screenshot(path=str(out/'progress-failure-mobile.png'),full_page=True)
        assert not errors,errors
        browser.close()
    print('PASS: running/rendering spinner, model, disabled duplicate action, real Manim playback, stopped error without spinner, mobile layout; no live AI')
finally:
    fixture.terminate()
    try:fixture.communicate(timeout=10)
    except subprocess.TimeoutExpired:fixture.kill();fixture.communicate()
