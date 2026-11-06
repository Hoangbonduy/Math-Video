"""Effective technical policy shared by prompts, checks and durable jobs.

Editorial requirements stay in guidelines; API documentation is not a capability.
This module has no Manim import, so it also runs in the script-writing process.
"""
import ast
import copy
import hashlib
import json
import math
from pathlib import Path
from src.shared._workflow_store import WorkflowError

VERSION = '1.0.0'
SCHEMA_VERSION = 1
ROOT = Path(__file__).resolve().parents[2]
ALLOWED_IMPORTS = ('manim', 'math', 'numpy', 'manim_kit', 'src.scene_design.manim_kit')
TRACK_ROLES = ('text', 'formula', 'shape', 'background')
LAYOUT_ROLES = ('hud', 'attached_label', 'focal', 'context')
FORBIDDEN_NAMES = frozenset(('open', 'eval', 'exec', 'compile', 'getattr', 'setattr',
    'delattr', 'globals', 'locals', 'vars', 'input', 'breakpoint', '__import__', 'config'))
FORBIDDEN_ATTRIBUTES = frozenset(('read_text','write_text','open','system','popen','load','save',
    'fromfile','tofile','ctypeslib','memmap','add_sound','loadtxt','savetxt','genfromtxt','savez','savez_compressed'))
POLICY = dict(camera_motion=False, zoom='transform_object_group',
    opening_motion_seconds=.8, duration_tolerance_frames=2,
    boundary_tolerance=.02, font_tolerance=.1, visible_opacity=.15,
    hud_min_y=3.8, max_api_names=16, max_repairs=3,
    missing_evidence='insufficient_evidence',
    coordinates=dict(direct_code='scene units, centered origin, y up',
                     legacy_design='normalized centers [0,1], y down',
                     math='declared units, independent of presentation scale'),
    cache='New fingerprints include contract digest. Saved unversioned calls are replay-only; create a new attempt for new calls.')

