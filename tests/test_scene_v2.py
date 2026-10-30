import copy,json,math,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from src.scene_design import scene_contract as c
from src.scene_design import scene_state as state
from src.scene_design import scene_render_v2 as renderer
from src.shared import _workflow_store as store
from src.scene_design import scene_generation as sg
from src.scene_design import scene_pipeline as pipeline
from src.shared.job_session import Session
from src.scene_design.design import DEFAULT_CONFIG
from tests.scene_v2_support import design,vehicle,story,DesignRouter,VisionRouter,binding,obj


class ContractTests(unittest.TestCase):
    def test_parameter_updates_point_secant_and_slope(self):
        plan=c.compile_design(design());before=state.state_at(plan,0);after=state.state_at(plan,3)
        self.assertEqual(before['parameters']['h'],1);self.assertAlmostEqual(after['parameters']['h'],.1)
        self.assertAlmostEqual(before['values']['slopeAB'],3);self.assertAlmostEqual(after['values']['slopeAB'],2.1)
        for frame in range(37):
            value=state.state_at(plan,frame/12);a,b=value['objects']['A']['center'],value['objects']['B']['center'];p,q=value['objects']['secant']['points']
            for dot in (a,b):self.assertAlmostEqual((q[0]-p[0])*(dot[1]-p[1])-(q[1]-p[1])*(dot[0]-p[0]),0,places=8)
            self.assertAlmostEqual(value['objects']['nameB']['center'][0]-b[0],.025)
        self.assertTrue(c.motion_report(plan)['passed'])
    def test_group_keeps_body_wheels_label_together(self):
        plan=c.compile_design(vehicle());a=state.state_at(plan,0);b=state.state_at(plan,3)
        for oid in ('body','wheel1','wheel2','name'):
            self.assertAlmostEqual(b['objects'][oid]['center'][0]-a['objects'][oid]['center'][0],.25)
    def test_position_tangent_and_dependent_numeric_labels(self):
        d=design();d['objects'].extend([obj('tangent','line',.5,.5,0,0),obj('moving','dot',.3,.2,.01,.02)])
        d['bindings'].extend([binding('derivative','tangent_line','tangent',source_a='A',extent='full'),binding('position','position','moving',x_expression='.3+h*.1',y_expression='.2')])
        plan=c.compile_design(d);first=state.state_at(plan,0);last=state.state_at(plan,3)
        self.assertAlmostEqual(first['values']['derivative'],2,places=6)
        self.assertAlmostEqual(first['objects']['moving']['center'][0],.4)
        self.assertAlmostEqual(last['objects']['moving']['center'][0],.31)
    def test_cycles_invalid_references_unsafe_expressions_and_motion(self):
        variants=[];d=design();d['bindings'][0]['x_expression']='pointB';d['bindings'][1]['x_expression']='pointA';variants.append(d)
        d=vehicle();d['groups'][0]['members'].append('car');variants.append(d)
        d=design();d['bindings'][2]['source_a']='missing';variants.append(d)
        d=design();d['graphs'][0]['expression']='__import__("os")';variants.append(d)
        for d in variants:
            with self.assertRaises(store.WorkflowError):c.compile_design(d)
        d=vehicle();d['timeline'][0]['dx']=1;self.assertFalse(c.motion_report(c.compile_design(d))['passed'])
    def test_storyboard_preserves_narration_duration_and_coverage(self):
        sc=story();c.validate_story(sc,dict(scene_id=sc['scene_id'],narration=sc['narration']),3)
        for key,value in [('narration','rewritten'),('duration',4)]:
            bad=copy.deepcopy(sc);bad[key]=value
            with self.assertRaises(store.WorkflowError):c.validate_story(bad,dict(scene_id=sc['scene_id'],narration=sc['narration']),3)


class RealRenderTests(unittest.TestCase):
    def test_render_relations_and_actual_audit(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);work=root/'draft';work.mkdir();config=copy.deepcopy(DEFAULT_CONFIG);config['render']['resolution']=[640,360]
            ref,checks,evidence=renderer.render(root,work,{},design(),dict(assets=[]),config,story())
            self.assertTrue(checks['passed'],checks);self.assertGreater(len(checks['states']),3)
            self.assertGreater(len(evidence['frames']),3)
            for frame in evidence['frames']:self.assertTrue((root/frame['path']).is_file())
            self.assertEqual(store.digest(root/ref['path']),ref['sha256'])
            output=Path(os.environ.get('SCENE_V2_EVIDENCE','/tmp/scene-v2-evidence'));output.mkdir(exist_ok=True,parents=True)
            import shutil
            shutil.copytree(work,output/'secant',dirs_exist_ok=True);shutil.copyfile(root/ref['path'],output/'secant.mp4')


