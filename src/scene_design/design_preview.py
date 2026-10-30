"""Render design JSON with fixed local Manim code; never call AI for preview."""
import hashlib
from pathlib import Path

from src.shared import _workflow_store as store
from src.shared import workflow as wf
from src.shared._workflow_store import WorkflowError
from src.shared.job_session import _job_lock, _json_bytes, _now
from src.shared.openrouter_client import redact


def _verify(root, reference):
    for path_key, hash_key in (('path','sha256'), ('code_path','code_sha256')):
        path=store.project_path(root,reference[path_key])
        if not path.is_file() or store.digest(path)!=reference[hash_key]:
            raise WorkflowError('ARTIFACT_CHANGED','Video hoặc mã Manim đã thay đổi; hãy kiểm tra lại bản lưu.')


def _artifact(project, reviews, scene_id, version):
    candidates=list(project['artifacts'])
    for revision in reviews.get('scene_revisions',[]):
        if revision['status']!='cancelled': candidates.extend(revision['designs'].values())
    return next((a for a in candidates if a['stage']=='scene_design'
                 and a['scene_id']==scene_id and a['version']==version and not a.get('invalidated')),None)


def _save(directory, job):
    job['updated_at']=_now()
    store.atomic_write(directory/'job.json',redact(job))


def render_design(root, directory, job, design, assets, config):
    """Compile data with the fixed template and execute it once, without an AI session."""
    from src.scene_design import scene_generation as sg
    from src.scene_design.scene_preview import source_for
    for asset in assets['assets']:
        for key,checksum in (('path','sha256'),('evidence_path','evidence_sha256')):
            target=store.project_path(root,asset[key])
            if not target.is_file() or store.digest(target)!=asset[checksum]:
                raise WorkflowError('ASSET_CHANGED','Saved preview asset changed')
    source=source_for(design,assets['assets'],config)
    checksum=hashlib.sha256(source.encode()).hexdigest()
    scene_id=design['scene_id']
    job.update(preview_engine='json',progress='rendering_manim')
    render=job.get('json_render')
    metadata=root/f"logs/{scene_id}_{render['version']}.json" if render else None
    report=store.read_json(metadata) if metadata and metadata.exists() else {}
    try:
        if render and render['source_sha256']==checksum and report.get('status')=='success':
            if store.digest(store.project_path(root,render['code_path']))!=checksum:
                raise WorkflowError('ARTIFACT_CHANGED','Saved preview source changed')
            output={k:report[k] for k in ('status','scene_id','version','video_path','log_path','error')}
            if render.get('sha256') and store.digest(Path(output['video_path']))!=render['sha256']:
                raise WorkflowError('ARTIFACT_CHANGED','Cached preview changed')
        else:
            if render:job.setdefault('preview_render_history',[]).append(render)
            version=sg._clip_version(root,scene_id)
            code_path=f'scenes/{scene_id}_{version}.py'
            store.materialize(root,code_path,content=source.encode())
            render=dict(version=version,code_path=code_path,source_sha256=checksum,status='running')
            job['json_render']=render;_save(directory,job)
            output=sg.RENDERER(root,scene_id,version,code_path,'GeneratedScene',config['render'])
        if output['status']!='success':
            raise WorkflowError('RENDER_FAILED',output.get('error') or 'Không render được preview; xem log render.')
        measured=store.read_json(Path(output['log_path']).with_suffix('.json'))['video']['duration_seconds']
        if abs(measured-design['duration'])>2/config['render']['fps']+1e-6:
            raise WorkflowError('SCENE_DURATION_MISMATCH',f"Actual {measured}s vs design {design['duration']}s")
        render.update(status='success',output=output,sha256=store.digest(Path(output['video_path'])))
        job['progress']='ready_for_review';_save(directory,job)
        reference=sg.render_reference(root,scene_id,Path(output['video_path']),
            dict(clip_version=render['version'],code_path=render['code_path']))
        return dict(reference,preview_engine='json')
    except Exception as exc:
        if render:
            render.update(status='failed',error=redact(str(exc)));_save(directory,job)
        raise


