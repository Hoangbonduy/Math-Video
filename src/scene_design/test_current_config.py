"""Current render settings must replace legacy settings without resetting jobs."""
import copy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from src.scene_design import scene_generation as sg
from src.shared import _workflow_store as store
from src.shared import workflow as wf
from src.shared.ai_config import get_model


class CurrentSceneConfigTests(unittest.TestCase):
    def setUp(self):
        billing = patch.object(sg, 'subscription_billing', return_value=None)
        billing.start()
        self.addCleanup(billing.stop)
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'project'
        wf.create_project(self.root, 'Current render config')
        store.materialize(self.root, 'source.md', content=b'Approved narration')
        store.atomic_write(self.root / 'script.json', dict(
            script=dict(title='Current script', scenes=[dict(
                scene_id='s1', narration='Approved narration', visual_intent='A moving dot')]),
            brief={}))
        for name in ('research.json', 'generation.json'):
            store.atomic_write(self.root / name, {})
        with store.locked_project(self.root):
            project, reviews = wf._load(self.root)
            wf._submit(self.root, project, reviews, 'script', 'v001', self.root / 'source.md', None, ['s1'])
            script = wf._latest(project, 'script', None)
            script.update(origin='ai', bundle={name: dict(
                path=name, sha256=store.digest(self.root / name))
                for name in ('script.json', 'research.json', 'generation.json')})
            self.script_sha256 = script['sha256']
            request = wf._request(reviews, script)
            request.update(status='approved', decision='approve', decided_at=sg._now(), read_check=dict(
                seconds=35, sha256=script['sha256']))
            project.update(stage='scene_design', status='ready', scene_generation=dict(
                path=sg.BASE + '/ledger.json'))
            wf._save(self.root, project, reviews)
        self.current = copy.deepcopy(sg.DEFAULT_CONFIG)
        self.current['render'].update(resolution=[640, 360], fps=30)
        self.legacy = copy.deepcopy(self.current)
        self.legacy['render'].update(resolution=[360, 640], fps=12)
        store.atomic_write(self.root / 'scene_config.json', self.current)
        self.old_path = self.root / sg.BASE / 'old_job' / 'job.json'
        self.old_job = dict(job_id='old_job', status='failed', input=dict(
            stage='scene_design', scene_id='s1', version='v001', model=get_model(),
            script_sha256='previous-script', config=self.legacy), calls=[dict(
                call_id='paid-call', phase='v2_storyboard_0', status='complete',
                actual_cost_usd=.05, reserved_cost_usd=.1)])
        self.save_old_job()
        store.atomic_write(self.root / sg.BASE / 'ledger.json', dict(
            budget_usd=.5, config=self.legacy,
            jobs=[str(self.old_path.relative_to(self.root))]))

    def save_old_job(self):
        self.old_path.parent.mkdir(parents=True, exist_ok=True)
        store.atomic_write(self.old_path, self.old_job)
        self.old_bytes = self.old_path.read_bytes()

    def run_until_design(self):
        seen = []
        def design(root, directory, session, context):
            seen.append((copy.deepcopy(context['config']), session.other_used))
            raise store.WorkflowError('TEST_STOP', 'Reached design without external calls')
        client = Mock()
        client.preflight.return_value = dict(model=get_model())
        with patch('src.scene_design.manim_runtime.info', return_value={}), patch.object(
                sg, 'CLIENT_FACTORY', return_value=client), patch.object(sg, '_design', side_effect=design):
            with self.assertRaisesRegex(store.WorkflowError, 'TEST_STOP'):
                sg.run_scenes(self.root, scene_id='s1')
        self.assertEqual(seen, [(self.current, .05)])
        self.assertEqual(self.old_path.read_bytes(), self.old_bytes)
        self.assertEqual(sg._index(self.root)['config'], self.current)
        self.assertEqual(len(sg._index(self.root)['jobs']), 2)
        job = sg._jobs(self.root)[-1]
        self.assertEqual(job['input']['config'], self.current)
        self.assertEqual(sg.summary(self.root)['budget_used_usd'], .05)
        client.complete.assert_not_called()

    def test_legacy_script_config_does_not_block_current_settings(self):
        self.run_until_design()

    def test_current_script_config_change_uses_new_job_and_keeps_paid_history(self):
        self.old_job['input']['script_sha256'] = self.script_sha256
        self.save_old_job()
        self.run_until_design()

    def test_unknown_paid_call_still_blocks_new_config(self):
        self.old_job['calls'][0]['status'] = 'uncertain'
        self.old_job['calls'][0]['actual_cost_usd'] = None
        self.save_old_job()
        with patch('src.scene_design.manim_runtime.info', return_value={}), patch.object(
                sg, 'CLIENT_FACTORY') as factory:
            with self.assertRaisesRegex(store.WorkflowError, 'API_RESULT_UNKNOWN'):
                sg.run_scenes(self.root, scene_id='s1')
        factory.assert_not_called()
        self.assertEqual(self.old_path.read_bytes(), self.old_bytes)
        self.assertEqual(len(sg._index(self.root)['jobs']), 1)
        self.assertEqual(sg.summary(self.root)['budget_used_usd'], .1)

    def test_config_change_cannot_resume_old_cached_input(self):
        candidate = copy.deepcopy(self.old_job['input'])
        candidate['config'] = copy.deepcopy(self.current)
        self.assertEqual(sg.resume_input([self.old_job], candidate, None), candidate)
        self.assertEqual(candidate['config'], self.current)


if __name__ == '__main__':
    unittest.main()
