"""Trusted Manim compiler and frame/state evidence for v2 scene designs."""
import hashlib
import json
import math
from pathlib import Path
from tempfile import TemporaryDirectory
from PIL import Image, ImageDraw, ImageFont
from src.shared import _workflow_store as store
from src.shared._workflow_store import WorkflowError
from src.scene_design.scene_contract import compile_design, important_times
from src.scene_design.scene_state import state_at, path_samples
from src.scene_design.design import validate_code
from src.shared.media_tools import binary,run

PROGRAM=r'''
class GeneratedScene(Scene):
    def construct(self):
        self.camera.background_color=STYLE['background']
        frame_w=8*PLAN['aspect'];frame_h=8
        def point(value):return np.array([(value[0]-.5)*frame_w,(.5-value[1])*frame_h,0])
        text_cache={}
        def make(oid,item):
            spec=PLAN['objects'][oid];kind=spec['kind'];opacity=item['opacity']
            width=max(item['width']*frame_w,.001);height=max(item['height']*frame_h,.001)
            color=spec['color'];center=point(item['center'])
            hl=item.get('highlight',0);scale_factor=item.get('scale',1)
            color=('#FACC15' if color.upper()==STYLE['accent'].upper() else STYLE['accent']) if hl>.18 else color
            if kind=='text':
                key=(oid,item['label'],color)
                if key not in text_cache:text_cache[key]=Text(item['label'],font=STYLE['font'],font_size=26,color=color)
                mob=text_cache[key].copy()
                factor=min(1,spec['width']*frame_w/max(mob.width,1e-8),spec['height']*frame_h/max(mob.height,1e-8))*scale_factor
                mob.scale(factor).rotate(-math.radians(item['angle'])).move_to(center).set_opacity(opacity)
            elif kind in ('rectangle','circle','dot'):
                name=oid.lower()
                if kind=='rectangle':
                    car=('car' in name and 'body' in name) or name=='car'
                    panel=any(word in name for word in ('panel','card','box','road'))
                    fill=.85 if car else .90 if 'road' in name else .35 if panel else .28
                    radius=.25 if car else .15 if panel else .12
                    mob=RoundedRectangle(width=width,height=height,corner_radius=min(width,height)*radius,
                        color=color,fill_color=color,fill_opacity=fill*opacity,
                        stroke_opacity=opacity,stroke_width=1.8 if panel else 2)
                else:
                    fill=1 if kind=='dot' else .75 if 'wheel' in name else .28
                    mob=Ellipse(width=width,height=height,color=color,fill_color=color,
                        stroke_color=STYLE['background'] if kind=='dot' else color,
                        fill_opacity=fill*opacity,stroke_opacity=opacity,stroke_width=2.5 if kind=='dot' else 2)
                mob.rotate(-math.radians(item['angle'])).move_to(center)
            elif kind in ('line','plot'):
                pts=[point(p) for p in item['points']]
                if kind=='line' and len(pts)==2:mob=Line(pts[0],pts[-1],color=color,stroke_opacity=opacity,stroke_width=3 if hl>.18 else 2.4)
                else:mob=VMobject(color=color,stroke_opacity=opacity,stroke_width=3).set_points_as_corners(pts)
            elif kind=='graph':
                parts=[Line(point(a),point(b),color=STYLE['foreground'],stroke_width=1.4,stroke_opacity=.6*opacity) for a,b in item['axes']]
                paths=[]
                for a,b in item['segments']:
                    if paths and np.linalg.norm(point(paths[-1][-1])-point(a))<1e-6:paths[-1].append(b)
                    else:paths.append([a,b])
                for pts in paths:parts.append(VMobject(color=color,stroke_width=3,stroke_opacity=opacity).set_points_as_corners([point(p) for p in pts]))
                mob=VGroup(*parts)
            else:
                mob=IMAGES[oid].copy();mob.scale(min(width/max(mob.width,1e-8),height/max(mob.height,1e-8)))
                mob.rotate(-math.radians(item['angle'])).move_to(center).set_opacity(opacity)
            return mob
        clock=ValueTracker(0);self.add(clock)
        current=state_at(PLAN,0);previous=dict(current['objects']);mobs={oid:make(oid,item) for oid,item in current['objects'].items()}
        self.add(*mobs.values());self.design_audit=[];seen=set()
        def normalized(value):return [float(value[0]/frame_w+.5),float(.5-value[1]/frame_h)]
        def update(controller,dt):
            t=clock.get_value();state=state_at(PLAN,t)
            for oid,item in state['objects'].items():
                old=previous[oid]
                if item==old:continue
                kind=PLAN['objects'][oid]['kind']
                if kind in ('rectangle','circle','dot','image','text') and all(
                        item[key]==old[key] for key in item if key!='center'):
                    mobs[oid].shift(point(item['center'])-point(old['center']))
                else:
                    mobs[oid].become(make(oid,item))
                previous[oid]=item
            index=int(round(t*RENDER_FPS))
            if index in AUDIT_FRAMES and index not in seen:
                seen.add(index);actual={}
                for oid,mob in mobs.items():
                    item=state['objects'][oid];kind=PLAN['objects'][oid]['kind']
                    actual[oid]=dict(center=normalized(mob.get_center()),width=float(mob.width/frame_w),height=float(mob.height/frame_h),label=item['label'],opacity=item['opacity'])
                    if kind in ('line','plot'):
                        actual[oid]['points']=[normalized(mob.get_start()),normalized(mob.get_end())]
                        vertices=[normalized(p) for p in mob.get_start_anchors()]+[normalized(mob.get_end())]
                        actual[oid]['path_point_count']=len(vertices)
                        actual[oid]['path_samples']=path_samples(vertices)
                    if kind in ('text','rectangle','circle','dot'):
                        painted=next((part for part in mob.get_family() if part.get_num_points()>0),mob)
                        actual[oid]['fill_color']=painted.get_fill_color().to_hex()
                        actual[oid]['stroke_color']=painted.get_stroke_color().to_hex()
                        actual[oid]['stroke_width']=float(painted.get_stroke_width())
                self.design_audit.append(dict(frame=index,time=float(t),parameters=state['parameters'],objects=actual))
        controller=VMobject();controller.add_updater(update);self.add(controller);update(controller,0)
        self.play(clock.animate.set_value(PLAN['duration']),run_time=PLAN['duration'],rate_func=linear)
'''