# Each row is a policy definition, not a copy of the scene data it governs.
_ROWS = [
 ('SC.API','Use only installed APIs and implemented, tested helpers; documentation alone grants no capability.',
  'Prevent invented helpers and reference/runtime drift.', 'all generated code', 'No external imports; approved literal assets only.',
  'src/shared/system_contract.py:ALLOWED_IMPORTS', ['static_validator','planner','codegen'],
  ['runtime signatures','selected references'], 'AST policy and installed signature checks', 'INVALID_SCENE_CODE','codegen',
  'from manim import Dot','import subprocess','test_static_policy'),
 ('SC.FRAME','Use the authoritative scene_theme profile at every resolution; presentation transforms preserve mathematical units.',
  'Keep preview, final and mathematical quantities consistent.', 'all scenes; legacy coordinates explicitly converted', 'Only pixel density changes at delivery.',
  'src/scene_design/scene_theme.py:profile',['planner','codegen','telemetry','final_renderer'],
  ['frame profile','encoded video','sample geometry'],'Compare preview/final geometry and units','SCENE_FRAME_MISMATCH','codegen',
  'same world coordinates at preview/final','scale quantities by pixel ratio','test_frame_and_snapshot'),
 ('SC.CAMERA','Keep camera geometry fixed; zoom by scaling/translating a declared object group.',
  'Cairo Scene pipeline does not implement moving-camera review.', 'presentation transforms', 'Background color is allowed.',
  'src/shared/system_contract.py:POLICY',['visual_intent','planner','static_validator'],
  ['source AST','presentation transforms'],'Reject camera controls including aliases','SCENE_CAMERA_UNSUPPORTED','planner',
  'group.animate.scale(2)','self.camera.frame.animate.scale(2)','test_camera_aliases'),
 ('SC.LAYOUT','Track semantic parts independently. HUD is fixed above geometry; attached labels follow declared sources. Context overflow requires an object/time window.',
  'A background/group label must not disable readability or collision checks.', 'visible tracked objects', 'Only context geometry may overflow in declared intervals; labels and focal objects never exempt.',
  'src/shared/system_contract.py:layout_findings',['planner','codegen','telemetry'],
  ['registry roles','overflow windows','all-frame boxes','font sizes'], 'Per-frame bounds, HUD position/motion, font and overlap checks','SCENE_LAYOUT_VIOLATION','codegen',
  'attached_label bound to a point','background role used to hide unreadable text','test_layout_roles'),
 ('SC.TIME','Events use local scene time, valid references and feasible before/after/simultaneous relations.',
  'Catch impossible plans before code generation.', 'declared events and relations', 'No event constraints required for legacy unstructured plans.',
  'src/shared/system_contract.py:preflight',['planner','orchestrator'],
  ['event ids','start/end','target_seconds'], 'Validate bounds, order, simultaneity and contradictory schedules','SCENE_PLAN_INVALID','planner',
  'reveal follows construction','reveal both before and after construction','test_event_relations'),
 ('SC.RELATION','Apply operation checks from declared relation kinds, never object names or topic words.',
  'Support new topics without branches in the pipeline.', 'only explicitly declared relations', 'Do not require graph or piece fields from unrelated scenes.',
  'src/shared/system_contract.py:RELATION_CHECKERS',['planner','relation_checker','reviewer'],
  ['relation specification','units','source identities','transforms','material'], 'Dispatch registered checker; unsupported kinds are capability errors','SCENE_CAPABILITY_MISSING','system',
  'compare equal units at shared scale','compare lengths at different scales without disclosure','test_relations'),
 ('SC.EVIDENCE','Separate declared continuity from rendered continuity. Missing images, event measurements or audio are insufficient evidence, never pass or a code error.',
  'Avoid false success and futile code repairs.', 'all evidence-based criteria', 'A scene may render independently from a complete declared starting state.',
  'src/shared/system_contract.py:evidence_result',['reviewer','orchestrator'],
  ['criterion-specific measured evidence'], 'Three-state result and explicit repair destination','SCENE_EVIDENCE_MISSING','orchestrator',
  'continuity unknown without previous frame','declare continuity passed from intended state alone','test_evidence_states'),
 ('SC.IMMUTABLE','Preserve approved narration, duration, acceptance criteria, artifact hashes and historical usage; never auto-approve.',
  'Keep human decisions and accounting durable.', 'all stages and repairs', 'Only explicit scene changes in a new reviewable attempt.',
  'src/shared/_workflow_store.py:materialize',['orchestrator','planner','codegen','reviewer'],
  ['approved input','job fingerprint','checksums','ledger'], 'Immutable writes, criterion preservation and human gates','ARTIFACT_CHANGED','orchestrator',
  'new attempt retains prior calls','delete ledger to reset costs','test_job_snapshot'),
 ('SC.CHANNEL','Distinguish narration, on-screen text and exported audio; text changes require measured timing when criteria depend on it.',
  'A silent preview cannot establish audible speech or reveal timing.', 'channel-specific criteria', 'Narration is immutable source data, not proof of audible output.',
  'src/shared/system_contract.py:evidence_result',['visual_intent','telemetry','reviewer','orchestrator'],
  ['channel','content changes','frame/audio evidence'], 'Check evidence channel before evaluating criterion','SCENE_EVIDENCE_MISSING','orchestrator',
  'on-screen question verified from frames','silent clip proves spoken closing','test_evidence_states'),
]


def rules():
    keys=('id','content','rationale','scope','exceptions','canonical_source','enforcers',
          'required_evidence','verification','error_code','destination','valid_example','invalid_example','test')
    return [dict(zip(keys,row),version=VERSION,status='implemented',condition=row[3],
                 tests=['tests/test_system_contract.py::'+row[-1]]) for row in _ROWS]


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,
                                    separators=(',',':'),allow_nan=False).encode()).hexdigest()


def load_contract(*, runtime=None):
    from src.scene_design import scene_theme
    value=dict(schema_version=SCHEMA_VERSION,version=VERSION,policy=copy.deepcopy(POLICY),
        frame=scene_theme.profile(),allowed_imports=list(ALLOWED_IMPORTS),
        track_roles=list(TRACK_ROLES),layout_roles=list(LAYOUT_ROLES),rules=rules(),
        relation_checkers={k:dict(implementation=v.__name__,status='implemented',
             evidence_scope='declared plan; rendered truth requires measured evidence') for k,v in RELATION_CHECKERS.items()},
        runtime=runtime or dict(status='not_probed'),
        sources={})
    for name in ('src/shared/system_contract.py','src/shared/job_session.py',
                 'src/idea/narration.py','src/idea/visuals.py','src/idea/content.py','src/idea/generation.py',
                 'src/scene_design/scene_revision.py',
                 'src/scene_design/scene_contract.py','src/scene_design/scene_code_pipeline.py',
                 'src/scene_design/scene_pipeline.py','src/scene_design/scene_telemetry.py',
                 'src/scene_design/scene_vision.py','src/scene_design/design.py',
                 'src/scene_design/render.py','src/scene_design/_render_worker.py',
                 'src/scene_design/scene_generation.py','src/scene_design/scene_theme.py',
                 'src/scene_design/manim_kit.py','src/scene_design/scene_references.py',
                 'guidelines/guidelines.md'):
        path=ROOT/name
        value['sources'][name]=hashlib.sha256(path.read_bytes()).hexdigest()
    value['digest']=digest(value)
    validate_contract(value)
    return value


