"""Version 2 storyboard/design contract and deterministic motion validation."""
import ast
import copy
import math
import re
from src.shared._workflow_store import WorkflowError
from src.idea.content import _object
from src.scene_design import scene_state as runtime

S={'type':'string'}; N={'type':'number'}; I={'type':'integer'}
def array(item,low=0,high=60):return dict(type='array',items=item,minItems=low,maxItems=high)
def enum(*items):return dict(type='string',enum=list(items))
CAPABILITIES=['groups','parameters','point_on_graph','line_through_points','label_follow','value_label','position','tangent_line','move','rotate','scale','appear','fade','highlight']
TEMPLATES={
    'freeform':'Custom explanatory diagram using supported primitives and relations.',
    'grouped_motion':'One coherent grouped illustration with a separate title/readout region.',
    'graph_relation':'A shared graph coordinate system, bound points/lines/labels and parameter-driven values.',
    'parameter_diagram':'One shared control parameter updates geometry and numerical labels.',
    'comparison':'Two clearly separated regions with matching scales and readable labels.'}
IDENTIFIER=dict(type='string',pattern='^[A-Za-z][A-Za-z0-9_]*$',description='Unique identifier such as beat_01, car_body, or speed_value; no spaces or JSON.')
NORMALIZED=dict(type='number',minimum=0,maximum=1)
STORY_BEAT=_object(dict(id=IDENTIFIER,start=N,end=N,narration_excerpt=S,show=S,motion=S,understand=S,criteria=array(S,1,6),required_capabilities=array(enum(*CAPABILITIES),0,14)))
STORY_SCHEMA=_object(dict(scene_id=S,narration=S,duration=N,goal=S,visual_strategy=S,beats=array(STORY_BEAT,1,8)))
OBJECT=_object(dict(id=IDENTIFIER,kind=enum('text','rectangle','circle','dot','line','plot','graph','image'),label=S,x=N,y=N,width=N,height=N,color=dict(type='string',pattern='^#[0-9a-fA-F]{6}$',description='Full six-digit hex color, e.g. #888888; never #888.'),asset_id=S,points=dict(array(array(NORMALIZED,2,2),0,200),description='Local path coordinates inside this object box, BOTH coordinates in [0,1]. Horizontal line [[0,0.5],[1,0.5]], vertical line [[0.5,1],[0.5,0]]. Use [] for non-path objects.'),overlap_with=array(S,0,20)))
GROUP=_object(dict(id=S,members=array(S,1,60)))
PARAMETER=_object(dict(id=S,value=N,min=N,max=N))
PARAM_TRACK=_object(dict(parameter=S,start=N,duration=N,**{'from':N,'to':N},easing=enum('linear','smooth')))
GRAPH=_object(dict(id=S,object_id=S,expression=S,variable=S,x_min=N,x_max=N,y_min=N,y_max=N,samples=I))
BINDING=_object(dict(id=S,kind=enum('point_on_graph','line_through_points','label_follow','value_label','position','tangent_line'),target=S,graph=S,source_a=S,source_b=S,x_expression=S,y_expression=S,expression=S,offset_x=N,offset_y=N,decimals=I,prefix=S,suffix=S,extent=enum('segment','full')))
BEAT=_object(dict(object_id=S,action=enum('appear','move','fade','highlight','rotate','scale'),start=N,duration=N,dx=N,dy=N,value=N,easing=enum('linear','smooth')))
DESIGN_SCHEMA=_object(dict(schema_version=dict(type='integer',enum=[2]),scene_id=S,narration=S,purpose=S,duration=N,aspect_ratio=N,template=enum(*TEMPLATES),
    objects=array(OBJECT,1,60),groups=array(GROUP,0,20),parameters=array(PARAMETER,0,12),parameter_tracks=array(PARAM_TRACK,0,32),graphs=array(GRAPH,0,8),bindings=array(BINDING,0,50),timeline=array(BEAT,0,80),fallback_note=S))
