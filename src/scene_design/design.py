"""Subject-independent scene schema, numeric data, layout preview and code checks."""
import ast
import copy
import hashlib
import json
import math
import re
from pathlib import Path

from src.shared._workflow_store import WorkflowError
from src.idea.content import _object, check_schema

TEXT = {'type': 'string'}
NUM = {'type': 'number'}
def array(item): return dict(type='array', items=item)
OBJECT = _object(dict(id=TEXT, kind=TEXT, label=TEXT, x=NUM, y=NUM, width=NUM, height=NUM,
                      color=dict(type='string',pattern='^#[0-9a-fA-F]{6}$'), asset_id=TEXT, points=array(array(NUM))))
BEAT = _object(dict(object_id=TEXT, action=TEXT, start=NUM, duration=NUM, dx=NUM, dy=NUM))
MATH = _object(dict(id=TEXT, expression=TEXT, variable=TEXT, start=NUM, end=NUM, count={'type':'integer'}, unit=TEXT))
DESIGN_SCHEMA = _object(dict(scene_id=TEXT, narration=TEXT, purpose=TEXT, duration=NUM,
    objects=array(OBJECT), timeline=array(BEAT), math=array(MATH), fallback_note=TEXT))
ASSET_SCHEMA = _object(dict(queries=array(TEXT)))
CODE_SCHEMA = _object(dict(source=TEXT, requires_design_change={'type':'boolean'}, reason=TEXT))
DEFAULT_CONFIG = dict(render=dict(quality='l', resolution=[640,360], fps=12, timeout_seconds=300),
                      background='#0D1726', foreground='#F2F6FC', accent='#56DFC5', font='DejaVu Sans')


def fail(message): raise WorkflowError('INVALID_SCENE_DESIGN', message)


def fit_candidate_layout(design):
    """Translate draft objects into the frame; never resize or change approved content."""
    if design.get('schema_version')==2:
        from src.scene_design.scene_contract import compile_design
        compile_design(design)
        return copy.deepcopy(design),[]
    schema_check(design,DESIGN_SCHEMA)
    candidate=copy.deepcopy(design);adjustments=[]
    for obj in candidate['objects']:
        width,height=obj['width'],obj['height']
        # Leave impossible dimensions and invalid centers to the strict validator.
        if not (0<=width<=1 and 0<=height<=1 and 0<=obj['x']<=1 and 0<=obj['y']<=1):continue
        x=min(max(obj['x'],width/2),1-width/2)
        y=min(max(obj['y'],height/2),1-height/2)
        if x!=obj['x'] or y!=obj['y']:
            adjustments.append(dict(object_id=obj['id'],before=dict(x=obj['x'],y=obj['y']),after=dict(x=x,y=y)))
            obj.update(x=x,y=y)
    return candidate,adjustments


def schema_check(value, schema, path='design'):
    # Keep structural errors precise; semantic checks below report all object errors.
    kind = schema['type']
    if kind in ('number', 'integer'):
        if type(value) not in ((int,) if kind == 'integer' else (int,float)) or not math.isfinite(value):
            fail(path+': Numeric data must be finite')
    elif kind == 'object':
        if not isinstance(value, dict): fail(path+': Expected object')
        missing=set(schema['properties'])-set(value);extra=set(value)-set(schema['properties'])
        if missing or extra: fail(path+': Missing fields '+repr(sorted(missing))+'; unknown fields '+repr(sorted(extra)))
        for k,v in value.items(): schema_check(v, schema['properties'][k],path+'.'+k)
    elif kind == 'array':
        if not isinstance(value,list): fail(path+': Expected array')
        for i,v in enumerate(value): schema_check(v,schema['items'],path+'['+str(i)+']')
    elif kind == 'string':
        if not isinstance(value,str): fail(path+': Expected string')
    elif kind == 'boolean':
        if type(value) is not bool:fail(path+': Expected boolean')
    else: check_schema(value,schema,path)


def design_schema(assets):
    """Make unavailable resources impossible to request in a fresh structured output."""
    schema=copy.deepcopy(DESIGN_SCHEMA)
    obj=schema['properties']['objects']['items']['properties']
    obj['kind']=dict(type='string',enum=['text','rectangle','circle','line','plot']+(['image'] if assets else []))
    obj['asset_id']=dict(type='string',enum=['']+[a['asset_id'] for a in assets])
    schema['properties']['objects'].update(minItems=1,maxItems=60)
    schema['properties']['timeline']['items']['properties']['action']=dict(type='string',enum=['appear','move','fade','highlight'])
    return schema


