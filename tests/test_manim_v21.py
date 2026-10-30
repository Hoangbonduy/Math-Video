"""V2.1 geometry prechecks and durable pre-render repair budget (offline AI)."""
import copy,json,math,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from src.scene_design import scene_contract as c,scene_state as state,scene_pipeline as pipeline
from src.shared import _workflow_store as store
from tests.scene_v2_support import obj,binding,design,story,DesignRouter
from tests import test_scene_v2 as fixtures


def cubic():
    d=design('s3',narration='Test',seconds=5)
    d.update(objects=[
        obj('curve_graph','graph',.34,.56,.52,.64),
        obj('point_center','dot',.34,.8,.022,.039),
        obj('center_label','text',.24,.72,.12,.05,'t = 1'),
        obj('point_left','dot',.132,.879,.02,.036),
        obj('point_right','dot',.548,.413,.02,.036),
        obj('secant','line',.34,.65,.416,.47)],
        parameters=[dict(id='h',value=.8,min=0,max=.9)],
        parameter_tracks=[dict(parameter='h',start=1,duration=3,**{'from':.8,'to':0},easing='smooth')],
        graphs=[dict(id='position_graph',object_id='curve_graph',expression='t**3',variable='t',
                     x_min=0,x_max=2,y_min=0,y_max=8,samples=80)],
        groups=[],timeline=[],
        bindings=[binding('c_bind','point_on_graph','point_center',graph='position_graph',x_expression='1'),
                  binding('l_bind','point_on_graph','point_left',graph='position_graph',x_expression='1-h'),
                  binding('r_bind','point_on_graph','point_right',graph='position_graph',x_expression='1+h'),
                  binding('s_bind','line_through_points','secant',source_a='point_left',source_b='point_right')])
    return d


def spec_checks():
    d=cubic();plan=c.compile_design(d)
    issues=c.motion_report(plan,12,[3.667])['issues']
    assert any(set(i['object_ids'])=={'center_label','secant'} for i in issues),issues
    next(o for o in d['objects'] if o['id']=='center_label').update(x=.34,y=.8)
    issues=c.motion_report(c.compile_design(d))['issues']
    assert any(set(i['object_ids'])=={'center_label','point_center'} for i in issues),issues
    for extent in ['segment','full']:
        d['bindings'][-1]['extent']=extent
        plan=c.compile_design(d)
        for t in [3.999,4,5]:
            value=state.state_at(plan,t)
            assert math.isfinite(value['values']['s_bind'])
            assert abs(value['values']['s_bind']-3)<1e-4
            points=value['objects']['secant']['points']
            assert len(points)==2 and math.dist(*points)>0
    return dict(line_text_detected=True,text_dot_detected=True,coincident_secant_is_tangent=True)


