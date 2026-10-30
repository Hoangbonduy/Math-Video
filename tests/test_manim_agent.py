"""Real Manim integration with an offline model provider; never uses live AI."""
import copy
import json
import os
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

from src.shared import workflow as wf
from src.shared import _workflow_store as store
from src.scene_design.render import render_scene
from src.shared.review import record_review
from src.shared.media_tools import probe, validate
from src.scene_design.design import validate_design
from src.scene_design.manim_runtime import info
from tests import test_scenes as scene_fixtures
from tests.test_scenes import SceneRouter
from src.shared.ai_config import MODEL


def motion_code(context):
    duration=context['approved_design']['duration']
    width=8*context['config']['render']['resolution'][0]/context['config']['render']['resolution'][1]
    background=context['config']['background']
    return f'''from manim import *
class GeneratedScene(Scene):
    def construct(self):
        duration = {duration!r}
        self.camera.background_color = {background!r}
        title = Text("Tốc độ thay đổi", font="DejaVu Sans", font_size=30)
        title.scale_to_fit_width({width!r} * 0.75).to_edge(UP, buff=0.3)
        axes = Axes(x_range=[0,3,1], y_range=[0,9,3], x_length={width!r}*0.72,
                    y_length=4.4, tips=False, axis_config={{"include_numbers":False,"stroke_width":2}})
        axes.move_to([0,-0.4,0])
        graph = axes.plot(lambda x: x*x, x_range=[0.2,2.8], color=BLUE_C)
        value = ValueTracker(2.6)
        fixed = Dot(axes.c2p(0.8,0.64), color=YELLOW)
        moving = always_redraw(lambda: Dot(axes.c2p(value.get_value(),value.get_value()**2),radius=0.08,color=TEAL_A))
        secant = always_redraw(lambda: Line(axes.c2p(0.8,0.64),axes.c2p(value.get_value(),value.get_value()**2),color=ORANGE))
        formula = Text("f(t) = t²",font="DejaVu Sans",font_size=24).to_edge(DOWN,buff=0.35)
        self.play(Create(axes),FadeIn(title),FadeIn(formula),run_time=0.8)
        self.play(Create(graph),FadeIn(fixed),run_time=0.8)
        self.add(secant,moving)
        self.play(value.animate.set_value(0.85),run_time=duration-2.6,rate_func=linear)
        self.wait(max(0.01,duration-self.time))
'''


class OfflineManimRouter(SceneRouter):
    def __init__(self):
        super().__init__()
        self.fail_first=False
        self.code_calls=0

    def complete(self,payload):
        props=payload['response_format']['json_schema']['schema']['properties']
        if 'source' not in props:
            return super().complete(payload)
        self.requests.append(copy.deepcopy(payload));self.code_calls+=1
        context=json.loads(payload['messages'][1]['content'])
        source=motion_code(context)
        if self.fail_first and self.code_calls==1:
            source="from manim import Scene\nclass GeneratedScene(Scene):\n    def construct(self):\n        raise ValueError('intentional render failure for repair validation')\n"
        return dict(id='offline-code',model=MODEL,usage=dict(cost=.0001,prompt_tokens=100,completion_tokens=200),
            choices=[dict(finish_reason='stop',message=dict(content=json.dumps(dict(source=source,requires_design_change=False,reason=''),ensure_ascii=False)))])