def validate_contract(value):
    required={'schema_version','version','policy','frame','allowed_imports','track_roles',
              'layout_roles','rules','relation_checkers','runtime','sources','digest'}
    def bad(message): raise WorkflowError('SYSTEM_CONTRACT_INVALID',message)
    if not isinstance(value,dict) or required-set(value):bad('Missing contract fields')
    if value['schema_version']!=SCHEMA_VERSION:bad('Unsupported contract schema')
    if value['digest']!=digest({k:v for k,v in value.items() if k!='digest'}):bad('Contract digest mismatch')
    seen=set()
    if set(POLICY)-set(value['policy']):bad('Missing technical policy')
    if set(value['track_roles'])!=set(TRACK_ROLES) or set(value['layout_roles'])!=set(LAYOUT_ROLES):bad('Conflicting roles')
    for rule in value['rules']:
        if set(rules()[0])-set(rule):bad('Incomplete rule metadata')
        if rule['id'] in seen:bad('Duplicate/conflicting rule '+rule['id'])
        seen.add(rule['id'])
        path=ROOT/rule['canonical_source'].split(':')[0]
        if not path.is_file():bad('Broken canonical source '+str(path))
        symbol=rule['canonical_source'].split(':')[-1]
        definitions={n.name for n in ast.walk(ast.parse(path.read_text())) if isinstance(n,(ast.FunctionDef,ast.ClassDef))}
        definitions.update(n.id for n in ast.walk(ast.parse(path.read_text())) if isinstance(n,ast.Name) and isinstance(n.ctx,ast.Store))
        if symbol not in definitions:bad('Broken canonical symbol '+rule['canonical_source'])
    if seen!={r['id'] for r in rules()}:bad('Missing/unknown rule IDs')
    if value['policy']['camera_motion'] or value['policy']['zoom']!='transform_object_group':
        bad('Conflicting camera/presentation policies')
    if value['frame']['min_font_size']<=0 or value['frame']['fps']<=0:bad('Invalid frame profile')
    for kind,entry in value['relation_checkers'].items():
        if kind not in RELATION_CHECKERS or entry['implementation']!=RELATION_CHECKERS[kind].__name__:
            bad('Capability without checker implementation: '+kind)
    return value


def bind_job(session, *, runtime=None):
    """Bind before any new call. Legacy responses remain replayable, never relabelled."""
    from src.shared import _workflow_store as store
    path=session.directory/'system_contract.json'
    current=load_contract(runtime=runtime)
    expected=session.job.get('input',{}).get('system_contract_digest')
    if expected and expected!=current['digest']:
        raise WorkflowError('SYSTEM_CONTRACT_CHANGED','Job fingerprint differs from current contract; start a fresh attempt.')
    if path.exists():
        saved=validate_contract(store.read_json(path))
        if saved['digest']!=current['digest']:
            raise WorkflowError('SYSTEM_CONTRACT_CHANGED','Contract/runtime changed; start a fresh attempt. Historical calls are retained.')
    elif session.job.get('calls'):
        raise WorkflowError('SYSTEM_CONTRACT_UPGRADE_REQUIRED','Saved unversioned job is replay-only; use a new attempt for current contract.')
    else:
        store.materialize(session.directory,'system_contract.json',content=(json.dumps(current,ensure_ascii=False,indent=2)+'\n').encode())
    session.job['system_contract']=dict(version=current['version'],digest=current['digest'],path='system_contract.json')
    session.save()
    return current


def prompt(contract=None):
    value=contract or load_contract()
    return ('\nSYSTEM_CONTRACT '+value['version']+' '+value['digest']+'\n'+
        '\n'.join(r['id']+': '+r['content']+' Exceptions: '+r['exceptions'] for r in value['rules'])+
        '\nEffective frame and policy: '+json.dumps(dict(frame=value['frame'],policy=value['policy']),ensure_ascii=False)+
        '\nScene facts and editorial requirements are separate inputs. Report conflicts with rule IDs; never weaken criteria.')


