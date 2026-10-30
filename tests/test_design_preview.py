"""Preview renders design JSON without an AI client or a paid request budget."""
from pathlib import Path
import unittest
from unittest.mock import patch

from src.shared import _workflow_store as store
from src.scene_design import scene_generation as sg
from src.shared import workflow as wf
from src.scene_design.design_preview import generate
from src.scene_design.render import render_scene
from tests import test_scenes as fixtures


class JsonPreviewTests(unittest.TestCase):
    def setUp(self):
        self.flow=fixtures.SceneFlowTests('test_two_topics_different_scene_counts_and_all_gates')
        self.flow.setUp();self.addCleanup(self.flow.doCleanups)
        self.root=self.flow.root
        self.flow.approved(1)
        self.design=wf.run_workflow(self.root,ai=True)['pending_reviews'][0]
        with store.locked_project(self.root):
            project,reviews=wf._load(self.root)
            artifact=next(a for a in project['artifacts'] if a['stage']=='scene_design')
            artifact.pop('manim_render');artifact.pop('render_engine')
            project['design_previews']={}
            wf._save(self.root,project,reviews)
        self.calls=len(self.flow.router.requests)
        self.flow.render.reset_mock()
        for target in ('src.scene_design.scene_generation.CLIENT_FACTORY','src.idea.generation.Session',
                       'src.scene_design.manim_agent.render_candidate','src.shared.openrouter_client.OpenRouterClient._send'):
            patch(target,side_effect=AssertionError('Preview must only execute saved code')).start()

    def preview(self):
        return generate(self.root,'scene_1',self.design['version'],self.design['sha256'])

    def test_saved_source_renders_without_ai_even_when_request_and_budget_limits_are_zero(self):
        original=self.design['manim_render']['code_path']
        source=(self.root/original).read_bytes()
        with patch('src.idea.generation.MAX_REQUESTS',0),patch('src.scene_design.scene_generation.BUDGET',0):
            result=self.preview()
            self.assertEqual(self.preview(),result)
        self.assertEqual((self.root/result['code_path']).read_bytes(),source)
        self.assertEqual((self.root/original).read_bytes(),source)
        self.assertEqual(self.flow.render.call_count,1)
        self.assertEqual(len(self.flow.router.requests),self.calls)
        self.assertEqual(sg._jobs(self.root)[-1]['calls'],[])

    def test_design_without_python_source_is_rendered_locally(self):
        with store.locked_project(self.root):
            project,reviews=wf._load(self.root)
            artifact=next(a for a in project['artifacts'] if a['stage']=='scene_design')
            for key in list(artifact['bundle']):
                if artifact['bundle'][key]['path'].endswith('.py'):del artifact['bundle'][key]
            wf._save(self.root,project,reviews)
        result=self.preview()
        self.assertEqual(result['preview_engine'],'json')
        self.assertTrue((self.root/result['code_path']).is_file())
        self.assertEqual(len(self.flow.router.requests),self.calls)

    def test_preview_uses_fixed_template_instead_of_selecting_an_old_python_file(self):
        other='scenes/other.py';store.materialize(self.root,other,content=b'not_python_source')
        with store.locked_project(self.root):
            project,reviews=wf._load(self.root)
            artifact=next(a for a in project['artifacts'] if a['stage']=='scene_design')
            artifact['bundle'][other]=dict(path=other,sha256=store.digest(self.root/other))
            artifact['code_path']=other
            wf._save(self.root,project,reviews)
        result=self.preview()
        self.assertNotEqual(result['code_path'],other)
        self.assertEqual(result['preview_engine'],'json')
        self.assertEqual(len(self.flow.router.requests),self.calls)

    def test_render_failure_keeps_source_and_log_without_agent_repair(self):
        source=self.root/self.design['manim_render']['code_path'];original=source.read_bytes()
        def fail(*args):
            result=fixtures.fake_render(*args)
            result.update(status='failed',error='RENDER_FAILED: intentional preview failure')
            store.atomic_write(Path(result['log_path']).with_suffix('.json'),result)
            return result
        self.flow.render.side_effect=fail
        self.assertRaisesRegex(store.WorkflowError,'intentional preview failure',self.preview)
        self.assertEqual(self.flow.render.call_count,1)
        self.assertEqual(source.read_bytes(),original)
        self.assertTrue((self.root/'logs/scene_1_v002.log').is_file())
        self.assertEqual(sg._jobs(self.root)[-1]['status'],'failed')
        self.assertEqual(sg._jobs(self.root)[-1]['calls'],[])
        self.flow.render.side_effect=fixtures.fake_render
        result=self.preview()
        self.assertEqual(result['render_version'],'v003')
        self.assertEqual(len(self.flow.router.requests),self.calls)

    def test_render_saved_before_registration_is_recovered_without_rerender(self):
        with patch('src.scene_design.scene_generation.render_reference',side_effect=RuntimeError('registration interrupted')):
            self.assertRaisesRegex(RuntimeError,'registration interrupted',self.preview)
        result=self.preview()
        self.assertEqual(result['render_version'],'v002')
        self.assertEqual(self.flow.render.call_count,1)
        self.assertEqual(len(self.flow.router.requests),self.calls)

    def test_source_changed_since_registration_is_never_executed(self):
        path=self.root/self.design['manim_render']['code_path'];path.write_text('changed')
        self.assertRaisesRegex(store.WorkflowError,'ARTIFACT_CHANGED',self.preview)
        self.flow.render.assert_not_called()

    def test_real_manim_preview_runs_existing_source_and_reuses_mp4(self):
        with patch('src.scene_design.scene_generation.RENDERER',side_effect=render_scene) as renderer:
            result=self.preview()
            self.assertEqual(self.preview(),result)
        renderer.assert_called_once()
        metadata=store.read_json(self.root/result['metadata_path'])
        self.assertEqual(metadata['status'],'success')
        self.assertGreater(metadata['video']['frame_count'],0)
        self.assertEqual(metadata['scene_file'],str(self.root/result['code_path']))
        self.assertEqual(len(self.flow.router.requests),self.calls)


