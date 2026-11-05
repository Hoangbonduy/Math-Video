"""Real portrait renders with deterministic fake planning/code/review providers."""
import copy
import json
import tempfile
from pathlib import Path
from unittest.mock import patch
import pytest
from src.scene_design import scene_code_pipeline as pipeline, scene_theme, scene_generation as sg
from src.scene_design.render import _settings
from src.scene_design.scene_telemetry import select_frames
from src.shared import _workflow_store as store
from src.shared.ai_config import get_model
from src.shared.job_session import Session
from tests.ai_support import FakeRouter
from tests.scene_v2_support import VisionRouter, story

SOURCE = """from manim import *
from manim_kit import vn_text, track, finish_scene
class GeneratedScene(Scene):
    def construct(self):
        self.camera.background_color = '#0D1726'
        title = track(self, 'title', vn_text('Chuyển động', 28).move_to([0,5,0]), 'text')
        point = track(self, 'point', Dot([-1,0,0], color='#56DFC5'), 'shape')
        self.add(title, point)
        self.play(point.animate.shift(2*RIGHT), run_time=2, rate_func=linear)
        finish_scene(self, 3)
"""


def visual_plan():
    plan=story(narration='Một điểm chuyển động từ trái sang phải.')
    plan['beats'][0].update(show='Một điểm và tiêu đề.',motion='Điểm di chuyển sang phải.',
        understand='Theo dõi chuyển động.',criteria=['Điểm di chuyển sang phải và luôn nằm trong vùng an toàn.'],
        required_capabilities=['Dot','Text'])
    plan.update(objects=[dict(id='title',role='text'),dict(id='point',role='shape')],
        recipe_ids=['explanatory_motion'],required_apis=['Dot','Text'])
    return plan


class CodeRouter(FakeRouter):
    def __init__(self, sources=None):
        super().__init__();self.sources=sources or [SOURCE];self.code_count=0
    def complete(self,payload):
        self.requests.append(copy.deepcopy(payload))
        props=payload['response_format']['json_schema']['schema']['properties']
        if 'summary' in props:
            context=json.loads(payload['messages'][1]['content'])
            sid=next(iter(context['snapshot']['scenes']))
            result=dict(summary='Sửa mã cảnh.',categories=['code'],scenes=[sid],resources=[],resource_updates=[],outside_scope=False)
        elif 'beats' in props:
            result=visual_plan()
        elif 'source' in props:
            result=dict(source=self.sources[min(self.code_count,len(self.sources)-1)],requires_design_change=False,reason='')
            self.code_count+=1
        else:
            raise AssertionError(props)
        return dict(id='direct-'+str(len(self.requests)),model=get_model(),
            usage=dict(prompt_tokens=100,completion_tokens=100,cost=.0001),
            choices=[dict(finish_reason='stop',message=dict(content=json.dumps(result,ensure_ascii=False)))])


def run_pipeline(root, router=None, repairs=2):
    router=router or CodeRouter();vision=VisionRouter()
    directory=root/'generation'/'direct';directory.mkdir(parents=True)
    job=dict(job_id='direct',input=dict(stage='scene_design',max_repairs=repairs),calls=[],status='working',pricing=router.preflight())
    session=Session(directory,job,router,None,budget_usd=.5,other_used=0)
    context=dict(scene=dict(scene_id='scene_1',narration=visual_plan()['narration']),
        target_seconds=3,config=copy.deepcopy(sg.DEFAULT_CONFIG),script={},brief={})
    with patch('src.scene_design.scene_vision.CLIENT_FACTORY',return_value=vision), \
         patch('src.scene_design.scene_vision.review_model',return_value=vision.model):
        result=pipeline.generate(root,directory,session,context)
    return result,session,context,vision


def test_preview_and_final_have_identical_logical_camera():
    a,_=_settings(scene_theme.render_profile(sg.DEFAULT_CONFIG['render']))
    b,_=_settings(scene_theme.render_profile(sg.DEFAULT_CONFIG['render'],final=True))
    assert (a['frame_width'],a['frame_height'])==(9,16)==(b['frame_width'],b['frame_height'])
    assert a['frame_rate']==b['frame_rate']==30
    assert b['pixel_width']==a['pixel_width']*3
    assert b['pixel_height']==a['pixel_height']*3


def test_frame_budget_prioritizes_actual_collision():
    telemetry=dict(fps=30,issues=[dict(time=1.2,problem='Rendered text bounding boxes overlap')],
        events=[dict(middle_frame=15,end_frame=29),dict(middle_frame=44,end_frame=59)])
    frames=select_frames(telemetry,90)
    assert len(frames)<=5
    assert {0,36,89}<=set(frames)


