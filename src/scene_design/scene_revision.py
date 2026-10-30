"""C6 revision cycles. Candidate designs remain separate until a group is published."""
import copy
import hashlib
import json
import math
from pathlib import Path
import uuid

from src.shared import _workflow_store as store
from src.shared._workflow_store import WorkflowError
from src.shared import workflow as wf
from src.scene_design import scene_generation as sg
from src.shared.ai_config import get_model
from src.idea.content import _object, parse_object
from src.shared.job_session import Session, _job_lock, _json_bytes, _now, _used
from src.shared.openrouter_client import response_text, redact
from src.scene_design.design import TEXT, array, schema_check

PLAN_SCHEMA=_object(dict(summary=TEXT,categories=array(TEXT),scenes=array(TEXT),resources=array(TEXT),resource_updates=array(_object(dict(resource_id=TEXT,content_json=TEXT))),outside_scope={'type':'boolean'}))
TERMINAL={'completed','cancelled'}


def cycles(reviews): return reviews.get('scene_revisions',[])
def active(reviews): return [r for r in cycles(reviews) if r['status'] not in TERMINAL]
def blocked(reviews): return bool(active(reviews))


def _find(reviews, revision_id):
    result=next((r for r in cycles(reviews) if r['id']==revision_id),None)
    if not result: raise WorkflowError('REVISION_NOT_FOUND','Unknown revision')
    return result


def dependencies(project):
    """File identity is explicit; equal numeric values are never inferred as shared."""
    resources={}
    for sid in project['scene_ids']:
        design=wf._latest(project,'scene_design',sid)
        if not design: continue
        refs={c['path']:c for c in design.get('bundle',{}).values()
              if c['path'].startswith('assets/') or c['path'].endswith('/data.json') or c['path']=='scene_config.json'}
        for ref in design.get('resource_refs',[]): refs[ref.get('resource_id',ref['path'])]=ref
        for key,ref in refs.items():
            entry=resources.setdefault(key,dict(path=ref['path'],sha256=ref['sha256'],scenes=[]))
            if entry['sha256']!=ref['sha256']: raise WorkflowError('DEPENDENCY_CONFLICT','Conflicting shared resource versions')
            entry['scenes'].append(sid)
    for key,ref in project.get('shared_resources',{}).items():
        resources[key]=copy.deepcopy(ref)
    return resources


def declare_shared_resource(project_dir,resource_id,path,sha256,scene_ids):
    """Explicitly bind an existing immutable resource; never infer shared numbers."""
    with store.locked_project(project_dir) as root:
        p,reviews=wf._load(root)
        if active(reviews): raise WorkflowError('REVISION_BUSY','Finish revisions before declaring dependencies')
        if not resource_id or not scene_ids or not set(scene_ids)<=set(p['scene_ids']): raise WorkflowError('INVALID_INPUT','Invalid dependency binding')
        target=store.project_path(root,path)
        registered=any(c['path']==path and c['sha256']==sha256 for a in p['artifacts'] for c in a.get('bundle',{}).values())
        if not registered or store.digest(target)!=sha256: raise WorkflowError('ARTIFACT_CHANGED','Resource must match a registered immutable bundle')
        if resource_id in p.get('shared_resources',{}): raise WorkflowError('RESOURCE_EXISTS','Resource ID already declared')
        p.setdefault('shared_resources',{})[resource_id]=dict(path=path,sha256=sha256,scenes=sorted(set(scene_ids)))
        wf._save(root,p,reviews)


def _snapshot(project):
    return dict(script=wf._latest(project,'script',None),
                scenes={s:{k:wf._latest(project,k,s) for k in ('scene_design','scene_clip')} for s in project['scene_ids']},
                dependencies=dependencies(project))


def _fresh(project,revision):
    if _snapshot(project)!=revision['snapshot']: raise WorkflowError('STALE_REVISION','Inputs changed; cancel and submit feedback against the current version')


