"""Acceptance regressions for the five-phase Manim SDK upgrade (offline AI)."""
import copy
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import av
import numpy as np
from PIL import Image
from src.scene_design import scene_contract as contract
from src.scene_design import scene_render_v2 as renderer
from src.scene_design import manim_kit as kit
from src.scene_design.design import DEFAULT_CONFIG, validate_code
from src.scene_design.render import render_scene
from src.shared import _workflow_store as store
from tests.scene_v2_support import obj, vehicle, story, binding


def roadside_scene():
    d=vehicle(seconds=2)
    for o in d['objects']:
        o['y']+=.20
    d['objects'][0]['id']='car_body'
    d['groups'][0]['members'][0]='car_body'
    road=obj('road','rectangle',.5,.76,.94,.20);road['color']='#1C2E4A'
    d['objects'].insert(0,road)
    d['objects'] += [
        obj('officer','rectangle',.58,.39,.04,.14),
        obj('radar','rectangle',.62,.36,.045,.03),
    ]
    d['groups'].append(dict(id='observer',members=['officer','radar']))
    return d


class CollisionAcceptanceTests(unittest.TestCase):
    def test_separate_lanes_pass_and_crossing_officer_fails_before_render(self):
        d=roadside_scene()
        start=time.perf_counter()
        report=contract.motion_report(contract.compile_design(d))
        self.assertTrue(report['passed'],report)
        self.assertLess(time.perf_counter()-start,.5)
        next(o for o in d['objects'] if o['id']=='officer')['y']=.70
        report=contract.motion_report(contract.compile_design(d))
        self.assertFalse(report['passed'])
        self.assertTrue(any('officer' in issue['object_ids'] and 'collides' in issue['problem']
                            for issue in report['issues']))
        # Explicit overlap exceptions and hidden objects remain supported.
        next(o for o in d['objects'] if o['id']=='officer')['overlap_with']=['car_body','wheel1','wheel2']
        self.assertTrue(contract.motion_report(contract.compile_design(d))['passed'])

    def test_position_bound_vehicle_is_checked_and_nested_parts_are_exempt(self):
        d=roadside_scene()
        d['timeline']=[]
        d['parameters']=[dict(id='progress',value=0,min=0,max=2)]
        d['parameter_tracks']=[dict(parameter='progress',start=0,duration=2,**{'from':0,'to':2},easing='linear')]
        d['bindings']=[binding('moving','position','car_body',x_expression='.35+.12*progress',y_expression='.39')]
        self.assertFalse(contract.motion_report(contract.compile_design(d))['passed'])
        d=vehicle();d['groups'].append(dict(id='whole',members=['car']))
        self.assertTrue(contract.motion_report(contract.compile_design(d))['passed'])


class SDKAcceptanceTests(unittest.TestCase):
    def test_vietnamese_text_shrinks_and_axes_do_not_need_latex(self):
        text=kit.vn_text('Toc do thay doi',max_width=1,max_height=.2)
        self.assertLessEqual(text.width,1.00001)
        self.assertLessEqual(text.height,.20001)
        axes,grid=kit.make_axes_with_grid((0,4,1),(0,16,4),x_ticks=(0,2,4),y_ticks=(4,8))
        np.testing.assert_allclose(axes.c2p(0,0),(1.10,-1.23,0))
        self.assertGreater(len(grid),0)
        for prefab in (kit.make_car(),kit.make_road(),kit.make_radar_officer(),
                       kit.make_panel(6,4,(0,0,0),'Panel','Subtitle'),
                       kit.make_metric_card(0,0,'TIME','t = 2 s')):
            self.assertGreater(prefab.width,0)

    def test_sdk_imports_allowed_without_relaxing_other_ast_restrictions(self):
        for module in ('manim_kit','src.scene_design.manim_kit'):
            source=f'from manim import Scene\nfrom {module} import *\nclass GeneratedScene(Scene):\n    def construct(self):\n        self.add(make_car())\n'
            self.assertEqual(validate_code(source,[]),source)
            for operation in ('import os','from pathlib import Path','open("file")',
                              'from .manim_kit import make_car','self.__dict__'):
                bad=source+'\n'+operation
                with self.assertRaises(store.WorkflowError):validate_code(bad,[])

    def test_worker_both_sdk_import_paths_wait_guards_and_duration_padding(self):
        for module in ('manim_kit','src.scene_design.manim_kit'):
            with self.subTest(module=module),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);source=root/'generated.py'
                source.write_text(f"""from manim import *
from {module} import make_car, make_radar_officer, make_road
class GeneratedScene(Scene):
    def construct(self):
        self.camera.background_color = '#0D1726'
        self.add(make_road())
        car=make_car().move_to([-3,0,0])
        self.add(car,make_radar_officer().move_to([0,2,0]))
        self.wait(0)
        self.wait(-1)
        self.wait(None)
        self.play(car.animate.shift(RIGHT),run_time=.75)
""")
                config=dict(quality='l',resolution=[320,180],fps=12,target_duration=1)
                result=render_scene(root,'scene_01','v001',source,'GeneratedScene',config)
                self.assertEqual(result['status'],'success',result)
                metadata=store.read_json(Path(result['log_path']).with_suffix('.json'))
                self.assertAlmostEqual(metadata['video']['duration_seconds'],1,delta=1/12)