def normalize_candidate_design(design, config):
    """Only normalize unambiguous formatting/defaults in an unapproved draft."""
    candidate,adjustments=fit_candidate_layout(design)
    for i,obj in enumerate(candidate['objects']):
        original=obj['color'];color=original.strip()
        if not color:
            color=config['foreground']
        elif re.fullmatch(r'#[0-9a-fA-F]{3}',color):
            color='#'+''.join(c*2 for c in color[1:])
        elif re.fullmatch(r'[0-9a-fA-F]{6}',color):
            color='#'+color
        if re.fullmatch(r'#[0-9a-fA-F]{6}',color):color=color.upper()
        if color!=original:
            obj['color']=color
            adjustments.append(dict(object_id=obj['id'],field='objects['+str(i)+'].color',before=original,after=color))
    return candidate,adjustments


def evaluate(expression, variable, value):
    """Bounded arithmetic interpreter; never calls eval/exec."""
    funcs = {k:getattr(math,k) for k in ('sin','cos','tan','sqrt','exp','log','fabs')}
    tree = ast.parse(expression, mode='eval')
    if len(list(ast.walk(tree))) > 80: fail('Expression too complex')
    def walk(n):
        if isinstance(n, ast.Constant) and type(n.value) in (int,float): return n.value
        if isinstance(n, ast.Name) and n.id == variable: return value
        if isinstance(n, ast.Name) and n.id in ('pi','e'): return getattr(math,n.id)
        if isinstance(n, ast.UnaryOp) and isinstance(n.op,(ast.UAdd,ast.USub)):
            return walk(n.operand) * (-1 if isinstance(n.op,ast.USub) else 1)
        if isinstance(n,ast.BinOp):
            a,b = walk(n.left),walk(n.right)
            if isinstance(n.op,ast.Add): return a+b
            if isinstance(n.op,ast.Sub): return a-b
            if isinstance(n.op,ast.Mult): return a*b
            if isinstance(n.op,ast.Div): return a/b
            if isinstance(n.op,ast.Pow) and abs(b)<=20 and abs(a)<=1e12: return a**b
        if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id in funcs and len(n.args)==1 and not n.keywords:
            return funcs[n.func.id](walk(n.args[0]))
        fail('Unsupported mathematical expression')
    result = walk(tree.body)
    if type(result) not in (int,float) or not math.isfinite(result) or abs(result)>1e12: fail('Invalid computed value')
    return result


