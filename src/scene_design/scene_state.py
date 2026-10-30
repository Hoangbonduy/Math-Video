"""Pure deterministic scene state, shared by validation and the trusted Manim program.

Expressions are precompiled arithmetic data. No eval, exec or provider calls.
Coordinates are normalized, y down. Geometric rotations account for frame aspect.
"""
import math


def expression(node, values):
    op = node[0]
    if op == 'n': return node[1]
    if op == 'v': return values[node[1]]
    if op == 'neg': result = -expression(node[1], values)
    elif op == 'pos': result = expression(node[1], values)
    elif op == 'fn':
        functions = dict(sin=math.sin, cos=math.cos, tan=math.tan, sqrt=math.sqrt,
                         exp=math.exp, log=math.log, fabs=math.fabs)
        result = functions[node[1]](expression(node[2], values))
    else:
        a, b = expression(node[1], values), expression(node[2], values)
        if op == '+': result = a+b
        elif op == '-': result = a-b
        elif op == '*': result = a*b
        elif op == '/': result = a/b
        elif op == '**':
            if abs(b)>20 or abs(a)>1e12: raise ValueError('Exponent outside supported range')
            result = a**b
        else: raise ValueError('Unsupported expression operator')
    if type(result) not in (int, float) or not math.isfinite(result) or abs(result)>1e12:
        raise ValueError('Nonfinite or excessive expression value')
    return result


def ease(value, kind):
    value = min(1.0, max(0.0, value))
    return value*value*(3-2*value) if kind == 'smooth' else value


def animation(beats, t):
    dx, dy, angle, scale, highlight = 0.0, 0.0, 0.0, 1.0, 0.0
    opacity = 0.0 if any(b['action']=='appear' for b in beats) else 1.0
    for beat in beats:
        if t < beat['start']: continue
        progress = ease((t-beat['start'])/beat['duration'], beat['easing'])
        action = beat['action']
        if action == 'move': dx += beat['dx']*progress; dy += beat['dy']*progress
        elif action == 'rotate': angle += beat['value']*progress
        elif action == 'scale': scale *= 1+(beat['value']-1)*progress
        elif action == 'appear': opacity = progress
        elif action == 'fade': opacity = 1-progress
        elif action == 'highlight':
            pulse = max(0.0, math.sin(math.pi*progress)) if progress < 1 else 0.0
            scale *= 1+.22*pulse
            highlight = max(highlight, pulse)
    return dict(dx=dx,dy=dy,angle=angle,scale=scale,opacity=opacity,highlight=highlight)


def transform(point, pivot, motion, aspect):
    x=(point[0]-pivot[0])*aspect*motion['scale']
    y=(point[1]-pivot[1])*motion['scale']
    angle=math.radians(motion['angle']); c,s=math.cos(angle),math.sin(angle)
    return [pivot[0]+(c*x-s*y)/aspect+motion['dx'],pivot[1]+s*x+c*y+motion['dy']]


def transform_object(plan, oid, point, motions):
    obj=plan['objects'][oid]
    point=transform(point,[obj['x'],obj['y']],motions[oid],plan['aspect'])
    for group in plan['ancestors'][oid]:
        point=transform(point,plan['pivots'][group],motions[group],plan['aspect'])
    return point


def graph_point(plan, graph, x, y, motions):
    obj=plan['objects'][graph['object_id']]
    point=[obj['x']-obj['width']/2+(x-graph['x_min'])/(graph['x_max']-graph['x_min'])*obj['width'],
           obj['y']+obj['height']/2-(y-graph['y_min'])/(graph['y_max']-graph['y_min'])*obj['height']]
    return transform_object(plan,graph['object_id'],point,motions)


def clip_segment(a,b,bounds):
    # Liang-Barsky clipping, preserving endpoints on the same mathematical line.
    xmin,xmax,ymin,ymax=bounds; dx,dy=b[0]-a[0],b[1]-a[1];low,high=0.0,1.0
    for p,q in [(-dx,a[0]-xmin),(dx,xmax-a[0]),(-dy,a[1]-ymin),(dy,ymax-a[1])]:
        if abs(p)<1e-12:
            if q<0: return []
        elif p<0: low=max(low,q/p)
        else: high=min(high,q/p)
    if low>high: return []
    return [[a[0]+low*dx,a[1]+low*dy],[a[0]+high*dx,a[1]+high*dy]]



def path_samples(points, limit=16):
    """Bounded vertex samples; stable indices always include both endpoints."""
    count = len(points)
    indices = sorted({round(i*(count-1)/(min(limit,count)-1)) for i in range(min(limit,count))}) if count > 1 else list(range(count))
    return [dict(index=i, point=list(points[i])) for i in indices]