def test_real_direct_pipeline_and_native_final(tmp_path):
    (path,extra),session,context,vision=run_pipeline(tmp_path)
    assert extra['design_pipeline']==3 and extra['quality_passed']
    ref=extra['manim_render']
    assert (ref['video']['width'],ref['video']['height'],ref['video']['fps'])==(360,640,30)
    assert 'layout_telemetry' not in ref['video']
    work=path.parent/'round_00'
    telemetry=store.read_json(work/'layout_telemetry.json')
    assert telemetry['checked_frames']>=89
    assert telemetry['events'][0]['end_frame']>=58
    assert telemetry['early_motion']
    assert telemetry['object_ids']==['point','title']
    evidence=store.read_json(work/'evidence.json')
    assert 3<=len(evidence['frames'])<=5
    assert len(vision.requests)==1
    artifact=dict(extra,path=str(path.relative_to(tmp_path)),sha256=store.digest(path))
    output=pipeline.render_final(tmp_path,artifact,context['config'])
    assert output['status']=='success',output
    meta=store.read_json(Path(output['log_path']).with_suffix('.json'))
    assert (meta['video']['width'],meta['video']['height'],meta['video']['fps'])==(1080,1920,30)
    assert meta['render_config']['resolution']==[1080,1920]
    assert meta['scene_file']==str(tmp_path/ref['code_path'])
    # Final evidence is validated without changing reviewed source or its hash.
    assert store.digest(tmp_path/ref['code_path'])==ref['code_sha256']
    before=len(session.job['calls'])
    with patch('src.scene_design.scene_vision.CLIENT_FACTORY',side_effect=AssertionError('No new call on replay')):
        assert pipeline.generate(tmp_path,path.parent,session,context)[0]==path
    assert len(session.job['calls'])==before


def test_layout_feedback_repairs_code_without_replanning(tmp_path):
    bad=SOURCE.replace('.move_to([0,5,0])','.move_to([4.4,5,0])')
    router=CodeRouter([bad,SOURCE])
    (path,extra),session,context,vision=run_pipeline(tmp_path,router)
    phases=[c['phase'] for c in session.job['calls']]
    assert phases.count('v3_plan_0')==1 and 'v3_plan_1' not in phases
    assert 'v3_code_1' in phases
    assert extra['quality_passed']
    assert store.read_json(path.parent/'round_00'/'result.json')['issues']


def test_plan_preserves_approved_duration(tmp_path):
    context=dict(scene=dict(scene_id='scene_1',narration=visual_plan()['narration']),target_seconds=3)
    plan=visual_plan()
    pipeline.validate_plan(plan,context)
    plan['duration']=4
    with pytest.raises(store.WorkflowError):pipeline.validate_plan(plan,context)


def test_formula_fit_rejects_unreadable_shrinking():
    from manim import Rectangle
    from src.scene_design.manim_kit import fit_to_safe_area
    with pytest.raises(ValueError,match='LAYOUT_REPLAN_REQUIRED'):
        fit_to_safe_area(Rectangle(width=20,height=2),min_scale=.8)


