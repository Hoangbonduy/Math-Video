"""Feedback on an unpublished failed scene must reach the next generation."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from src.shared import workflow as wf
from src.shared import _workflow_store as store
from src.scene_design import scene_generation as sg
from tests.test_scene_v2_workflow import approved_project
from tests.scene_v2_support import DesignRouter

class FailedSceneFeedbackTests(unittest.TestCase):
    def test_failed_scene_feedback_reaches_new_attempt_and_survives_resume(self):
        with tempfile.TemporaryDirectory() as folder:
            root=approved_project(Path(folder))
            def context(root,p,r):
                return wf._latest(p,'script',None),store.read_json(root/'input.json'),{'scene_1':3}
            observed=[]
            def fail(root,directory,session,context):
                observed.append(context)
                raise store.WorkflowError('SCENE_QUALITY_FAILED','Objects collide')
            with patch.object(sg,'_context',side_effect=context), patch.object(sg,'CLIENT_FACTORY',return_value=DesignRouter()), patch.object(sg,'_design',side_effect=fail), patch('src.scene_design.manim_runtime.info'):
                with self.assertRaises(store.WorkflowError):sg.run_scenes(root,scene_id='scene_1')
                job=sg._jobs(root)[-1]
                before=(root/sg.BASE/job['job_id']/'job.json').read_bytes()
                saved=root/sg.BASE/job['job_id']/'round_00';saved.mkdir()
                store.atomic_write(saved/'design.json',{'old_design':True})
                store.atomic_write(saved/'storyboard.json',{'old_story':True})
                with self.assertRaisesRegex(store.WorkflowError,'STALE_REVIEW'):
                    sg.request_failed_feedback(root,'scene_1','obsolete','Separate objects')
                sg.request_failed_feedback(root,'scene_1',job['job_id'],'Separate objects')
                for attempt in ['feedback-attempt',None]:
                    with self.assertRaisesRegex(store.WorkflowError,'Objects collide'):
                        sg.run_scenes(root,scene_id='scene_1',attempt_id=attempt)
                    self.assertEqual(observed[-1]['feedback'],'Separate objects')
                    self.assertIn('Objects collide',observed[-1]['previous_error'])
                    if attempt:
                        self.assertNotIn('previous_design',observed[-1])
                        self.assertNotIn('previous_storyboard',observed[-1])
                self.assertEqual(len(sg._jobs(root)),2)
                self.assertEqual(before,(root/sg.BASE/job['job_id']/'job.json').read_bytes())
                self.assertFalse(wf.get_status(root)['pending_reviews'])

    def test_empty_feedback_rejected(self):
        for text in [None,'','  ','x'*10001]:
            with self.assertRaisesRegex(store.WorkflowError,'INVALID_INPUT'):
                sg.request_failed_feedback('/unused','s1','job',text)

if __name__=='__main__':unittest.main()
