"""Manim V2 fidelity, recovery and compatibility; no live AI."""
import copy
import math
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.scene_design import scene_state as state, scene_contract as contract
from src.scene_design import scene_render_v2 as renderer, scene_pipeline as pipeline
from src.scene_design import scene_generation as sg
from src.scene_design.design import DEFAULT_CONFIG
from src.shared import _workflow_store as store
from tests.scene_v2_support import obj, binding, design, vehicle, story
from tests import test_scene_v2 as fixtures


class StateTests(unittest.TestCase):
    def test_highlight_pulse_and_group_inheritance(self):
        beat=dict(action='highlight',start=1,duration=2,easing='linear')
        for t,scale,pulse in [(0,1,0),(1,1,0),(2,1.22,1),(3,1,0),(4,1,0)]:
            value=state.animation([beat],t)
            self.assertAlmostEqual(value['scale'],scale)
            self.assertAlmostEqual(value['highlight'],pulse)
        d=vehicle();d['timeline']=[dict(beat,object_id='car',dx=0,dy=0,value=0)]
        value=state.state_at(contract.compile_design(d),2)
        for item in value['objects'].values():
            self.assertAlmostEqual(item['scale'],1.22);self.assertEqual(item['highlight'],1)

    def test_wave_vertices_preserved_and_unshaped_fallback_has_endpoints_and_cycles(self):
        for oid,cycles in [('emitted_wave',5),('reflected_wave',9),('echo_wave',9)]:
            d=vehicle();d['timeline']=[];d['groups']=[]
            wave=obj(oid,'line',.5,.5,.6,.01);wave['points']=[[0,.5],[1,.5]]
            d['objects']=[wave];plan=contract.compile_design(d)
            points=state.state_at(plan,0)['objects'][oid]['points']
            self.assertEqual(len(points),65)
            self.assertEqual(points[0],[.2,.5]);self.assertEqual(points[-1],[.8,.5])
            self.assertGreaterEqual(max(p[1] for p in points)-min(p[1] for p in points),.05-1e-8)
            signs=[1 if p[1]>.5+1e-9 else -1 if p[1]<.5-1e-9 else 0 for p in points]
            signs=[s for s in signs if s]
            self.assertEqual(sum(a!=b for a,b in zip(signs,signs[1:])),2*cycles-1)
            wave['points']=[[0,.5],[.3,0],[.6,1],[1,.5]]
            plan=contract.compile_design(d);points=state.state_at(plan,0)['objects'][oid]['points']
            self.assertEqual(len(points),4)
            self.assertAlmostEqual(points[1][1],.495)
            self.assertAlmostEqual(points[2][1],.505)
        wave['id']='mathematical_plot';plan=contract.compile_design(d)
        self.assertEqual(len(state.state_at(plan,0)['objects']['mathematical_plot']['points']),4)


class BindingCollisionTests(unittest.TestCase):
    def test_radar_segment_tracks_world_endpoints_across_groups(self):
        d=vehicle();d['objects'] += [obj('radar','rectangle',.8,.25,.06,.06),obj('beam','line',.5,.5,0,0)]
        d['bindings']=[binding('tracking','line_through_points','beam',source_a='radar',source_b='body')]
        plan=contract.compile_design(d)
        for t in [0,.5,1.5,3]:
            v=state.state_at(plan,t)['objects']
            self.assertEqual(v['beam']['points'],[v['radar']['center'],v['body']['center']])
        wave=copy.deepcopy(d)
        wave['objects'][-1]['id']='emitted_wave'
        wave['bindings'][0]['target']='emitted_wave'
        v=state.state_at(contract.compile_design(wave),1.5)['objects']
        self.assertEqual(len(v['emitted_wave']['points']),65)
        self.assertEqual(v['emitted_wave']['points'][0],v['radar']['center'])
        self.assertEqual(v['emitted_wave']['points'][-1],v['body']['center'])
        grouped=copy.deepcopy(d)
        grouped['groups'].append(dict(id='beam_group',members=['beam']))
        grouped['timeline'].append(dict(object_id='beam_group',action='move',start=0,duration=1,dx=.1,dy=0,value=0,easing='linear'))
        with self.assertRaisesRegex(store.WorkflowError,'cannot transform independently'):contract.compile_design(grouped)
        d['timeline'].append(dict(object_id='beam',action='move',start=0,duration=1,dx=.1,dy=0,value=0,easing='linear'))
        with self.assertRaisesRegex(store.WorkflowError,'Bound geometry'):contract.compile_design(d)

    def test_background_tokens_do_not_hide_roadside_officer(self):
        d=vehicle()
        d['objects'].append(obj('road_surface','rectangle',.5,.56,.7,.12))
        self.assertTrue(contract.motion_report(contract.compile_design(d))['passed'])
        d['objects'].append(obj('roadside_officer','rectangle',.55,.5,.06,.10))
        issues=contract.motion_report(contract.compile_design(d))['issues']
        self.assertTrue(any('roadside_officer' in i['object_ids'] for i in issues))
        d['objects'].pop()
        d['timeline'].append(dict(object_id='road_surface',action='move',start=0,duration=3,dx=.01,dy=0,value=0,easing='linear'))
        self.assertFalse(contract.motion_report(contract.compile_design(d))['passed'])


