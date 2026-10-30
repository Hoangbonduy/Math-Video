"""Browser coverage of the actual opening-form -> script -> scene workflow."""
from pathlib import Path
import subprocess
import os
import sys
from playwright.sync_api import sync_playwright

root=Path(__file__).resolve().parents[1]
fixture=subprocess.Popen([sys.argv[1],'-m','tests.idea_browser_fixture'],cwd=root,stdout=subprocess.PIPE,text=True)
try:
    url=fixture.stdout.readline().strip()
    assert url.startswith('http://127.0.0.1:')
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(headless=True,args=['--no-sandbox'],executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'))
        page=browser.new_page(viewport={'width':1280,'height':900})
        errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
        page.goto(url)
        topic=page.get_by_label('Chủ đề / câu hỏi mở đầu')
        topic.wait_for();assert topic.input_value()==''
        def old_server(route):
            response=route.fetch(); data=response.json(); data.pop('api_version',None)
            route.fulfill(response=response,json=data)
        page.route('**/api/state',old_server)
        page.reload()
        page.get_by_text('Ứng dụng vừa được cập nhật. Hãy khởi động lại máy chủ và tải lại trang để dùng chức năng tạo kịch bản.',exact=True).wait_for()
        assert page.get_by_role('button',name='Tạo kịch bản từ ý tưởng →',exact=True).is_disabled()
        page.unroute('**/api/state',old_server)
        page.wait_for_function("() => !document.querySelector('.idea-bottom button').disabled")
        assert page.locator('#notice').inner_text()==''
        page.get_by_role('button',name='Tạo kịch bản từ ý tưởng →',exact=True).click()
        assert page.evaluate('document.activeElement.id')=='idea-topic'
        assert not page.request.get(url+'/api/state').json()['project']['artifacts']
        topic.fill('Vì sao có ngày và đêm?')
        goal=page.get_by_label('Sau khi xem, người xem hiểu được gì?')
        goal.fill('Hiểu sự tự quay của Trái Đất')
        page.get_by_label('Người xem',exact=True).fill('Học sinh lớp 6')
        page.get_by_label('Mô tả phong cách').fill('Nền sáng, quả địa cầu đơn giản')
        page.get_by_label('Độ dài dự kiến').fill('30–45 giây')
        page.get_by_label('Facebook · video dọc').check()
        page.get_by_role('button',name='Lưu bản nháp',exact=True).click()
        page.wait_for_function("() => document.querySelector('#notice').textContent.startsWith('Đã lưu yêu cầu')")
        # Server draft survives a browser context without localStorage.
        other=browser.new_page()
        other.goto(url)
        other.get_by_label('Chủ đề / câu hỏi mở đầu').wait_for()
        assert other.get_by_label('Chủ đề / câu hỏi mở đầu').input_value()=='Vì sao có ngày và đêm?'
        other.close()
        page.get_by_role('button',name='Tạo kịch bản từ ý tưởng →',exact=True).click()
        page.get_by_role('button',name='Duyệt kịch bản và tạo thiết kế cảnh →',exact=True).wait_for(timeout=30000)
        assert goal.input_value()=='Hiểu sự tự quay của Trái Đất'
        page.get_by_label('Góp ý kịch bản').fill('Giải thích đơn giản hơn')
        page.get_by_role('button',name='Yêu cầu sửa kịch bản',exact=True).click()
        page.wait_for_function("() => document.querySelector('#script-review').textContent.includes('v002')")
        read=page.get_by_label('Thời gian bạn đọc thử lời dẫn (giây)')
        read.fill('40')
        page.get_by_role('button',name='Duyệt kịch bản và tạo thiết kế cảnh →',exact=True).click()
        page.get_by_role('heading',name='Thiết kế cảnh bằng Manim').wait_for()
        page.wait_for_function("() => document.querySelectorAll('#scenes button').length===2 && document.querySelector('#viewer video')")
        state=page.request.get(url+'/api/state').json()['project']
        assert state['topic']=='Vì sao có ngày và đêm?'
        assert state['brief']['audience']=='Học sinh lớp 6'
        assert state['stage']=='scene_design'
        for _ in range(2):
            page.get_by_role('button',name='Duyệt thiết kế',exact=True).click()
            if _==0: page.locator('#scenes button').nth(1).click()
        page.wait_for_function("""async () => {
            const data = await (await fetch('/api/state')).json();
            return data.project.stage === 'scene_clip' && data.project.status === 'awaiting_review'
                && !Object.values(data.tasks).some(task => ['queued','working'].includes(task.state));
        }""")
        page.get_by_role('link',name='01 Ý tưởng').click()
        for width in (390,1280):
            page.set_viewport_size({'width':width,'height':900})
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        page.get_by_label('Chủ đề / câu hỏi mở đầu').fill('Vì sao có mùa?')
        with page.expect_response(lambda response: response.url.endswith('/api/start')) as started:
            page.get_by_role('button',name='Tạo kịch bản mới từ ý tưởng →',exact=True).click()
        assert started.value.ok, started.value.text()
        page.wait_for_function("() => document.querySelector('#script-review').textContent.includes('v003')")
        state=page.request.get(url+'/api/state').json()['project']
        assert state['topic']=='Vì sao có mùa?'
        assert all(a.get('invalidated') for a in state['artifacts'] if a['stage']!='script')
        assert not errors,errors
        browser.close()
    print('PASS: old server detection, requires topic, server draft, generation, script revision/approval, designs, format, new idea, responsive layout, no JS errors')
finally:
    fixture.terminate()
    try:fixture.communicate(timeout=10)
    except subprocess.TimeoutExpired:fixture.kill();fixture.communicate()
