"""Inspectable rendered drafts; never workflow artifacts or review approvals."""
from pathlib import Path
from src.shared import _workflow_store as store
from src.shared._workflow_store import WorkflowError


def publish(root, work, session, reference):
    root,work=Path(root),Path(work)
    checks=store.read_json(work/'render_checks.json')
    motion=store.read_json(work/'motion_checks.json')
    if not checks['passed'] or not motion['passed']:
        raise WorkflowError('DRAFT_INVALID','Bản nháp cần qua kiểm tra chuyển động và render.')
    for key,digest in [('path','sha256'),('code_path','code_sha256')]:
        if reference.get(digest) and store.digest(store.project_path(root,reference[key]))!=reference[digest]:
            raise WorkflowError('ARTIFACT_CHANGED','Video hoặc mã render đã thay đổi.')
    paths=[work/n for n in ('design.json','storyboard.json','render_checks.json','motion_checks.json','evidence.json')]
    paths += [store.project_path(root,reference[k]) for k in ('path','code_path','log_path','metadata_path')]
    paths += list(work.glob('contact_sheet*.jpg'))
    paths += [work/n for n in ('local_layout_changes.json','source.json') if (work/n).exists()]
    files=[dict(path=str(p.relative_to(root)),sha256=store.digest(p)) for p in paths]
    record=dict(job_id=session.job['job_id'],input=session.job.get('input',{}),reference=reference,files=files,
                design_path=str((work/'design.json').relative_to(root)),storyboard_path=str((work/'storyboard.json').relative_to(root)),
                render_checks_path=str((work/'render_checks.json').relative_to(root)),motion_checks_path=str((work/'motion_checks.json').relative_to(root)))
    path=work/'draft.json'
    if path.exists():
        if store.read_json(path)!=record:raise WorkflowError('CACHE_CORRUPT','Bằng chứng bản nháp đã thay đổi.')
    else:store.atomic_write(path,record)
    previous=session.job.get('draft',{})
    session.job['draft']=dict(path=str(path.relative_to(root)),sha256=store.digest(path),review=previous.get('review'))
    session.save()
    return record


def current(root):
    root=Path(root);project=store.read_json(root/'project.json')
    script=next((a for a in reversed(project['artifacts']) if a['stage']=='script' and not a.get('invalidated')),None)
    index=root/'generation/checkpoint_5/ledger.json'
    if not script or not index.exists():return {}
    from src.shared.ai_config import get_model,get_backend
    result={}
    for path in store.read_json(index)['jobs']:
        job=store.read_json(store.project_path(root,path));i=job['input'];ref=job.get('draft')
        if not ref or i.get('stage')!='scene_design' or i.get('script_sha256')!=script['sha256'] or i.get('model')!=get_model() or i.get('ai_backend','openrouter')!=get_backend():continue
        try:
            record_path=store.project_path(root,ref['path'])
            if record_path.is_symlink() or store.digest(record_path)!=ref['sha256']:continue
            record=store.read_json(record_path)
            if record['job_id']!=job['job_id'] or record['input']!=i:continue
            result[i['scene_id']]=dict(record,record_path=ref['path'],record_sha256=ref['sha256'],review=ref.get('review'),status=job['status'])
        except (OSError,KeyError,WorkflowError):continue
    return result


def verify(root,draft):
    root=Path(root)
    for item in draft['files']:
        path=store.project_path(root,item['path'])
        if path.is_symlink() or not path.is_file() or store.digest(path)!=item['sha256']:
            raise WorkflowError('ARTIFACT_CHANGED','Bằng chứng bản nháp đã thay đổi: '+item['path'])
    return draft


def media_files(root):
    allowed=set()
    for draft in current(root).values():
        try:verify(root,draft)
        except (OSError,WorkflowError):continue
        allowed.update(store.project_path(root,item['path']).resolve() for item in draft['files'])
        allowed.add(store.project_path(root,draft['record_path']).resolve())
    return allowed


def recover_rendered(root):
    """Attach existing valid renders after an upgrade, without model/TTS calls."""
    from types import SimpleNamespace
    from src.scene_design import scene_pipeline
    root=Path(root);index=root/'generation/checkpoint_5/ledger.json'
    if not index.exists():return
    for relative in store.read_json(index)['jobs']:
        path=store.project_path(root,relative);job=store.read_json(path)
        if job.get('draft') or job.get('status') in ('working','planned') or job['input'].get('stage')!='scene_design':continue
        directory=path.parent
        for work in sorted(directory.glob('round_*'),reverse=True):
            record_path=work/'job.json'
            if not record_path.exists() or not (work/'render_checks.json').exists():continue
            render=store.read_json(record_path).get('relation_render',{})
            if render.get('status')!='success':continue
            try:
                session=SimpleNamespace(job=job,save=lambda:store.atomic_write(path,job))
                publish(root,work,session,render['reference'])
                phase='v2_visual_review_'+str(int(work.name.split('_')[-1]))
                call=next((c for c in job['calls'] if c['phase']==phase and c['status']=='complete'),None)
                if call:
                    response_path=directory/'calls'/(call['call_id']+'.json')
                    if store.digest(response_path)!=call['response_sha256']:raise WorkflowError('CACHE_CORRUPT','Phản hồi đánh giá đã thay đổi.')
                    def cached_call(request_phase,*args,**kwargs):
                        if request_phase!=phase:raise WorkflowError('CACHE_CORRUPT','Sai bước đánh giá lưu sẵn.')
                        return store.read_json(response_path)
                    session.call=cached_call
                    review=scene_pipeline._review(root,session,None,store.read_json(work/'storyboard.json'),store.read_json(work/'design.json'),store.read_json(work/'evidence.json'),store.read_json(work/'render_checks.json'),int(work.name.split('_')[-1]))
                    job['draft']['review']=dict(passed=review['passed'],summary=review['summary'],criteria_passed=sum(c['passed'] for c in review['criteria_results']),criteria_total=len(review['criteria_results']))
                    if not review['passed']:
                        job.setdefault('error_history',[]).append(dict(error=job.get('last_error'),reason='recovered_saved_visual_review'))
                        job['quality_issues']=review['issues']
                        job['last_error']='SCENE_QUALITY_FAILED: Đã dựng bản nháp; còn tiêu chí hình ảnh chưa đạt. Có thể xem video và chạy lại một lượt mới.'
                    session.save()
                break
            except (OSError,KeyError,ValueError,WorkflowError):
                # A missing/corrupt render remains unavailable; never infer quality.
                continue
