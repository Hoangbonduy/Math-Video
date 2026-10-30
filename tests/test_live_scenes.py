"""Explicit C5 live runner: never approves a script, design or clip."""
import os
from pathlib import Path
import unittest
from src.shared import _workflow_store as store
from src.shared.workflow import get_status, run_workflow

ENABLED=os.environ.get('RUN_LIVE_SCENE_TESTS')=='1' and bool(os.environ.get('OPENROUTER_API_KEY'))

@unittest.skipUnless(ENABLED,'Requires explicit RUN_LIVE_SCENE_TESTS=1 and key loaded before importing tests')
class LiveSceneTests(unittest.TestCase):
    def test_existing_projects_at_their_current_human_gate(self):
        root=Path('data/projects/checkpoint_4_live').resolve()
        states=[get_status(root/name) for name in ('dao_ham','ngay_dem')]
        for state in states:
            self.assertIn(state['stage'],('scene_design','scene_clip','final_video'),
                          'Approve both C4 scripts after actually reading them; live tests never approve for you')
        outcomes=[]
        for name in ('dao_ham','ngay_dem'):
            directory=root/name
            state=run_workflow(directory,ai=True)
            self.assertFalse(state['integrity_issues'])
            self.assertLessEqual(state['scene_generation']['budget_used_usd'],.5)
            again=run_workflow(directory,ai=True)
            self.assertEqual(state,again)
            outcomes.append(state)
        store.atomic_write(root/'checkpoint_5_acceptance.json',dict(live=True,auto_approved=False,outcomes=outcomes))