def path_points(plan, oid, points=None, scale=1.0):
    """Preserve declared vertices; only synthesize unshaped decorative waves."""
    obj = plan['objects'][oid]
    raw = obj['points'] or [[0,0],[1,1]]
    local = points if points is not None else [[obj['x']+(x-.5)*obj['width'],obj['y']+(y-.5)*obj['height']] for x,y in raw]
    tokens = oid.lower().split('_')
    if 'wave' not in tokens:
        return local
    aspect = plan['aspect']
    a,b = local[0],local[-1]
    dx,dy = (b[0]-a[0])*aspect,b[1]-a[1]
    length = math.hypot(dx,dy)
    if length <= 1e-9:
        return local
    # Distances perpendicular to the baseline, accounting for frame aspect.
    if len(local)>2 and any(abs(dx*(p[1]-a[1])-dy*(p[0]-a[0])*aspect)/length>1e-6 for p in local[1:-1]):
        return local
    cycles = 9 if any(t in tokens for t in ('reflected','reflect','return','echo')) else 5
    amplitude = max(obj['height'],.05)*scale/2
    points = []
    for i in range(65):
        u = i/64
        offset = amplitude*math.sin(2*math.pi*cycles*u)
        points.append([a[0]+u*dx/aspect-dy/length*offset/aspect,
                       a[1]+u*dy+dx/length*offset])
    points[0],points[-1] = list(a),list(b)
    return points


def graph_derivative(graph, x, values):
    """Finite difference inside the declared domain, including its endpoints."""
    step=(graph['x_max']-graph['x_min'])*1e-5
    a=max(graph['x_min'],x-step);b=min(graph['x_max'],x+step)
    left=expression(graph['code'],dict(values,**{graph['variable']:a}))
    right=expression(graph['code'],dict(values,**{graph['variable']:b}))
    return (right-left)/(b-a)