def request_revision(project_dir,scene_id,version,sha256,feedback,*,time_seconds=None,request_id=None):
    if not isinstance(feedback,str) or not feedback.strip() or len(feedback)>10000: raise WorkflowError('INVALID_INPUT','Feedback is required, maximum 10000 characters')
    if time_seconds is not None and (type(time_seconds) not in (int,float) or not math.isfinite(time_seconds) or time_seconds<0): raise WorkflowError('INVALID_INPUT','Invalid clip timestamp')
    with store.locked_project(project_dir) as root:
        p,reviews=wf._load(root)
        for r in cycles(reviews):
            if request_id and r.get('request_id')==request_id:
                if (r['scene_id'],r['base']['version'],r['base']['sha256'],r['feedback'],r['time_seconds'])!=(scene_id,version,sha256,feedback.strip(),time_seconds):
                    raise WorkflowError('REQUEST_CONFLICT','Request ID already has another payload')
                return copy.deepcopy(r)
        if p['stage'] not in ('scene_clip','scene_av','final_video') or (not p.get('c7_enabled') and any(a['stage']=='final_video' for a in p['artifacts'])):
            raise WorkflowError('WRONG_STAGE','C6 clip revisions are available before final-video generation')
        base=wf._latest(p,'scene_clip',scene_id)
        if not base or (base['version'],base['sha256'])!=(version,sha256): raise WorkflowError('STALE_REVIEW','Reload the current clip')
        if base.get('origin')!='ai_scene': raise WorkflowError('STRUCTURED_CLIP_REQUIRED','C6 needs a C5 source bundle')
        wf._verify(root,base)
        if any(scene_id in r['affected'] for r in active(reviews)): raise WorkflowError('REVISION_BUSY','Scene already has an open revision')
        revision=dict(id=uuid.uuid4().hex,request_id=request_id,scene_id=scene_id,base=copy.deepcopy(base),
                      feedback=feedback.strip(),time_seconds=time_seconds,status='analyzing',affected=[scene_id],
                      snapshot=_snapshot(p),created_at=_now(),designs={},clips={},last_error=None,authorized=True)
        reviews.setdefault('scene_revisions',[]).append(revision)
        from src.preview.av_workflow import invalidate
        invalidate(p, reviews, [scene_id])
        p['stage']='scene_clip'; p.pop('assembly_manifest',None)
        wf._save(root,p,reviews)
        return copy.deepcopy(revision)


def _session(root,revision,phase,sid,retry):
    """All C6 calls are registered in the existing C5 project ledger."""
    index=sg._index(root)
    job_id='c6_'+revision['id']+'_r'+str(revision.get('round',0))+'_'+phase+'_'+sid
    directory=root/sg.BASE/job_id; directory.mkdir(exist_ok=True)
    path=str((directory/'job.json').relative_to(root))
    if path in index['jobs']: job=store.read_json(directory/'job.json')
    else:
        if (directory/'job.json').exists(): job=store.read_json(directory/'job.json')
        elif any(directory.iterdir()): raise WorkflowError('STATE_MISSING','C6 job ledger is missing')
        else:
            job=dict(job_id=job_id,input=dict(stage=phase,scene_id=sid,revision_id=revision['id'],model=get_model()),calls=[],status='working',created_at=_now())
            store.atomic_write(directory/'job.json',job)
        index['jobs'].append(path); store.atomic_write(root/sg.BASE/'ledger.json',index)
    if job['input']['model']!=get_model(): raise WorkflowError('MODEL_MISMATCH','Restore the model of the unfinished revision')
    other=sum(_used(j) for j in sg._jobs(root) if j['job_id']!=job_id)
    selected=retry if any(c['call_id']==retry for c in job['calls']) else None
    session=Session(directory,job,sg.CLIENT_FACTORY(),selected,budget_usd=sg.BUDGET,other_used=other)
    if 'pricing' not in job: job['pricing']=session.client.preflight(search=False); session.save()
    return session


