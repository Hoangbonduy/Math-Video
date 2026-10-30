"""One user action runs the remaining scenes in approved order."""
import json
import threading
import time
import unittest
import urllib.request
from unittest.mock import patch

from src.shared import workflow as wf
from src.shared import _workflow_store as store
from src.shared.review_server import ReviewServer
from src.scene_design import scene_generation as sg
from tests import test_scenes as fixtures


class AutoContinueTests(unittest.TestCase):
    def setUp(self):
        self.flow=fixtures.SceneFlowTests('test_two_topics_different_scene_counts_and_all_gates')
        self.flow.setUp();self.addCleanup(self.flow.doCleanups)
        self.root=self.flow.root
        self.flow.approved(3)

    def test_selected_scene_runs_remaining_order_without_approval_or_duplicates(self):
        state=wf.run_workflow(self.root,ai=True,scene_id='scene_2',auto_continue=True)
        self.assertEqual([a['scene_id'] for a in state['pending_reviews']],['scene_2','scene_3'])
        self.assertEqual(state['stage'],'scene_design')
        self.assertTrue(all(r['status']=='pending' for r in state['review_history'] if r['stage']=='scene_design'))
        jobs=sg._jobs(self.root)
        self.assertEqual([j['input']['scene_id'] for j in jobs],['scene_2','scene_3'])
        calls=len(self.flow.router.requests)
        wf.run_workflow(self.root,ai=True,scene_id='scene_2',auto_continue=True)
        self.assertEqual(len(self.flow.router.requests),calls)
        self.assertEqual(len(sg._jobs(self.root)),2)

    def test_local_scene_failure_is_saved_and_next_scenes_still_run(self):
        original=sg._legacy_design
        order=[]
        def design(root,directory,session,context):
            sid=context['scene']['scene_id'];order.append(sid)
            if sid=='scene_1':raise store.WorkflowError('SCENE_QUALITY_FAILED','first scene quality')
            return original(root,directory,session,context)
        with patch.object(sg,'_design',side_effect=design):
            with self.assertRaisesRegex(store.WorkflowError,'SCENE_GENERATION_FAILED'):
                wf.run_workflow(self.root,ai=True,scene_id='scene_1',scene_trial=True,auto_continue=True)
        self.assertEqual(order,['scene_1','scene_2','scene_3'])
        jobs=sg._jobs(self.root)
        self.assertEqual([j['status'] for j in jobs],['failed','awaiting_review','awaiting_review'])
        self.assertTrue(all(j['input']['max_repairs']==2 for j in jobs))
        self.assertEqual(len(wf.get_status(self.root)['pending_reviews']),2)

    def test_uncertain_provider_result_stops_sequence_without_sending_next_scene(self):
        with patch.object(sg,'_design',side_effect=store.WorkflowError('API_RESULT_UNKNOWN','unresolved request')):
            with self.assertRaisesRegex(store.WorkflowError,'API_RESULT_UNKNOWN'):
                wf.run_workflow(self.root,ai=True,scene_id='scene_1',auto_continue=True)
        self.assertEqual(len(sg._jobs(self.root)),1)

    def test_one_http_continue_starts_sequence_and_duplicate_click_does_not_rerun(self):
        with patch('src.shared.ai_config.scene_trial_enabled',return_value=True):
            server=ReviewServer(self.root,0)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(lambda:(server.shutdown(),server.server_close(),thread.join()))
        data=json.dumps(dict(request_id='auto-sequence-test',scene_id='scene_2',new_attempt=False)).encode()
        def post():
            request=urllib.request.Request(server.origin+'/api/continue',data=data,
                headers={'Origin':server.origin,'X-Review-Token':server.token,'Content-Type':'application/json'})
            with urllib.request.urlopen(request,timeout=10) as response:return json.load(response)
        self.assertTrue(post()['ok'])
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            with server.jobs_lock:task=dict(server.jobs.get('workflow',{}))
            if task.get('state') in ('done','failed'):break
            time.sleep(.05)
        self.assertEqual(task.get('state'),'done',task)
        self.assertEqual([j['input']['scene_id'] for j in sg._jobs(self.root)],['scene_2','scene_3'])
        calls=len(self.flow.router.requests)
        self.assertTrue(post()['replayed'])
        self.assertEqual(len(self.flow.router.requests),calls)
