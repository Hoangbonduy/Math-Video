"""Browser C7 acceptance. Paid calls are replaced inside the fixture process."""
from pathlib import Path
import subprocess
import sys
from playwright.sync_api import sync_playwright
root=Path(__file__).resolve().parents[1];output=root/'data/checkpoint_7_validation';output.mkdir(exist_ok=True)
fixture=subprocess.Popen([sys.argv[1],'-m','tests.av_browser_fixture'],cwd=root,stdout=subprocess.PIPE,text=True)
try:
    url=fixture.stdout.readline().strip();assert url.startswith('http://127.0.0.1:')
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,args=['--no-sandbox']);page=browser.new_page(viewport={'width':1440,'height':1050});errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
        page.goto(url+'/#preview');page.get_by_role('heading',name='Kiểm tra trước khi xuất').wait_for()
        page.locator('#viewer video').wait_for();page.locator('#viewer video').evaluate('(v)=>v.load()');page.wait_for_timeout(700)
        assert page.locator('#viewer video').evaluate('(v)=>v.videoWidth')==1920
        assert page.get_by_role('button',name='Ghép bản xem trước cuối',exact=True).count()==0
        page.screenshot(path=str(output/'av-scene-desktop.png'),full_page=True)
        for index in range(2):
            page.locator('#scene-strip button').nth(index).click();page.get_by_role('button',name='Duyệt bản dựng',exact=True).click();page.get_by_text('Đã duyệt đúng phiên bản đang xem.',exact=True).wait_for()
            page.wait_for_timeout(300)
        page.get_by_role('button',name='Ghép bản xem trước cuối',exact=True).click()
        try: page.get_by_role('heading',name='Duyệt toàn bộ video · v001',exact=True).wait_for(timeout=120000)
        except Exception:
            print('Notice:',page.locator('#notice').inner_text(),flush=True)
            print('Tasks:',page.request.get(url+'/api/state').json()['tasks'],flush=True)
            print('JS errors:',errors,flush=True)
            page.screenshot(path=str(output/'failure.png'),full_page=True)
            raise
        page.locator('#final-review video').evaluate('(v)=>{v.currentTime=1;}')
        page.screenshot(path=str(output/'final-review-desktop.png'),full_page=True)
        page.get_by_role('button',name='Duyệt video cuối',exact=True).click()
        page.get_by_text('Đã duyệt đúng phiên bản đang xem.',exact=True).wait_for();page.wait_for_timeout(300)
        for name,filename in [('Tải MP4','final.mp4'),('Tải âm thanh WAV','narration.wav'),('Tải phụ đề SRT','final.srt'),('Danh sách phiên bản đã ghép','final_manifest.json')]:
            link=page.get_by_role('link',name=name,exact=True);response=page.request.get(url+link.get_attribute('href'));assert response.ok; (output/filename).write_bytes(response.body())
        for screen in ('design','preview'):
            page.goto(url+'/#'+screen);page.get_by_role('heading',level=1).wait_for();page.set_viewport_size({'width':390,'height':844});assert page.evaluate('document.documentElement.scrollWidth<=innerWidth');page.screenshot(path=str(output/(screen+'-mobile.png')),full_page=True)
        assert not errors,errors;browser.close()
    print('PASS: native 1080p AV playback, per-scene approvals, assembly, final approval, four downloads, mobile layout, no JS errors')
finally:
    fixture.terminate()
    try:fixture.communicate(timeout=10)
    except subprocess.TimeoutExpired:fixture.kill();fixture.communicate()
