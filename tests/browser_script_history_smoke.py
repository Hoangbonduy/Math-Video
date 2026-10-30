"""Real browser checks for local history, downloads, same-idea regeneration and reload."""
from pathlib import Path
import os
import subprocess
import sys
from tempfile import TemporaryDirectory
from playwright.sync_api import sync_playwright

root=Path(__file__).resolve().parents[1]
fixture=subprocess.Popen([sys.argv[1],'-m','tests.idea_browser_fixture'],cwd=root,stdout=subprocess.PIPE,text=True)
try:
    url=fixture.stdout.readline().strip()
    assert url.startswith('http://127.0.0.1:')
    with sync_playwright() as playwright, TemporaryDirectory() as downloads:
        browser=playwright.chromium.launch(headless=True,args=['--no-sandbox'],
            executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'))
        page=browser.new_page(viewport={'width':1440,'height':1000},accept_downloads=True)
        errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
        page.goto(url)
        page.get_by_label('Chủ đề / câu hỏi mở đầu').fill('Vì sao có ngày và đêm?')
        page.get_by_role('button',name='Tạo kịch bản từ ý tưởng →',exact=True).click()
        page.get_by_role('button',name='Duyệt kịch bản và tạo thiết kế cảnh →',exact=True).wait_for(timeout=30000)
        old=page.request.get(url+'/api/script-history?version=v001').json()
        assert old['created_at'] and old['markdown']
        page.reload()
        page.get_by_role('button',name='Mở kịch bản hiện tại',exact=True).wait_for()
        assert page.locator('#script-review .script-scene').count()==0
        page.get_by_role('link',name='↺ Lịch sử kịch bản').click()
        page.get_by_role('heading',name='Lịch sử kịch bản',exact=True).wait_for()
        assert page.locator('.script-history-item').count()==1
        assert 'GMT+7' in page.locator('.script-history-item time').inner_text()
        page.get_by_role('button',name='Xem kịch bản',exact=True).click()
        page.get_by_label('Kịch bản đầy đủ',exact=True).wait_for()
        assert page.get_by_label('Kịch bản đầy đủ',exact=True).text_content()==old['markdown']
        assert page.get_by_role('button',name='Duyệt kịch bản và tạo thiết kế cảnh →',exact=True).count()==0
        for name,ext in [('Tải kịch bản .md','md'),('Tải dữ liệu .json','json')]:
            with page.expect_download() as result:
                page.get_by_role('link',name=name,exact=True).click()
            path=Path(downloads)/('script.'+ext); result.value.save_as(path)
            assert path.stat().st_size>20
            if ext=='md': assert path.read_text()==old['markdown']
        page.get_by_role('button',name='Mở kịch bản hiện tại để duyệt →',exact=True).click()
        page.get_by_role('button',name='Tạo kịch bản mới từ ý tưởng →',exact=True).click()
        page.wait_for_function("() => document.querySelector('#script-review').textContent.includes('v002')")
        history=page.request.get(url+'/api/script-history').json()
        assert [item['version'] for item in history['items']]==['v002','v001']
        saved=page.request.get(url+'/api/script-history?version=v001').json()
        assert saved['markdown']==old['markdown'] and saved['created_at']==old['created_at'] and saved['archived']
        page.get_by_role('button',name='Ý tưởng mới',exact=True).click()
        page.get_by_label('Chủ đề / câu hỏi mở đầu').wait_for()
        assert page.locator('#script-review .script-scene').count()==0
        page.get_by_label('Chủ đề / câu hỏi mở đầu').fill('Vì sao có mùa?')
        page.get_by_role('button',name='Tạo kịch bản mới từ ý tưởng →',exact=True).click()
        page.wait_for_function("() => document.querySelector('#script-review').textContent.includes('v003')")
        page.get_by_role('link',name='↺ Lịch sử kịch bản').click()
        page.wait_for_function("() => document.querySelectorAll('.script-history-item').length === 3")
        old_card=page.locator('.script-history-item').last
        open_old=old_card.locator('button.primary')
        if not open_old.is_disabled():
            open_old.click()
        page.get_by_label('Kịch bản đầy đủ',exact=True).wait_for()
        assert page.get_by_label('Kịch bản đầy đủ',exact=True).text_content()==old['markdown']
        assert page.get_by_role('button',name='Mở kịch bản hiện tại để duyệt →',exact=True).count()==0
        page.get_by_role('searchbox',name='Tìm kịch bản trong lịch sử').fill('v002')
        assert page.locator('.script-history-item').count()==1
        page.get_by_role('searchbox',name='Tìm kịch bản trong lịch sử').fill('')
        assert page.locator('.script-history-item').count()==3
        output=root/'data/history_validation';output.mkdir(exist_ok=True)
        for width,name in [(1440,'history_desktop.png'),(390,'history_mobile.png')]:
            page.set_viewport_size({'width':width,'height':1000})
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),width
            page.screenshot(path=str(output/name),full_page=True)
        page.reload()
        page.wait_for_function("() => document.querySelectorAll('.script-history-item').length === 3")
        old_card=page.locator('.script-history-item').last
        page.once('dialog',lambda dialog:dialog.dismiss())
        old_card.get_by_role('button',name='Xóa kịch bản v001',exact=True).click()
        assert page.locator('.script-history-item').count()==3
        page.once('dialog',lambda dialog:dialog.accept())
        old_card.get_by_role('button',name='Xóa kịch bản v001',exact=True).click()
        page.wait_for_function("() => document.querySelectorAll('.script-history-item').length === 2 && !document.querySelector('.danger-button:disabled')")
        assert page.request.get(url+'/api/script-history?version=v001').status==404
        assert page.request.get(url+'/media?path='+old['json_path']).status==403
        current_card=page.locator('.script-history-item').first
        def confirm_current(dialog):
            assert 'đang sử dụng' in dialog.message and 'bản dựng' in dialog.message
            dialog.accept()
        page.once('dialog',confirm_current)
        current_card.get_by_role('button',name='Xóa kịch bản v003',exact=True).click()
        page.wait_for_function("() => document.querySelectorAll('.script-history-item').length === 1 && !document.querySelector('.danger-button:disabled')")
        state=page.request.get(url+'/api/state').json()['project']
        assert state['stage']=='script' and state['status']=='ready' and not state['scene_ids']
        page.reload()
        page.wait_for_function("() => document.querySelectorAll('.script-history-item').length === 1")
        assert 'v002' in page.locator('.script-history-item').inner_text()
        output=root/'data/history_validation'
        for width,name in [(1440,'history_delete_desktop.png'),(390,'history_delete_mobile.png')]:
            page.set_viewport_size({'width':width,'height':1000})
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),width
            page.screenshot(path=str(output/name),full_page=True)
        assert not errors,errors
        browser.close()
    print('PASS: script history, downloads, new versions, search, delete cancellation, archived/current deletion, reload persistence, protected downloads, desktop/mobile, no JS errors')
finally:
    fixture.terminate()
    try:fixture.communicate(timeout=10)
    except subprocess.TimeoutExpired:fixture.kill();fixture.communicate()