def _save(root,revision):
    with store.locked_project(root):
        p,reviews=wf._load(root)
        current=_find(reviews,revision['id'])
        if current['status']=='cancelled': raise WorkflowError('REVISION_CANCELLED','Revision cancelled')
        current.clear(); current.update(copy.deepcopy(revision))
        if p.get('c7_enabled'):
            from src.preview.av_workflow import invalidate
            invalidate(p,reviews,revision['affected'])
        wf._save(root,p,reviews)


def amend_revision(project_dir,revision_id,feedback,*,expected_round=None):
    if not isinstance(feedback,str) or not feedback.strip() or len(feedback)>10000: raise WorkflowError('INVALID_INPUT','Feedback is required')
    with store.locked_project(project_dir) as root:
        p,reviews=wf._load(root); r=_find(reviews,revision_id)
        if expected_round is not None and r.get('round',0)!=expected_round: raise WorkflowError('STALE_REVIEW','Reload this revision round')
        if r['status'] not in ('failed','stale','out_of_scope','awaiting_scope','awaiting_design','awaiting_clip'):
            raise WorkflowError('REVISION_BUSY','Wait for the current work to finish')
        history=r.setdefault('round_history',[])
        history.append({k:copy.deepcopy(r.get(k)) for k in ('feedback','status','plan','designs','clips','resource_versions','last_error')})
        r.update(round=r.get('round',0)+1,feedback=feedback.strip(),snapshot=_snapshot(p),
                 base=copy.deepcopy(wf._latest(p,'scene_clip',r['scene_id'])),status='analyzing',
                 affected=[r['scene_id']],designs={},clips={},last_error=None)
        for key in ('plan','plan_sha256','resource_versions'): r.pop(key,None)
        wf._save(root,p,reviews); return copy.deepcopy(r)


def approve_scope(project_dir,revision_id,plan_sha256,*,expected_round=None):
    with store.locked_project(project_dir) as root:
        p,reviews=wf._load(root); r=_find(reviews,revision_id); _fresh(p,r)
        if expected_round is not None and r.get('round',0)!=expected_round: raise WorkflowError('STALE_REVIEW','Reload this revision round')
        if r.get('plan_sha256')!=plan_sha256: raise WorkflowError('STALE_REVIEW','Reload the impact plan')
        if r['status']=='planned': return copy.deepcopy(r)
        if r['status']!='awaiting_scope': raise WorkflowError('WRONG_STAGE','Scope is not awaiting approval')
        r.update(status='planned',authorized=True); wf._save(root,p,reviews); return copy.deepcopy(r)


def cancel_revision(project_dir,revision_id):
    with store.locked_project(project_dir) as root:
        p,reviews=wf._load(root); r=_find(reviews,revision_id)
        if r['status']=='cancelled': return copy.deepcopy(r)
        if r['designs'] or r['clips'] or r['status'] not in ('awaiting_scope','out_of_scope','failed','planned','stale'):
            raise WorkflowError('CANNOT_CANCEL','Cancel only before new candidates have been created and while no task is running')
        r['status']='cancelled'
        if wf._all_approved(p,reviews,'scene_clip'):
            if p.get('c7_enabled'):
                from src.preview.av_workflow import restore_cancelled
                restore_cancelled(root,p,reviews)
            else: p['stage']='final_video'
            manifest(root,p,reviews)
        wf._save(root,p,reviews)
        result=copy.deepcopy(r)
    return result