def finding(rule,problem,*,destination='planner',object_ids=(),event_id=None,time=0,fix='Repair the declared plan',code='SCENE_PLAN_INVALID'):
    return dict(rule_id=rule,error_code=code,destination=destination,category='timing' if rule=='SC.TIME' else 'relation',
        object_ids=list(object_ids),event_id=event_id,time=time,problem=problem,fix=fix)


def finite(value): return type(value) in (int,float) and math.isfinite(value)


def preflight(plan,seconds):
    """Validate typed declarations; prose is never guessed from topic keywords."""
    issues=[];objects=plan.get('objects',[]);ids=[o['id'] for o in objects]
    if len(ids)!=len(set(ids)):issues.append(finding('SC.LAYOUT','Duplicate object IDs'))
    for obj in objects:
        role=obj.get('layout_role')
        if role is not None and role not in LAYOUT_ROLES:
            issues.append(finding('SC.LAYOUT','Unknown layout_role',object_ids=[obj['id']]))
    constraints=plan.get('constraints') or {}
    events={}
    for event in constraints.get('events',[]):
        eid=event['id'];start,end=event['start'],event['end']
        if eid in events:issues.append(finding('SC.TIME','Duplicate event '+eid,event_id=eid))
        events[eid]=event
        if not all(finite(v) for v in (start,end)) or not 0<=start<=end<=seconds:
            issues.append(finding('SC.TIME','Event outside local scene duration',event_id=eid))
        if set(event['object_ids'])-set(ids):issues.append(finding('SC.TIME','Event references unknown object',event_id=eid,object_ids=event['object_ids']))
    for relation in constraints.get('event_relations',[]):
        a,b=events.get(relation['source']),events.get(relation['target'])
        if not a or not b:
            issues.append(finding('SC.TIME','Unknown event reference '+relation['id'],event_id=relation['id']));continue
        kind=relation['kind']
        valid=(a['end']<=b['start']+1e-6 if kind=='before' else b['end']<=a['start']+1e-6 if kind=='after'
               else abs(a['start']-b['start'])<=1e-6 and abs(a['end']-b['end'])<=1e-6 if kind=='simultaneous' else False)
        if not valid:issues.append(finding('SC.TIME','Conflicting '+kind+' events '+a['id']+' / '+b['id'],event_id=relation['id']))
    edges={eid:[] for eid in events}
    for r in constraints.get('event_relations',[]):
        a,b=r['source'],r['target']
        if a in events and b in events and r['kind'] in ('before','after'):
            if r['kind']=='after':a,b=b,a
            edges[a].append(b)
    visiting=set();visited=set()
    def cyclic(eid):
        if eid in visiting:return True
        if eid in visited:return False
        visiting.add(eid)
        if any(cyclic(other) for other in edges[eid]):return True
        visiting.remove(eid);visited.add(eid);return False
    if any(cyclic(eid) for eid in edges):issues.append(finding('SC.TIME','Cyclic before/after requirements'))
    byid={o['id']:o for o in objects}
    for window in constraints.get('overflow_windows',[]):
        obj=byid.get(window['object_id'],{})
        if obj.get('layout_role')!='context' or obj.get('role') in ('text','formula') or not window.get('reason','').strip():
            issues.append(finding('SC.LAYOUT','Only explicitly justified context geometry may overflow',object_ids=[window['object_id']]))
        if not all(finite(window[k]) for k in ('start','end')) or not 0<=window['start']<window['end']<=seconds:
            issues.append(finding('SC.TIME','Invalid overflow interval',object_ids=[window['object_id']]))
    for relation in constraints.get('relations',[]):
        kind=relation.get('kind');checker=RELATION_CHECKERS.get(kind)
        if checker is None:
            issues.append(finding('SC.RELATION','Checker unavailable: '+str(kind),destination='system',code='SCENE_CAPABILITY_MISSING',fix='Implement and test this relation checker before advertising support'));continue
        issues.extend(checker(relation,set(ids)))
    return dict(status='fail' if issues else 'pass',passed=not issues,issues=issues)