def generate(project_dir,scene_id,version,sha256):
    from src.scene_design import scene_generation as sg
    from src.scene_design.scene_preview import VERSION
    with _job_lock(Path(project_dir).resolve()):
        with store.locked_project(project_dir) as root:
            project,reviews=wf._load(root)
            artifact=_artifact(project,reviews,scene_id,version)
            if not artifact or artifact['sha256']!=sha256:
                raise WorkflowError('STALE_REVIEW','Reload the design')
            wf._verify(root,artifact);snapshot=dict(artifact)
            cached=project.get('design_previews',{}).get(scene_id+':'+version)
            if cached and cached.get('renderer')=='manim':
                if cached['design_sha256']!=sha256:
                    raise WorkflowError('STALE_REVIEW','Reload the motion preview')
                _verify(root,cached)
                return cached
            embedded=artifact.get('manim_render')
            if embedded:
                _verify(root,embedded)
                reference=dict(embedded,render_version=embedded['version'],scene_id=scene_id,
                               version=version,design_sha256=sha256)
                project.setdefault('design_previews',{})[scene_id+':'+version]=reference
                wf._save(root,project,reviews)
                return reference
            config=sg._config(root)
            design=store.read_json(root/artifact['path'])
            assets_path=Path(artifact.get('review_path') or root/artifact['path']).parent/'assets.json'
            assets=store.read_json(assets_path) if assets_path.exists() else dict(assets=[])
            fingerprint=dict(kind='manim_design_preview',stage='scene_design',scene_id=scene_id,
                version=version,design_sha256=sha256,config=config,renderer_version=VERSION)
            base=root/sg.BASE;base.mkdir(parents=True,exist_ok=True)
            if not (base/'ledger.json').exists():
                if project.get('scene_generation') or any(base.iterdir()):
                    raise WorkflowError('STATE_MISSING','Missing preview ledger; existing costs are preserved')
                store.atomic_write(base/'ledger.json',dict(budget_usd=sg.BUDGET,config=config,jobs=[]))
                project['scene_generation']=dict(path=str((base/'ledger.json').relative_to(root)))
                wf._save(root,project,reviews)
            job_id='manim_'+scene_id+'_'+version+'_'+hashlib.sha256(_json_bytes(fingerprint)).hexdigest()[:16]
            directory=base/job_id;directory.mkdir(exist_ok=True)
            relative=str((directory/'job.json').relative_to(root));index=sg._index(root)
            if (directory/'job.json').exists():job=store.read_json(directory/'job.json')
            elif any(directory.iterdir()):raise WorkflowError('STATE_MISSING','Missing local preview job')
            else:
                job=dict(job_id=job_id,input=fingerprint,status='planned',calls=[],created_at=_now(),last_error=None)
                _save(directory,job)
            if relative not in index['jobs']:
                index['jobs'].append(relative);store.atomic_write(base/'ledger.json',index)
        try:
            job.update(status='working',last_error=None);_save(directory,job)
            reference=render_design(root,directory,job,design,assets,config)
            reference.update(scene_id=scene_id,render_version=reference['version'],version=version,design_sha256=sha256)
            with store.locked_project(root):
                project,reviews=wf._load(root);wf._verify(root,snapshot)
                current=_artifact(project,reviews,scene_id,version)
                if not current or current['sha256']!=sha256 or sg._config(root)!=config:
                    raise WorkflowError('STALE_REVIEW','Design inputs changed during render')
                _verify(root,reference)
                project.setdefault('design_previews',{})[scene_id+':'+version]=reference
                job.update(status='awaiting_review',last_error=None);_save(directory,job)
                wf._save(root,project,reviews)
            return reference
        except Exception as exc:
            job.update(status='failed',last_error=redact(str(exc)));_save(directory,job)
            raise
