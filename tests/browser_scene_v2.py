import os,json,sys,subprocess
from pathlib import Path
sys.path.insert(0,'/tmp/scene-v2-ui')
from playwright.sync_api import sync_playwright
base=Path(__file__).resolve().parents[1];evidence=base/'evidence';evidence.mkdir(exist_ok=True)
env=dict(os.environ,PYTHONPATH=str(base),PATH='/home/hoang/.conda/envs/video/bin:/usr/bin:/bin')
fixture=subprocess.Popen([sys.executable,'-m','tests.scene_v2_browser_fixture'],cwd=base,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
try:
    url=fixture.stdout.readline().strip();assert url.startswith('http://127.0.0.1:'),url
    os.environ['LD_LIBRARY_PATH']='/tmp/scene-v2-ui-libs/extracted/usr/lib/x86_64-linux-gnu'
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path='/home/hoang/.cache/ms-playwright/chromium-1243/chrome-linux64/chrome',headless=True,args=['--no-sandbox'])
        page=browser.new_page(viewport=dict(width=1440,height=1080));errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
        page.goto(url+'/#design');page.get_by_text('Storyboard và kết quả kiểm tra cảnh',exact=True).wait_for()
        page.get_by_text('Storyboard và kết quả kiểm tra cảnh',exact=True).click()
        page.get_by_text('B nằm trên đồ thị trong suốt chuyển động.',exact=True).wait_for()
        assert page.locator('.design-quality img').count()>0
        page.wait_for_function('() => [...document.querySelectorAll(".design-quality img")].every(i=>i.complete&&i.naturalWidth>0)')
        assert page.get_by_role('button',name='Duyệt thiết kế',exact=True).is_enabled()
        for name in ('Tải storyboard JSON','Tải báo cáo kiểm tra'):
            link=page.get_by_role('link',name=name,exact=True);assert page.request.get(url+link.get_attribute('href')).ok
        video=page.locator('#viewer video');page.wait_for_function('() => document.querySelector("#viewer video").readyState>=2')
        video.evaluate('(v)=>v.play()');page.wait_for_timeout(500);assert video.evaluate('(v)=>v.currentTime')>.2;video.evaluate('(v)=>v.pause()')
        page.screenshot(path=str(evidence/'design-v2-desktop.png'),full_page=True)
        page.get_by_role('tab',name='Đồ thị & liên kết',exact=True).click()
        assert '5 quan hệ phụ thuộc' in page.locator('#editor').inner_text()
        page.set_viewport_size(dict(width=390,height=844))
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        page.screenshot(path=str(evidence/'design-v2-mobile.png'),full_page=True)
        with page.expect_response(lambda r:r.url.endswith('/api/approve')) as approved:
            page.get_by_role('button',name='Duyệt thiết kế',exact=True).click()
        assert approved.value.ok,approved.value.text()
        page.wait_for_timeout(1500)
        state=page.request.get(url+'/api/state').json()['project']
        assert state['stage']=='scene_clip';assert any(a['stage']=='scene_clip' for a in state['artifacts'])
        assert not errors,errors
        browser.close()
    report=dict(passed=True,checks=['storyboard and review report','actual frame images served','relation inspector','mobile width','human approval','approved video reused','no JavaScript errors'],paid_calls=0)
    (evidence/'browser-report.json').write_text(json.dumps(report,indent=2));print(json.dumps(report))
finally:
    fixture.terminate()
    try:out,err=fixture.communicate(timeout=10)
    except subprocess.TimeoutExpired:fixture.kill();out,err=fixture.communicate()
    if fixture.returncode not in (0,-15):print(err[-3000:])
