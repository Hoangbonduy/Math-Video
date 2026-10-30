"""Render saved model designs in the app, without contacting a model or TTS."""
import copy,hashlib
from pathlib import Path
from types import SimpleNamespace
from src.shared import _workflow_store as store
from src.shared import workflow as wf
from src.scene_design import scene_generation as sg
from src.scene_design import scene_contract as contract
from src.scene_design import scene_render_v2
from src.scene_design import scene_drafts
from src.shared._workflow_store import WorkflowError
from src.shared.job_session import _job_lock, _now
from src.shared.ai_config import get_model,get_backend


def fit_labels(design,fps=12,assets=()):
    """Bounded local label placement; preserves math, text, timing and visibility."""
    result=copy.deepcopy(design);changes=[]
    for _ in range(6):
        report=contract.motion_report(contract.compile_design(result,assets=assets),fps)
        if report['passed']:return result,changes,report
        if any(i['category']!='layout' for i in report['issues']):return result,changes,report
        ids=list(dict.fromkeys(oid for issue in report['issues'] for oid in issue['object_ids']))
        best=None;score=len(report['issues'])
        owners={b['target']:b for b in result['bindings']}
        for oid in ids:
            obj=next(o for o in result['objects'] if o['id']==oid)
            bind=owners.get(oid)
            if obj['kind']!='text' or bind and bind['kind'] not in ('label_follow','value_label'):continue
            for dx,dy in [(0,-.04),(0,.04),(-.04,0),(.04,0),(0,-.08),(0,.08),(-.08,0),(.08,0),(-.06,-.06),(.06,-.06),(-.06,.06),(.06,.06),(0,-.12),(0,.12)]:
                candidate=copy.deepcopy(result)
                if bind and bind['kind']=='label_follow':
                    target=next(b for b in candidate['bindings'] if b['id']==bind['id']);target['offset_x']+=dx;target['offset_y']+=dy
                else:
                    target=next(o for o in candidate['objects'] if o['id']==oid);target['x']+=dx;target['y']+=dy
                check=contract.motion_report(contract.compile_design(candidate,assets=assets),fps)
                if len(check['issues'])<score:
                    best=(candidate,dict(object_id=oid,dx=dx,dy=dy,reason='Separate visible text boxes and keep labels in frame'));score=len(check['issues'])
                if score==0:break
            if score==0:break
        if best is None:return result,changes,report
        result,change=best;changes.append(change)
    return result,changes,contract.motion_report(contract.compile_design(result,assets=assets),fps)


def available(root):
    root=Path(root);index=root/sg.BASE/'ledger.json'
    if not index.exists():return []
    project=store.read_json(root/'project.json');script=wf._latest(project,'script',None)
    return sorted({j['input']['scene_id'] for j in sg._jobs(root) if j['input'].get('stage')=='scene_design' and j['input'].get('script_sha256')==script['sha256'] and any((root/sg.BASE/j['job_id']).glob('round_*/design.json'))}) if script else []


