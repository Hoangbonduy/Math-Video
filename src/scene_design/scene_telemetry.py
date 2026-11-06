"""Inspect actual Cairo geometry at every encoded frame; no synthetic layout model."""
import math
from src.scene_design.scene_theme import profile


class LayoutTelemetry:
    def __init__(self, scene, fps):
        self.scene, self.fps = scene, fps
        self.frame = 0
        self.events, self.issues, self.samples = [], [], []
        self.candidates, self.candidate_seen = [], set()
        self.seen = set()
        self.object_ids = set()
        self.previous = None
        self.early_motion = False
        self.active_motion = None
        self.font_corrections = {}
        self.active_fonts = {}

    def snapshot(self):
        from manim import Text, MathTex, Tex
        registry = dict(getattr(self.scene, 'layout_registry', {}))
        covered = {id(part) for entry in registry.values()
                   for part in entry['mobject'].get_family()}
        # Unregistered text still participates in readability/collision checks.
        for mob in self.scene.get_mobject_family_members():
            if isinstance(mob, (Text, MathTex, Tex)) and id(mob) not in covered:
                oid = 'untracked_'+str(id(mob))
                registry[oid] = dict(mobject=mob, role='text', overlap_with=[])
                covered.update(id(part) for part in mob.get_family())
        visible = {id(m) for m in self.scene.get_mobject_family_members()}
        result = {}
        for oid, entry in registry.items():
            mob = entry['mobject']
            if id(mob) not in visible or not mob.has_points() and not mob.submobjects:
                continue
            family = [p for p in mob.get_family() if p.has_points()]
            if not family:
                continue
            def opacity(p):
                return max(float(p.get_fill_opacity()) if hasattr(p, 'get_fill_opacity') else 1,
                           float(p.get_stroke_opacity()) if hasattr(p, 'get_stroke_opacity') else 1)
            if max(opacity(p) for p in family) < .15:
                continue
            box = [float(mob.get_left()[0]), float(mob.get_right()[0]),
                   float(mob.get_bottom()[1]), float(mob.get_top()[1])]
            if not all(math.isfinite(v) for v in box):
                raise ValueError('Nonfinite render geometry: '+oid)
            result[oid] = dict(role=entry['role'], box=[round(v,5) for v in box],
                               overlap_with=entry['overlap_with'])
            if entry['role'] in ('text','formula'):
                result[oid]['text'] = str(getattr(mob, 'text', getattr(mob, 'tex_string', '')))
                if hasattr(mob, 'font_size'):
                    raw=float(mob.font_size)*self.font_corrections.get(id(mob),1)
                    transition=self.active_fonts.get(id(mob))
                    if transition:
                        source_size,target_size,start,length,rate=transition
                        alpha=min(1,max(0,(self.frame-start)/max(1,length-1)))
                        raw=source_size+(target_size-source_size)*rate(alpha)
                    result[oid]['font_size'] = round(raw, 3)
        self.object_ids.update(registry)
        return result

    def capture(self, count=1):
        objects = self.snapshot()
        row = dict(frame=self.frame, time=round(self.frame/self.fps,6), objects=objects)
        if self.active_motion is not None:
            for oid,item in objects.items():
                if item['role']=='background':continue
                box=item['box']
                stat=self.active_motion.setdefault(oid,dict(first_frame=self.frame,last_frame=self.frame,
                    first_box=box,last_box=box,box_min=box,box_max=box,changed_frames=0))
                if box!=stat['last_box']:
                    stat['first_change_frame']=stat.get('first_change_frame',self.frame)
                    stat['last_change_frame']=self.frame
                    stat['changed_frames']+=1
                stat['last_box']=box;stat['last_frame']=self.frame+count-1
                stat['box_min']=[min(a,b) for a,b in zip(stat['box_min'],box)]
                stat['box_max']=[max(a,b) for a,b in zip(stat['box_max'],box)]
        bounds = profile()['safe_bounds']
        def issue(ids, problem, fix, category='layout'):
            key = (tuple(ids), problem)
            if key not in self.seen and len(self.issues)<50:
                self.seen.add(key)
                self.issues.append(dict(category=category, time=row['time'],
                    object_ids=ids, problem=problem, fix=fix))
                if len(self.samples)<100:
                    self.samples.append(row)
        if not objects and self.frame == 0:
            issue([], 'First encoded frame has no visible tracked content', 'Show a visual anchor at t=0', 'timing')
        for oid, item in objects.items():
            if item['role']=='background':
                continue
            a = item['box']
            if a[0]<bounds[0]-.02 or a[1]>bounds[1]+.02 or a[2]<bounds[2]-.02 or a[3]>bounds[3]+.02:
                issue([oid], 'Rendered object leaves the safe area', 'Adjust shift/scale/path; split the plan if minimum readable text cannot fit')
            if item.get('font_size',100) < profile()['min_font_size']-.1:
                issue([oid], 'Rendered font is below the readability minimum',
                      f'Measured font_size={item["font_size"]}; minimum={profile()["min_font_size"]}. '
                      'Use vn_text(content, size=28) and remove undersizing scale factors; wrap or split only if needed.')
        def candidate(ids, problem):
            key=(tuple(ids),problem)
            if key not in self.candidate_seen and len(self.candidates)<40:
                self.candidate_seen.add(key)
                self.candidates.append(dict(time=row['time'],object_ids=ids,problem=problem,
                    bbox_only=True))
        foreground=[(oid,item) for oid,item in objects.items() if item['role']!='background']
        for oid,item in foreground:
            a=item['box']
            if min(a[0]-bounds[0],bounds[1]-a[1],a[2]-bounds[2],bounds[3]-a[3])<.12:
                candidate([oid],'Rendered object is close to a safe-area edge')
        for i,(aid,a) in enumerate(foreground):
            for bid,b in foreground[i+1:]:
                if a['role'] in ('text','formula') and b['role'] in ('text','formula'):
                    continue
                if bid in a['overlap_with'] or aid in b['overlap_with']:
                    continue
                x,y=a['box'],b['box']
                if min(x[1],y[1])-max(x[0],y[0])>.02 and min(x[3],y[3])-max(x[2],y[2])>.02:
                    candidate([aid,bid],'Possible rendered geometry overlap; inspect actual visible paths')
        labels = [(oid,item) for oid,item in objects.items() if item['role'] in ('text','formula')]
        for i,(aid,a) in enumerate(labels):
            for bid,b in labels[i+1:]:
                if bid in a['overlap_with'] or aid in b['overlap_with']:
                    continue
                x,y=a['box'],b['box']
                if min(x[1],y[1])-max(x[0],y[0])>.02 and min(x[3],y[3])-max(x[2],y[2])>.02:
                    issue([aid,bid], 'Rendered text bounding boxes overlap', 'Change buff/shift/visibility or motion timing')
        geometry = {k:v['box'] for k,v in objects.items() if v['role']!='background'}
        if self.previous is not None and self.frame/self.fps<=.8 and geometry!=self.previous:
            self.early_motion = True
        self.previous = geometry
        # One dense scan, bounded retained state. Frozen spans are represented once.
        if (self.frame == 0 or self.frame % max(1,round(self.fps)) == 0) and len(self.samples)<100:
            self.samples.append(row)
        self.frame += count

    def install(self):
        renderer = self.scene.renderer
        original_render, original_freeze = renderer.render, renderer.freeze_current_frame
        def render(scene, time, moving_mobjects=None):
            original_render(scene, time, moving_mobjects)
            self.capture()
        def freeze(duration):
            count = int(duration*self.fps)
            original_freeze(duration)
            if count:
                self.capture(count)
        renderer.render, renderer.freeze_current_frame = render, freeze
        original_play = self.scene.play
        def play(*args, **kwargs):
            start = self.frame
            registry=getattr(self.scene,'layout_registry',{})
            animations=[];affected=set();font_targets=[]
            def describe(animation):
                nested=getattr(animation,'animations',())
                if nested:
                    for child in nested:describe(child)
                    return
                mob=getattr(animation,'mobject',None)
                family={id(p) for p in mob.get_family()} if mob is not None else set()
                ids=[oid for oid,entry in registry.items()
                     if family & {id(p) for p in entry['mobject'].get_family()}]
                affected.update(ids)
                entry=dict(type=type(animation).__name__,object_ids=ids)
                target=getattr(animation,'target_mobject',None)
                builder=type(animation).__name__=='_AnimationBuilder'
                if builder:target=getattr(mob,'target',None)
                if not builder and target is not None and hasattr(target,'text') and getattr(mob,'text',None)!=target.text:
                    entry['target_text']=str(target.text)[:300]
                # Text.font_size is derived from its construction-time glyph height.
                # Transform changes glyphs without refreshing that baseline. Calibrate
                # telemetry against the target so 28pt text morphing to '1' stays 28pt;
                # genuine shrinking to a 12pt target must still fail.
                if mob is not None and target is not None and hasattr(mob,'font_size') and hasattr(target,'font_size'):
                    correction=self.font_corrections.get(id(mob),1)
                    source_size=float(mob.font_size)*correction
                    target_correction=self.font_corrections.get(id(target),correction if builder else 1)
                    target_size=float(target.font_size)*target_correction
                    duration=kwargs.get('run_time',1 if builder else getattr(animation,'run_time',1))
                    from manim import smooth
                    rate=kwargs.get('rate_func',smooth if builder else getattr(animation,'rate_func',smooth))
                    self.active_fonts[id(mob)]=(source_size,target_size,start,max(1,round(duration*self.fps)),rate)
                    font_targets.append((mob,target_size))
                animations.append(entry)
            for animation in args:describe(animation)
            self.active_motion={}
            try:value = original_play(*args, **kwargs)
            finally:
                motion=self.active_motion
                self.active_motion=None
                self.active_fonts={}
                for mob,size in font_targets:
                    raw=float(mob.font_size)
                    if raw>0:self.font_corrections[id(mob)]=size/raw
            end = max(start, self.frame-1)
            self.events.append(dict(start_frame=start, end_frame=end,middle_frame=(start+end)//2,
                animations=animations,is_wait=bool(animations) and all(a['type']=='Wait' for a in animations),
                motion={oid:stat for oid,stat in motion.items() if oid in affected or stat['changed_frames']}))
            return value
        self.scene.play = play

    def report(self):
        return dict(checked_frames=self.frame, fps=self.fps, events=self.events,
                    object_ids=sorted(self.object_ids), issues=self.issues,candidates=self.candidates,
                    early_motion=self.early_motion, samples=self.samples)


