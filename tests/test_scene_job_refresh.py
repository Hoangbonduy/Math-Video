"""Changed models/scripts must not deadlock scenes or discard billed history."""
import json
import hashlib
import os
from unittest.mock import patch
from src.scene_design import scene_generation as sg
from src.shared import _workflow_store as store
from src.shared.workflow import run_workflow, get_status
from tests.test_scenes import SceneFlowTests


class SceneJobRefreshTests(SceneFlowTests):
    def test_explicitly_rejected_job_refreshes_provider_pricing_on_retry(self):
        from src.shared.openrouter_client import HTTPFailure
        self.approved()
        with patch.object(self.router,'complete',side_effect=HTTPFailure(400,'Provider unavailable')):
            self.assertRaisesRegex(store.WorkflowError,'API_CONFIGURATION',run_workflow,self.root,ai=True,scene_id='scene_1')
        checks=self.router.preflights
        state=run_workflow(self.root,ai=True,scene_id='scene_1')
        self.assertEqual(self.router.preflights,checks+1)
        self.assertEqual(len(state['pending_reviews']),1)
        self.assertEqual(sg._jobs(self.root)[0]['calls'][0]['status'],'rejected')

    def save_old_fingerprint(self,job):
        previous=job['job_id']
        from src.shared.job_session import _json_bytes
        job['job_id']=previous.rsplit('_',1)[0]+'_'+hashlib.sha256(_json_bytes(job['input'])).hexdigest()[:16]
        old_dir=self.root/sg.BASE/previous
        new_dir=self.root/sg.BASE/job['job_id']
        old_dir.rename(new_dir)
        store.atomic_write(new_dir/'job.json',job)
        index=sg._index(self.root)
        index['jobs']=[p.replace(previous,job['job_id']) for p in index['jobs']]
        store.atomic_write(self.root/sg.BASE/'ledger.json',index)

    # Inherit the shared setup, not the entire scenario suite.
    def test_model_change_creates_new_job_preserving_history_and_cost(self):
        self.approved()
        with patch.object(self.router,'reserve',return_value=.6):
            self.assertRaisesRegex(store.WorkflowError,'BUDGET_EXCEEDED',run_workflow,self.root,ai=True,scene_id='scene_1')
        old=sg._jobs(self.root)[0]
        old['input']['model']='old/provider-model'
        self.save_old_fingerprint(old)
        before=(self.root/sg.BASE/old['job_id']/'job.json').read_bytes()
        state=run_workflow(self.root,ai=True,scene_id='scene_1')
        self.assertEqual(len(state['pending_reviews']),1)
        self.assertEqual((self.root/sg.BASE/old['job_id']/'job.json').read_bytes(),before)
        self.assertEqual(len(sg._jobs(self.root)),2)
        jobs=state['scene_generation']['jobs']
        self.assertFalse(jobs[0]['current']);self.assertTrue(jobs[1]['current'])

    def test_old_script_failed_job_does_not_block_current_script(self):
        self.approved()
        with patch.object(self.router,'complete',side_effect=ValueError('request unknown')):
            self.assertRaisesRegex(store.WorkflowError,'API_RESULT_UNKNOWN',run_workflow,self.root,ai=True,scene_id='scene_1')
        old=sg._jobs(self.root)[0]
        old['input']['script_sha256']='old-script-checksum'
        # Simulate a resolved old response; its reserved/actual cost must remain counted.
        old['calls'][0].update(status='complete',actual_cost_usd=.001,usage_incomplete=False)
        self.save_old_fingerprint(old)
        state=run_workflow(self.root,ai=True,scene_id='scene_1')
        self.assertEqual(len(state['pending_reviews']),1)
        self.assertGreaterEqual(state['scene_generation']['budget_used_usd'],.0012-1e-9)

    def test_model_change_cannot_bypass_unresolved_request(self):
        self.approved()
        with patch.object(self.router,'complete',side_effect=TimeoutError('lost reply')):
            self.assertRaisesRegex(store.WorkflowError,'API_RESULT_UNKNOWN',run_workflow,self.root,ai=True,scene_id='scene_1')
        old=sg._jobs(self.root)[0];old['input']['model']='old/provider-model'
        self.save_old_fingerprint(old)
        before=sg.summary(self.root)['budget_used_usd']
        calls=len(self.router.requests)
        self.assertRaisesRegex(store.WorkflowError,'API_RESULT_UNKNOWN',run_workflow,self.root,ai=True,scene_id='scene_1')
        self.assertEqual(len(sg._jobs(self.root)),1)
        self.assertEqual(len(self.router.requests),calls)
        self.assertEqual(sg.summary(self.root)['budget_used_usd'],before)

    def test_progress_saved_before_provider_calls(self):
        self.approved();progress=[]
        original=self.router.complete
        def complete(payload):
            progress.append(sg._jobs(self.root)[-1].get('progress'))
            return original(payload)
        with patch.object(self.router,'complete',side_effect=complete):
            state=run_workflow(self.root,ai=True,scene_id='scene_1')
        self.assertEqual(progress,['planning_assets','designing_scene'])
        self.assertEqual(state['scene_generation']['jobs'][-1]['progress'],'ready_for_review')


# Avoid re-running inherited tests; shared cases remain in test_scenes.
for _name in list(SceneFlowTests.__dict__):
    if _name.startswith('test_') and _name not in SceneJobRefreshTests.__dict__:
        setattr(SceneJobRefreshTests,_name,None)
del SceneFlowTests
