"""Run with a Python containing Playwright; model/render are faked in the fixture.
Usage: python tests/browser_review_smoke.py /path/to/video/bin/python
"""
from pathlib import Path
import os
import subprocess
import sys
import tempfile
from playwright.sync_api import sync_playwright

root=Path(__file__).resolve().parents[1]
fixture=subprocess.Popen([sys.argv[1],'-m','tests.review_browser_fixture'],cwd=root,stdout=subprocess.PIPE,text=True)
try:
    url=fixture.stdout.readline().strip()
    assert url.startswith('http://127.0.0.1:')
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,args=['--no-sandbox'],executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'))
        page=browser.new_page(viewport={'width':1280,'height':900})
        errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.goto(url+'/#design')
        page.get_by_role('heading',name='Thiết kế cảnh bằng Manim').wait_for()
        page.get_by_role('tab',name='Hình minh họa',exact=True).click()
        page.get_by_text('Hình minh họa & chuyển động',exact=True).wait_for()
        page.get_by_role('link',name='01 Ý tưởng').click()
        page.get_by_label('Sau khi xem, người xem hiểu được gì?').fill('Hiểu tốc độ thay đổi')
        page.get_by_role('button',name='Lưu bản nháp',exact=True).click()
        page.reload()
        page.get_by_label('Sau khi xem, người xem hiểu được gì?').wait_for()
        assert page.get_by_label('Sau khi xem, người xem hiểu được gì?').input_value()=='Hiểu tốc độ thay đổi'
        page.get_by_role('link',name='03 Xem bản dựng').click()
        page.locator('#scene-strip button').nth(1).click()
        assert page.locator('#controls').inner_text().startswith('scene_2')
        page.get_by_label('Góp ý',exact=True).fill('Sửa lỗi hiển thị đúng thiết kế đã duyệt')
        page.locator('#scene-strip button').nth(0).click()
        page.locator('#scene-strip button').nth(1).click()
        assert page.get_by_label('Góp ý',exact=True).input_value()=='Sửa lỗi hiển thị đúng thiết kế đã duyệt'
        page.get_by_role('button',name='Gửi góp ý',exact=True).click()
        page.get_by_text('Vòng sửa · Chờ duyệt clip mới',exact=True).wait_for(timeout=30000)
        page.get_by_role('button',name='Duyệt clip mới',exact=True).click()
        page.get_by_text('Vòng sửa · Hoàn tất',exact=True).wait_for(timeout=30000)
        video=page.locator('#viewer video')
        video.evaluate('(v)=>{v.currentTime=.5;}')
        assert video.evaluate('(v)=>v.currentTime')>=.4
        page.get_by_label('Lịch sử phiên bản').select_option('scene_clip:v001')
        page.get_by_text('Bạn đang xem bản cũ. Chọn bản mới nhất để gửi góp ý hoặc duyệt.',exact=True).wait_for()
        assert page.get_by_role('button',name='Duyệt bản dựng',exact=True).count()==0
        assert page.get_by_label('Lịch sử phiên bản').input_value()=='scene_clip:v001'
        for screen in ('idea','design','preview'):
            page.goto(url+'/#'+screen)
            page.get_by_role('heading',level=1).wait_for()
            page.set_viewport_size({'width':390,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),screen
        assert not errors,errors
        page.set_viewport_size({'width':1280,'height':900})
        page.screenshot(path=str(Path(tempfile.gettempdir())/'math-video-review-smoke.png'),full_page=True)
        browser.close()
    print('PASS: three screens, draft persistence, scene selection, feedback persistence, revision, approval, old version, video seek, mobile overflow, no JS errors')
finally:
    fixture.terminate()
    try: fixture.communicate(timeout=10)
    except subprocess.TimeoutExpired: fixture.kill(); fixture.communicate()