class GeometryTests(unittest.TestCase):
    def test_pdf_three_required_checks(self):
        self.assertTrue(all(spec_checks().values()))

    def test_polyline_intermediate_vertex_graph_curve_axes_and_overlap_exceptions(self):
        d=design();d.update(groups=[],bindings=[],parameters=[],parameter_tracks=[],graphs=[],timeline=[])
        label=obj('label','text',.5,.8,.12,.06,'label')
        path=obj('path','plot',.5,.5,.8,.6);path['points']=[[0,0],[.5,1],[1,0]]
        d['objects']=[label,path]
        self.assertTrue(any(i['object_ids']==['label','path'] for i in c.motion_report(c.compile_design(d))['issues']))
        for owner,other in [('label','path'),('path','label')]:
            variant=copy.deepcopy(d)
            next(o for o in variant['objects'] if o['id']==owner)['overlap_with']=[other]
            self.assertTrue(c.motion_report(c.compile_design(variant))['passed'])
        d=design();d.update(objects=[d['objects'][1],obj('label','text',.566,.6396,.10,.05,'curve')],
                            groups=[],bindings=[],parameters=[],parameter_tracks=[],timeline=[])
        self.assertTrue(any(i['object_ids']==['label','curve'] for i in c.motion_report(c.compile_design(d))['issues']))
        d['objects'][1].update(x=.5,y=.87)
        self.assertFalse(c.motion_report(c.compile_design(d))['passed'])

    def test_visibility_and_rotated_group_world_coordinates(self):
        d=design(seconds=3);d.update(groups=[],bindings=[],parameters=[],parameter_tracks=[],graphs=[])
        d['objects']=[obj('label','text',.5,.5,.2,.08,'text'),obj('path','line',.5,.5,.8,.01)]
        d['objects'][1]['points']=[[0,.5],[1,.5]]
        d['timeline']=[dict(object_id='label',action='fade',start=0,duration=.2,dx=0,dy=0,value=0,easing='linear'),
                       dict(object_id='path',action='appear',start=2,duration=.5,dx=0,dy=0,value=0,easing='linear')]
        self.assertTrue(c.motion_report(c.compile_design(d))['passed'])
        d['timeline']=[];d['groups']=[dict(id='whole',members=['label','path'])]
        d['timeline']=[dict(object_id='whole',action='rotate',start=0,duration=1,dx=0,dy=0,value=30,easing='linear')]
        self.assertFalse(c.motion_report(c.compile_design(d))['passed'])

    def test_convergence_preserves_scalar_readout_and_rejects_coincident_unrelated_points(self):
        d=cubic()
        d['objects'].append(obj('slope','text',.8,.1,.3,.05,'slope'))
        d['bindings'].append(binding('readout','value_label','slope',expression='s_bind',prefix='slope='))
        value=state.state_at(c.compile_design(d),4)
        self.assertEqual(value['objects']['slope']['label'],'slope=3.00')
        d['objects']=[o for o in d['objects'] if o['kind']!='graph']
        d.update(graphs=[],parameters=[],parameter_tracks=[],bindings=[binding('link','line_through_points','secant',source_a='point_left',source_b='point_right')])
        for o in d['objects']:
            if o['id'] in ('point_left','point_right'):o.update(x=.4,y=.5)
        with self.assertRaisesRegex(ValueError,'Coincident'):state.state_at(c.compile_design(d),0)

    def test_tangent_fallback_at_domain_endpoint_uses_one_sided_derivative(self):
        d=cubic();d['graphs'][0].update(expression='t**2')
        d['parameter_tracks']=[];d['parameters'][0]['value']=0
        for b in d['bindings'][:3]:b['x_expression']='0'
        value=state.state_at(c.compile_design(d),0)
        self.assertAlmostEqual(value['values']['s_bind'],0,delta=1e-4)
        self.assertEqual(len(value['objects']['secant']['points']),2)


class PrecheckRouter(DesignRouter):
    def __init__(self,fix=True):
        super().__init__();self.patch_count=0;self.fix=fix
    def complete(self,payload):
        response=super().complete(payload)
        props=payload['response_format']['json_schema']['schema']['properties']
        context=json.loads(payload['messages'][1]['content'])
        value=json.loads(response['choices'][0]['message']['content'])
        if 'schema_version' in props:
            next(o for o in value['objects'] if o['id']=='title')['x']=1.1
        elif 'layout' in props and next(o for o in context['design']['objects'] if o['id']=='title')['x']>.9:
            self.patch_count+=1
            value['layout']=[dict(id='title',x=.5 if self.fix and self.patch_count>=2 else 1.05,y=.08,width=.8,height=.08)]
        response['choices'][0]['message']['content']=json.dumps(value,ensure_ascii=False)
        return response


class InnerRepairTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.PipelineTests('test_full_pipeline_actual_render_visual_gate_and_cache')
        self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.f.job['input']['max_repairs']=2
        self.router=PrecheckRouter();self.f.session.client=self.router

    def generate(self):
        f=self.f
        return pipeline.generate(f.root,f.directory,f.session,f.context)

    def test_two_inner_patches_preserve_both_visual_repair_rounds(self):
        f=self.f;issue=dict(category='timing',beat_id='approach',object_ids=['B'],time=1,problem='Timing fixture',fix='Keep criterion')
        f.vision.reviews=[[issue],[issue],[]]
        path,extra=self.generate()
        phases=[x['phase'] for x in f.job['calls']]
        self.assertEqual(self.router.patch_count,2)
        self.assertIn('v2_patch_0_pre_1',phases);self.assertIn('v2_patch_0_pre_2',phases)
        self.assertIn('v2_patch_1',phases);self.assertIn('v2_patch_2',phases)
        self.assertEqual(len(f.vision.requests),3)
        self.assertEqual(store.read_json(f.directory/'quality.json')['repairs'],2)
        self.assertEqual(f.job['repair_round'],2)
        self.assertEqual(next(o for o in store.read_json(f.directory/'round_00/pre_render_00/design.json')['objects'] if o['id']=='title')['x'],1.1)
        self.assertEqual(next(o for o in store.read_json(f.directory/'round_00/design.json')['objects'] if o['id']=='title')['x'],.5)

    def test_resume_after_second_paid_patch_does_not_repeat_requests(self):
        f=self.f;original=pipeline._write
        def stop(path,data,**kwargs):
            if path.parent.name=='pre_render_02' and path.name=='design.json':
                raise store.WorkflowError('TEST_STOP','after paid patch')
            return original(path,data,**kwargs)
        with patch.object(pipeline,'_write',side_effect=stop):
            with self.assertRaisesRegex(store.WorkflowError,'TEST_STOP'):self.generate()
        self.assertEqual(self.router.patch_count,2);self.assertEqual(len(f.vision.requests),0)
        calls=len(self.router.requests)
        self.generate()
        self.assertEqual(len(self.router.requests),calls);self.assertEqual(len(f.vision.requests),1)
        calls=len(f.job['calls']);self.generate();self.assertEqual(len(f.job['calls']),calls)

    def test_uncertain_inner_request_never_retries_automatically(self):
        f=self.f;original=self.router.complete
        def lost(payload):
            props=payload['response_format']['json_schema']['schema']['properties']
            if 'layout' in props:raise TimeoutError('lost patch response')
            return original(payload)
        with patch.object(self.router,'complete',side_effect=lost):
            with self.assertRaisesRegex(store.WorkflowError,'API_RESULT_UNKNOWN'):self.generate()
            calls=len(f.job['calls'])
            with self.assertRaisesRegex(store.WorkflowError,'API_RESULT_UNKNOWN'):self.generate()
        self.assertEqual(len(f.job['calls']),calls)
        self.assertEqual(f.job['calls'][-1]['phase'],'v2_patch_0_pre_1')
        self.assertEqual(len(f.vision.requests),0)

    def test_exhausted_inner_loop_stops_without_spending_visual_rounds_or_rendering(self):
        f=self.f;self.router.fix=False
        with patch.object(pipeline.scene_render_v2,'render',side_effect=AssertionError('precheck must stop')):
            with self.assertRaisesRegex(store.WorkflowError,'SCENE_QUALITY_FAILED'):self.generate()
        self.assertEqual(self.router.patch_count,2);self.assertEqual(len(f.vision.requests),0)
        self.assertEqual(f.job['repair_round'],0)
        self.assertEqual(store.read_json(f.directory/'quality.json')['repairs'],0)
        self.assertEqual(store.read_json(f.directory/'round_00/result.json')['stage'],'pre_render')
        self.assertFalse((f.directory/'round_01').exists())
        calls=len(f.job['calls'])
        with self.assertRaisesRegex(store.WorkflowError,'SCENE_QUALITY_FAILED'):self.generate()
        self.assertEqual(len(f.job['calls']),calls)

    def test_explicit_zero_repairs_keeps_no_auto_repair_policy(self):
        self.f.job['input']['max_repairs']=0
        with self.assertRaisesRegex(store.WorkflowError,'SCENE_QUALITY_FAILED'):self.generate()
        self.assertEqual(self.router.patch_count,0)
        self.assertEqual(len(self.f.vision.requests),0)

    def test_immutable_stage_rejects_tampering_but_unpublished_pointer_can_be_replaced(self):
        f=self.f;original=pipeline._write
        def stop(path,data,**kwargs):
            if path.parent.name=='pre_render_01':raise store.WorkflowError('TEST_STOP','before stage write')
            return original(path,data,**kwargs)
        with patch.object(pipeline,'_write',side_effect=stop):
            with self.assertRaisesRegex(store.WorkflowError,'TEST_STOP'):self.generate()
        path=f.directory/'round_00/pre_render_00/design.json'
        value=store.read_json(path);value['purpose']='tampered';store.atomic_write(path,value)
        with self.assertRaisesRegex(store.WorkflowError,'CACHE_CORRUPT'):self.generate()