def generate(project_dir,scene_id,attempt_id):
    if not isinstance(attempt_id,str) or not attempt_id or len(attempt_id)>128:raise WorkflowError('INVALID_INPUT','Thiếu mã lượt dựng.')
    from src.scene_design.manim_runtime import info
    info()
    root=Path(project_dir).resolve()
    with _job_lock(root):
        with store.locked_project(root):
            project,reviews=wf._load(root)
            if scene_id not in project['scene_ids']:raise WorkflowError('SCENE_NOT_FOUND','Không có cảnh này.')
            script,payload,durations=sg._context(root,project,reviews);config=sg._config(root)
            scene=next(s for s in payload['script']['scenes'] if s['scene_id']==scene_id)
            job_id='saved_design_'+scene_id+'_'+hashlib.sha256(attempt_id.encode()).hexdigest()[:16]
            if not (root/sg.BASE/'ledger.json').exists():raise WorkflowError('SAVED_DESIGN_INVALID','Chưa có thiết kế đã lưu.')
            directory=root/sg.BASE/job_id;directory.mkdir(exist_ok=True)
            path=directory/'job.json'
            index=sg._index(root)
            if path.exists():
                job=store.read_json(path)
                if job['input']['script_sha256']!=script['sha256']:raise WorkflowError('STALE_JOB','Kịch bản đã đổi.')
                if job.get('draft'):scene_drafts.verify(root,scene_drafts.current(root)[scene_id])
            else:
                job=dict(job_id=job_id,input=dict(kind='saved_design_render',stage='scene_design',scene_id=scene_id,version=wf._next_version(project,'scene_design',scene_id),script_sha256=script['sha256'],model=get_model(),ai_backend=get_backend(),attempt_id=attempt_id,trial_mode=True,max_repairs=0),status='working',progress='checking_relations',calls=[],created_at=_now(),max_repairs=0,repair_round=0,design_pipeline=2,last_error=None)
                store.atomic_write(path,job);index['jobs'].append(str(path.relative_to(root)));store.atomic_write(root/sg.BASE/'ledger.json',index)
        if job.get('draft'):return wf.get_status(root)
        def save():store.atomic_write(path,job)
        try:
            selection=[];chosen=None
            for prior in reversed(sg._jobs(root)):
                i=prior['input']
                if prior['job_id']==job_id or i.get('kind')=='saved_design_render' or i.get('scene_id')!=scene_id or i.get('stage')!='scene_design' or i.get('script_sha256')!=script['sha256']:continue
                source=root/sg.BASE/prior['job_id']
                for candidate in sorted(source.glob('round_*/design.json'),reverse=True):
                    try:
                        design=store.read_json(candidate);assets=store.read_json(source/'v2_assets.json');contract.compile_design(design,scene,durations[scene_id],assets['assets'])
                        if abs(design['aspect_ratio']-config['render']['resolution'][0]/config['render']['resolution'][1])>1e-6:raise WorkflowError('INVALID_SCENE_DESIGN','Sai tỷ lệ khung hình.')
                        repaired,changes,checks=fit_labels(design,config['render']['fps'],assets['assets'])
                        if not checks['passed']:raise WorkflowError('INVALID_SCENE_DESIGN',str(checks['issues']))
                        story=store.read_json(candidate.with_name('storyboard.json'));contract.validate_story(story,scene,durations[scene_id])
                        chosen=(candidate,repaired,changes,checks,story,assets);break
                    except (WorkflowError,OSError,KeyError,ValueError) as exc:selection.append(dict(path=str(candidate.relative_to(root)),error=str(exc)))
                if chosen:break
            if not chosen:raise WorkflowError('SAVED_DESIGN_INVALID','Chưa có thiết kế đã lưu có thể dựng an toàn. Cần lượt Codex mới khi hạn mức khả dụng.')
            candidate,design,changes,checks,story,assets=chosen
            work=directory/'round_00';work.mkdir(exist_ok=True)
            for name,data in [('design.json',design),('storyboard.json',story),('motion_checks.json',checks),('local_layout_changes.json',changes),('source.json',dict(path=str(candidate.relative_to(root)),sha256=store.digest(candidate),rejected_candidates=selection))]:
                target=work/name
                if target.exists() and store.read_json(target)!=data:raise WorkflowError('CACHE_CORRUPT','Dữ liệu lượt dựng đã thay đổi.')
                if not target.exists():store.atomic_write(target,data)
            job.update(progress='rendering_draft');save()
            render_job=store.read_json(work/'job.json') if (work/'job.json').exists() else {}
            reference,render_checks,manifest=scene_render_v2.render(root,work,render_job,design,assets,config,story)
            if not render_checks['passed']:raise WorkflowError('RENDER_FAILED',str(render_checks['issues']))
            with store.locked_project(root):
                current,_=wf._load(root)
                if wf._latest(current,'script',None)['sha256']!=script['sha256']:raise WorkflowError('STALE_JOB','Kịch bản đã đổi trong khi dựng; kết quả vẫn được lưu.')
                scene_drafts.publish(root,work,SimpleNamespace(job=job,save=save),reference)
                job['draft']['review']=dict(status='not_run',summary='Dựng từ thiết kế Codex đã lưu. Chưa đánh giá lại bằng AI; không gọi Codex hoặc FPT.')
                job.update(status='draft_ready',progress='draft_ready',last_error=None);save()
            return wf.get_status(root)
        except Exception as exc:
            job.update(status='failed',last_error=str(exc));save();raise