def approve_design(project_dir,revision_id,scene_id,sha256,*,expected_round=None):
    with store.locked_project(project_dir) as root:
        p,reviews=wf._load(root); r=_find(reviews,revision_id); _fresh(p,r)
        if expected_round is not None and r.get('round',0)!=expected_round: raise WorkflowError('STALE_REVIEW','Reload this revision round')
        if scene_id not in r['designs']: raise WorkflowError('REVIEW_NOT_FOUND','No candidate design')
        candidate=r['designs'][scene_id]
        if candidate['sha256']!=sha256: raise WorkflowError('STALE_REVIEW','Reload the candidate')
        wf._verify(root,candidate)
        if candidate.get("design_pipeline")==2:
            from src.scene_design.scene_pipeline import verify_quality
            verify_quality(root,candidate)
        if candidate.get('approved'): return copy.deepcopy(r)
        if r['status']!='awaiting_design': raise WorkflowError('WRONG_STAGE','Design is not awaiting approval')
        candidate.update(approved=True,approved_at=_now())
        if all(d.get('approved') for d in r['designs'].values()) and len(r['designs'])==len(r['affected']): r['status']='rendering'
        wf._save(root,p,reviews); return copy.deepcopy(r)


def guard_review(reviews,artifact):
    for r in active(reviews):
        if artifact['scene_id'] in r['affected'] and artifact['stage']=='scene_clip':
            candidate=r['clips'].get(artifact['scene_id'])
            if not candidate or candidate['version']!=artifact['version']:
                raise WorkflowError('STALE_REVIEW','This clip has an active revision; review its new candidate')


def reconcile(project,reviews):
    for r in active(reviews):
        if r['status']=='awaiting_clip' and all(wf._request(reviews,c)['status']=='approved' for c in r['clips'].values()):
            r['status']='completed'; r['completed_at']=_now()


def manifest(root,project,reviews):
    if project.get('c7_enabled'):
        from src.preview.av_workflow import invalidate
        changed=[sid for sid in project['scene_ids'] if (a:=wf._latest(project,'scene_av',sid)) and a['inputs']['clip']!=wf._latest(project,'scene_clip',sid)]
        if changed: invalidate(project,reviews,changed)
    if blocked(reviews) or not wf._all_approved(project,reviews,'scene_clip'): return
    entries=[]
    for sid in project['scene_ids']:
        clip=wf._latest(project,'scene_clip',sid); wf._verify(root,clip)
        entries.append(dict(scene_id=sid,version=clip['version'],path=clip['path'],sha256=clip['sha256'],bundle=clip.get('bundle',{})))
    content=_json_bytes(dict(scene_ids=project['scene_ids'],clips=entries))
    path='assembly_manifests/'+hashlib.sha256(content).hexdigest()+'.json'
    store.materialize(root,path,content=content)
    project['assembly_manifest']=dict(path=path,sha256=store.digest(root/path))


def _advance(root):
    with store.locked_project(root):
        p,reviews=wf._load(root)
        if p['stage']=='scene_clip' and wf._all_approved(p,reviews,'scene_clip'):
            p['stage']='scene_av' if p.get('c7_enabled') else 'final_video'; manifest(root,p,reviews); wf._save(root,p,reviews)