ISSUE=_object(dict(category=enum('meaning','relation','layout','timing','unsupported'),beat_id=S,object_ids=array(S,0,20),time=N,problem=S,fix=S))
REVIEW_SCHEMA=_object(dict(passed={'type':'boolean'},summary=S,issues=array(ISSUE,0,20),criteria_results=array(_object(dict(beat_id=S,criterion=S,passed={'type':'boolean'},evidence=S)),1,48)))


def fail(message,code='INVALID_SCENE_DESIGN'):raise WorkflowError(code,message)


def structure(value,schema,path='design'):
    kind=schema['type']
    if kind=='object':
        if not isinstance(value,dict):fail(path+': expected object')
        missing=set(schema['properties'])-set(value);extra=set(value)-set(schema['properties'])
        if missing or extra:fail(path+': missing '+repr(sorted(missing))+'; unsupported fields '+repr(sorted(extra)))
        for key,child in schema['properties'].items():structure(value[key],child,path+'.'+key)
    elif kind=='array':
        if not isinstance(value,list) or not schema.get('minItems',0)<=len(value)<=schema.get('maxItems',1000):fail(path+': invalid array size')
        for index,item in enumerate(value):structure(item,schema['items'],path+'['+str(index)+']')
    elif kind in ('number','integer'):
        if type(value) not in ((int,) if kind=='integer' else (int,float)) or not math.isfinite(value):fail(path+': expected finite '+kind)
    elif kind=='string':
        if not isinstance(value,str) or len(value)>8000:fail(path+': expected bounded string')
    elif kind=='boolean':
        if type(value)is not bool:fail(path+': expected boolean')
    if 'enum' in schema and value not in schema['enum']:fail(path+': unsupported value '+repr(value))


def validate_story(story,scene,seconds):
    structure(story,STORY_SCHEMA,'storyboard')
    if story['scene_id']!=scene['scene_id'] or story['narration']!=scene['narration'] or abs(story['duration']-seconds)>1e-6:fail('Storyboard must preserve approved scene_id, narration and target duration')
    if not story['goal'].strip() or not story['visual_strategy'].strip():fail('Storyboard needs a goal and visual strategy')
    previous=0;ids=set();parts=[]
    for beat in story['beats']:
        if not re.fullmatch('[A-Za-z][A-Za-z0-9_]*',beat['id']) or beat['id'] in ids:fail('Storyboard beat IDs must be unique identifiers')
        ids.add(beat['id'])
        if abs(beat['start']-previous)>1e-6 or not beat['start']<beat['end']<=seconds+1e-6:fail('Storyboard beats must cover the full duration consecutively')
        for key in ('narration_excerpt','show','motion','understand'):
            if not beat[key].strip():fail('Storyboard beat '+beat['id']+' needs '+key)
        if any(not criterion.strip() for criterion in beat['criteria']):fail('Storyboard criteria cannot be empty')
        parts.append(beat['narration_excerpt']);previous=beat['end']
    if abs(previous-seconds)>1e-6 or ' '.join(' '.join(parts).split())!=' '.join(scene['narration'].split()):fail('Storyboard excerpts must partition the approved narration exactly, without additions or omissions')
    return story


def expression_code(text,allowed):
    try:tree=ast.parse(text,mode='eval')
    except (SyntaxError,ValueError):fail('Invalid arithmetic expression: '+repr(text))
    if len(list(ast.walk(tree)))>80:fail('Expression is too complex')
    names=set()
    def walk(node):
        if isinstance(node,ast.Constant) and type(node.value) in (int,float) and math.isfinite(node.value):return ['n',node.value]
        if isinstance(node,ast.Name):
            if node.id not in allowed:fail('Unknown expression name '+repr(node.id)+' in '+repr(text))
            names.add(node.id);return ['v',node.id]
        if isinstance(node,ast.UnaryOp) and isinstance(node.op,(ast.USub,ast.UAdd)):return ['neg' if isinstance(node.op,ast.USub) else 'pos',walk(node.operand)]
        if isinstance(node,ast.BinOp):
            ops={ast.Add:'+',ast.Sub:'-',ast.Mult:'*',ast.Div:'/',ast.Pow:'**'}
            if type(node.op) in ops:return [ops[type(node.op)],walk(node.left),walk(node.right)]
        if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id in ('sin','cos','tan','sqrt','exp','log','fabs') and len(node.args)==1 and not node.keywords:return ['fn',node.func.id,walk(node.args[0])]
        fail('Unsupported expression: '+repr(text))
    return walk(tree.body),names


