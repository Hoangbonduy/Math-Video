"""Reproduce the saved image/text failure and repair it through an actual Manim render."""
import json,shutil,unittest
from pathlib import Path
from unittest.mock import patch
from src.shared import workflow as wf
from src.scene_design import scene_generation as sg
from src.shared import _workflow_store as store
from src.shared.media_tools import validate
from src.scene_design.render import render_scene
from tests import test_scenes as fixtures
from tests.test_design_agent_contract import RepairRouter


def repaired_design(context):
    def obj(id,kind,x,y,width,height,label=''):
        return dict(id=id,kind=kind,label=label,x=x,y=y,width=width,height=height,color='#56DFC5',asset_id='',points=[])
    objects=[obj('car','rectangle',.3,.70,.4,.16),obj('wheel_left','circle',.18,.81,.045,.08),
             obj('wheel_right','circle',.42,.81,.045,.08),obj('speedometer_face','circle',.78,.32,.18,.32),
             obj('speedometer_needle','line',.78,.32,.07,.08),obj('speed_text','text',.78,.54,.25,.07,'40 km/h')]
    seconds=context['target_seconds']
    return dict(scene_id=context['scene']['scene_id'],narration=context['scene']['narration'],purpose='Xe chuyển động và đồng hồ tốc độ',duration=seconds,
                objects=objects,timeline=[dict(object_id=id,action='move',start=.6,duration=(seconds-.6)*.6,dx=.25,dy=0) for id in ('car','wheel_left','wheel_right')],
                math=[],fallback_note='Vẽ thân xe bằng hình chữ nhật và bánh xe bằng hình tròn; không dùng ảnh ngoài.')


def source(context,guarded=True):
    duration=context['approved_design']['duration']
    width=8*context['config']['render']['resolution'][0]/context['config']['render']['resolution'][1]
    background=context['config']['background']
    code=f'''from manim import *
import numpy as np
class GeneratedScene(Scene):
    def construct(self):
        duration = {duration!r}
        width = {width!r}
        self.camera.background_color = {background!r}
        title = Text("Tốc độ tại một khoảnh khắc", font="DejaVu Sans", font_size=30).to_edge(UP, buff=0.4)
        body = RoundedRectangle(width=width*.4, height=8*.16, corner_radius=.12, color="#56DFC5", fill_opacity=.35).move_to([width*(.3-.5),8*(.5-.70),0])
        left = Circle(radius=.32,color="#F2F6FC",fill_opacity=.4).move_to([width*(.18-.5),8*(.5-.81),0])
        right = Circle(radius=.32,color="#F2F6FC",fill_opacity=.4).move_to([width*(.42-.5),8*(.5-.81),0])
        car = VGroup(body,left,right)
        center = np.array([width*(.78-.5),8*(.5-.32),0])
        dial = Circle(radius=width*.09,color="#F2F6FC").move_to(center)
        speed = ValueTracker(40)
        needle = always_redraw(lambda: Line(center,center+.95*np.array([np.cos(np.pi*(1-speed.get_value()/100)),np.sin(np.pi*(1-speed.get_value()/100)),0]),color="#56DFC5"))
        reading = always_redraw(lambda: Text(f"{{speed.get_value():.0f}} km/h",font="DejaVu Sans",font_size=26,color="#F2F6FC").move_to([center[0],8*(.5-.54),0]))
        self.play(FadeIn(title),FadeIn(car),Create(dial),FadeIn(reading),run_time=.6)
        self.add(needle)
        self.play(car.animate.shift(RIGHT*width*.25),speed.animate.set_value(80),run_time=(duration-.6)*.6,rate_func=linear)
        self.play(speed.animate.set_value(45),run_time=(duration-.6)*.4,rate_func=linear)
        remaining=duration-self.time
        if remaining>1e-6:
            self.wait(remaining)
'''
    if not guarded:
        code=code.replace('        remaining=duration-self.time\n        if remaining>1e-6:\n            self.wait(remaining)','        self.wait(max(0,duration-self.time))')
    return code


class CarRepairRouter(RepairRouter):
    def __init__(self):
        super().__init__();self.code_calls=0
    def complete(self,payload):
        response=super().complete(payload)
        properties=payload['response_format']['json_schema']['schema']['properties']
        context=json.loads(payload['messages'][1]['content'])
        if 'objects' in properties and self.design_calls>2:
            response['choices'][0]['message']['content']=json.dumps(repaired_design(context),ensure_ascii=False)
        if 'source' in properties:
            self.code_calls+=1
            response['choices'][0]['message']['content']=json.dumps(dict(source=source(context,guarded=self.code_calls>1),requires_design_change=False,reason=''),ensure_ascii=False)
        return response


class AgentReplayRenderTests(unittest.TestCase):
    def test_saved_bad_candidates_can_resume_to_a_real_manim_movie(self):
        import av
        import numpy as np
        flow=fixtures.SceneFlowTests('test_two_topics_different_scene_counts_and_all_gates')
        flow.setUp();self.addCleanup(flow.doCleanups);flow.approved(1)
        router=CarRepairRouter()
        patch('src.scene_design.scene_generation.CLIENT_FACTORY',return_value=router).start()
        patch('src.scene_design.scene_generation.RENDERER',side_effect=render_scene).start()
        patch('src.shared.openrouter_client.OpenRouterClient._send',side_effect=AssertionError('Live AI prohibited in replay')).start()
        with patch('src.scene_design.scene_generation.DESIGN_REPAIR_LIMIT',1):
            self.assertRaisesRegex(store.WorkflowError,'Unknown image',wf.run_workflow,flow.root,ai=True,scene_id='scene_1')
        state=wf.run_workflow(flow.root,ai=True,scene_id='scene_1')
        artifact=state['pending_reviews'][0];rendered=artifact['manim_render'];video=flow.root/rendered['path']
        metadata=validate(video,audio=False,resolution=[640,360],fps=12)
        self.assertAlmostEqual(float(metadata['format']['duration']),30,delta=2/12+1e-6)
        self.assertEqual(artifact['render_engine'],'manim')
        self.assertEqual(router.design_calls,3)
        self.assertEqual(len(router.requests),4)
        self.assertEqual(router.code_calls,0)
        self.assertEqual(rendered['preview_engine'],'json')
        self.assertEqual(sg._jobs(flow.root)[0]['status'],'awaiting_review')
        with av.open(str(video)) as movie:
            frames=[f.to_ndarray(format='rgb24') for i,f in enumerate(movie.decode(video=0)) if i in (24,240)]
        self.assertEqual(len(frames),2)
        self.assertGreater(float(np.mean(np.abs(frames[0].astype(float)-frames[1].astype(float)))),.03)
        evidence=Path(__file__).resolve().parents[1]/'output/json_preview_validation';evidence.mkdir(exist_ok=True)
        for key in ('path','code_path','log_path','metadata_path'):
            shutil.copy2(flow.root/rendered[key],evidence/Path(rendered[key]).name)
        store.atomic_write(evidence/'replay-result.json',dict(live_ai=False,real_manim=True,design_repairs=2,code_repairs=0,requests=4,render=rendered,review='pending'))