def frame_times(design,story,fps):
    last=max(0,math.ceil(design['duration']*fps)-1)
    frames=sorted({min(last,max(0,int(math.floor(t*fps+1e-6)))) for t in important_times(design,story)})
    return frames


def review_frames(design,story,fps,frames):
    """Bound image input independently of the complete geometric audit."""
    candidates=set(frames)
    if len(candidates)<=64:return sorted(candidates)
    last=max(0,math.ceil(design['duration']*fps)-1)
    def encoded(t):
        return min(last,max(0,int(math.floor(t*fps+1e-6))))
    # At most 26 anchors for the contract's eight beats. Keep every beat's
    # beginning/middle/end sample, plus the video endpoints.
    anchors={0,last}
    for beat in story['beats']:
        span=beat['end']-beat['start']
        anchors.update(encoded(t) for t in (
            beat['start']+span*.15,(beat['start']+beat['end'])/2,beat['end']-span*.05))
    selected=anchors & candidates
    def fill(pool):
        remaining=set(pool)-selected
        while remaining and len(selected)<64:
            # Cover the largest unsampled temporal gap; earlier frame wins ties.
            frame=max(remaining,key=lambda f:(min(abs(f-s) for s in selected),-f))
            selected.add(frame);remaining.remove(frame)
    # A short highlight may otherwise fall between uniformly selected frames.
    peaks={encoded(t['start']+t['duration']/2) for t in design['timeline'] if t['action']=='highlight'}
    fill(peaks & candidates)
    fill(candidates)
    return sorted(selected)