def _unique(items,name):
    result={}
    for item in items:
        key=item['id']
        if not re.fullmatch('[A-Za-z][A-Za-z0-9_]*',key) or key in result:fail(name+': invalid or duplicate id '+repr(key))
        result[key]=item
    return result


def compile_design(design,scene=None,seconds=None,assets=()):
    structure(design,DESIGN_SCHEMA)
    if scene and (design['scene_id']!=scene['scene_id'] or design['narration']!=scene['narration']):fail('Design must preserve approved scene_id and narration')
    if seconds is not None and abs(design['duration']-seconds)>1e-6:fail('Design must preserve target duration')
    if not 0<design['duration']<=60 or not .4<=design['aspect_ratio']<=2.5 or not design['purpose'].strip():fail('Invalid duration, aspect ratio or scene purpose')
    d=copy.deepcopy(design);objects=_unique(d['objects'],'objects');groups=_unique(d['groups'],'groups');parameters=_unique(d['parameters'],'parameters');graphs=_unique(d['graphs'],'graphs');binds=_unique(d['bindings'],'bindings')
    if set(objects)&set(groups) or set(parameters)&(set(binds)|{'pi','e'}) or set(binds)&{'pi','e'}:fail('Object/group IDs and parameter/binding expression names must not conflict')
    parents={};pivots={};ancestors={};asset_ids={a['asset_id'] for a in assets}
    for group in groups.values():
        if len(set(group['members']))!=len(group['members']):fail('Duplicate group member')
        for member in group['members']:
            if member not in objects and member not in groups:fail('Unknown group member '+member)
            if member in parents:fail('Objects/groups can have only one parent: '+member)
            parents[member]=group['id']
    def parent_chain(oid):
        result=[];current=oid
        while current in parents:
            current=parents[current]
            if current==oid or current in result:fail('Cyclic group dependency')
            result.append(current)
        return result
    for oid in list(objects)+list(groups):ancestors[oid]=parent_chain(oid)
    def pivot(gid):
        if gid in pivots:return pivots[gid]
        points=[]
        for member in groups[gid]['members']:
            if member in objects:
                o=objects[member];points.extend([[o['x']-o['width']/2,o['y']-o['height']/2],[o['x']+o['width']/2,o['y']+o['height']/2]])
            else:points.append(pivot(member))
        value=[(min(p[k] for p in points)+max(p[k] for p in points))/2 for k in (0,1)];pivots[gid]=value;return value
    for gid in groups:pivot(gid)
    for obj in objects.values():
        if not re.fullmatch('#[0-9a-fA-F]{6}',obj['color']):fail('Color must be #RRGGBB: '+obj['id'])
        if obj['kind'] in ('line','plot'):
            if not 0<=obj['width']<=1 or not 0<=obj['height']<=1:fail('Invalid path box')
            if obj['kind']=='plot' and len(obj['points'])<2:fail('Plot requires at least two points')
        elif not 0<obj['width']<=1 or not 0<obj['height']<=1:fail('Invalid object size: '+obj['id'])
        if obj['kind']=='text' and not obj['label'].strip():fail('Empty text object '+obj['id'])
        if obj['kind']!='text' and obj['label'].strip():fail('Use a separate text object for the label of '+obj['id'])
        if obj['kind']=='image' and obj['asset_id'] not in asset_ids:fail('Image must reference a licensed available asset')
        if obj['kind']!='image' and obj['asset_id']:fail('Vector object cannot reference an image asset')
        if any(any(not 0<=v<=1 for v in point) for point in obj['points']):fail('Plot points must be normalized within their box: '+obj['id']+' points='+repr(obj['points'])+'. Each x,y must be in [0,1]; use [[0,0.5],[1,0.5]] for horizontal or [[0.5,1],[0.5,0]] for vertical, not signed Manim coordinates.')
        if any(oid not in objects or oid==obj['id'] for oid in obj['overlap_with']):fail('Unknown overlap exception')
    for param in parameters.values():
        if not param['min']<=param['value']<=param['max'] or param['min']>=param['max']:fail('Invalid control parameter range')
    tracks={oid:[] for oid in list(objects)+list(groups)};last={}
    for beat in sorted(d['timeline'],key=lambda b:b['start']):
        if beat['object_id'] not in tracks:fail('Unknown timeline target '+beat['object_id'])
        if beat['start']<0 or beat['duration']<=0 or beat['start']+beat['duration']>d['duration']+1e-6:fail('Timeline event outside scene duration')
        key=(beat['object_id'],beat['action'])
        if beat['start']<last.get(key,-1)-1e-8:fail('Overlapping timeline actions for '+beat['object_id'])
        last[key]=beat['start']+beat['duration']
        if beat['action']=='scale' and beat['value']<=0:fail('Scale must stay positive')
        tracks[beat['object_id']].append(beat)
    previous={};param_tracks=sorted(d['parameter_tracks'],key=lambda t:t['start'])
    for track in param_tracks:
        p=parameters.get(track['parameter'])
        if not p:fail('Unknown control parameter '+track['parameter'])
        if track['start']<0 or track['duration']<=0 or track['start']+track['duration']>d['duration']+1e-6:fail('Parameter animation outside scene duration')
        end,value=previous.get(p['id'],(-1,p['value']))
        if track['start']<end-1e-8 or abs(track['from']-value)>1e-7:fail('Parameter tracks must be continuous and cannot overlap')
        if not all(p['min']<=track[k]<=p['max'] for k in ('from','to')):fail('Animated parameter exceeds declared range')
        previous[p['id']]=(track['start']+track['duration'],track['to'])
    names=set(parameters)|{'pi','e'};scalar=set(b['id'] for b in binds.values() if b['kind'] in ('point_on_graph','line_through_points','tangent_line','value_label'))
    graph_targets=set()
    for graph in graphs.values():
        oid=graph['object_id']
        if oid not in objects or objects[oid]['kind']!='graph' or oid in graph_targets:fail('Each graph must reference a unique graph object')
        graph_targets.add(oid)
        if not graph['x_min']<graph['x_max'] or not graph['y_min']<graph['y_max'] or not 2<=graph['samples']<=200:fail('Invalid graph domain/range/sampling')
        if not re.fullmatch('[A-Za-z][A-Za-z0-9_]*',graph['variable']) or graph['variable'] in names:fail('Graph variable must be independent from controls')
        graph['code'],_=expression_code(graph['expression'],names|{graph['variable']})
    if graph_targets!={o['id'] for o in objects.values() if o['kind']=='graph'}:fail('Every graph object needs a graph specification')
    # A relation ID is an unambiguous alias for its target object, but an existing
    # object ID always wins. Resolve before dependency sorting and validation.
    for bind in binds.values():
        for key in ('source_a','source_b'):
            ref=bind[key]
            if ref and ref not in objects and ref in binds:
                bind[key]=binds[ref]['target']
        ref=bind['graph']
        if ref and ref not in graphs:
            matches=[g['id'] for g in graphs.values() if g['object_id']==ref]
            if len(matches)==1:bind['graph']=matches[0]
    owners={};dependencies={}
    for bind in binds.values():
        oid=bind['target'];kind=bind['kind']
        if oid not in objects:fail('Unknown relation target '+oid+('; this is a group: use a timeline move/rotate/scale on the group, not a binding' if oid in groups else '; target must be an object ID'))
        if oid in owners:fail('An object can have only one relation; combine its source parameter instead')
        owners[oid]=bind['id'];deps=set()
        for key in ('source_a','source_b'):
            if bind[key]:
                if bind[key] not in objects:fail('Unknown relation source '+bind[key])
                deps.add(bind[key])
        if kind=='point_on_graph':
            if objects[oid]['kind'] not in ('dot','circle'):fail('point_on_graph target must be dot/circle')
            if bind['graph'] not in graphs:fail('Unknown graph relation '+repr(bind['graph'])+'; use graphs[].id from '+repr(list(graphs))+', not object_id')
            deps.add(graphs[bind['graph']]['object_id'])
        if kind in ('line_through_points','tangent_line'):
            if objects[oid]['kind']!='line' or not bind['source_a'] or kind=='line_through_points' and not bind['source_b']:fail('Line relation needs a line target and point source(s)')
        if kind in ('label_follow','value_label') and objects[oid]['kind']!='text':fail('Label relation must target text')
        if kind=='position' and objects[oid]['kind'] not in ('text','rectangle','circle','dot','image'):fail('position binding requires an object with a center, not a path/graph')
        if kind=='label_follow' and not bind['source_a']:fail('Label needs a source object')
        if not 0<=bind['decimals']<=5:fail('Value labels support 0..5 decimal places')
        expressions={'point_on_graph':['x_expression'],'position':['x_expression','y_expression'],'value_label':['expression']}.get(kind,[])
        used=set()
        for key in expressions:
            code,ref=expression_code(bind[key],names|scalar);bind[{'x_expression':'x_code','y_expression':'y_code','expression':'code'}[key]]=code;used|=ref&scalar
        dependencies[bind['id']]=(deps,used)
        if kind!='position' and any(b['action'] in ('move','rotate','scale') for b in tracks[oid]):fail('Bound geometry cannot move independently: '+oid+'; animate its shared parameter/source/group')
    for oid,obj in objects.items():
        if obj['kind']=='line' and oid not in owners and obj['width']==0 and obj['height']==0 and len(obj['points'])<2:fail('Unbound line must have visible geometry: '+oid)
    edges={bid:set(refs)|{owners[oid] for oid in deps if oid in owners} for bid,(deps,refs) in dependencies.items()}
    ordered=[];remaining=set(binds)
    while remaining:
        ready=[bid for bid in binds if bid in remaining and not edges[bid]&remaining]
        if not ready:fail('Cyclic relation/parameter dependency: '+repr(sorted(remaining)))
        for bid in ready:ordered.append(binds[bid]);remaining.remove(bid)
    for bind in ordered:
        if bind['kind'] in ('point_on_graph','line_through_points','tangent_line','label_follow'):
            deps=dependencies[bind['id']][0]
            def moving_chain(oid):
                return [gid for gid in ancestors[oid] if any(b['action'] in ('move','rotate','scale','highlight') for b in tracks[gid])]
            # Segments use already transformed world endpoints. Radar and car
            # may belong to different moving hierarchies without double transforms.
            if bind['kind']=='line_through_points' and bind['extent']=='segment':
                if any(gid not in moving_chain(oid) for gid in moving_chain(bind['target']) for oid in deps):
                    fail('Bound segment group cannot transform independently of its endpoints: '+bind['id'])
                continue
            if any(moving_chain(oid)!=moving_chain(bind['target']) for oid in deps):fail('Related geometry must share the same moving group hierarchy: '+bind['id'])
    return dict(objects=objects,groups=groups,parameters=d['parameters'],parameter_tracks=param_tracks,graphs=graphs,bindings=ordered,
                tracks=tracks,ancestors=ancestors,pivots=pivots,aspect=d['aspect_ratio'],duration=d['duration'])