def resume_revision(project_dir,revision_id,*,retry_uncertain=None):
    root=Path(project_dir).resolve()
    with _job_lock(root):
        with store.locked_project(root):
            p,reviews=wf._load(root); r=copy.deepcopy(_find(reviews,revision_id))
            if r['status'] in TERMINAL or r['status'] in ('awaiting_scope','awaiting_design','awaiting_clip','out_of_scope','stale'): return r
            _fresh(p,r)
            if retry_uncertain and not any(c['call_id']==retry_uncertain and c['status'] in ('uncertain','in_flight') for j in sg._jobs(root) if j['input'].get('revision_id')==r['id'] for c in j['calls']):
                raise WorkflowError('UNKNOWN_REQUEST','Select an uncertain call belonging to this revision')
        try:
            if 'plan' not in r:
                session=_session(root,r,'analysis',r['scene_id'],retry_uncertain)
                resource_contents={}
                for key,ref in r['snapshot']['dependencies'].items():
                    if ref['path'].endswith('.json') and not ref['path'].endswith('.license.json'):
                        resource_contents[key]=store.read_json(root/ref['path'])
                context=dict(feedback=r['feedback'],timestamp=r['time_seconds'],snapshot=r['snapshot'],resource_contents=resource_contents)
                result=parse_object(response_text(session.call('analysis',sg._messages(
                    'Classify feedback. categories: code, layout, text, motion, data, image. Use code ONLY for implementation fixes matching the approved design. '
                    'Return all affected scene IDs and explicit shared resource IDs from dependencies. Changes to narration, scene order/count or approved duration are outside_scope. '
                    'Do not infer shared math from equal values. Explain what changes and what remains. '
                    'resource_updates contains replacements for changed shared JSON resources: resource_id plus content_json. '
                    'For v1 math, replacement uses datasets with expression,variable,start,end,count,unit,id (points are recomputed). For schema_version=2 data, replacement datasets must preserve the full graph schema including id,object_id,expression,variable,x_min,x_max,y_min,y_max,samples. '
                    'Style replacement uses the same config keys. Never provide arbitrary file paths or image bytes. Empty array for local/code-only changes.',context),schema=PLAN_SCHEMA)))
                schema_check(result,PLAN_SCHEMA)
                if not set(result['categories'])<=set(('code','layout','text','motion','data','image')) or not result['categories']:
                    raise WorkflowError('INVALID_REVISION_PLAN','Unknown change category')
                affected=set(result['scenes'])|{r['scene_id']}
                if any(u['resource_id'] not in result['resources'] for u in result['resource_updates']):
                    raise WorkflowError('INVALID_REVISION_PLAN','Resource updates must belong to the declared scope')
                if result['resources'] and result['categories']==['code']:
                    raise WorkflowError('INVALID_REVISION_PLAN','Shared resource edits require design approval')
                for resource in result['resources']:
                    if resource not in r['snapshot']['dependencies']: raise WorkflowError('INVALID_REVISION_PLAN','Unknown resource')
                    affected.update(r['snapshot']['dependencies'][resource]['scenes'])
                if not affected<=set(p['scene_ids']): raise WorkflowError('INVALID_REVISION_PLAN','Unknown affected scene')
                with store.locked_project(root):
                    current,rv=wf._load(root); _fresh(current,r)
                    if any(set(other['affected']) & affected for other in active(rv) if other['id']!=r['id']):
                        raise WorkflowError('REVISION_BUSY','Scope overlaps another revision')
                r.update(plan=result,plan_sha256=hashlib.sha256(_json_bytes(result)).hexdigest(),affected=sorted(affected),
                         status='out_of_scope' if result['outside_scope'] else 'awaiting_scope' if len(affected)>1 else 'planned')
                session.job['status']='complete'; session.save()
                _save(root,r)
                if r['status']!='planned': return r
            if 'resource_versions' not in r:
                r['resource_versions']=[]
                resource_dir=root/sg.BASE/('c6_resources_'+r['id']); resource_dir.mkdir(exist_ok=True)
                for update in r['plan']['resource_updates']:
                    old=r['snapshot']['dependencies'][update['resource_id']]
                    if not old['path'].endswith('.json') or old['path'].endswith('.license.json'):
                        raise WorkflowError('INVALID_REVISION_PLAN','Only math/style JSON resources may be replaced directly')
                    content=parse_object(update['content_json'])
                    if old['path']=='scene_config.json':
                        base_config=store.read_json(root/'scene_config.json')
                        import re
                        if (set(content)!=set(sg.DEFAULT_CONFIG) or content['render']!=base_config['render'] or content['font']!=base_config['font']
                            or any(not isinstance(content.get(k),str) or not re.fullmatch('#[0-9a-fA-F]{6}',content[k]) for k in ('background','foreground','accent'))):
                            raise WorkflowError('INVALID_REVISION_PLAN','Render settings are fixed in C6')
                        sg._settings(content['render'])
                    else:
                        if not isinstance(content.get('datasets'),list): raise WorkflowError('INVALID_REVISION_PLAN','Math resource needs datasets')
                        from src.scene_design.design import validate_design
                        owner=old['scenes'][0]; baseline=r['snapshot']['scenes'][owner]['scene_design']
                        sample=store.read_json(root/baseline['path'])
                        if sample.get('schema_version')==2:
                            sample['graphs']=content['datasets']
                        else:sample['math']=[{k:v for k,v in x.items() if k!='points'} for x in content['datasets']]
                        assets=store.read_json(Path(baseline['review_path']).parent/'assets.json')['assets']
                        checked=validate_design(sample,dict(scene_id=owner,narration=sample['narration']),sample['duration'],assets)
                        content=dict(schema_version=2,datasets=sample['graphs']) if sample.get('schema_version')==2 else checked
                    name=hashlib.sha256(_json_bytes(content)).hexdigest()+'.json'
                    sg._write(resource_dir,name,content)
                    path=str((resource_dir/name).relative_to(root))
                    r['resource_versions'].append(dict(resource_id=update['resource_id'],path=path,sha256=store.digest(root/path),scenes=old['scenes']))
                _save(root,r)
            design_change=set(r['plan']['categories'])!={'code'} or any(r['snapshot']['scenes'][sid]['scene_design'].get('design_pipeline')==2 for sid in r['affected'])
            if design_change and not all(d.get('approved') for d in r['designs'].values()) or design_change and not r['designs']:
                for sid in r['affected']:
                    if sid in r['designs']: continue
                    session=_session(root,r,'revision_design',sid,retry_uncertain)
                    context=_context(root,p,reviews,r,sid)
                    if r['designs'] and any(key.startswith('assets/') for key in r['plan']['resources']):
                        first=next(iter(r['designs'].values()))
                        context['shared_assets']=store.read_json(Path(first['review_path']).parent/'assets.json')
                    source,extra=sg._design(root,session.directory,session,context)
                    session.job['status']='complete'; session.save()
                    paths=[x for x in session.directory.rglob('*') if x.is_file() and x.name!='job.json']
                    if extra.get('manim_render'):
                        paths.extend(root/extra['manim_render'][key] for key in ('path','code_path','log_path','metadata_path'))
                    assets=store.read_json(session.directory/'assets.json')
                    paths.extend(root/ref['path'] for ref in r['resource_versions'] if sid in ref['scenes'])
                    for a in assets['assets']: paths.extend([root/a['path'],root/a['evidence_path']])
                    r['designs'][sid]=dict(stage='scene_design',scene_id=sid,version='v'+str(max([int(wf._next_version(p,'scene_design',sid)[1:])-1]+[int(h['designs'][sid]['version'][1:]) for h in r.get('round_history',[]) if sid in (h.get('designs') or {})])+1).zfill(3),
                        path=str(source.relative_to(root)),sha256=store.digest(source),bundle=sg._bundle(root,paths),resource_refs=[ref for ref in r['resource_versions'] if sid in ref['scenes']],approved=False,**extra)
                    _save(root,r)
                r['status']='awaiting_design'; _save(root,r); return r
            for sid in r['affected']:
                if sid in r['clips']: continue
                session=_session(root,r,'revision_code',sid,retry_uncertain)
                context=_context(root,p,reviews,r,sid)
                design=r['designs'].get(sid) or r['snapshot']['scenes'][sid]['scene_design']
                wf._verify(root,design)
                directory=Path(design['review_path']).parent
                context.update(approved_design=store.read_json(root/design['path']),data=store.read_json(directory/'data.json'),assets=store.read_json(directory/'assets.json'))
                if design.get('manim_render') and sid in r['designs']:
                    context.update(design_artifact=design,reuse_approved_render=True)
                source,extra=sg._clip(root,session.directory,session,context)
                session.job['status']='complete'; session.save()
                paths=[root/extra['code_path'],source,root/f"logs/{sid}_{extra['clip_version']}.json",root/f"logs/{sid}_{extra['clip_version']}.log",root/design['path']]
                paths += [root/c['path'] for c in design['bundle'].values()]
                r['clips'][sid]=dict(stage='scene_clip',scene_id=sid,version=extra['clip_version'],path=str(source.relative_to(root)),
                    sha256=store.digest(source),bundle=sg._bundle(root,paths),**extra)
                _save(root,r)
            with store.locked_project(root):
                p,reviews=wf._load(root); _fresh(p,r)
                for sid in r['affected']:
                    if sid in r['designs']:
                        d=r['designs'][sid]
                        p['artifacts'].append(dict(d,origin='ai_scene',created_at=_now(),mock=False,revision_id=r['id']))
                        reviews['requests'].append(dict(stage='scene_design',scene_id=sid,version=d['version'],status='approved',decision='approve',feedback=None,requested_at=_now(),decided_at=d['approved_at']))
                    c=r['clips'][sid]
                    wf._submit(root,p,reviews,'scene_clip',c['version'],root/c['path'],sid,None)
                    wf._latest(p,'scene_clip',sid).update(origin='ai_scene',bundle=c['bundle'],code_path=c['code_path'],revision_id=r['id'])
                for ref in r.get('resource_versions',[]):
                    if ref['resource_id'] in p.get('shared_resources',{}):
                        p['shared_resources'][ref['resource_id']]={k:ref[k] for k in ('path','sha256','scenes')}
                r['status']='awaiting_clip'; _find(reviews,r['id']).update(r)
                wf._save(root,p,reviews)
            return r
        except Exception as exc:
            if 'session' in locals():
                session.job.update(status='failed',last_error=redact(str(exc))); session.save()
            r.update(status='stale' if isinstance(exc,WorkflowError) and exc.code=='STALE_REVISION' else 'failed',last_error=redact(str(exc)))
            _save(root,r)
            if isinstance(exc,WorkflowError): raise
            raise WorkflowError('REVISION_FAILED',redact(str(exc))) from None