def validate_design(design, scene, seconds, assets):
    if design.get('schema_version')==2:
        from src.scene_design.scene_contract import validate_v2
        return validate_v2(design,scene,seconds,assets)
    schema_check(design,DESIGN_SCHEMA)
    issues=[]
    def issue(path,message):issues.append(path+': '+message)
    if design['scene_id'] != scene['scene_id']:issue('scene_id','Approved scene ID changed')
    if design['narration'] != scene['narration']:issue('narration','Approved narration changed; copy it EXACTLY')
    if abs(design['duration']-seconds)>1e-6:issue('duration','Must equal target_seconds '+repr(seconds))
    if not design['purpose'].strip():issue('purpose','Expected a nonempty purpose')
    if not 1<=len(design['objects'])<=60:issue('objects','Object limit: 1..60')
    if len(design['timeline'])>120:issue('timeline','Timeline limit: 120')
    ids=set();asset_ids={a['asset_id'] for a in assets}
    for i,obj in enumerate(design['objects']):
        path='objects['+str(i)+'] ('+obj['id']+')'
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*',obj['id']) or obj['id'] in ids:issue(path+'.id','Invalid/duplicate object ID')
        ids.add(obj['id'])
        if obj['kind'] not in ('text','rectangle','circle','line','image','plot'):issue(path+'.kind','Unsupported object kind')
        if not re.fullmatch(r'#[0-9a-fA-F]{6}',obj['color']):issue(path+'.color','Use hex colors (#RRGGBB); received '+repr(obj['color']))
        line=obj['kind']=='line'
        valid_size=(0<=obj['width']<=1 and 0<=obj['height']<=1 and (obj['width']>0 or obj['height']>0)) if line else (0<obj['width']<=1 and 0<obj['height']<=1)
        if not valid_size:issue(path,'Invalid size for '+obj['id']+': width='+repr(obj['width'])+', height='+repr(obj['height'])+'; a line may be horizontal or vertical')
        if not (obj['width']/2-1e-9<=obj['x']<=1-obj['width']/2+1e-9 and obj['height']/2-1e-9<=obj['y']<=1-obj['height']/2+1e-9):
            issue(path,'Object '+obj['id']+' outside frame: center='+repr((obj['x'],obj['y']))+', size='+repr((obj['width'],obj['height'])))
        if obj['kind']=='text' and not obj['label'].strip():issue(path+'.label','Empty text object: supply visible text, or remove this object and its timeline entries')
        if obj['kind']=='plot' and len(obj['points'])<2:issue(path+'.points','Plot requires at least two points')
        if obj['kind']=='image' and obj['asset_id'] not in asset_ids:
            issue(path+'.asset_id','Unknown image: choose an available_assets ID, or replace the image with actual rectangle/circle/line vector objects. An empty asset_id is not a vector fallback')
        if obj['kind']!='image' and obj['asset_id']:issue(path+'.asset_id','Unexpected image reference on a vector object')
        if any(len(p)!=2 or any(not 0<=v<=1 for v in p) for p in obj['points']) or len(obj['points'])>200:issue(path+'.points','Invalid plot points; expected 2D normalized coordinates, at most 200')
    for i,beat in enumerate(design['timeline']):
        path='timeline['+str(i)+']'
        if beat['object_id'] not in ids:issue(path+'.object_id','Unknown animation reference '+repr(beat['object_id']))
        if beat['action'] not in ('appear','move','fade','highlight'):issue(path+'.action','Unsupported animation action')
        if beat['start']<0 or beat['duration']<=0 or beat['start']+beat['duration']>seconds+1e-6:issue(path,'Animation outside scene; start >= 0, duration > 0, end <= '+repr(seconds))
    data=[]
    if len(design['math'])>10:issue('math','Too many mathematical datasets; at most 10')
    dataset_ids=set()
    for i,spec in enumerate(design['math']):
        path='math['+str(i)+']'
        if spec['id'] in dataset_ids:issue(path+'.id','Duplicate dataset ID')
        dataset_ids.add(spec['id'])
        if not spec['id'] or not re.fullmatch('[A-Za-z]+',spec['variable']) or not 2<=spec['count']<=200 or spec['end']<=spec['start']:
            issue(path,'Invalid math sampling');continue
        try:
            points=[]
            for k in range(spec['count']):
                x=spec['start']+(spec['end']-spec['start'])*k/(spec['count']-1)
                points.append([x,evaluate(spec['expression'],spec['variable'],x)])
            data.append(dict(spec,points=points))
        except (WorkflowError,ValueError,SyntaxError,ArithmeticError,RecursionError) as exc:issue(path+'.expression','Invalid math: '+str(exc))
    if issues:fail('\n'.join(issues))
    return dict(datasets=data)


def layout_png(design, assets, config):
    from PIL import Image, ImageDraw, ImageFont
    import io
    width,height=config['render']['resolution']
    image=Image.new('RGB',(width,height),config['background']); draw=ImageDraw.Draw(image)
    font=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',max(12,int(height*.045)))
    for obj in design['objects']:
        x,y,w,h=(obj[k]*scale for k,scale in (('x',width),('y',height),('width',width),('height',height)))
        box=(int(x-w/2),int(y-h/2),int(x+w/2),int(y+h/2))
        if obj['kind']=='image':
            asset=next(a for a in assets if a['asset_id']==obj['asset_id'])
            with Image.open(asset['_absolute_path']) as source:
                source=source.convert('RGB'); source.thumbnail((max(1,int(w)),max(1,int(h))))
                image.paste(source,(int(x-source.width/2),int(y-source.height/2)))
        elif obj['kind']=='circle': draw.ellipse(box,outline=obj['color'],width=2)
        elif obj['kind']=='line': draw.line((box[0],box[1],box[2],box[3]),fill=obj['color'],width=2)
        elif obj['kind']=='plot' and len(obj['points'])>=2:
            draw.line([(box[0]+p[0]*w,box[3]-p[1]*h) for p in obj['points']],fill=obj['color'],width=2)
        elif obj['kind']!='text': draw.rectangle(box,outline=obj['color'],width=2)
        if obj['label']: draw.text((box[0]+3,box[1]+3),obj['label'],font=font,fill=obj['color'])
    stream=io.BytesIO(); image.save(stream,format='PNG'); return stream.getvalue()


