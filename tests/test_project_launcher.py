import contextlib
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import uuid

import main
from src.shared import workflow as wf
from src.shared.project_launcher import ProjectLauncher


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / 'projects'
        self.server = ProjectLauncher(0, self.root)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.tmp.cleanup()

    def request(self, path, data=None, origin=True, token=True):
        headers = {}
        if origin:
            headers['Origin'] = self.server.origin
        if token:
            headers['X-Review-Token'] = self.server.token
        payload = None if data is None else json.dumps(data).encode()
        return urlopen(Request(self.server.origin + path, data=payload, headers=headers), timeout=5)

    def test_create_list_open_isolated_projects_and_reopen_after_restart(self):
        with patch('src.shared.review_server.ReviewServer.recover') as recover:
            first = dict(topic='Đạo hàm', request_id=str(uuid.uuid4()))
            second = dict(topic='Hình học', request_id=str(uuid.uuid4()))
            a = json.load(self.request('/api/projects/create', first))['id']
            self.assertEqual(json.load(self.request('/api/projects/create', first))['id'], a)
            b = json.load(self.request('/api/projects/create', second))['id']
            self.assertNotEqual(a, b)
            projects = json.load(self.request('/api/projects'))['projects']
            self.assertEqual({p['topic'] for p in projects}, {'Đạo hàm', 'Hình học'})
            recover.assert_not_called()
            url_a = json.load(self.request('/api/projects/open', dict(id=a)))['url']
            url_b = json.load(self.request('/api/projects/open', dict(id=b)))['url']
            self.assertNotEqual(url_a, url_b)
            self.assertEqual(json.load(self.request('/api/projects/open', dict(id=a)))['url'], url_a)
            self.assertEqual(recover.call_count, 2)
            for url in (url_a, url_b):
                self.assertEqual(urlopen(url, timeout=5).status, 200)
            self.assertEqual(wf.get_status(self.root / a)['idea_draft']['topic'], 'Đạo hàm')
            self.assertEqual(wf.get_status(self.root / b)['idea_draft']['topic'], 'Hình học')
            self.server.shutdown()
            self.server.server_close()
            self.thread.join()
            self.server = ProjectLauncher(0, self.root)
            self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
            self.thread.start()
            self.assertEqual(len(json.load(self.request('/api/projects'))['projects']), 2)
            reopened = json.load(self.request('/api/projects/open', dict(id=a)))['url']
            self.assertEqual(urlopen(reopened, timeout=5).status, 200)

    def test_restarted_session_rejects_old_token_before_create_and_refresh_recovers(self):
        import secrets
        old_token = self.server.token
        self.server.token = secrets.token_urlsafe(32)
        data = dict(topic='Restart recovery', request_id=str(uuid.uuid4()))
        request = Request(self.server.origin + '/api/projects/create',
            data=json.dumps(data).encode(), headers={
                'Origin': self.server.origin, 'X-Review-Token': old_token})
        with self.assertRaises(HTTPError) as exc:
            urlopen(request, timeout=5)
        error = json.load(exc.exception)
        self.assertEqual(error['code'], 'LOCAL_SESSION_EXPIRED')
        self.assertFalse(any(self.root.glob('video_*')))
        response = self.request('/api/projects')
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        session = json.load(response)
        request = Request(self.server.origin + '/api/projects/create',
            data=json.dumps(data).encode(), headers={
                'Origin': self.server.origin, 'X-Review-Token': session['token']})
        created = json.load(urlopen(request, timeout=5))
        self.assertTrue((self.root / created['id'] / 'project.json').is_file())
        self.assertEqual(json.load(self.request('/api/projects/create', data))['id'], created['id'])

    def test_review_returns_to_original_launcher(self):
        with patch('src.shared.review_server.ReviewServer.recover'):
            identifier = json.load(self.request('/api/projects/create', dict(topic='Draft', request_id=str(uuid.uuid4()))))['id']
            url = json.load(self.request('/api/projects/open', dict(id=identifier)))['url']
            response = urlopen(url.split('/#')[0] + '/projects', timeout=5)
            self.assertEqual(response.url.rstrip('/'), self.server.origin)
            self.assertIn('Tên project mới', response.read().decode())
            server = self.server.projects[(self.root / identifier).resolve()][0]
            self.assertIsNone(server.project_launcher)

    def test_direct_review_can_return_to_project_picker(self):
        from src.shared.review_server import ReviewServer
        server = ReviewServer(self.root / 'direct', port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            response = urlopen(server.origin + '/projects', timeout=5)
            self.assertIn('Tên project mới', response.read().decode())
            self.assertEqual(response.url.rstrip('/'), server.projects_url())
            projects = json.load(urlopen(response.url.rstrip('/') + '/api/projects', timeout=5))['projects']
            self.assertEqual([item['id'] for item in projects], ['direct'])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
        self.assertIsNone(server.project_launcher)

    def test_existing_nested_project_and_invalid_requests(self):
        wf.create_project(self.root / 'old' / 'video_moi', 'Existing')
        (self.root / 'bad').mkdir()
        (self.root / 'bad/project.json').write_text('broken')
        projects = json.load(self.request('/api/projects'))['projects']
        self.assertEqual(projects, [dict(id='old/video_moi', topic='Existing')])
        outside = Path(self.tmp.name) / 'outside'
        wf.create_project(outside, 'Outside')
        (self.root / 'link').symlink_to(outside, target_is_directory=True)
        for identifier in ('../outside', str(outside), 'link', '.', 'missing'):
            with self.assertRaises(HTTPError) as exc:
                self.request('/api/projects/open', dict(id=identifier))
            self.assertEqual(exc.exception.code, 409)
        data = dict(topic='Title', request_id=str(uuid.uuid4()))
        for headers in (dict(origin=False), dict(token=False)):
            with self.assertRaises(HTTPError) as exc:
                self.request('/api/projects/create', data, **headers)
            self.assertEqual(exc.exception.code, 403)
        self.assertFalse(any(self.root.glob('video_*')))
        for invalid in (dict(topic='', request_id=str(uuid.uuid4())), dict(topic='Title', request_id='bad'), []):
            with self.assertRaises(HTTPError) as exc:
                self.request('/api/projects/create', invalid)
            self.assertEqual(exc.exception.code, 409)


    def test_delete_removes_only_selected_data_and_closes_open_server(self):
        with patch('src.shared.review_server.ReviewServer.recover'):
            first = json.load(self.request('/api/projects/create', dict(topic='First', request_id=str(uuid.uuid4()))))['id']
            second = json.load(self.request('/api/projects/create', dict(topic='Second', request_id=str(uuid.uuid4()))))['id']
            (self.root / first / 'extra').mkdir()
            (self.root / first / 'extra' / 'video.mp4').write_bytes(b'video')
            url = json.load(self.request('/api/projects/open', dict(id=first)))['url']
            self.assertEqual(urlopen(url, timeout=5).status, 200)
            self.assertEqual(json.load(self.request('/api/projects/delete', dict(id=first))), {'ok': True})
            self.assertFalse((self.root / first).exists())
            self.assertTrue((self.root / second / 'project.json').exists())
            self.assertEqual([p['id'] for p in json.load(self.request('/api/projects'))['projects']], [second])
            with self.assertRaises(Exception):
                urlopen(url, timeout=2)

    def test_delete_rejects_active_jobs_unsafe_paths_and_missing_authorization(self):
        with patch('src.shared.review_server.ReviewServer.recover'):
            first = json.load(self.request('/api/projects/create', dict(topic='First', request_id=str(uuid.uuid4()))))['id']
            root = self.root / first
            self.request('/api/projects/open', dict(id=first)).close()
            server = self.server.projects[root.resolve()][0]
            server.jobs['busy'] = dict(state='working')
            for identifier, headers in [(first, {}), ('../outside', {}), (first, {'origin': False}), (first, {'token': False})]:
                with self.assertRaises(HTTPError):
                    self.request('/api/projects/delete', dict(id=identifier), **headers)
                self.assertTrue((root / 'project.json').exists())
            server.jobs.clear()
            (self.root / 'alias').symlink_to(root, target_is_directory=True)
            with self.assertRaises(HTTPError):
                self.request('/api/projects/delete', dict(id='alias'))
            self.assertTrue(root.exists())
            wf.create_project(root / 'nested', 'Nested')
            with self.assertRaises(HTTPError):
                self.request('/api/projects/delete', dict(id=first))
            self.assertTrue((root / 'nested' / 'project.json').exists())


class LauncherCLITests(unittest.TestCase):
    def test_no_project_argument_opens_picker(self):
        with patch('src.shared.project_launcher.serve_projects') as serve:
            self.assertEqual(main.main(['serve-review', '--port', '9876']), 0)
            serve.assert_called_once_with(9876)

    def test_explicit_project_keeps_existing_entry_point(self):
        with patch('src.shared.review_server.serve_review') as serve:
            self.assertEqual(main.main(['serve-review', '--project-dir', 'saved']), 0)
            serve.assert_called_once_with('saved', 8765)

    def test_other_commands_still_require_an_explicit_project(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main.main(['status']), 1)
        self.assertIn('--project-dir', output.getvalue())


if __name__ == '__main__':
    unittest.main()