class EvidenceAcceptanceTests(unittest.TestCase):
    def test_actual_vehicle_render_extracts_all_frames_in_one_ffmpeg_process(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);work=root/'round_00';work.mkdir()
            style=copy.deepcopy(DEFAULT_CONFIG);style['render']['resolution']=[640,360]
            with patch.object(renderer,'run',wraps=renderer.run) as process:
                ref,checks,manifest=renderer.render(root,work,{},roadside_scene(),dict(assets=[]),style,story(seconds=2))
            self.assertTrue(checks['passed'],checks)
            self.assertEqual(process.call_count,1)
            self.assertGreater(len(manifest['frames']),3)
            frame_map={r['frame']:r for r in manifest['frames']}
            with av.open(str(root/ref['path'])) as movie:
                for index,frame in enumerate(movie.decode(video=0)):
                    if index in frame_map:
                        with Image.open(root/frame_map[index]['path']) as extracted:
                            delta=np.abs(np.asarray(extracted).astype(float)-frame.to_ndarray(format='rgb24').astype(float))
                            self.assertLess(delta.mean(),1)
            self.assertFalse(list((work/'frames').glob('.batch-*')))

    def test_empty_frames_skip_ffmpeg_and_missing_last_frame_cannot_reuse_stale_png(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);work=root/'draft';work.mkdir()
            store.atomic_write(root/'metadata.json',dict(video=dict(design_audit=[])))
            d=vehicle();plan=contract.compile_design(d)
            ref=dict(metadata_path='metadata.json',path='unused.mp4',sha256='unused')
            with patch.object(renderer,'run') as process:
                checks,manifest=renderer.evidence(root,work,ref,d,story(),plan,[],DEFAULT_CONFIG)
            process.assert_not_called();self.assertEqual(manifest['frames'],[])
            # Simulate a batch missing the last frame, then a successful fallback.
            def extract(args):
                output=Path(args[-1])
                if 'batch_' in output.name:
                    Image.new('RGB',(4,4)).save(output.parent/'batch_00001.png')
                else:
                    Image.new('RGB',(4,4),'red').save(output)
            with patch.object(renderer,'run',side_effect=extract) as process:
                _,manifest=renderer.evidence(root,work,ref,d,story(),plan,[1,0,1],DEFAULT_CONFIG)
            self.assertEqual(process.call_count,2)
            self.assertEqual([r['frame'] for r in manifest['frames']],[0,1])
            with patch.object(renderer,'run'):
                with self.assertRaisesRegex(store.WorkflowError,'No encoded video frame'):
                    renderer.evidence(root,work,ref,d,story(),plan,[0],DEFAULT_CONFIG)


class PipelineAssetAcceptanceTests(unittest.TestCase):
    def test_default_and_shared_assets_skip_llm_but_opt_in_keeps_search(self):
        from src.scene_design import scene_pipeline as pipeline
        from src.shared.job_session import Session
        from tests.scene_v2_support import DesignRouter, VisionRouter
        cases=[('0',None,False),('true',None,True),
               ('true',dict(assets=[],failures=[],fallback_required=False,source='shared'),False)]
        for enabled,shared,expected_call in cases:
            with self.subTest(enabled=enabled,shared=shared),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);router=DesignRouter();vision=VisionRouter()
                job=dict(job_id='assets',input={},calls=[],status='working',pricing=router.preflight(search=False))
                context=dict(scene=dict(scene_id='scene_1',narration=story()['narration']),
                             target_seconds=3,config=copy.deepcopy(DEFAULT_CONFIG),script={},brief={})
                if shared is not None:context['shared_assets']=shared
                with patch.dict('os.environ',dict(AI_BACKEND='openrouter',
                        OPENROUTER_MODEL='anthropic/claude-haiku-5.5',
                        SCENE_REVIEW_MODEL=vision.model,ENABLE_WIKIMEDIA_ASSETS=enabled)), \
                     patch.object(pipeline.scene_vision,'CLIENT_FACTORY',return_value=vision), \
                     patch.object(pipeline.scene_render_v2,'render',side_effect=store.WorkflowError('TEST_STOP','before render')):
                    with self.assertRaisesRegex(store.WorkflowError,'TEST_STOP'):
                        pipeline.generate(root,root,Session(root,job,router,budget_usd=.5),context)
                self.assertEqual('v2_assets' in [c['phase'] for c in job['calls']],expected_call)
                cached=store.read_json(root/'v2_assets.json')
                self.assertEqual(cached,shared or dict(assets=[],failures=[],fallback_required=expected_call))