class ExtraRenderTests(unittest.TestCase):
    def test_group_rotation_scaling_parameter_position_and_image_render(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);work=root/'draft';work.mkdir()
            from PIL import Image
            Image.new('RGB',(32,32),'#44aa88').save(root/'asset.png')
            d=vehicle(seconds=1);d['objects'].append(dict(obj('picture','image',.8,.2,.1,.1),asset_id='test'))
            d['groups'].append(dict(id='whole',members=['car']))
            d['timeline'].extend([dict(object_id='whole',action='rotate',start=0,duration=1,dx=0,dy=0,value=10,easing='linear'),dict(object_id='whole',action='scale',start=0,duration=1,dx=0,dy=0,value=.9,easing='linear')])
            sc=story(seconds=1);ref,checks,manifest=renderer.render(root,work,{},d,dict(assets=[dict(asset_id='test',path='asset.png')]),DEFAULT_CONFIG,sc)
            self.assertTrue(checks['passed'],checks);self.assertTrue((root/ref['path']).is_file())


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.directory=self.root/'generation/test';self.directory.mkdir(parents=True)
        self.router=DesignRouter();self.vision=VisionRouter();self.job=dict(job_id='test',input={},calls=[],status='working',pricing=self.router.preflight(search=False))
        self.session=Session(self.directory,self.job,self.router,budget_usd=.5)
        self.story=story();self.context=dict(scene=dict(scene_id='scene_1',narration=self.story['narration']),script=dict(title='Cát tuyến',scenes=[]),brief={},target_seconds=3,config=copy.deepcopy(DEFAULT_CONFIG),feedback=None)
        patcher=patch('src.scene_design.scene_vision.CLIENT_FACTORY',lambda:self.vision);patcher.start();self.addCleanup(patcher.stop)
    def test_full_pipeline_actual_render_visual_gate_and_cache(self):
        path,extra=pipeline.generate(self.root,self.directory,self.session,self.context)
        self.assertEqual(extra['design_pipeline'],2);self.assertTrue(pipeline.verify_quality(self.root,dict(sha256=store.digest(path),**extra))['passed'])
        self.assertEqual(len(self.router.requests),2)
        self.assertNotIn("v2_assets",[call["phase"] for call in self.job["calls"]]);self.assertEqual(len(self.vision.requests),1)
        self.assertTrue(any(part['type']=='image_url' for part in self.vision.requests[0]['messages'][1]['content']))
        calls=len(self.job['calls']);pipeline.generate(self.root,self.directory,self.session,self.context);self.assertEqual(len(self.job['calls']),calls)
        (self.root/extra['storyboard_path']).write_text('{}')
        with self.assertRaisesRegex(store.WorkflowError,'ARTIFACT_CHANGED'):pipeline.verify_quality(self.root,dict(sha256=store.digest(path),**extra))
    def test_max_three_repairs_never_publishes_failed_candidate(self):
        issue=dict(category='timing',beat_id='approach',object_ids=[],time=1,problem='Chuyển động quá nhanh.',fix='Kéo giãn nhịp trong thời lượng cảnh.')
        self.vision.reviews=[[issue]]
        with self.assertRaisesRegex(store.WorkflowError,'SCENE_QUALITY_FAILED'):pipeline.generate(self.root,self.directory,self.session,self.context)
        self.assertEqual(len(self.vision.requests),4);self.assertEqual(len(list(self.directory.glob('round_*'))),4)
        self.assertFalse((self.directory/'design.json').exists());self.assertFalse(store.read_json(self.directory/'quality.json')['passed'])
        calls=len(self.job['calls'])
        with self.assertRaisesRegex(store.WorkflowError,'SCENE_QUALITY_FAILED'):pipeline.generate(self.root,self.directory,self.session,self.context)
        self.assertEqual(len(self.job['calls']),calls)
    def test_budget_and_missing_vision_stop_before_design(self):
        self.router.reserve=lambda *a:1
        with self.assertRaisesRegex(store.WorkflowError,'BUDGET_EXCEEDED'):pipeline.generate(self.root,self.directory,self.session,self.context)
        self.assertEqual(len(self.router.requests),0)
    def test_layout_and_relation_repairs_target_correct_fields(self):
        for category in ('layout','relation'):
            with self.subTest(category=category),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);directory=root/'job';directory.mkdir();router=DesignRouter();vision=VisionRouter([[dict(category=category,beat_id='approach',object_ids=['B'],time=1,problem='Lỗi cần sửa.',fix='Sửa đúng phần được chỉ định.')],[]])
                job=dict(job_id='test',calls=[],status='working',pricing=router.preflight(search=False))
                with patch('src.scene_design.scene_vision.CLIENT_FACTORY',return_value=vision):pipeline.generate(root,directory,Session(directory,job,router,budget_usd=.5),self.context)
                phases=[call['phase'] for call in job['calls']]
                self.assertIn('v2_patch_1' if category=='layout' else 'v2_design_1',phases)
                self.assertEqual(len(router.stories),1)
    def test_unsupported_stops_without_substitute_and_missing_vision(self):
        issue=dict(category='unsupported',beat_id='approach',object_ids=[],time=1,problem='Bộ dựng thiếu khả năng cần thiết.',fix='Bổ sung bộ dựng trước khi tiếp tục.')
        self.vision.reviews=[[issue]]
        with self.assertRaisesRegex(store.WorkflowError,'SCENE_CAPABILITY_MISSING'):pipeline.generate(self.root,self.directory,self.session,self.context)
        self.assertFalse((self.directory/'design.json').exists());self.assertEqual(len(self.vision.requests),4)
    def test_targeted_repair_routes(self):
        issue=dict(category='meaning',beat_id='approach',object_ids=[],time=1,problem='Sai ý cần giải thích.',fix='Sửa storyboard để diễn đạt đúng.')
        self.vision.reviews=[[issue],[]]
        pipeline.generate(self.root,self.directory,self.session,self.context)
        self.assertEqual(len(self.router.stories),2);self.assertEqual(len(self.router.design_requests),2)
        self.assertEqual(self.router.design_requests[1]['issues'][0]['category'],'meaning')

if __name__=='__main__':unittest.main()