class ManimAgentTests(unittest.TestCase):
    def setUp(self):
        self.flow=scene_fixtures.SceneFlowTests('test_two_topics_different_scene_counts_and_all_gates')
        self.flow.setUp();self.addCleanup(self.flow.doCleanups)
        self.root=self.flow.root
        self.router=OfflineManimRouter()
        patch('src.scene_design.scene_generation.CLIENT_FACTORY',return_value=self.router).start()
        patch('src.scene_design.scene_generation.RENDERER',side_effect=render_scene).start()
        patch('src.shared.openrouter_client.OpenRouterClient._send',side_effect=AssertionError('Live AI is forbidden in this test')).start()

    def export(self,state,name):
        if os.environ.get('SAVE_MANIM_VALIDATION')!='1':
            return
        output=Path(__file__).resolve().parents[1]/'data/manim_agent_validation'/name
        output.mkdir(parents=True,exist_ok=True)
        entries=[]
        for artifact in state['artifacts']:
            rendered=artifact.get('manim_render')
            if not rendered:
                continue
            for key in ('path','code_path','log_path','metadata_path'):
                source=self.root/rendered[key]
                shutil.copy2(source,output/source.name)
            entries.append(dict(scene_id=artifact['scene_id'],design_version=artifact['version'],render=rendered))
        store.atomic_write(output/'validation.json',dict(live_ai=False,real_manim=True,runtime=info(),entries=entries))

    def test_actual_manim_landscape_and_portrait_render_before_review(self):
        import av
        import numpy as np
        from src.idea.service import apply_idea
        for portrait in (False,True):
            with self.subTest(portrait=portrait):
                self.root=Path(self.flow.temp.name)/('portrait' if portrait else 'landscape')
                self.flow.root=self.root
                self.flow.approved(2,topic='Đồ thị và tiếp tuyến')
                if portrait:
                    config=dict(render=dict(quality='l',resolution=[360,640],fps=12,timeout_seconds=300),
                                background='#0D1726',foreground='#F2F6FC',accent='#56DFC5',font='DejaVu Sans')
                    store.atomic_write(self.root/'scene_config.json',config)
                state=wf.run_workflow(self.root,ai=True)
                self.assertEqual(state['stage'],'scene_design')
                self.assertEqual(state['status'],'awaiting_review')
                self.assertEqual(len(state['pending_reviews']),2)
                hashes={}
                for item in state['pending_reviews']:
                    rendered=item['manim_render'];path=self.root/rendered['path']
                    target=store.read_json(Path(item['path']))['duration']
                    data=validate(path,audio=False,resolution=[360,640] if portrait else [640,360],fps=12)
                    self.assertAlmostEqual(float(data['format']['duration']),target,delta=2/12)
                    self.assertEqual(rendered['runtime']['renderer'],'cairo')
                    self.assertIn('/video/',rendered['runtime']['python'])
                    self.assertEqual(rendered['runtime']['version'],info()['version'])
                    with av.open(str(path)) as movie:
                        frames=[frame.to_ndarray(format='rgb24') for i,frame in enumerate(movie.decode(video=0)) if i in (36,120)]
                    self.assertEqual(len(frames),2)
                    self.assertGreater(float(np.mean(np.abs(frames[0].astype(float)-frames[1].astype(float)))),0.03)
                    hashes[item['scene_id']]=rendered['sha256']
                    record_review(self.root,'scene_design',item['version'],'approve',scene_id=item['scene_id'],
                                  expected_preview_sha256=state['design_previews'][item['scene_id']+':'+item['version']]['sha256'])
                self.export(state,'portrait' if portrait else 'landscape')
                with patch('src.scene_design.scene_generation.CLIENT_FACTORY',side_effect=AssertionError('Promotion may not call AI')):
                    state=wf.run_workflow(self.root,ai=True)
                self.assertEqual(state['stage'],'scene_clip')
                for item in state['pending_reviews']:
                    self.assertTrue(item['reused_design_render'])
                    self.assertEqual(item['sha256'],hashes[item['scene_id']])

    def test_explicit_code_revision_can_still_use_agent_repair(self):
        from src.scene_design import scene_generation as sg
        from src.shared.job_session import Session
        from src.scene_design.manim_agent import render_candidate
        self.flow.approved(1)
        item=wf.run_workflow(self.root,ai=True)['pending_reviews'][0]
        self.assertEqual(self.router.code_calls,0) # initial preview is compiled locally
        directory=self.root/'generation/code_revision_test';directory.mkdir()
        job=dict(job_id='code_revision_test',calls=[],status='working')
        session=Session(directory,job,self.router,budget_usd=.5)
        job['pricing']=self.router.preflight(search=False)
        design=store.read_json(Path(item['path']))
        context=dict(approved_design=design,assets=dict(assets=[]),config=sg._config(self.root),
                     scene=dict(scene_id=item['scene_id']),design_candidate=False)
        self.router.fail_first=True
        video,rendered=render_candidate(self.root,directory,session,context)
        self.assertEqual(rendered['clip_version'],'v003')
        self.assertIn('intentional render failure',(self.root/'logs/scene_1_v002.log').read_text())
        repair=self.router.requests[-1]['messages'][-1]['content']
        self.assertIn('ValueError',repair)
        self.assertEqual(self.router.code_calls,2)
        validate(video,audio=False,resolution=[640,360],fps=12)
        calls=len(self.router.requests)
        render_candidate(self.root,directory,session,context)
        self.assertEqual(len(self.router.requests),calls)

    def test_missing_video_environment_fails_before_paid_calls(self):
        self.flow.approved(1)
        calls=len(self.router.requests)
        with patch.dict(os.environ,{'MANIM_PYTHON':'/nonexistent/python'}):
            with self.assertRaisesRegex(store.WorkflowError,'DEPENDENCY_ERROR'):
                wf.run_workflow(self.root,ai=True)
        self.assertEqual(len(self.router.requests),calls)


class DesignLineTests(unittest.TestCase):
    def test_horizontal_and_vertical_lines_are_valid_and_identified_errors_are_clear(self):
        base=dict(scene_id='s',narration='Lời đọc',purpose='Hai trục',duration=5,objects=[],timeline=[],math=[],fallback_note='')
        def line(**kwargs):
            return dict(id='axis',kind='line',label='',x=.5,y=.5,width=.8,height=0,color='#FFFFFF',asset_id='',points=[],**kwargs)
        scene=dict(scene_id='s',narration='Lời đọc')
        for width,height in ((.8,0),(0,.8)):
            base['objects']=[dict(line(),width=width,height=height)]
            validate_design(base,scene,5,[])
        base['objects']=[dict(line(),x=.99)]
        with self.assertRaisesRegex(store.WorkflowError,'Object axis outside frame'):
            validate_design(base,scene,5,[])
        base['objects']=[dict(line(),width=0,height=0)]
        with self.assertRaisesRegex(store.WorkflowError,'Invalid size for axis'):
            validate_design(base,scene,5,[])