def source_for(design,assets,style,story):
    plan=compile_design(design,assets=assets)
    if abs(plan['aspect']-style['render']['resolution'][0]/style['render']['resolution'][1])>1e-6:
        raise WorkflowError('INVALID_SCENE_DESIGN','aspect_ratio must match the configured video format')
    fps=style['render']['fps'];frames=frame_times(design,story,fps)
    engine=Path(__file__).with_name('scene_state.py').read_text(encoding='utf-8')
    paths={a['asset_id']:a['path'] for a in assets}
    images=', '.join(repr(o['id'])+': ImageMobject('+repr(paths[o['asset_id']])+')' for o in design['objects'] if o['kind']=='image')
    source=('from manim import *\nimport numpy as np\n'+engine+'\nPLAN = '+repr(plan)+'\nSTYLE = '+repr(style)+'\nRENDER_FPS = '+repr(fps)+'\nAUDIT_FRAMES = '+repr(frames)+'\nIMAGES = {'+images+'}\n'+PROGRAM)
    return validate_code(source,assets),plan,frames


def _audit(plan,actual,frames,fps):
    issues=[];found={row['frame']:row for row in actual}
    for frame in frames:
        row=found.get(frame)
        if not row:
            issues.append(dict(category='relation',time=frame/fps,object_ids=[],problem='Missing actual Manim state for keyframe',fix='Repair the renderer audit capture.'));continue
        expected=state_at(plan,row['time'])
        for oid,spec in plan['objects'].items():
            rendered=row['objects'].get(oid);target=expected['objects'][oid]
            if rendered is None:
                issues.append(dict(category='relation',time=row['time'],object_ids=[oid],problem='Object missing from rendered state',fix='Repair renderer object mapping.'));continue
            if spec['kind'] in ('dot','circle','text','rectangle','image'):
                if math.dist(target['center'],rendered['center'])>1e-5:
                    issues.append(dict(category='relation',time=row['time'],object_ids=[oid],problem='Rendered position differs from bound model',fix='Repair the renderer relation update.'))
            if spec['kind'] in ('line','plot'):
                expected_points=[target['points'][0],target['points'][-1]]
                if max(math.dist(a,b) for a,b in zip(expected_points,rendered.get('points',[])))>1e-5:
                    issues.append(dict(category='relation',time=row['time'],object_ids=[oid],problem='Rendered line endpoints violate the relation model',fix='Repair line endpoints in the renderer.'))
            if spec['kind'] in ('line','plot'):
                expected_samples=path_samples(target['points'])
                actual_samples=rendered.get('path_samples',[])
                if (rendered.get('path_point_count')!=len(target['points']) or len(actual_samples)!=len(expected_samples)
                    or any(a.get('index')!=b['index'] or math.dist(a.get('point',[]),b['point'])>1e-5 for a,b in zip(actual_samples,expected_samples))):
                    issues.append(dict(category='relation',time=row['time'],object_ids=[oid],problem='Rendered path loses or changes intermediate vertices',fix='Preserve all declared path vertices and render the shared state geometry.'))
            if spec['kind']=='text' and target['label']!=rendered['label']:
                issues.append(dict(category='relation',time=row['time'],object_ids=[oid],problem='Rendered numerical label differs from its control',fix='Repair the bound value label.'))
    return dict(passed=not issues,checked_keyframes=len(frames),issues=issues[:20],states=actual)