class PolicyTests(unittest.TestCase):
    def test_limits_and_invalid_value_before_runtime_or_provider(self):
        self.assertEqual(sg.repair_limit(False),2)
        with patch.dict(os.environ,{},clear=True):self.assertEqual(sg.repair_limit(True),2)
        for text,expected in [('0',0),('1',1),('2',2),('3',3),('-2',0),('8',3)]:
            with patch.dict(os.environ,{'SCENE_TRIAL_REPAIRS':text}):
                self.assertEqual(sg.repair_limit(True),expected)
        with patch.dict(os.environ,{'SCENE_TRIAL_REPAIRS':'bad'}),patch('src.scene_design.manim_runtime.info') as runtime,patch.object(sg,'CLIENT_FACTORY') as client:
            with self.assertRaisesRegex(store.WorkflowError,'INVALID_INPUT'):sg.run_scenes('/unused',trial_mode=True)
        runtime.assert_not_called();client.assert_not_called()

    def test_old_input_is_retained_until_explicit_new_attempt(self):
        old=dict(stage='scene_design',scene_id='s1',model='model',design_prompt_version=4,max_repairs=0)
        new=dict(old,design_prompt_version=5,max_repairs=2)
        jobs=[dict(status='failed',input=old)]
        self.assertEqual(sg.resume_input(jobs,new,None),old)
        self.assertEqual(sg.resume_input(jobs,new,'new-attempt'),new)
        self.assertEqual(sg.resume_input(jobs,dict(new,model='other'),None),dict(new,model='other'))
        feedback=[dict(category='unsupported',fix='Keep the wave')]
        result=pipeline.repair_feedback(feedback,0,2)
        self.assertEqual(feedback[0]['category'],'unsupported')
        self.assertEqual(result[0]['category'],'relation')
        self.assertEqual(pipeline.repair_feedback(feedback,2,2),feedback)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.flow=fixtures.PipelineTests('test_full_pipeline_actual_render_visual_gate_and_cache')
        self.flow.setUp();self.addCleanup(self.flow.doCleanups)
        self.flow.job['input']['max_repairs']=2
        self.issue=dict(category='unsupported',beat_id='approach',object_ids=['B'],time=1,problem='Need supported drawing',fix='Keep the same criterion')

    def generate(self):
        f=self.flow
        return pipeline.generate(f.root,f.directory,f.session,f.context)

    def test_unsupported_repairs_and_keeps_critic_evidence_immutable(self):
        f=self.flow;f.vision.reviews=[[self.issue],[]]
        path,extra=self.generate()
        original=store.read_json(f.directory/'round_00/visual_review.json')
        self.assertEqual(original['issues'][0]['category'],'unsupported')
        self.assertEqual(f.router.design_requests[1]['issues'][0]['category'],'relation')
        self.assertTrue(path.exists());calls=len(f.job['calls'])
        self.generate();self.assertEqual(len(f.job['calls']),calls)

    def test_cached_unsupported_round_resumes_without_duplicate_calls(self):
        f=self.flow;f.vision.reviews=[[self.issue],[]]
        original=pipeline._design
        def stop(*args):
            if args[4]==1:raise store.WorkflowError('TEST_STOP','interruption')
            return original(*args)
        with patch.object(pipeline,'_design',side_effect=stop):
            with self.assertRaisesRegex(store.WorkflowError,'TEST_STOP'):self.generate()
        self.assertEqual(len(f.vision.requests),1)
        self.generate()
        self.assertEqual(len(f.vision.requests),2)
        self.assertEqual(len(f.router.stories),1)
        self.assertEqual(store.read_json(f.directory/'round_00/result.json')['issues'][0]['category'],'unsupported')

    def test_terminal_unsupported_persists_issues_and_does_not_repeat_requests(self):
        f=self.flow;f.vision.reviews=[[self.issue]]
        with self.assertRaisesRegex(store.WorkflowError,'SCENE_CAPABILITY_MISSING'):self.generate()
        self.assertEqual(len(f.vision.requests),3)
        self.assertEqual(f.job['quality_issues'][0]['category'],'unsupported')
        self.assertFalse(store.read_json(f.directory/'quality.json')['passed'])
        calls=len(f.job['calls'])
        with self.assertRaisesRegex(store.WorkflowError,'SCENE_CAPABILITY_MISSING'):self.generate()
        self.assertEqual(len(f.job['calls']),calls)