def design_markdown(d):
    if d.get('schema_version')==2:
        lines=['# '+d['purpose'],'','## Lời dẫn đã duyệt',d['narration'],'','## Thiết kế có quan hệ',f"Mẫu: {d['template']} · Thời lượng: {d['duration']:.3f}s",'']
        lines.extend('- '+o['id']+': '+o['kind']+' — '+o['label'] for o in d['objects'])
        lines.extend(['','## Nhóm / tham số / quan hệ',json.dumps({k:d[k] for k in ('groups','parameters','graphs','bindings','parameter_tracks','timeline')},ensure_ascii=False,indent=2)])
        return '\n'.join(lines)

    lines=[f"# {d['scene_id']} — {d['purpose']}", '', f"Thời lượng: {d['duration']:.3f} giây", '', '## Lời đọc đã duyệt',d['narration'],'','## Bố cục']
    lines += [f"- {o['id']}: {o['kind']} — {o['label']}; vị trí ({o['x']}, {o['y']}), kích thước {o['width']} × {o['height']}; ảnh: {o['asset_id'] or 'không'}" for o in d['objects']]
    lines += ['','## Chuyển động']+[f"- {b['start']:.3f}s + {b['duration']:.3f}s: {b['object_id']} {b['action']} ({b['dx']}, {b['dy']})" for b in d['timeline']]
    lines += ['','## Dữ liệu toán',json.dumps(d['math'],ensure_ascii=False),'','## Phương án ảnh',d['fallback_note'],'','Thiết kế được render bằng Manim thành video chuyển động để xem và duyệt. Bản dựng không tiếng sử dụng đúng video đã duyệt.']
    return '\n'.join(lines)+'\n'


def validate_code(source, assets):
    """Reject non-scene operations. This is a code policy, not an OS sandbox."""
    try: tree=ast.parse(source)
    except SyntaxError as exc: raise WorkflowError('INVALID_SCENE_CODE',str(exc)) from None
    forbidden={'open','eval','exec','compile','getattr','setattr','delattr','globals','locals','vars','input','breakpoint','__import__','config'}
    for node in ast.walk(tree):
        if isinstance(node,(ast.Import,ast.ImportFrom)):
            modules=[a.name for a in node.names] if isinstance(node,ast.Import) else [node.module or '']
            allowed_modules = ('manim','math','numpy','manim_kit','src.scene_design.manim_kit')
            if any(m not in allowed_modules for m in modules) or getattr(node,'level',0):
                raise WorkflowError('INVALID_SCENE_CODE','Only manim, manim_kit, math and numpy imports are supported')
        if isinstance(node,ast.Name) and (node.id in forbidden or node.id.startswith('__')): raise WorkflowError('INVALID_SCENE_CODE','Forbidden operation')
        if isinstance(node,ast.Attribute) and (node.attr.startswith('_') or node.attr in ('read_text','write_text','open','system','popen','load','save','fromfile','tofile','ctypeslib','memmap','add_sound','loadtxt','savetxt','genfromtxt','savez','savez_compressed')): raise WorkflowError('INVALID_SCENE_CODE','Forbidden attribute')
        if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id=='ImageMobject':
            allowed={a['path'] for a in assets}
            if not node.args or not isinstance(node.args[0],ast.Constant) or node.args[0].value not in allowed:
                raise WorkflowError('INVALID_SCENE_CODE','ImageMobject requires a literal approved asset path')
    classes=[n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='GeneratedScene' and any(isinstance(b,ast.Name) and b.id=='Scene' for b in n.bases)]
    if len(classes)!=1: raise WorkflowError('INVALID_SCENE_CODE','Expected GeneratedScene(Scene)')
    return source
