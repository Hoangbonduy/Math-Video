"""Subject-independent policy pairs; expected outcomes fixed before checker use."""
import copy
import json
from unittest.mock import patch
import pytest
from src.shared import system_contract as sc
from src.shared import _workflow_store as store
from src.scene_design.design import validate_code


def plan():
    return dict(objects=[dict(id='status',role='text',layout_role='hud'),
        dict(id='body',role='shape',layout_role='context'),
        dict(id='point',role='shape',layout_role='focal'),
        dict(id='label',role='text',layout_role='attached_label')],
        constraints=dict(events=[dict(id='construct',start=0,end=1,object_ids=['body'],channel='visual',content=''),
            dict(id='reveal',start=1,end=2,object_ids=['status'],channel='onscreen',content='value')],
            event_relations=[dict(id='order',kind='before',source='construct',target='reveal')],
            overflow_windows=[dict(object_id='body',start=.5,end=1.5,reason='presentation closeup')],relations=[]))


def test_frame_and_snapshot():
    from src.scene_design.scene_theme import profile
    value=sc.load_contract(runtime=dict(version='0.22.1',renderer='cairo'))
    assert value['frame']==profile()
    assert value['policy']['coordinates']['direct_code']!=value['policy']['coordinates']['legacy_design']
    assert value['digest']==sc.load_contract(runtime=dict(version='0.22.1',renderer='cairo'))['digest']
    assert value['digest']!=sc.load_contract(runtime=dict(version='other',renderer='cairo'))['digest']
    assert all(r['status']=='implemented' for r in value['rules'])


def resign(value):value['digest']=sc.digest({k:v for k,v in value.items() if k!='digest'});return value


@pytest.mark.parametrize('change',[
    lambda x:x.pop('frame'),lambda x:x['rules'].pop(),
    lambda x:x['rules'].append(x['rules'][0]),
    lambda x:x['rules'][0].update(canonical_source='src/shared/system_contract.py:nonexistent'),
    lambda x:x['policy'].update(camera_motion=True),
    lambda x:x['relation_checkers'].update(fake=dict(implementation='invented'))])
def test_load_validation(change):
    value=sc.load_contract();change(value);resign(value)
    with pytest.raises(store.WorkflowError,match='SYSTEM_CONTRACT_INVALID'):sc.validate_contract(value)


def test_static_policy():
    source='from manim import *\nclass GeneratedScene(Scene):\n def construct(self):\n  self.camera.background_color = "#112233"\n'
    assert validate_code(source,[])==source
    for statement in ('import subprocess','open("file")','self.add_sound("x")','import os'):
        with pytest.raises(store.WorkflowError):validate_code(source+'  '+statement+'\n',[])


@pytest.mark.parametrize('statement',['self.camera.frame.animate.scale(2)',
    'cam=self.camera\n  cam.frame.scale(2)','self.camera.set_frame_width(4)',
    'thing(self.camera)','self.camera.__dict__'])
def test_camera_aliases(statement):
    source='from manim import *\nclass GeneratedScene(Scene):\n def construct(self):\n  '+statement+'\n'
    with pytest.raises(store.WorkflowError,match='SC.CAMERA'):validate_code(source,[])


def test_event_relations():
    good=plan();assert sc.preflight(good,2)['passed']
    bad=copy.deepcopy(good);bad['constraints']['event_relations'][0]['kind']='after'
    report=sc.preflight(bad,2)
    assert not report['passed'] and report['issues'][0]['destination']=='planner'
    bad=copy.deepcopy(good);bad['constraints']['events'][1]['end']=3
    assert not sc.preflight(bad,2)['passed']
    bad=copy.deepcopy(good);bad['constraints']['event_relations'][0]['target']='missing'
    assert not sc.preflight(bad,2)['passed']
    both=copy.deepcopy(good);both['constraints']['events'][1].update(start=0,end=1)
    both['constraints']['event_relations'][0]['kind']='simultaneous'
    assert sc.preflight(both,2)['passed']
    both['constraints']['event_relations'].append(dict(id='conflict',kind='before',source='construct',target='reveal'))
    assert not sc.preflight(both,2)['passed']
    instant=copy.deepcopy(good)
    for e in instant['constraints']['events']:e.update(start=0,end=0)
    instant['constraints']['event_relations'].append(dict(id='cycle',kind='before',source='reveal',target='construct'))
    assert not sc.preflight(instant,2)['passed']


def test_layout_roles():
    context=dict(role='background',layout_role='context',box=[-10,10,-10,10],
        overflow_windows=[dict(start=.5,end=1.5,reason='closeup')])
    assert not sc.layout_findings('arbitrary',context,1)
    assert sc.layout_findings('arbitrary',context,0)
    for role in ('focal','hud','attached_label'):
        assert sc.layout_findings('arbitrary',dict(context,layout_role=role),1)
    assert sc.layout_findings('arbitrary',dict(context,role='text',font_size=12),1)
    assert sc.layout_findings('arbitrary',dict(context,has_text=True),1)
    # Graph label may move, HUD must remain pinned.
    label=dict(role='text',layout_role='attached_label',enforce_hud=True,box=[0,1,0,1],font_size=28)
    assert not sc.layout_findings('label',label,1,initial_box=[-1,0,0,1])
    assert sc.layout_findings('label',dict(label,layout_role='hud'),1,initial_box=[-1,0,0,1])
    hud=dict(label,layout_role='hud',box=[0,1,5,6])
    assert not sc.layout_findings('label',hud,0,initial_box=[0,1,5,6])
    bad=plan();bad['constraints']['overflow_windows'][0]['object_id']='label'
    assert not sc.preflight(bad,2)['passed']