class RealFidelityTests(unittest.TestCase):
    def test_encoded_highlight_dot_outline_and_complete_path_audit(self):
        d=design(seconds=2)
        d.update(groups=[],parameters=[],parameter_tracks=[],graphs=[],bindings=[],timeline=[])
        wave=obj('emitted_wave','line',.5,.62,.6,.06)
        wave['points']=[[0,.5],[.25,0],[.5,1],[.75,0],[1,.5]]
        d['objects']=[obj('label','text',.5,.25,.7,.08,'Highlight'),obj('point','dot',.5,.43,.025,.045),
                      wave,obj('reflected_wave','line',.5,.78,.6,.015)]
        d['timeline']=[dict(object_id='label',action='highlight',start=0,duration=2,dx=0,dy=0,value=0,easing='linear')]
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);work=root/'draft';work.mkdir()
            config=copy.deepcopy(DEFAULT_CONFIG);config['render']['resolution']=[640,360]
            with patch.object(renderer,'run',wraps=renderer.run) as processes:
                ref,checks,manifest=renderer.render(root,work,{},d,dict(assets=[]),config,story(seconds=2))
            self.assertTrue(checks['passed'],checks);self.assertEqual(processes.call_count,1)
            rows={r['frame']:r for r in checks['states']}
            first=rows[0]['objects']['label'];middle=rows[12]['objects']['label']
            self.assertAlmostEqual(middle['width']/first['width'],1.22,places=5)
            self.assertEqual(first['fill_color'],'#56DFC5');self.assertEqual(middle['fill_color'],'#FACC15')
            dot=rows[12]['objects']['point']
            self.assertEqual(dot['stroke_color'],config['background']);self.assertEqual(dot['stroke_width'],2.5)
            self.assertEqual(rows[12]['objects']['emitted_wave']['path_point_count'],5)
            self.assertEqual(rows[12]['objects']['reflected_wave']['path_point_count'],65)
            plan=contract.compile_design(d);frames=[r['frame'] for r in checks['states']]
            bad=copy.deepcopy(checks['states'])
            bad[0]['objects']['emitted_wave']['path_samples'][1]['point'][1]+=.01
            self.assertFalse(renderer._audit(plan,bad,frames,config['render']['fps'])['passed'])
            bad=copy.deepcopy(checks['states']);bad[0]['objects']['emitted_wave']['path_point_count']=2
            self.assertFalse(renderer._audit(plan,bad,frames,config['render']['fps'])['passed'])


class WorkerBoundaryTests(unittest.TestCase):
    def test_duration_padding_does_not_hide_a_deficit_greater_than_five_seconds(self):
        from src.scene_design.render import render_scene
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'short.py'
            source.write_text("""from manim import *
class GeneratedScene(Scene):
    def construct(self):
        self.add(Dot())
        self.wait(0)
        self.wait(.25)
""")
            result=render_scene(root,'short','v001',source,'GeneratedScene',
                                dict(quality='l',resolution=[320,180],fps=12,target_duration=6))
            self.assertEqual(result['status'],'success',result)
            metadata=store.read_json(Path(result['log_path']).with_suffix('.json'))
            self.assertAlmostEqual(metadata['video']['duration_seconds'],.25,delta=1/12)