def important_times(design,story):
    duration=design['duration'];times={0.0,max(0,duration-1e-5)}
    for beat in story['beats']:
        span=beat['end']-beat['start']
        times.update([beat['start']+span*.15,(beat['start']+beat['end'])/2,beat['end']-span*.05])
    for track in design['parameter_tracks']+design['timeline']:
        times.update([track['start'],track['start']+track['duration']/2,min(duration-1e-5,track['start']+track['duration'])])
    times=sorted({round(max(0,min(duration-1e-5,t)),6) for t in times})
    # Keep validation milestones; apply the review image budget after mapping
    # to encoded frames, where nearby timestamps often share one frame.
    if len(times)>400:fail('Too many motion milestones for the supported scene contract')
    return times


def motion_report(plan,fps=12,extra_times=()):
    count=math.ceil(plan['duration']*fps);times=sorted({round(min(plan['duration'],i/fps),7) for i in range(count+1)}|set(extra_times))
    issues=[];samples=[];seen=set();checked=0
    def add(issue):
        key=(issue["category"],tuple(issue["object_ids"]),issue["problem"])
        if key not in seen: seen.add(key);issues.append(issue)
    # Precompute independent moving pairs once, rather than scanning tracks
    # for every pair at every sampled frame. Large layout backgrounds are excluded.
    roots={oid:(plan['ancestors'][oid][-1] if plan['ancestors'][oid] else oid) for oid in plan['objects']}
    positioned={b['target'] for b in plan['bindings'] if b['kind']=='position'}
    moving={oid:any(b['action']=='move' for node in [oid]+plan['ancestors'][oid]
                    for b in plan['tracks'].get(node,[])) or oid in positioned for oid in plan['objects']}
    shapes=[oid for oid,obj in plan['objects'].items()
            if obj['kind'] in ('rectangle','circle','image') and obj['width']*obj['height']<.16]
    backgrounds={oid for oid in shapes
                 if plan['objects'][oid]['kind']=='rectangle' and not moving[oid]
                 and set(oid.lower().split('_')) & {'panel','road','card','screen','bg'}}
    pairs=[(aid,bid) for i,aid in enumerate(shapes) for bid in shapes[i+1:]
           if roots[aid]!=roots[bid] and (moving[aid] or moving[bid])
           and aid not in backgrounds and bid not in backgrounds
           and bid not in plan['objects'][aid]['overlap_with']
           and aid not in plan['objects'][bid]['overlap_with']]
    paths=[oid for oid,obj in plan['objects'].items() if obj['kind'] in ('line','plot','graph')]
    dots=[oid for oid,obj in plan['objects'].items() if obj['kind']=='dot']
    def intentional(a,b):
        return b in plan['objects'][a]['overlap_with'] or a in plan['objects'][b]['overlap_with']
    for t in times:
        checked+=1
        try:state=runtime.state_at(plan,t)
        except (ValueError,TypeError,KeyError,ArithmeticError,OverflowError) as exc:
            issues.append(dict(category='relation',time=t,object_ids=[],problem=str(exc),fix='Repair the relation or parameter range.'));break
        visible=[]
        for oid,obj in plan['objects'].items():
            item=state['objects'][oid]
            if item['opacity']<.1:continue
            box=runtime.object_box(plan,oid,state)
            if min(box[0],box[1])<-.002 or max(box[2],box[3])>1.002:
                add(dict(category='layout',time=t,object_ids=[oid],problem='Object leaves the frame during motion',fix='Repair its center/size or group motion path; x/y are box centers.'))
            if obj['kind']=='text':visible.append((oid,box))
        for index,(aid,a) in enumerate(visible):
            for bid,b in visible[index+1:]:
                if bid in plan['objects'][aid]['overlap_with'] or aid in plan['objects'][bid]['overlap_with']:continue
                if min(a[2],b[2])-max(a[0],b[0])>.002 and min(a[3],b[3])-max(a[1],b[1])>.002:
                    add(dict(category='layout',time=t,object_ids=[aid,bid],problem='Text boxes overlap during motion',fix='Separate the text layout or change its visibility times.'))
        # Use the same world-space vertices as Manim, including graph axes
        # and every interior vertex of a polyline. Broad-phase segment boxes
        # avoid running clipping on paths far from a label.
        segments={}
        for oid in paths:
            item=state['objects'][oid]
            if item['opacity']<.1:continue
            pairs_here=(item['segments']+item.get('axes',[])) if plan['objects'][oid]['kind']=='graph' else list(zip(item['points'],item['points'][1:]))
            segments[oid]=[(a,b,(min(a[0],b[0]),min(a[1],b[1]),max(a[0],b[0]),max(a[1],b[1]))) for a,b in pairs_here]
        dot_boxes={oid:runtime.object_box(plan,oid,state) for oid in dots if state['objects'][oid]['opacity']>=.1}
        for aid,box in visible:
            if not state['objects'][aid]['label'].strip():continue
            # Ignore a mere boundary touch, retaining the interior of even
            # very small labels. The box includes scale, rotation and groups.
            ix=min(.002,(box[2]-box[0])/4);iy=min(.002,(box[3]-box[1])/4)
            a=[box[0]+ix,box[1]+iy,box[2]-ix,box[3]-iy]
            problem='Line or graph intersects text box during motion'
            for bid,parts in segments.items():
                if intentional(aid,bid) or ('layout',(aid,bid),problem) in seen:continue
                for start,end,b in parts:
                    if b[2]<a[0] or b[0]>a[2] or b[3]<a[1] or b[1]>a[3]:continue
                    clipped=runtime.clip_segment(start,end,[a[0],a[2],a[1],a[3]])
                    if clipped and math.dist(*clipped)>1e-6:
                        add(dict(category='layout',time=t,object_ids=[aid,bid],problem=problem,
                                 fix='Move the label outside the swept curve/secant/tangent and axes; use a clear exterior offset, such as below-right on a convex upward graph.'))
                        break
            for bid,b in dot_boxes.items():
                if intentional(aid,bid):continue
                if min(a[2],b[2])-max(a[0],b[0])>1e-6 and min(a[3],b[3])-max(a[1],b[1])>1e-6:
                    add(dict(category='layout',time=t,object_ids=[aid,bid],problem='Text box overlaps dot during motion',
                             fix='Offset the label from the dot, preserving the mathematical point; allow room for highlight scaling.'))
        boxes={oid:runtime.object_box(plan,oid,state) for oid in shapes if state['objects'][oid]['opacity']>=.1}
        for aid,bid in pairs:
            if aid not in boxes or bid not in boxes:continue
            a,b=boxes[aid],boxes[bid]
            if min(a[2],b[2])-max(a[0],b[0])>.015 and min(a[3],b[3])-max(a[1],b[1])>.015:
                add(dict(category='layout',time=t,object_ids=[aid,bid],
                         problem=f'Moving object {aid} collides with separate object {bid}',
                         fix='Separate vertical/horizontal lanes; place roadside observers above or below the moving vehicle lane.'))
        if len(issues)>=20:break
        if any(abs(t-x)<1e-5 for x in extra_times):samples.append(state)
    return dict(passed=not issues,sample_count=checked,issues=issues[:20],states=samples)


def validate_v2(design,scene,seconds,assets):
    plan=compile_design(design,scene,seconds,assets)
    report=motion_report(plan)
    if not report['passed']:fail('Motion validation: '+repr(report['issues']))
    return dict(schema_version=2,datasets=[dict(id=g['id'],expression=g['expression'],variable=g['variable'],start=g['x_min'],end=g['x_max'],count=g['samples']) for g in design['graphs']])