def _refs(relation,ids):
    missing=set(relation.get('sources',[])+relation.get('targets',[]))-ids
    return [finding('SC.RELATION','Unknown relation references: '+repr(sorted(missing)),object_ids=sorted(missing))] if missing else []


def check_dependency(relation,ids):
    issues=_refs(relation,ids)
    if not relation.get('sources') or not relation.get('targets') or not relation.get('expression','').strip():
        issues.append(finding('SC.RELATION','Dependency needs source, target and expression'))
    return issues


def check_comparison(relation,ids):
    issues=_refs(relation,ids)
    quantities=relation.get('quantities',[])
    if len(quantities)<2 or any(not q.get('unit') or not finite(q.get('scale')) or q['scale']<=0 for q in quantities):
        issues.append(finding('SC.RELATION','Comparison needs quantities with units and positive presentation scales'))
    elif len({(q['unit'],q['scale']) for q in quantities})!=1:
        issues.append(finding('SC.RELATION','Comparison requires matching units and scales; convert explicitly first'))
    return issues


def check_pieces(relation,ids):
    issues=_refs(relation,ids)
    pieces=relation.get('pieces',[])
    if not pieces or any(not p.get('source_id') or p['source_id'] not in ids or not p.get('material') or not finite(p.get('area')) or p['area']<=0 or len(p.get('transform',[]))!=6 or not all(finite(x) for x in p.get('transform',[])) for p in pieces):
        issues.append(finding('SC.RELATION','Pieces require source identity, positive area, material and finite 2D affine transform'))
    else:
        area=relation.get('source_area')
        if not finite(area) or not math.isclose(sum(p['area'] for p in pieces),area,rel_tol=1e-8,abs_tol=1e-8):
            issues.append(finding('SC.RELATION','Piece areas do not conserve source area'))
        for p in pieces:
            a,b,c,d,_,_=p['transform']
            if not (math.isclose(a*a+c*c,1,abs_tol=1e-8) and math.isclose(b*b+d*d,1,abs_tol=1e-8) and math.isclose(a*b+c*d,0,abs_tol=1e-8)):
                issues.append(finding('SC.RELATION','Material transform is not rigid; use separate presentation transform'))
        if len({p['material'] for p in pieces})!=1:issues.append(finding('SC.RELATION','Pieces change material'))
    return issues


def check_sequence(relation,ids):
    issues=_refs(relation,ids)
    steps=relation.get('steps',[])
    if not steps or any(a['after']!=b['before'] for a,b in zip(steps,steps[1:])):
        issues.append(finding('SC.RELATION','State sequence is empty or discontinuous'))
    return issues


RELATION_CHECKERS={'label_follow':check_dependency,'function_dependency':check_dependency,
    'unit_comparison':check_comparison,'piece_provenance':check_pieces,'state_sequence':check_sequence}


def register_relation(kind,checker):
    if not kind or kind in RELATION_CHECKERS or not callable(checker):
        raise WorkflowError('SYSTEM_CONTRACT_INVALID','Duplicate/invalid relation checker')
    RELATION_CHECKERS[kind]=checker


def evidence_result(kind,evidence,*,matches=None):
    required={'declared_continuity':('start_state','end_state'),
        'rendered_continuity':('previous_frame','current_frame'),
        'event_timing':('measured_events',),'onscreen':('frames',),
        'narration':('approved_narration',),'audio':('audio',)}
    if kind not in required:return dict(status='unsupported',destination='system',rule_id='SC.RELATION')
    missing=[k for k in required[kind] if not evidence.get(k)]
    if missing or matches is None:
        return dict(status='insufficient_evidence',destination='orchestrator',rule_id='SC.EVIDENCE',missing=missing,
                    passed=None,error_code='SCENE_EVIDENCE_MISSING')
    return dict(status='pass' if matches else 'fail',passed=bool(matches),destination=None if matches else 'codegen',rule_id='SC.EVIDENCE')


