import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from src.scene_design import scene_generation as sg
from src.scene_design import scene_pipeline as pipeline
from src.scene_design import scene_contract as c
from src.shared import workflow as wf
from src.shared import _workflow_store as store
from src.shared.job_session import Session
from src.shared.review import record_review
from tests.scene_v2_support import DesignRouter,VisionRouter,design,vehicle,story,binding

class TrialTests(unittest.TestCase):
    def test_failed_trial_scene_continues_batch_and_retains_previous_jobs(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);wf.create_project(root,'Thử cảnh')
            source=root/'script.md';source.write_text(story()['narration'])
            wf.submit_artifact(root,'script','v001',source,scene_ids=['scene_1','scene_2'])
            record_review(root,'script','v001','approve')
            scenes=[dict(scene_id=s,narration=story()['narration']) for s in ['scene_1','scene_2']]
            def context(root,p,r):return dict(wf._latest(p,'script',None),bundle={}),dict(script=dict(scenes=scenes),brief={}),dict(scene_1=3,scene_2=3)
            router=DesignRouter()
            with patch('src.scene_design.scene_generation._context',side_effect=context),patch('src.scene_design.scene_generation.CLIENT_FACTORY',return_value=router),patch('src.scene_design.scene_generation._design',side_effect=store.WorkflowError('SCENE_QUALITY_FAILED','trial failure')) as generate:
                with self.assertRaisesRegex(store.WorkflowError,'trial failure'):wf.run_workflow(root,ai=True,scene_id='scene_1')
                old=sg._jobs(root)[0];old_path=root/sg.BASE/old['job_id']/'job.json';old_bytes=old_path.read_bytes();generate.reset_mock()
                with self.assertRaisesRegex(store.WorkflowError,'trial failure'):wf.run_workflow(root,ai=True,scene_trial=True)
                self.assertEqual(generate.call_count,2)
                jobs=sg._jobs(root);self.assertEqual(len(jobs),3)
                self.assertEqual([j['input']['scene_id'] for j in jobs[1:]],['scene_1','scene_2'])
                self.assertTrue(all(j['input']['max_repairs']==2 and j['status']=='failed' for j in jobs[1:]))
                self.assertEqual(old_path.read_bytes(),old_bytes)
                self.assertFalse(wf.get_status(root)['pending_reviews'])

    def test_visual_failure_does_not_trigger_a_second_round_or_publish(self):
        from src.scene_design.design import DEFAULT_CONFIG
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);work=root/'job';work.mkdir();router=DesignRouter()
            vision=VisionRouter([[dict(category='timing',beat_id='approach',object_ids=[],time=1,problem='Chưa đạt nhịp.',fix='Sửa nhịp.')]])
            job=dict(job_id='trial',input=dict(trial_mode=True,max_repairs=0),calls=[],status='working',pricing=router.preflight(search=False))
            context=dict(scene=dict(scene_id='scene_1',narration=story()['narration']),script={},brief={},target_seconds=3,config=copy.deepcopy(DEFAULT_CONFIG),feedback=None)
            with patch('src.scene_design.scene_vision.CLIENT_FACTORY',return_value=vision):
                with self.assertRaisesRegex(store.WorkflowError,'1 lượt'):pipeline.generate(root,work,Session(work,job,router,budget_usd=.5),context)
            self.assertEqual(len(vision.requests),1);self.assertEqual(len(list(work.glob('round_*'))),1)
            self.assertFalse((work/'design.json').exists());self.assertEqual(job['max_repairs'],0)
            calls=len(job['calls'])
            with self.assertRaises(store.WorkflowError):pipeline.generate(root,work,Session(work,job,router,budget_usd=.5),context)
            self.assertEqual(len(job['calls']),calls)

    def test_visual_review_accepts_known_groups_and_rejects_unknown_ids(self):
        from types import SimpleNamespace
        sc=story();d=vehicle();issue=dict(category='layout',beat_id='approach',object_ids=['car'],time=1,problem='Nhóm xe chưa rõ.',fix='Tăng tương phản nhóm xe.')
        review=dict(passed=False,summary='Chưa đạt.',issues=[issue],criteria_results=[dict(beat_id=b['id'],criterion=criterion,passed=False,evidence='Nhóm xe chưa rõ trong khung hình.') for b in sc['beats'] for criterion in b['criteria']])
        response=dict(model='review',choices=[dict(message=dict(content=json.dumps(review)),finish_reason='stop')])
        session=SimpleNamespace(job=dict(vision_pricing=dict(model='review')),call=lambda *a,**k:response)
        with patch('src.scene_design.scene_vision.messages',return_value=[]):
            self.assertFalse(pipeline._review(Path('.'),session,None,sc,d,{}, {},0)['passed'])
            issue['object_ids']=['foreign_group'];response['choices'][0]['message']['content']=json.dumps(review)
            with self.assertRaisesRegex(store.WorkflowError,'INVALID_VISUAL_REVIEW'):pipeline._review(Path('.'),session,None,sc,d,{}, {},0)

    def test_reference_errors_explain_supported_ids(self):
        d=vehicle();d['bindings']=[binding('move_car','position','car',x_expression='.5',y_expression='.5')]
        with self.assertRaisesRegex(store.WorkflowError,'this is a group'):c.compile_design(d)
        d=design();d['bindings'][0]['graph']='foreign_graph'
        with self.assertRaisesRegex(store.WorkflowError,r'graphs\[\].id'):c.compile_design(d)

    def test_motion_errors_are_unique_but_all_frames_are_checked(self):
        d=vehicle();d['objects'][0]['x']=-.5
        result=c.motion_report(c.compile_design(d))
        self.assertFalse(result['passed']);self.assertEqual(result['sample_count'],37)
        self.assertEqual(sum(i['object_ids']==['body'] for i in result['issues']),1)

if __name__=='__main__':unittest.main()