def select_frames(telemetry, frame_count, limit=5):
    """Always prioritize the worst geometry, plus play midpoint/end and opening."""
    last = max(0, frame_count-1)
    selected = {0, last}
    ranked = sorted(telemetry.get('issues',[])+telemetry.get('candidates',[]), key=lambda x:(
        'safe area' not in x['problem'], 'overlap' not in x['problem'], x['time']))
    for issue in ranked:
        if len(selected)>=limit:
            break
        selected.add(min(last,max(0,round(issue['time']*telemetry['fps']))))
    events=[event for event in telemetry.get('events',[]) if not event.get('is_wait')]
    def importance(event):
        priority=0
        for animation in event.get('animations',[]):
            kind=animation['type']
            if animation.get('target_text') is not None:priority=max(priority,4)
            elif kind in ('Create','Write','DrawBorderThenFill','ShowPassingFlash','MoveAlongPath'):priority=max(priority,3)
            elif kind in ('Indicate','Wiggle','Flash'):priority=max(priority,2)
            elif kind not in ('Wait','FadeOut'):priority=max(priority,1)
        movement=0
        for stat in event.get('motion',{}).values():
            a,b=stat['first_box'],stat['last_box']
            area=max(.01,(a[1]-a[0])*(a[3]-a[2]))
            movement=max(movement,area*(abs(a[0]+a[1]-b[0]-b[1])+abs(a[2]+a[3]-b[2]-b[3])))
        return priority,movement,-event['start_frame']
    # Prefer brief semantic changes over idle holds or evenly spaced static images.
    for event in sorted((e for e in events if e.get('animations')),key=importance,reverse=True):
        if len(selected)>=limit:break
        selected.add(min(last,max(0,event['middle_frame'])))
    pool = {min(last,max(0,event[key])) for event in events
            for key in ('middle_frame','end_frame')}
    pool.add(min(last,round(.5*telemetry['fps'])))
    while pool-selected and len(selected)<limit:
        selected.add(max(pool-selected,key=lambda f:(min(abs(f-s) for s in selected),-f)))
    return sorted(selected)