def layout_findings(oid,item,time,*,contract=None,initial_box=None):
    value=contract or load_contract();p=value['policy'];frame=value['frame'];issues=[]
    box=item['box'];role=item.get('layout_role') or ('hud' if item['role'] in ('text','formula') else 'context' if item['role']=='background' else 'focal')
    def add(problem,fix):issues.append(finding('SC.LAYOUT',problem,destination='codegen',object_ids=[oid],time=time,fix=fix,code='SCENE_LAYOUT_VIOLATION'))
    bounds=frame['safe_bounds']
    exempt=(role=='context' and item['role'] not in ('text','formula') and not item.get('has_text',False)
            and any(w['start']<=time<=w['end'] and w.get('reason') for w in item.get('overflow_windows',[])))
    if not exempt and any((box[0]<bounds[0]-p['boundary_tolerance'],box[1]>bounds[1]+p['boundary_tolerance'],box[2]<bounds[2]-p['boundary_tolerance'],box[3]>bounds[3]+p['boundary_tolerance'])):
        add('Rendered object leaves the safe area','Repair position/path; context overflow needs a declared object/time window')
    if item.get('font_size',frame['min_font_size'])<frame['min_font_size']-p['font_tolerance']:
        add('Rendered font is below the readability minimum','Wrap or split content; preserve minimum font size')
    if role=='hud' and item.get('enforce_hud',False):
        if box[2]<p['hud_min_y']:add('HUD crosses the geometry region','Pin this HUD in the upper region or declare an attached_label relation')
        if initial_box and any(abs(a-b)>p['boundary_tolerance'] for a,b in zip([(box[0]+box[1])/2,(box[2]+box[3])/2],[(initial_box[0]+initial_box[1])/2,(initial_box[2]+initial_box[3])/2])):
            add('HUD moves from its fixed anchor','Change text in place; keep its anchor fixed')
    return issues


def validate_camera(tree):
    """No camera aliases/indirect access. Only background_color assignment is supported."""
    for node in ast.walk(tree):
        if isinstance(node,ast.Attribute) and node.attr=='camera':
            # Parent must be the explicitly allowed leaf; returning the camera
            # itself (alias, argument, subscript) is not permitted.
            parents=[p for p in ast.walk(tree) if isinstance(p,ast.Attribute) and p.value is node]
            if len(parents)!=1 or parents[0].attr!='background_color':
                raise WorkflowError('INVALID_SCENE_CODE',f'SC.CAMERA line {node.lineno}: camera controls unsupported; transform the object group (planner/codegen).')
        if isinstance(node,ast.Name) and node.id in ('MovingCameraScene','ThreeDScene','ZoomedScene'):
            raise WorkflowError('INVALID_SCENE_CODE',f'SC.CAMERA line {node.lineno}: unsupported camera scene class')


def route_issue(issue):
    """Missing evidence never becomes a request to rewrite scene code."""
    if issue.get('destination'):return issue['destination']
    if issue.get('category') in ('evidence','missing_evidence'):return 'orchestrator'
    if issue.get('category')=='unsupported':return 'system'
    if issue.get('category')=='meaning':return 'planner'
    return 'codegen'


def constraints_schema():
    def obj(**fields):return dict(type='object',properties=fields,required=list(fields),additionalProperties=False)
    def arr(item):return dict(type='array',items=item,maxItems=120)
    s=dict(type='string');n=dict(type='number');strings=arr(s)
    base=dict(sources=strings,targets=strings)
    variants=[]
    for kind in RELATION_CHECKERS:
        fields=dict(id=s,kind=dict(type='string',enum=[kind]),**base)
        if kind in ('label_follow','function_dependency'):fields.update(expression=s)
        elif kind=='unit_comparison':fields.update(quantities=arr(obj(unit=s,scale=n)))
        elif kind=='piece_provenance':fields.update(source_area=n,pieces=arr(obj(source_id=s,area=n,material=s,transform=dict(type='array',items=n,minItems=6,maxItems=6))))
        elif kind=='state_sequence':fields.update(steps=arr(obj(before=s,after=s)))
        variants.append(obj(**fields))
    return obj(events=arr(obj(id=s,start=n,end=n,object_ids=strings,
        channel=dict(type='string',enum=['visual','onscreen','narration','audio']),content=s)),
        event_relations=arr(obj(id=s,kind=dict(type='string',enum=['before','after','simultaneous']),source=s,target=s)),
        overflow_windows=arr(obj(object_id=s,start=n,end=n,reason=s)),relations=arr(dict(anyOf=variants)))


def plan_schema(base,context):
    schema=copy.deepcopy(base)
    if context.get('contract_schema_version')==SCHEMA_VERSION:
        schema['properties']['constraints']=constraints_schema()
        schema['required'].append('constraints')
        objects=schema['properties']['objects']['items']
        objects['properties']['layout_role']=dict(type='string',enum=list(LAYOUT_ROLES))
        objects['required'].append('layout_role')
    return schema