class ExhaustedScenePreviewTests(unittest.TestCase):
    def setUp(self):
        self.flow=fixtures.SceneFlowTests('test_two_topics_different_scene_counts_and_all_gates')
        self.flow.setUp();self.addCleanup(self.flow.doCleanups)
        self.root=self.flow.root;self.flow.approved(1)

    def test_saved_design_finishes_after_six_calls_without_creating_client(self):
        with patch('src.scene_design.scene_generation.RENDERER',side_effect=store.WorkflowError('STOP','before render')):
            self.assertRaisesRegex(store.WorkflowError,'STOP',wf.run_workflow,self.root,ai=True,scene_id='scene_1')
        job=sg._jobs(self.root)[0];directory=self.root/sg.BASE/job['job_id']
        import copy
        while len(job['calls'])<6:
            call=copy.deepcopy(job['calls'][-1]);call.update(call_id='old_'+str(len(job['calls'])),phase='code_'+str(len(job['calls'])))
            job['calls'].append(call)
        store.atomic_write(directory/'job.json',job)
        calls=copy.deepcopy(job['calls']);before=len(self.flow.router.requests)
        with patch('src.scene_design.scene_generation.CLIENT_FACTORY',side_effect=AssertionError('No AI for cached design')),             patch('src.idea.generation.MAX_REQUESTS',0),patch('src.scene_design.scene_generation.BUDGET',0):
            state=wf.run_workflow(self.root,ai=True,scene_id='scene_1')
        self.assertEqual(state['pending_reviews'][0]['manim_render']['preview_engine'],'json')
        self.assertEqual(sg._jobs(self.root)[0]['calls'],calls)
        self.assertEqual(len(self.flow.router.requests),before)

    def test_old_failed_design_can_be_restored_from_saved_response_without_api(self):
        with patch('src.scene_design.scene_generation.RENDERER',side_effect=store.WorkflowError('STOP','before render')):
            self.assertRaisesRegex(store.WorkflowError,'STOP',wf.run_workflow,self.root,ai=True,scene_id='scene_1')
        job=sg._jobs(self.root)[0];directory=self.root/sg.BASE/job['job_id']
        for name in ('design.json','data.json','design.md'):(directory/name).unlink()
        before=len(self.flow.router.requests)
        with patch('src.scene_design.scene_generation.CLIENT_FACTORY',side_effect=AssertionError('Use cached JSON')):
            state=wf.run_workflow(self.root,ai=True,scene_id='scene_1')
        self.assertEqual(len(state['pending_reviews']),1)
        self.assertEqual(len(self.flow.router.requests),before)