def evidence(root,directory,reference,design,story,plan,frames,style):
    metadata=store.read_json(root/reference['metadata_path'])
    actual=metadata['video'].get('design_audit',[])
    report=_audit(plan,actual,frames,style['render']['fps'])
    store.atomic_write(directory/'render_checks.json',report)
    # Extract the encoded video frames, not a synthetic drawing of the JSON.
    target=directory/'frames';target.mkdir(exist_ok=True)
    fps=style['render']['fps'];paths=[]
    # A fresh directory prevents a resumed round from using stale batch files.
    ordered=review_frames(design,story,fps,frames)
    if ordered:
        with TemporaryDirectory(prefix='.batch-',dir=target) as temporary:
            batch=Path(temporary)
            select_expr='+'.join(f'eq(n\\,{frame})' for frame in ordered)
            run([binary('ffmpeg'),'-y','-v','error','-i',str(root/reference['path']),
                 '-vf','select='+select_expr,'-vsync','0',str(batch/'batch_%05d.png')])
            for index,frame in enumerate(ordered,start=1):
                path=target/f'frame_{frame:05}.png';encoded=batch/f'batch_{index:05}.png'
                if encoded.is_file():
                    encoded.replace(path)
                else:
                    # A missing terminal frame must not reuse an old image.
                    path.unlink(missing_ok=True)
                    run([binary('ffmpeg'),'-y','-v','error','-i',str(root/reference['path']),
                         '-vf',f'select=eq(n\\,{frame})','-vsync','0','-frames:v','1',str(path)])
                if not path.is_file():
                    raise WorkflowError('RENDER_FAILED','No encoded video frame at '+str(frame))
                paths.append(dict(frame=frame,time=frame/fps,path=str(path.relative_to(root)),sha256=store.digest(path)))
    sheets=[];width,height=style['render']['resolution'];thumb_width=640 if width>=height else 300;thumb_height=round(height*thumb_width/width)
    font=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',18)
    for index in range(0,len(paths),6):
        chunk=paths[index:index+6];canvas=Image.new('RGB',(2*thumb_width,3*(thumb_height+32)),'#f3f6f8');draw=ImageDraw.Draw(canvas)
        for cell,ref in enumerate(chunk):
            x=(cell%2)*thumb_width;y=(cell//2)*(thumb_height+32)
            with Image.open(root/ref['path']) as image:canvas.paste(image.convert('RGB').resize((thumb_width,thumb_height)),(x,y+32))
            draw.text((x+8,y+5),f"t={ref['time']:.3f}s · frame {ref['frame']}",font=font,fill='#173746')
        path=directory/f'contact_sheet_{index//6:02}.jpg';canvas.save(path,quality=86)
        sheets.append(dict(path=str(path.relative_to(root)),sha256=store.digest(path),frames=chunk))
    manifest=dict(frames=paths,contact_sheets=sheets,video_sha256=reference['sha256'],render_checks_path=str((directory/'render_checks.json').relative_to(root)),
                  sampling=dict(audit_frame_count=len(set(frames)),review_frame_count=len(ordered),limit=64,
                                strategy='beat anchors, highlight peaks, then temporal coverage'))
    store.atomic_write(directory/'evidence.json',manifest)
    return report,manifest


def render(root,directory,job,design,assets,style,story):
    from src.scene_design import scene_generation as sg
    source,plan,frames=source_for(design,assets['assets'],style,story)
    checksum=hashlib.sha256(source.encode()).hexdigest();record=job.get('relation_render')
    if record and record['source_sha256']==checksum and record['status']=='success':
        ref=record['reference']
        for key,digest in [('path','sha256'),('code_path','code_sha256')]:
            if store.digest(root/ref[key])!=ref[digest]:raise WorkflowError('ARTIFACT_CHANGED','Cached v2 render changed')
    else:
        version=sg._clip_version(root,design['scene_id']);path=f"scenes/{design['scene_id']}_{version}.py"
        store.materialize(root,path,content=source.encode())
        job['relation_render']=dict(source_sha256=checksum,status='running',code_path=path,version=version)
        store.atomic_write(directory/'job.json',job)
        result=sg.RENDERER(root,design['scene_id'],version,path,'GeneratedScene',dict(style['render'],target_duration=design['duration']))
        if result['status']!='success':raise WorkflowError('RENDER_FAILED',result.get('error') or 'Manim render failed')
        metadata=store.read_json(Path(result['log_path']).with_suffix('.json'))
        if abs(metadata['video']['duration_seconds']-design['duration'])>2/style['render']['fps']+1e-6:raise WorkflowError('SCENE_DURATION_MISMATCH','Rendered duration differs from the fixed input duration')
        ref=sg.render_reference(root,design['scene_id'],Path(result['video_path']),dict(clip_version=version,code_path=path))
        # Audit is available in the metadata; keep the public video summary small.
        ref['video'].pop('design_audit',None)
        job['relation_render']=dict(source_sha256=checksum,status='success',reference=ref)
        store.atomic_write(directory/'job.json',job)
    checks,manifest=evidence(root,directory,ref,design,story,plan,frames,style)
    return ref,checks,manifest
