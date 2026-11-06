"""Bounded public scene activity; never expose private reasoning or prompts."""
import json
import re
from pathlib import Path
from src.shared import _workflow_store as store
from src.shared._workflow_store import WorkflowError
from src.shared.openrouter_client import redact

PHASES = {'planning_visuals':'Đang lập kế hoạch hình ảnh và chuyển động',
    'reading_references':'Đang chọn recipe và kiểm tra API',
    'writing_code':'Đang viết mã Manim theo kế hoạch',
    'repairing_code':'Đang sửa mã theo phản hồi kiểm tra',
    'rendering_draft':'Đang render và thu dữ liệu bố cục',
    'evaluating_frames':'Đang đối chiếu ảnh render với tiêu chí',
    'ready_for_review':'Đã có kết quả để duyệt',
    'quality_failed':'Đã dừng; còn lỗi cần sửa'}


def public_event(event):
    kind=event.get('type','')
    item=event.get('item') or {}
    if not isinstance(item,dict):return None
    item_type=item.get('type','')
    # CLI reasoning/analysis events are private, not a user-facing transcript.
    if item_type in ('reasoning','analysis') or 'reasoning' in kind or 'analysis' in kind:
        return None
    status=item.get('status') or kind
    if item_type in ('command_execution','web_search','mcp_tool_call','tool_call','file_change'):
        return dict(kind='tool',id=str(item.get('id','')),status=status,
            name=str(item.get('tool') or item.get('name') or item_type),
            detail=redact(str(item.get('command') or item.get('query') or item.get('arguments') or item.get('changes') or ''))[:4000])
    if item_type=='agent_message':
        text=item.get('text','')
        try:
            result=json.loads(text)
            if isinstance(result,dict) and isinstance(result.get('source'),str):
                return None
        except (ValueError,TypeError):pass
        # Final/user-facing messages only, never the reasoning event type.
        return dict(kind='message',status=status,text=redact(str(text))[:12000])
    if kind in ('thread.started','turn.started','turn.completed','turn.failed','error'):
        return dict(kind='status',status=kind,text=redact(str(event.get('message') or (event.get('error') or {}).get('message') or kind))[:2000])
    return None


def _tail(path,limit=160000):
    if not path.is_file():return ''
    with path.open('rb') as stream:
        stream.seek(max(0,path.stat().st_size-limit))
        return stream.read(limit).decode('utf-8',errors='replace')


def render_state(root,directory,scene_id):
    """Read actual worker results, including previews blocked by layout checks."""
    result={}
    for work in sorted(Path(directory).glob('round_*')):
        path=work/'code_render.json'
        if not path.is_file():continue
        record=store.read_json(path)
        status=record.get('status')
        version=record.get('version')
        if re.fullmatch(r'[A-Za-z0-9_-]+',str(scene_id)) and re.fullmatch(r'v[0-9]+',str(version)):
            metadata=Path(root)/'logs'/f'{scene_id}_{version}.json'
            if metadata.is_file():status=store.read_json(metadata).get('status',status)
        if status=='running' and (work/'result.json').exists():status='failed'
        result=dict(render_status=status,render_version=version)
    return result


def snapshot(root,scene_id,job_id):
    root=Path(root)
    index=root/'generation/checkpoint_5/ledger.json'
    if not index.exists():raise WorkflowError('SCENE_JOB_NOT_FOUND','Chưa có lượt tạo cảnh.')
    job=None;directory=None
    for relative in store.read_json(index)['jobs']:
        path=store.project_path(root,relative)
        candidate=store.read_json(path)
        if candidate.get('job_id')==job_id and candidate.get('input',{}).get('scene_id')==scene_id:
            job=candidate;directory=path.parent;break
    if job is None:raise WorkflowError('SCENE_JOB_NOT_FOUND','Không tìm thấy lượt tạo cảnh trong project này.')
    calls=[];events=[]
    for call in job.get('calls',[])[-12:]:
        calls.append({k:call.get(k) for k in ('call_id','phase','status','started_at','finished_at')})
        cid=call.get('call_id','')
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',cid):continue
        trace=store.project_path(root,str((directory/'codex_calls'/cid/'events.jsonl').relative_to(root)))
        for line in _tail(trace).splitlines():
            try:event=public_event(json.loads(line))
            except (ValueError,TypeError,AttributeError):continue
            if event and event['kind']=='tool':
                event['phase']=call.get('phase','')
                events.append(event)
    plan=[]
    for path in sorted(directory.glob('round_*/storyboard.json')):
        data=store.read_json(path)
        plan=[dict(show=redact(str(beat.get('show','')))[:2000],
                   motion=redact(str(beat.get('motion','')))[:2000])
              for beat in data.get('beats',[])[:8]]
    references=[]
    for path in sorted(directory.glob('round_*/references.json')):
        data=store.read_json(path)
        references=[dict(name=name,available=bool(info.get('available'))) for name,info in data.get('installed_apis',{}).items()]
    # Local orchestration tools also matter when the CLI itself has no tool.
    local_events=[]
    for work in sorted(directory.glob('round_*')):
        ref=work/'references.json'
        if ref.is_file():
            data=store.read_json(ref)
            local_events.append(dict(kind='tool',phase=work.name,name='Tra API / reference',
                status='failed' if data.get('missing') else 'completed',
                detail=', '.join(name+(' ✓' if info.get('available') else ' · không tìm thấy') for name,info in data.get('installed_apis',{}).items())))
        record=work/'code_render.json'
        if record.is_file():
            data=store.read_json(record)
            version=data.get('version')
            metadata=root/'logs'/f'{scene_id}_{version}.json'
            report=store.read_json(metadata) if re.fullmatch(r'v[0-9]+',str(version)) and metadata.is_file() else {}
            status=report.get('status') or data.get('status','')
            if status=='running' and (work/'result.json').exists():status='failed'
            local_events.append(dict(kind='tool',phase=work.name,name='Render Manim / Cairo',
                status=status,detail=str(data.get('code_path') or (data.get('reference') or {}).get('code_path') or '')))
    return redact(dict(job_id=job_id,scene_id=scene_id,status=job.get('status'),
        progress=job.get('progress'),summary=PHASES.get(job.get('progress'),'Đang xử lý tác vụ tạo cảnh'),
        calls=calls,events=(events[-80:]+local_events[-20:]),plan=plan,
        references=references,streaming=any(c.get('status')=='in_flight' for c in calls)))