def state_at(plan, t):
    t=float(t)
    values=dict(pi=math.pi,e=math.e)
    values.update({p['id']:p['value'] for p in plan['parameters']})
    for track in plan['parameter_tracks']:
        if t < track['start']: continue
        progress=ease((t-track['start'])/track['duration'],track['easing'])
        values[track['parameter']]=track['from']+(track['to']-track['from'])*progress
    motions={oid:animation(beats,t) for oid,beats in plan['tracks'].items()}
    objects={}
    for oid,obj in plan['objects'].items():
        chain=[oid]+plan['ancestors'][oid]
        opacity=math.prod(motions[n]['opacity'] for n in chain)
        scale=math.prod(motions[n]['scale'] for n in chain)
        highlight=max(motions[n]['highlight'] for n in chain)
        angle=sum(motions[n]['angle'] for n in chain)
        center=transform_object(plan,oid,[obj['x'],obj['y']],motions)
        item=dict(center=center,width=obj['width']*scale,height=obj['height']*scale,
                  scale=scale,highlight=highlight,angle=angle,opacity=opacity,label=obj['label'],points=[],segments=[],data_point=None,graph='')
        if obj['kind'] in ('plot','line'):
            item['points']=[transform_object(plan,oid,p,motions) for p in path_points(plan,oid)]
        objects[oid]=item
    for graph in plan['graphs'].values():
        item=objects[graph['object_id']];points=[]
        for index in range(graph['samples']):
            x=graph['x_min']+(graph['x_max']-graph['x_min'])*index/(graph['samples']-1)
            y=expression(graph['code'],dict(values,**{graph['variable']:x}))
            points.append((x,y))
        # Split/clip every segment, never draw a spurious edge across an offscreen gap.
        for a,b in zip(points,points[1:]):
            pair=clip_segment(a,b,[graph['x_min'],graph['x_max'],graph['y_min'],graph['y_max']])
            if pair:item['segments'].append([graph_point(plan,graph,x,y,motions) for x,y in pair])
        axis_x=min(graph['y_max'],max(graph['y_min'],0));axis_y=min(graph['x_max'],max(graph['x_min'],0))
        item['axes']=[[graph_point(plan,graph,graph['x_min'],axis_x,motions),graph_point(plan,graph,graph['x_max'],axis_x,motions)],
                      [graph_point(plan,graph,axis_y,graph['y_min'],motions),graph_point(plan,graph,axis_y,graph['y_max'],motions)]]
    for bind in plan['bindings']:
        target=objects[bind['target']];kind=bind['kind']
        if kind=='point_on_graph':
            graph=plan['graphs'][bind['graph']]
            x=expression(bind['x_code'],values);y=expression(graph['code'],dict(values,**{graph['variable']:x}))
            if not graph['x_min']-1e-9<=x<=graph['x_max']+1e-9 or not graph['y_min']-1e-9<=y<=graph['y_max']+1e-9:
                raise ValueError('Point '+bind['target']+' lies outside graph domain/range')
            target['center']=graph_point(plan,graph,x,y,motions);target['data_point']=[x,y];target['graph']=bind['graph'];values[bind['id']]=x
        elif kind in ('line_through_points','tangent_line'):
            first=objects[bind['source_a']]
            if kind=='line_through_points':
                second=objects[bind['source_b']];a,b=first['center'],second['center']
                if first['graph'] and first['graph']==second['graph']:
                    graph=plan['graphs'][first['graph']];p,q=first['data_point'],second['data_point']
                    if abs(q[0]-p[0])<1e-6:
                        slope=graph_derivative(graph,p[0],values)
                        span=(graph['x_max']-graph['x_min'])*.18
                        x0,x1=(p[0]-span,p[0]+span) if bind['extent']=='segment' else (graph['x_min'],graph['x_max'])
                        data=[[x0,p[1]+slope*(x0-p[0])],[x1,p[1]+slope*(x1-p[0])]]
                    else:
                        slope=(q[1]-p[1])/(q[0]-p[0])
                        data=[[graph['x_min'],p[1]+slope*(graph['x_min']-p[0])],[graph['x_max'],p[1]+slope*(graph['x_max']-p[0])]]
                        if bind['extent']=='segment':data=[p,q]
                    pair=clip_segment(data[0],data[1],[graph['x_min'],graph['x_max'],graph['y_min'],graph['y_max']])
                    target['points']=[graph_point(plan,graph,x,y,motions) for x,y in pair]
                else:
                    if math.dist(a,b)<1e-7:raise ValueError('Coincident points cannot define line '+bind['target'])
                    if bind['extent']!='segment':raise ValueError('Extended line requires two points on one graph')
                    target['points']=[a,b];slope=(b[1]-a[1])/(b[0]-a[0]) if abs(b[0]-a[0])>1e-9 else None
            else:
                if not first['graph']:raise ValueError('Tangent source must be a point on a graph')
                graph=plan['graphs'][first['graph']];p=first['data_point'];slope=graph_derivative(graph,p[0],values)
                pair=clip_segment([graph['x_min'],p[1]+slope*(graph['x_min']-p[0])],[graph['x_max'],p[1]+slope*(graph['x_max']-p[0])], [graph['x_min'],graph['x_max'],graph['y_min'],graph['y_max']])
                target['points']=[graph_point(plan,graph,x,y,motions) for x,y in pair]
            if len(target['points'])!=2:raise ValueError('Line is not visible within graph')
            target['center']=[sum(p[k] for p in target['points'])/2 for k in (0,1)]
            values[bind['id']]=slope
        elif kind=='label_follow':
            center=objects[bind['source_a']]['center'];target['center']=[center[0]+bind['offset_x'],center[1]+bind['offset_y']]
        elif kind=='position':
            point=[expression(bind['x_code'],values),expression(bind['y_code'],values)]
            target['center']=transform_object(plan,bind['target'],point,motions)
        elif kind=='value_label':
            value=expression(bind['code'],values);values[bind['id']]=value
            target['label']=bind['prefix']+format(value,'.'+str(bind['decimals'])+'f')+bind['suffix']
    # A bound wave obtains its world endpoints after the relation pass.
    # Apply the same decorative fallback there instead of losing its crests.
    for bind in plan['bindings']:
        oid=bind['target']
        if bind['kind']=='line_through_points' and 'wave' in oid.lower().split('_'):
            item=objects[oid]
            item['points']=path_points(plan,oid,item['points'],item['scale'])
    return dict(time=t,parameters={p['id']:values[p['id']] for p in plan['parameters']},objects=objects,values=values)


def object_box(plan, oid, state):
    obj=plan['objects'][oid];item=state['objects'][oid]
    if obj['kind'] in ('line','plot') and item['points']:
        return [min(p[0] for p in item['points']),min(p[1] for p in item['points']),max(p[0] for p in item['points']),max(p[1] for p in item['points'])]
    cx,cy=item['center'];w,h=item['width']/2,item['height']/2;a=math.radians(item['angle']);aspect=plan['aspect']
    dx=abs(math.cos(a))*w+abs(math.sin(a))*h/aspect;dy=abs(math.sin(a))*w*aspect+abs(math.cos(a))*h
    return [cx-dx,cy-dy,cx+dx,cy+dy]