def _context(root,p,reviews,r,sid):
    script,payload,durations=sg._context(root,p,reviews)
    baseline=r['snapshot']['scenes'][sid]
    context=dict(script=payload['script'],brief=payload['brief'],scene=next(s for s in payload['script']['scenes'] if s['scene_id']==sid),
        target_seconds=durations[sid],config=copy.deepcopy(r['snapshot']['dependencies'].get('scene_config.json') and store.read_json(root/'scene_config.json') or sg.DEFAULT_CONFIG),
        feedback=r['feedback'],revision_plan=r['plan'],shared_resources=r['plan']['resources'])
    for ref in baseline['scene_design'].get('resource_refs',[]):
        if ref['resource_id']=='scene_config.json': context['config']=store.read_json(root/ref['path'])
    context['resource_overrides']=[dict(ref,content=store.read_json(root/ref['path'])) for ref in r.get('resource_versions',[]) if sid in ref['scenes']]
    for override in context['resource_overrides']:
        if override['resource_id']=='scene_config.json': context['config']=override['content']
        elif override['content'].get('schema_version')==2:
            context['effective_graphs']=override['content']['datasets']
        elif 'datasets' in override['content']:
            context['effective_math']=[{k:v for k,v in spec.items() if k!='points'} for spec in override['content']['datasets']]
    previous=next((h['designs'][sid] for h in reversed(r.get('round_history',[])) if sid in (h.get('designs') or {})),None)
    context['previous_design']=store.read_json(root/(previous or baseline['scene_design'])['path'])
    if baseline['scene_clip']: context['previous_code']=(root/baseline['scene_clip']['code_path']).read_text()
    return context
