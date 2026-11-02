"""Selected-scene controls never enqueue other scenes."""
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.shared.review_server import ReviewServer


class SingleSceneRoutingTests(unittest.TestCase):
    def run_request(self, stage, scene_id, retry=None, attempt=None):
        queued = []
        server = SimpleNamespace(root=Path('/project'), scene_trial=True,
                                 enqueue=lambda key, work: queued.append((key, work)))
        with patch('src.shared.review_server.wf.get_status', return_value={'stage': stage}), \
             patch('src.shared.review_server.wf.run_workflow') as run:
            ReviewServer.continue_scenes(server, scene_id, retry, attempt)
            self.assertEqual(len(queued), 1)
            key, work = queued[0]
            work()
        return key, run.call_args

    def test_selected_scene_isolated_for_both_scene_stages(self):
        for stage in ('scene_design', 'scene_clip'):
            with self.subTest(stage=stage):
                key, call = self.run_request(stage, 'scene_2', attempt='fresh')
                self.assertEqual(key, 'generate:scene_2')
                self.assertEqual(call.kwargs['scene_id'], 'scene_2')
                self.assertFalse(call.kwargs['auto_continue'])
                self.assertEqual(call.kwargs['attempt_id'], 'fresh')

    def test_cancelled_job_gets_new_stable_cache_key(self):
        from src.scene_design.scene_generation import fresh_after_cancelled
        original=dict(stage='scene_design',scene_id='s5',version='v004',
                      script_sha256='script',model='model')
        jobs=[dict(job_id='old_job',status='cancelled',input=original,calls=[])]
        first=fresh_after_cancelled(jobs,original)
        self.assertEqual(first['attempt_id'],'reset-old_job')
        self.assertEqual(fresh_after_cancelled(jobs,original),first)
        self.assertEqual(original.get('attempt_id'),None)
        self.assertEqual(fresh_after_cancelled(jobs,dict(original,attempt_id='manual'))['attempt_id'],'manual')
        self.assertEqual(fresh_after_cancelled(jobs,dict(original,version='v005')).get('attempt_id'),None)

    def test_retry_is_scoped_and_global_workflow_is_preserved(self):
        key, call = self.run_request('scene_design', 'scene_1', retry='call_1')
        self.assertEqual(key, 'generate:scene_1')
        self.assertFalse(call.kwargs['auto_continue'])
        self.assertEqual(call.kwargs['retry_uncertain'], 'call_1')
        key, call = self.run_request('scene_design', None)
        self.assertEqual(key, 'workflow')
        self.assertTrue(call.kwargs['auto_continue'])
        key, call = self.run_request('script', None)
        self.assertEqual(key, 'workflow')
        self.assertFalse(call.kwargs['auto_continue'])


if __name__ == '__main__':
    unittest.main()