def test_new_workflow_human_gates_native_av_and_cached_calls(tmp_path):
    from tests.test_scene_v2_workflow import approved_project
    from tests.test_av import wave_bytes
    from src.shared import workflow as wf
    from src.shared.review import record_review
    from src.preview import av_workflow as av
    from src.shared.media_tools import probe
    root=approved_project(tmp_path,narration=visual_plan()['narration'],brief=dict(min_seconds=0,max_seconds=30))
    router,vision=CodeRouter(),VisionRouter()
    def context(root,p,reviews):
        return wf._latest(p,'script',None),store.read_json(root/'input.json'),{'scene_1':3}
    def synth(root,text,config,retry=None):
        path=root/'sample.wav'
        if not path.exists():path.write_bytes(wave_bytes(1))
        return dict(path='sample.wav',sha256=store.digest(path),duration=1,text=text)
    with patch.object(sg,'CLIENT_FACTORY',return_value=router), \
         patch.object(sg,'_context',side_effect=context), \
         patch('src.scene_design.scene_vision.CLIENT_FACTORY',return_value=vision), \
         patch('src.scene_design.scene_vision.review_model',return_value=vision.model):
        state=sg.run_scenes(root,scene_id='scene_1')
        design=state['pending_reviews'][0]
        assert design['design_pipeline']==3
        calls=len(router.requests)+len(vision.requests)
        record_review(root,'scene_design',design['version'],'approve',scene_id='scene_1')
        clip=sg.run_scenes(root,scene_id='scene_1')['pending_reviews'][0]
        assert clip['sha256']==design['manim_render']['sha256']
        assert len(router.requests)+len(vision.requests)==calls
        record_review(root,'scene_clip',clip['version'],'approve',scene_id='scene_1')
        from src.scene_design import scene_revision as rev
        cycle=rev.request_revision(root,'scene_1',clip['version'],clip['sha256'],'Sửa cách đặt nhãn.')
        cycle=rev.resume_revision(root,cycle['id'])
        assert cycle['status']=='awaiting_design'
        candidate=cycle['designs']['scene_1']
        assert candidate['design_pipeline']==3
        rev.approve_design(root,cycle['id'],'scene_1',candidate['sha256'])
        cycle=rev.resume_revision(root,cycle['id'])
        new_clip=cycle['clips']['scene_1']
        record_review(root,'scene_clip',new_clip['version'],'approve',scene_id='scene_1')
        calls=len(router.requests)+len(vision.requests)
        av.configure(root,dict(vnd_per_million_chars=333334,vnd_per_usd=25000))
        with patch.object(av,'CLIENT_FACTORY',return_value=router),patch.object(av,'SYNTHESIZER',side_effect=synth):
            state=av.generate(root)
            candidate=state['pending_reviews'][0]
            assert candidate['stage']=='scene_av'
            assert av.delivery_resolution(state)==[1080,1920]
            stream=next(s for s in probe(candidate['path'])['streams'] if s['codec_type']=='video')
            assert (stream['width'],stream['height'],stream['avg_frame_rate'])==(1080,1920,'30/1')
            assert len(router.requests)+len(vision.requests)==calls
            av.generate(root)
            assert len(router.requests)+len(vision.requests)==calls
            record_review(root,'scene_av',candidate['version'],'approve',scene_id='scene_1')
            final=av.generate(root)['pending_reviews'][0]
            assert final['stage']=='final_video'
            stream=next(s for s in probe(final['path'])['streams'] if s['codec_type']=='video')
            assert (stream['width'],stream['height'],stream['avg_frame_rate'])==(1080,1920,'30/1')
            assert not (root/'final.mp4').exists()


def test_runtime_failure_repairs_code_without_new_plan(tmp_path):
    bad=SOURCE.replace('        self.add(title, point)',"        raise ValueError('fixture runtime failure')")
    (path,extra),session,context,vision=run_pipeline(tmp_path,CodeRouter([bad,SOURCE]))
    phases=[c['phase'] for c in session.job['calls']]
    assert extra['quality_passed']
    assert 'v3_plan_1' not in phases and 'v3_code_1' in phases
    assert 'fixture runtime failure' in store.read_json(path.parent/'round_00'/'result.json')['issues'][0]['problem']


def test_missing_installed_api_replans_before_coding(tmp_path):
    class MissingAPIRouter(CodeRouter):
        def complete(self,payload):
            response=super().complete(payload)
            result=json.loads(response['choices'][0]['message']['content'])
            if 'beats' in result and sum('beats' in p['response_format']['json_schema']['schema']['properties'] for p in self.requests)==1:
                result['required_apis']=['UnavailableFixtureManimAPI']
                response['choices'][0]['message']['content']=json.dumps(result,ensure_ascii=False)
            return response
    (path,extra),session,context,vision=run_pipeline(tmp_path,MissingAPIRouter())
    phases=[c['phase'] for c in session.job['calls']]
    assert extra['quality_passed']
    assert 'v3_plan_1' in phases and 'v3_code_0' not in phases and 'v3_code_1' in phases


def test_final_layout_change_is_rejected():
    before=dict(checked_frames=30,object_ids=['label'],issues=[],samples=[dict(frame=0,
        objects=dict(label=dict(box=[0,1,0,1])))])
    after=copy.deepcopy(before)
    after['samples'][0]['objects']['label']['box']=[1,2,0,1]
    result=pipeline.compare_layouts(before,after,dict(objects=[dict(id='label')]))
    assert not result['passed']
    assert 'differs' in result['issues'][0]['problem']


def test_validated_preview_accepts_average_frame_rate_rounding(tmp_path):
    render=sg.RENDERER
    def encoded_average(*args,**kwargs):
        result=render(*args,**kwargs)
        assert result['status']=='success',result
        metadata_path=Path(result['log_path']).with_suffix('.json')
        metadata=store.read_json(metadata_path)
        # A worker-validated video may report this average due to timestamps.
        metadata['video']['fps']=30.00412688805128
        store.atomic_write(metadata_path,metadata)
        return result
    with patch.object(sg,'RENDERER',side_effect=encoded_average):
        (path,extra),session,context,vision=run_pipeline(tmp_path,repairs=0)
    assert extra['quality_passed']
    assert extra['manim_render']['video']['fps']==30.00412688805128
    assert len(vision.requests)==1