def relations():
    return [dict(id='follow',kind='label_follow',sources=['point'],targets=['label'],expression='point + offset'),
        dict(id='graph',kind='function_dependency',sources=['body'],targets=['point'],expression='f(t)'),
        dict(id='compare',kind='unit_comparison',sources=['body'],targets=['point'],quantities=[dict(unit='m',scale=1),dict(unit='m',scale=1)]),
        dict(id='pieces',kind='piece_provenance',sources=['body'],targets=['point'],source_area=6,
            pieces=[dict(source_id='body',area=2,material='A',transform=[1,0,0,1,2,0]),dict(source_id='body',area=4,material='A',transform=[0,-1,1,0,0,3])]),
        dict(id='process',kind='state_sequence',sources=['body'],targets=['point'],steps=[dict(before='initial',after='ready'),dict(before='ready',after='done')])]


def test_relations():
    p=plan();p['constraints']['relations']=relations();assert sc.preflight(p,2)['passed']
    changes=[lambda r:r.update(expression=''),lambda r:r.update(targets=['missing']),
        lambda r:r['quantities'][1].update(scale=2),lambda r:r['pieces'][0].update(area=3),
        lambda r:r['steps'][1].update(before='wrong')]
    for idx,mutate in enumerate(changes):
        bad=copy.deepcopy(p);mutate(bad['constraints']['relations'][idx]);assert not sc.preflight(bad,2)['passed']
    for matrix in ([2,0,0,2,0,0],[1,1,0,1,0,0]):
        bad=copy.deepcopy(p);bad['constraints']['relations'][3]['pieces'][0]['transform']=matrix
        assert not sc.preflight(bad,2)['passed']
    unknown=plan();unknown['constraints']['relations']=[dict(kind='new_mechanism')]
    result=sc.preflight(unknown,2);assert not result['passed'] and result['issues'][0]['destination']=='system'
    # No irrelevant graph/piece/data requirements on an unrelated scene.
    assert sc.preflight(dict(objects=[dict(id='anything',role='shape')]),3)['passed']


def test_name_parameter_and_topic_invariance():
    p=plan();p['constraints']['relations']=relations();assert sc.preflight(p,2)['passed']
    # Replace every stable ID, leaving roles/relations unchanged.
    text=json.dumps(p)
    for a,b in [('status','unrelated_hud'),('body','network_node'),('point','car'),('label','temperature')]:
        text=text.replace('"'+a+'"','"'+b+'"')
    renamed=json.loads(text);assert sc.preflight(renamed,2)['passed']
    renamed['constraints']['events'][1]['content']='A different subject and different question'
    renamed['constraints']['relations'][3]['source_area']=12
    for piece in renamed['constraints']['relations'][3]['pieces']:piece['area']*=2
    assert sc.preflight(renamed,2)['passed']


def test_evidence_states():
    for kind,data in [('rendered_continuity',dict(current_frame='image')),('audio',dict(approved_narration='words')),
                      ('event_timing',dict(events=['intent'])),('onscreen',dict(approved_narration='question'))]:
        result=sc.evidence_result(kind,data,matches=True)
        assert result['status']=='insufficient_evidence' and result['passed'] is None and result['destination']=='orchestrator'
    assert sc.evidence_result('declared_continuity',dict(start_state={'x':0},end_state={'x':0}),matches=True)['passed']
    assert sc.evidence_result('rendered_continuity',dict(previous_frame='a',current_frame='b'),matches=False)['status']=='fail'
    assert sc.evidence_result('audio',dict(audio='clip.wav'),matches=True)['passed']
    assert sc.evidence_result('audio',dict(audio='clip.wav'))['status']=='insufficient_evidence'


def test_evidence_routing():
    assert sc.route_issue(dict(category='evidence'))=='orchestrator'
    assert sc.route_issue(dict(category='unsupported'))=='system'
    assert sc.route_issue(dict(category='meaning'))=='planner'
    assert sc.route_issue(dict(category='layout'))=='codegen'


def session(tmp_path,calls=None):
    from src.shared.job_session import Session
    from tests.ai_support import FakeRouter
    return Session(tmp_path,dict(job_id='test',input={},calls=calls or []),FakeRouter())


def test_job_snapshot(tmp_path):
    s=session(tmp_path);a=sc.bind_job(s);b=sc.bind_job(s)
    assert a==b and store.read_json(tmp_path/'system_contract.json')==a
    assert store.read_json(tmp_path/'job.json')['system_contract']['digest']==a['digest']
    # A changed policy cannot consume cached phase data under a new label.
    with patch.dict(sc.POLICY,hud_min_y=4):
        with pytest.raises(store.WorkflowError,match='SYSTEM_CONTRACT_CHANGED'):sc.bind_job(s)
    legacy=session(tmp_path/'legacy',[dict(call_id='old',status='complete')]);legacy.directory.mkdir()
    with pytest.raises(store.WorkflowError,match='SYSTEM_CONTRACT_UPGRADE_REQUIRED'):sc.bind_job(legacy)
    assert legacy.job['calls']==[dict(call_id='old',status='complete')]


def test_nested_text():
    from manim import VGroup,Text,Rectangle
    from src.scene_design.manim_kit import track
    from src.scene_design.scene_telemetry import LayoutTelemetry
    class Scene:
        def get_mobject_family_members(self):return self.mob.get_family()
    scene=Scene();scene.mob=VGroup(Rectangle(width=3,height=2),Text('too small',font_size=12))
    track(scene,'group',scene.mob,'background')
    telemetry=LayoutTelemetry(scene,30);objects=telemetry.snapshot()
    labels=[x for x in objects.values() if x['role']=='text']
    assert len(labels)==1 and labels[0]['font_size']==pytest.approx(12)
    assert sc.layout_findings('nested',labels[0],0)
