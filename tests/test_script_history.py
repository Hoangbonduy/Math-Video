"""Regression checks for persistent, timestamped script history and new runs."""
import json
import os
from pathlib import Path
import threading
import time
import urllib.error
import urllib.request
from unittest.mock import patch

from src.shared import workflow as wf
from src.shared.ai_config import MODEL
from src.idea.service import apply_idea
from src.script_history.service import history, detail, delete
from src.shared.review_server import ReviewServer
from tests.test_idea import IdeaCase, IDEA


class ScriptHistoryTests(IdeaCase):
    def setUp(self):
        super().setUp()
        patch.dict(os.environ, AI_BACKEND='openrouter', OPENROUTER_MODEL=MODEL).start()

    def generate(self, idea=IDEA):
        apply_idea(self.project, idea, force_new=True)
        return wf.run_workflow(self.project, ai=True)

    def test_delete_archived_script_removes_content_preserves_current_and_usage(self):
        first = self.generate()['artifacts'][-1]
        self.generate()
        current = detail(self.project, 'v002')
        before = wf.get_status(self.project)
        calls = len(self.router.requests)
        job = self.project / 'generation' / first['job_id']
        ledger = (job / 'job.json').read_bytes()
        deleted = delete(self.project, 'v001', first['sha256'])
        self.assertFalse(deleted['reset_to_script'])
        self.assertEqual([i['version'] for i in history(self.project)['items']], ['v002'])
        self.assertEqual(detail(self.project, 'v002'), current)
        self.assertFalse(Path(first['path']).exists())
        self.assertFalse((job / 'script.md').exists())
        self.assertFalse((job / 'script.json').exists())
        self.assertEqual((job / 'job.json').read_bytes(), ledger)
        self.assertEqual(wf.get_status(self.project)['generation'], before['generation'])
        self.assertEqual(len(self.router.requests), calls)
        self.error('SCRIPT_NOT_FOUND', detail, self.project, 'v001')
        delete(self.project, 'v001', first['sha256'])
        self.assertEqual([a['version'] for a in wf.get_status(self.project)['artifacts']], ['v002'])

    def test_delete_current_resets_workflow_preserves_render_and_never_reuses_version(self):
        from src.shared.review import record_review
        self.generate()
        current = self.generate()['artifacts'][-1]
        record_review(self.project, 'script', 'v002', 'approve', read_seconds=40)
        design = self.project / 'saved-design.json'
        design.write_text('{"saved":true}')
        wf.submit_artifact(self.project, 'scene_design', 'v001', design, scene_id='scene_01')
        before = wf.get_status(self.project)
        rendered = next(a for a in before['artifacts'] if a['stage'] == 'scene_design')
        result = delete(self.project, 'v002', current['sha256'])
        self.assertTrue(result['reset_to_script'])
        state = wf.get_status(self.project)
        self.assertEqual((state['stage'], state['status'], state['scene_ids']), ('script', 'ready', []))
        self.assertFalse(state['integrity_issues'])
        self.assertTrue(Path(rendered['path']).is_file())
        self.assertFalse(history(self.project)['items'][0]['current'])
        self.assertEqual(self.generate()['artifacts'][-1]['version'], 'v003')

    def test_delete_validates_identity_and_symlinks_before_state_changes(self):
        first = self.generate()['artifacts'][-1]
        self.generate()
        before = self.snapshots()
        self.error('SCRIPT_NOT_FOUND', delete, self.project, '../../project.json', first['sha256'])
        self.error('ARTIFACT_CHANGED', delete, self.project, 'v001', None)
        self.error('ARTIFACT_CHANGED', delete, self.project, 'v001', '0' * 64)
        self.assertEqual(self.snapshots(), before)
        path = Path(first['path'])
        path.unlink()
        path.symlink_to(self.project / 'project.json')
        self.error('INVALID_INPUT', delete, self.project, 'v001', first['sha256'])
        self.assertEqual(self.snapshots(), before)

    def test_delete_missing_file_and_retry_interrupted_cleanup(self):
        first = self.generate()['artifacts'][-1]
        self.generate()
        Path(first['path']).unlink()
        original_unlink = Path.unlink
        def fail_script_json(path, *args, **kwargs):
            if path.name == 'script.json':
                raise OSError('interrupted cleanup')
            return original_unlink(path, *args, **kwargs)
        with patch.object(Path, 'unlink', fail_script_json):
            self.error('IO_ERROR', delete, self.project, 'v001', first['sha256'])
        self.assertEqual([i['version'] for i in history(self.project)['items']], ['v002'])
        delete(self.project, 'v001', first['sha256'])
        self.assertFalse((self.project / first['bundle']['script.json']['path']).exists())
        self.assertFalse(wf.get_status(self.project)['integrity_issues'])

    def test_http_delete_requires_session_blocks_busy_and_persists(self):
        first = self.generate()['artifacts'][-1]
        self.generate()
        saved = detail(self.project, 'v001')
        server = ReviewServer(self.project, 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def post(headers=None):
            payload = dict(request_id='delete-v001', version='v001', sha256=first['sha256'])
            request = urllib.request.Request(server.origin + '/api/script-delete', json.dumps(payload).encode(),
                headers=headers or {'Origin':server.origin, 'X-Review-Token':server.token, 'Content-Type':'application/json'})
            return urllib.request.urlopen(request, timeout=10)
        try:
            for headers in ({'Origin':'http://evil.invalid', 'X-Review-Token':server.token}, {'Origin':server.origin}):
                with self.assertRaises(urllib.error.HTTPError) as error:
                    post(headers)
                self.assertEqual(error.exception.code, 403)
            server.jobs['busy'] = dict(state='working')
            with self.assertRaises(urllib.error.HTTPError) as error:
                post()
            self.assertEqual(error.exception.code, 409)
            self.assertEqual(len(history(self.project)['items']), 2)
            server.jobs.clear()
            with post() as response:
                self.assertTrue(json.load(response)['ok'])
            with post() as response:
                self.assertTrue(json.load(response)['ok'])
            for path in ('/api/script-history?version=v001', '/media?path=' + saved['json_path']):
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(server.origin + path, timeout=10)
                self.assertIn(error.exception.code, (403, 404))
        finally:
            server.shutdown(); server.server_close(); thread.join()
        server = ReviewServer(self.project, 0)
        try:
            self.assertEqual([i['version'] for i in history(server.root)['items']], ['v002'])
        finally:
            server.server_close()

    def test_same_idea_new_run_keeps_both_immutable_versions_and_times(self):
        first = self.generate()['artifacts'][-1]
        saved = detail(self.project, 'v001')
        calls = len(self.router.requests)
        second = self.generate()['artifacts'][-1]
        self.assertEqual(second['version'], 'v002')
        self.assertGreater(len(self.router.requests), calls)
        self.assertNotEqual(first['job_id'], second['job_id'])
        items = history(self.project)['items']
        self.assertEqual([i['version'] for i in items], ['v002', 'v001'])
        self.assertTrue(items[0]['current'])
        self.assertTrue(items[1]['archived'])
        self.assertEqual(items[1]['created_at'], first['created_at'])
        self.assertEqual(detail(self.project, 'v001')['markdown'], saved['markdown'])
        self.assertEqual(detail(self.project, 'v001')['sha256'], saved['sha256'])
        self.assertTrue(detail(self.project, 'v002')['json_path'])

    def test_opening_history_and_unknown_version_never_generate_or_mutate(self):
        self.generate()
        before = self.snapshots()
        calls = len(self.router.requests)
        self.assertEqual(len(history(self.project)['items']), 1)
        self.assertTrue(detail(self.project, 'v001')['markdown'])
        for version in ('v999', '../project.json', '/etc/passwd'):
            self.error('SCRIPT_NOT_FOUND', detail, self.project, version)
        self.assertEqual(self.snapshots(), before)
        self.assertEqual(len(self.router.requests), calls)

    def test_legacy_markdown_script_is_visible_without_ai_bundle(self):
        wf.run_workflow(self.project, mock=True)
        item = detail(self.project, 'v001')
        self.assertTrue(item['markdown'])
        self.assertFalse(item['script'])
        self.assertIsNone(item['json_path'])
        self.assertTrue(item['created_at'])
        self.assertFalse(self.router.requests)

    def test_missing_archived_file_does_not_hide_other_versions(self):
        first = self.generate()['artifacts'][-1]
        self.generate()
        from pathlib import Path
        Path(first['path']).unlink()
        items = history(self.project)['items']
        self.assertTrue(items[0]['available'])
        self.assertFalse(items[1]['available'])
        self.error('ARTIFACT_CHANGED', detail, self.project, 'v001')

    def test_http_history_survives_server_restart_and_start_replay(self):
        server = None
        thread = None
        def start():
            nonlocal server, thread
            server = ReviewServer(self.project, 0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
        def stop():
            server.shutdown(); server.server_close(); thread.join()
        start()
        try:
            def get(path):
                with urllib.request.urlopen(server.origin + path, timeout=10) as response:
                    return json.load(response)
            def post(request_id):
                data=json.dumps(dict(request_id=request_id,idea=IDEA)).encode()
                request=urllib.request.Request(server.origin+'/api/start',data=data,
                    headers={'Origin':server.origin,'X-Review-Token':server.token,'Content-Type':'application/json'})
                with urllib.request.urlopen(request,timeout=10) as response:
                    return json.load(response)
            def idle():
                deadline=time.monotonic()+15
                while time.monotonic()<deadline:
                    state=get('/api/state')
                    if not any(t['state'] in ('queued','working') for t in state['tasks'].values()):
                        self.assertFalse([t['error'] for t in state['tasks'].values() if t.get('error')])
                        return state
                    time.sleep(.02)
                self.fail('Generation did not finish')
            self.assertIn('script_history', get('/api/state')['capabilities'])
            post('new-run-1'); idle()
            calls=len(self.router.requests)
            self.assertTrue(post('new-run-1')['replayed'])
            idle()
            self.assertEqual(len(self.router.requests),calls)
            post('new-run-2'); idle()
            before=get('/api/script-history')
            saved=get('/api/script-history?version=v001')
            self.assertEqual(len(before['items']),2)
            self.assertTrue(saved['markdown'])
            self.assertTrue(saved['archived'])
            with urllib.request.urlopen(server.origin+'/media?path='+saved['json_path']) as response:
                self.assertTrue(json.load(response)['script'])
            with self.assertRaises(urllib.error.HTTPError) as error:
                get('/api/script-history?version=../../project.json')
            self.assertEqual(error.exception.code,404)
            with self.assertRaises(urllib.error.HTTPError) as error:
                get('/media?path=project.json')
            self.assertEqual(error.exception.code,403)
            stop(); start()
            calls=len(self.router.requests)
            self.assertEqual(get('/api/script-history'),before)
            self.assertEqual(get('/api/script-history?version=v001'),saved)
            self.assertEqual(len(self.router.requests),calls)
        finally:
            stop()
