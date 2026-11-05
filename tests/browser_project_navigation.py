"""Offline browser checks for saving and returning to the project picker."""
from pathlib import Path
import subprocess
import sys
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = '''
from pathlib import Path
from tempfile import TemporaryDirectory
import sys
from src.shared.project_launcher import ProjectLauncher
from src.shared.review_server import ReviewServer
with TemporaryDirectory() as directory:
    if sys.argv[1] == 'launcher':
        server = ProjectLauncher(0, Path(directory))
    else:
        server = ReviewServer(Path(directory) / 'draft', 0)
    print(server.origin, flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
'''

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True, args=['--no-sandbox'])
    try:
        for mode in ('launcher', 'standalone'):
            fixture = subprocess.Popen([sys.argv[1], '-c', FIXTURE, mode], cwd=ROOT, stdout=subprocess.PIPE, text=True)
            try:
                origin = fixture.stdout.readline().strip()
                assert origin.startswith('http://127.0.0.1:')
                page = browser.new_page(viewport={'width':1280,'height':900})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(origin)
                if mode == 'launcher':
                    page.get_by_label('Tên project mới').fill('Project ban đầu')
                    page.get_by_role('button', name='Tạo project', exact=True).click()
                topic = page.get_by_label('Chủ đề / câu hỏi mở đầu')
                topic.wait_for()
                project_url = page.url
                assert page.locator('#idea-reference').count() == 0
                assert page.get_by_text('Tài liệu / video tham khảo', exact=True).count() == 0
                topic.fill('Ý tưởng được giữ lại')
                page.get_by_label('Ý tưởng kịch bản').fill('Nội dung bản nháp')
                # Failed saves must leave the user with their inputs on the form.
                page.route('**/api/idea-draft', lambda route: route.fulfill(status=409, json={'error':'Không lưu được bản nháp'}))
                page.get_by_role('button', name='Ý tưởng mới', exact=True).click()
                page.get_by_text('Không lưu được bản nháp', exact=True).wait_for()
                assert page.url == project_url
                assert topic.input_value() == 'Ý tưởng được giữ lại'
                page.unroute('**/api/idea-draft')
                page.get_by_role('button', name='Ý tưởng mới', exact=True).click()
                page.get_by_role('heading', name='Project của bạn', exact=True).wait_for()
                home_url = page.url
                # Reopen in a clean context to verify the server persisted the draft.
                clean = browser.new_context()
                other = clean.new_page()
                other.goto(project_url)
                other.get_by_label('Chủ đề / câu hỏi mở đầu').wait_for()
                assert other.get_by_label('Chủ đề / câu hỏi mở đầu').input_value() == 'Ý tưởng được giữ lại'
                assert other.get_by_label('Ý tưởng kịch bản').input_value() == 'Nội dung bản nháp'
                other.get_by_label('Ý tưởng kịch bản').fill('Bản nháp trước khi tạo mới')
                other.get_by_role('button', name='Ý tưởng mới', exact=True).click()
                other.get_by_role('heading', name='Project của bạn', exact=True).wait_for()
                assert other.url == home_url
                other.get_by_label('Tên project mới').fill('Project thứ hai')
                other.get_by_role('button', name='Tạo project', exact=True).click()
                other.get_by_label('Chủ đề / câu hỏi mở đầu').wait_for()
                assert other.get_by_label('Chủ đề / câu hỏi mở đầu').input_value() == 'Project thứ hai'
                assert other.url != project_url
                other.goto(project_url)
                other.get_by_label('Ý tưởng kịch bản').wait_for()
                assert other.get_by_label('Ý tưởng kịch bản').input_value() == 'Bản nháp trước khi tạo mới'
                for width in (390, 1280):
                    other.set_viewport_size({'width':width,'height':900})
                    assert other.evaluate('document.documentElement.scrollWidth <= innerWidth')
                assert not errors, errors
                clean.close()
                page.close()
                print('PASS:', mode, 'save failure, save and return, new idea, isolated projects, persisted drafts, reference removal, responsive form')
            finally:
                fixture.terminate()
                try:
                    fixture.communicate(timeout=10)
                except subprocess.TimeoutExpired:
                    fixture.kill()
                    fixture.communicate()
    finally:
        browser.close()
