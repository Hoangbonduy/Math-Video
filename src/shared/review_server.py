"""Loopback-only review UI. Mutation requires matching Origin, token and request ID."""
import concurrent.futures
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import secrets
import threading
from urllib.parse import urlsplit,parse_qs,quote

from src.shared import workflow as wf
from src.shared import _workflow_store as store
from src.shared._workflow_store import WorkflowError
from src.shared.review import record_review
from src.scene_design import scene_revision as rev
from src.shared.env import load_local_env

API_VERSION = 1


class ReviewServer(ThreadingHTTPServer):
    daemon_threads=True
    def __init__(self,project_dir,port=8765):
        load_local_env()
        from src.shared.ai_config import scene_trial_enabled
        self.scene_trial=scene_trial_enabled()
        self.root=Path(project_dir).resolve()
        if not self.root.exists() or not any(self.root.iterdir()):
            wf.create_project(self.root, 'Video mới')
            from src.idea.service import save_draft, default_values
            save_draft(self.root, default_values())
        wf.get_status(self.root)
        self.token=secrets.token_urlsafe(32); self.jobs={}; self.jobs_lock=threading.RLock()
        self.pool=concurrent.futures.ThreadPoolExecutor(max_workers=1)
        from src.shared.codex_auth import CodexAuth
        self.codex_auth=CodexAuth()
        super().__init__(('127.0.0.1',port),Handler)
        self.origin=f'http://127.0.0.1:{self.server_port}'

    def enqueue(self,key,callback):
        with self.jobs_lock:
            if self.codex_auth.busy:
                raise WorkflowError('CODEX_AUTH_BUSY','Hoàn tất hoặc hủy đăng nhập Codex trước khi chạy tác vụ.')
            if key in self.jobs and self.jobs[key]['state'] in ('queued','working'): return
            self.jobs[key]=dict(state='queued',error=None)
        def work():
            with self.jobs_lock: self.jobs[key]['state']='working'
            try:
                callback()
                with self.jobs_lock: self.jobs[key]['state']='done'
            except Exception as exc:
                from src.shared.openrouter_client import redact
                with self.jobs_lock: self.jobs[key]=dict(state='failed',error=redact(str(exc)))
        self.pool.submit(work)

    def continue_scenes(self, scene_id=None, retry_uncertain=None, attempt_id=None):
        stage=wf.get_status(self.root)['stage']
        sequence=stage in ('scene_design','scene_clip') and not retry_uncertain
        key='workflow' if sequence or not scene_id else 'generate:'+scene_id
        self.enqueue(key,lambda:wf.run_workflow(
            self.root,ai=True,scene_id=scene_id,retry_uncertain=retry_uncertain,
            scene_trial=self.scene_trial,attempt_id=attempt_id,auto_continue=sequence))

    def recover(self):
        from src.scene_design.scene_drafts import recover_rendered
        recover_rendered(self.root)
        state=wf.get_status(self.root)
        if state['stage']=='script' and state.get('generation',{}).get('status') in ('working','planned'):
            self.enqueue('workflow',lambda:wf.run_workflow(self.root,ai=True,scene_trial=self.scene_trial))
        from src.scene_design.design_preview import generate as preview
        for sid in state['scene_ids']:
            artifact=next((a for a in reversed(state['artifacts']) if a['stage']=='scene_design' and a['scene_id']==sid and a.get('origin')=='ai_scene' and not a.get('invalidated')),None)
            if artifact and artifact.get('manim_render') and sid+':'+artifact['version'] not in state.get('design_previews',{}):
                self.enqueue('preview:'+sid,lambda a=artifact:preview(self.root,a['scene_id'],a['version'],a['sha256']))
        for job in state['audio_generation']['jobs']:
            if job['status']=='working' and state['stage'] in ('scene_av','final_video'):
                from src.preview.av_workflow import generate
                detail=next((store.read_json(path) for path in (self.root/'generation/checkpoint_7/jobs').glob('*/job.json') if store.read_json(path)['job_id']==job['job_id']),None)
                if detail:
                    sid=detail['input']['inputs']['clip']['scene_id']
                    self.enqueue('c7:'+sid,lambda scene=sid:generate(self.root,scene_id=scene))
        for job in state.get('scene_generation',{}).get('jobs',[]):
            if not job.get('current',True): continue
            if job['status'] in ('working','planned') and not job['job_id'].startswith('c6_'):
                if job.get('kind')=='saved_design_render':
                    from src.scene_design.scene_replay import generate as replay
                    detail=store.read_json(self.root/'generation/checkpoint_5'/job['job_id']/'job.json')
                    self.enqueue('generate:'+job['scene_id'],lambda sid=job['scene_id'],aid=detail['input']['attempt_id']:replay(self.root,sid,aid))
                elif job.get('kind')=='manim_design_preview':
                    artifact=next((a for a in state['artifacts'] if a['stage']=='scene_design' and a['scene_id']==job['scene_id'] and a['version']==job.get('version') and not a.get('invalidated')),None)
                    if artifact:
                        self.enqueue('preview:'+job['scene_id'],lambda a=artifact:preview(self.root,a['scene_id'],a['version'],a['sha256']))
                elif job['stage']==state['stage']:
                    self.continue_scenes(job['scene_id'])
        for r in state['scene_revisions']:
            if r['status'] in ('analyzing','planned','rendering'):
                self.enqueue(r['id'],lambda rid=r['id']:rev.resume_revision(self.root,rid))

    def server_close(self):
        self.codex_auth.close()
        super().server_close(); self.pool.shutdown(wait=True)


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def _host(self): return self.headers.get('Host')==self.server.origin.removeprefix('http://')
    def send_json(self,status,data):
        payload=json.dumps(data,ensure_ascii=False).encode(); self.send_response(status)
        self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Content-Length',str(len(payload)))
        self.send_header('Cache-Control','no-store'); self.end_headers()
        try: self.wfile.write(payload)
        except (BrokenPipeError,ConnectionResetError): pass
    def _headers(self,kind,size,status=200):
        self.send_response(status); self.send_header('Content-Type',kind); self.send_header('Content-Length',str(size))
        self.send_header('X-Content-Type-Options','nosniff'); self.send_header('Cache-Control','no-store')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; media-src 'self'; frame-ancestors 'none'")
    def do_GET(self):
        if not self._host(): self.send_json(403,dict(error='Invalid host')); return
        url=urlsplit(self.path)
        if url.path=='/api/codex-auth':
            if self.headers.get('X-Review-Token')!=self.server.token:
                self.send_json(403,dict(error='Invalid local review session')); return
            self.send_json(200,self.server.codex_auth.snapshot(force=True)); return
        if url.path=='/api/state':
            state=wf.get_status(self.server.root)
            from src.preview.continuous_preview import status as preview_status
            from src.idea.service import default_values
            state['idea_defaults']=default_values()
            state['scene_trial_mode']=self.server.scene_trial
            from src.scene_design.scene_drafts import current as current_drafts
            state['scene_drafts']=current_drafts(self.server.root)
            from src.scene_design.scene_replay import available as saved_design_scenes
            state['saved_design_scenes']=saved_design_scenes(self.server.root)
            state['continuous_preview']=preview_status(self.server.root)
            from src.preview.music import state as music_state
            state['music']=music_state(self.server.root)
            with self.server.jobs_lock: tasks=dict(self.server.jobs)
            from src.shared.ai_config import get_model
            self.send_json(200,dict(project=state,token=self.server.token,tasks=tasks,api_version=API_VERSION,codex_auth=self.server.codex_auth.snapshot(),ai_provider=dict(backend=state['ai_backend'],model=get_model()),capabilities=['script_history','new_generation','manim_design','continuous_preview','scene_design_v2','codex_auth','ai_provider'])); return
        if url.path=='/api/diagnostics':
            from datetime import datetime,timezone
            from src.scene_design.scene_drafts import current as current_drafts
            from src.preview.continuous_preview import status as preview_status
            with self.server.jobs_lock:tasks=dict(self.server.jobs)
            self.send_json(200,dict(created_at=datetime.now(timezone.utc).isoformat(),project=wf.get_status(self.server.root),drafts=current_drafts(self.server.root),preview=preview_status(self.server.root),tasks=tasks))
            return
        if url.path=='/api/script-history':
            from src.script_history.service import history, detail
            try:
                version=parse_qs(url.query).get('version',[None])[0]
                result=detail(self.server.root,version) if version else history(self.server.root)
                self.send_json(200,result)
            except WorkflowError as exc:
                self.send_json(404 if exc.code=='SCRIPT_NOT_FOUND' else 409,dict(error=str(exc)))
            return
        if url.path=='/api/manim-runtime':
            from src.scene_design.manim_runtime import info
            try: self.send_json(200,info())
            except WorkflowError as exc: self.send_json(200,dict(available=False,error=str(exc)))
            return
        if url.path=='/api/context':
            self.send_json(200,self._context()); return
        if url.path=='/music-media':
            from src.preview.music import track
            try: _, target=track(parse_qs(url.query).get('id',[''])[0])
            except WorkflowError as exc:
                self.send_json(403,dict(error=str(exc))); return
            self._file(target); return
        if url.path=='/media':
            path=parse_qs(url.query).get('path',[''])[0]
            try: target=self._media(path)
            except WorkflowError as exc: self.send_json(403,dict(error=str(exc))); return
            self._file(target); return
        web=Path(__file__).with_name('review_web')
        files={'/':web/'index.html','/app.js':web/'app.js','/style.css':web/'style.css'}
        for feature in ('idea','scene_design','preview','script_history'):
            files['/'+feature+'/web.js']=Path(__file__).resolve().parents[1]/feature/'web.js'
        if url.path not in files: self.send_json(404,dict(error='Not found')); return
        self._file(files[url.path])
    def _context(self):
        """Read registered content for the editor without opening arbitrary JSON files."""
        state=wf.get_status(self.server.root)
        def read(path):
            try:
                if Path(path).is_absolute(): path=str(Path(path).relative_to(self.server.root))
                target=store.project_path(self.server.root,path)
                if target.suffix!='.json' or target.is_symlink() or target.stat().st_size>2_000_000: return {}
                value=json.loads(target.read_text(encoding='utf-8'))
                return value if isinstance(value,dict) else {}
            except (WorkflowError,OSError,ValueError): return {}
        script=next((a for a in reversed(state['artifacts']) if a['stage']=='script' and not a.get('invalidated')),{})
        content=read(script.get('bundle',{}).get('script.json',{}).get('path',''))
        research=read(script.get('bundle',{}).get('research.json',{}).get('path',''))
        designs={}; config=read('scene_config.json')
        artifacts=[a for a in state['artifacts'] if a['stage']=='scene_design' and not a.get('invalidated')]
        for revision in state['scene_revisions']:
            if revision['status']!='cancelled': artifacts.extend(revision['designs'].values())
        for artifact in artifacts:
            design=read(artifact.get('path',''))
            if design: designs[artifact['scene_id']+':'+artifact['version']]=design
            for name,component in artifact.get('bundle',{}).items():
                if name=='scene_config.json': config=read(component['path'])
        return dict(script=content.get('script',{}),brief=state.get('brief') or content.get('brief',{}),
                    sources=research.get('sources',[]),
                    duration_estimate=content.get('duration_estimate',{}),designs=designs,
                    render=config.get('render',{}))
    def _media(self,path):
        target=store.project_path(self.server.root,path)
        if target.suffix.lower() not in ('.png','.jpg','.jpeg','.mp4','.md','.wav','.mp3','.srt','.json','.py','.log'): raise WorkflowError('MEDIA_DENIED','Not review media')
        state=wf.get_status(self.server.root); allowed=set()
        artifacts=list(state['artifacts'])
        from src.preview.continuous_preview import status as preview_status
        previews=preview_status(self.server.root)
        artifacts.extend(previews[mode] for mode in ('silent','audio') if previews[mode])
        artifacts.extend(state.get('design_previews',{}).values())
        from src.scene_design.scene_drafts import media_files
        allowed.update(media_files(self.server.root))
        if state.get('voice_sample'): artifacts.append(state['voice_sample'])
        for r in state['scene_revisions']:
            artifacts.extend(r['designs'].values()); artifacts.extend(r['clips'].values())
        for a in artifacts:
            for name in ('path','review_path','preview_path','audio_path','narration_path','subtitle_path','code_path','log_path','metadata_path','quality_path','storyboard_path'):
                if a.get(name):
                    p=Path(a[name]); allowed.add(p.resolve() if p.is_absolute() else (self.server.root/p).resolve())
            allowed.update((self.server.root/c['path']).resolve() for name,c in a.get('bundle',{}).items() if name=='script.json' or a.get('design_pipeline')==2 and name.endswith(('quality.json','storyboard.json','evidence.json','visual_review.json','render_checks.json','motion_checks.json','result.json')) or Path(c['path']).suffix not in ('.json',))
            if a.get('manifest_path'): allowed.add((self.server.root/a['manifest_path']).resolve())
        if target.is_symlink() or target.resolve() not in allowed or not target.is_file(): raise WorkflowError('MEDIA_DENIED','Not registered review media')
        return target
    def _file(self,path):
        size=path.stat().st_size; start,end=0,size-1; status=200
        value=self.headers.get('Range')
        if value:
            import re
            match=re.fullmatch(r'bytes=(\d*)-(\d*)',value)
            if not match or not size: self.send_json(416,dict(error='Invalid range')); return
            a,b=match.groups()
            if not a and b: start=max(0,size-int(b))
            elif a: start=int(a); end=min(size-1,int(b)) if b else size-1
            else: self.send_json(416,dict(error='Invalid range')); return
            if start>end or start>=size: self.send_json(416,dict(error='Range outside media')); return
            status=206
        self._headers(mimetypes.guess_type(path)[0] or 'application/octet-stream',end-start+1,status)
        self.send_header('Accept-Ranges','bytes')
        if status==206: self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
        self.end_headers()
        try:
            with path.open('rb') as f:
                f.seek(start); left=end-start+1
                while left:
                    data=f.read(min(left,65536))
                    if not data: break
                    self.wfile.write(data); left-=len(data)
        except (BrokenPipeError,ConnectionResetError): pass
    def do_POST(self):
        if not self._host() or self.headers.get('Origin')!=self.server.origin or self.headers.get('X-Review-Token')!=self.server.token:
            self.send_json(403,dict(error='Invalid local review session')); return
        try:
            length=int(self.headers.get('Content-Length',0))
            if not 0<length<=20000: raise WorkflowError('INVALID_INPUT','Request size limit')
            data=json.loads(self.rfile.read(length))
            request_id=data.get('request_id')
            if not isinstance(request_id,str) or not 1<=len(request_id)<=100: raise WorkflowError('INVALID_INPUT','Request ID is required')
            if self.path=='/api/ai-provider':
                with self.server.jobs_lock:
                    if any(t['state'] in ('queued','working') for t in self.server.jobs.values()) or self.server.codex_auth.busy:
                        raise WorkflowError('JOB_BUSY','Chờ tác vụ hoặc phiên đăng nhập hoàn tất trước khi đổi nguồn AI.')
                    from src.shared.ai_config import set_backend
                    result=set_backend(data.get('backend'))
                self.send_json(200,result); return
            if self.path in ('/api/codex-login','/api/codex-logout','/api/codex-login-cancel'):
                with self.server.jobs_lock:
                    if any(t['state'] in ('queued','working') for t in self.server.jobs.values()):
                        raise WorkflowError('JOB_BUSY','Chờ tác vụ hiện tại hoàn tất trước khi đổi tài khoản Codex.')
                    if self.path=='/api/codex-login-cancel':
                        result=self.server.codex_auth.cancel()
                    else:
                        result=self.server.codex_auth.start(request_id,method=data.get('method','device'),logout=self.path=='/api/codex-logout')
                self.send_json(200,result); return
            if self.server.codex_auth.busy and self.path!='/api/idea-draft':
                raise WorkflowError('CODEX_AUTH_BUSY','Hoàn tất hoặc hủy đăng nhập Codex trước khi chạy tác vụ.')
            if self.path=='/api/script-delete':
                from src.script_history.service import delete
                with self.server.jobs_lock:
                    if any(t['state'] in ('queued','working') for t in self.server.jobs.values()):
                        raise WorkflowError('JOB_BUSY','Chờ tác vụ đang chạy hoàn tất trước khi xóa kịch bản.')
                    result=delete(self.server.root,data.get('version'),data.get('sha256'))
                self.send_json(200,result); return
            with store.locked_project(self.server.root) as root:
                p,rv=wf._load(root)
                previous=rv.get('web_actions',{}).get(request_id)
                digest=hashlib.sha256(json.dumps([self.path,data],sort_keys=True).encode()).hexdigest()
                if previous:
                    if previous['digest']!=digest: raise WorkflowError('REQUEST_CONFLICT','Request ID reused with different payload')
                    if previous.get('done'): self.send_json(200,dict(ok=True,replayed=True)); return
                rv.setdefault('web_actions',{})[request_id]=dict(digest=digest,done=False)
                wf._save(root,p,rv)
            root=self.server.root; sid=data.get('scene_id'); version=data.get('version'); checksum=data.get('sha256')
            if self.path=='/api/idea-draft':
                from src.idea.service import save_draft
                save_draft(root,data['idea'])
            elif self.path=='/api/script-edit':
                from src.idea.service import edit_script
                edit_script(root, version, scenes=data.get('scenes'), scene_id=sid, narration=data.get('narration'), visual_intent=data.get('visual_intent'), expected_sha256=checksum)
            elif self.path=='/api/start':
                from src.idea.service import apply_idea
                with self.server.jobs_lock:
                    if any(t['state'] in ('queued','working') for t in self.server.jobs.values()):
                        raise WorkflowError('JOB_BUSY','Đang xử lý. Hãy chờ hoàn tất trước khi bắt đầu lại từ ý tưởng.')
                    apply_idea(root,data['idea'],force_new=True)
                    self.server.jobs.clear()
                    self.server.enqueue('workflow',lambda:wf.run_workflow(root,ai=True,scene_trial=self.server.scene_trial))
            elif self.path=='/api/design-preview':
                from src.scene_design.design_preview import generate
                self.server.enqueue('preview:'+str(sid),lambda:generate(root,sid,version,checksum))
            elif self.path=='/api/preview-build':
                from src.preview.continuous_preview import generate
                with_audio=data.get('with_audio',False)
                if type(with_audio) is not bool: raise WorkflowError('INVALID_INPUT','with_audio phải là boolean')
                self.server.enqueue('continuous-preview',lambda:generate(root,with_audio=with_audio))
            elif self.path=='/api/music-settings':
                from src.preview.music import configure
                configure(root,data['config'])
                with self.server.jobs_lock:
                    if self.server.jobs.get('continuous-preview',{}).get('state') not in ('working','queued'):
                        self.server.jobs.pop('continuous-preview',None)
            elif self.path=='/api/av-settings':
                from src.preview.av_workflow import configure
                configure(root,data['config'])
                with self.server.jobs_lock:
                    for key in ('voice-sample','continuous-preview'):
                        if self.server.jobs.get(key,{}).get('state') not in ('working','queued'):self.server.jobs.pop(key,None)
            elif self.path=='/api/audio-retry':
                from src.preview.av_workflow import retry_audio
                self.server.enqueue('retry:'+data['call_id'],lambda:retry_audio(root,data['call_id']))
            elif self.path=='/api/audio-sample':
                from src.preview.av_workflow import sample
                self.server.enqueue('voice-sample',lambda:sample(root,data.get('retry_uncertain')))
            elif self.path in ('/api/av-generate','/api/assemble'):
                from src.preview.av_workflow import generate
                self.server.enqueue('c7:'+str(sid),lambda:generate(root,scene_id=sid,retry_uncertain=data.get('retry_uncertain')))
            elif self.path=='/api/feedback':
                if data.get('stage')=='script':
                    record_review(root,'script',version,'request_changes',feedback=data.get('feedback'),expected_sha256=checksum)
                    self.server.enqueue('workflow',lambda:wf.run_workflow(root,ai=True,scene_trial=self.server.scene_trial))
                elif data.get('stage')=='scene_av':
                    from src.preview.av_workflow import reopen,generate
                    reopen(root,sid,version,checksum,data.get('feedback'))
                    self.server.enqueue('c7:'+str(sid),lambda:generate(root,scene_id=sid))
                elif data.get('stage')=='final_video':
                    record_review(root,'final_video',version,'request_changes',feedback=data.get('feedback'),expected_sha256=checksum)
                elif data.get('stage')=='scene_design':
                    record_review(root,'scene_design',version,'request_changes',scene_id=sid,feedback=data.get('feedback'),expected_sha256=checksum)
                    self.server.enqueue('generate:'+str(sid),lambda:wf.run_workflow(root,ai=True,scene_id=sid,scene_trial=self.server.scene_trial))
                else:
                    r=rev.request_revision(root,sid,version,checksum,data.get('feedback'),time_seconds=data.get('time_seconds'),request_id=request_id)
                    self.server.enqueue(r['id'],lambda:rev.resume_revision(root,r['id']))
            elif self.path=='/api/approve':
                result=record_review(root,data['stage'],version,'approve',scene_id=sid,read_seconds=data.get('read_seconds'),expected_sha256=checksum,expected_preview_sha256=data.get('preview_sha256'))
                if result['stage']!=data['stage'] and result['stage'] in ('scene_design','scene_clip'):
                    self.server.enqueue('workflow',lambda:wf.run_workflow(root,ai=True,scene_trial=self.server.scene_trial))
            elif self.path=='/api/scope':
                rev.approve_scope(root,data['revision_id'],data['plan_sha256'],expected_round=data['round'])
                self.server.enqueue(data['revision_id'],lambda:rev.resume_revision(root,data['revision_id']))
            elif self.path=='/api/amend':
                r=rev.amend_revision(root,data['revision_id'],data.get('feedback'),expected_round=data['round'])
                self.server.enqueue(r['id'],lambda:rev.resume_revision(root,r['id']))
            elif self.path=='/api/cancel': rev.cancel_revision(root,data['revision_id'])
            elif self.path=='/api/approve-design':
                rev.approve_design(root,data['revision_id'],sid,checksum,expected_round=data['round'])
                self.server.enqueue(data['revision_id'],lambda:rev.resume_revision(root,data['revision_id']))
            elif self.path=='/api/render-saved':
                from src.scene_design.scene_replay import generate as replay
                if not sid:raise WorkflowError('INVALID_INPUT','Chọn một cảnh trước khi dựng.')
                self.server.enqueue('generate:'+sid,lambda:replay(root,sid,request_id))
            elif self.path=='/api/continue':
                rid=data.get('revision_id')
                fresh=data.get('new_attempt',False)
                if type(fresh) is not bool or fresh and (rid or not sid or data.get('retry_uncertain')):
                    raise WorkflowError('INVALID_INPUT','Lượt mới cần chọn một cảnh và không kèm request chưa rõ kết quả')
                if rid:
                    self.server.enqueue(rid,lambda:rev.resume_revision(root,rid,retry_uncertain=data.get('retry_uncertain')))
                else:
                    self.server.continue_scenes(sid,data.get('retry_uncertain'),request_id if fresh else None)
            else: raise WorkflowError('INVALID_INPUT','Unknown action')
            with store.locked_project(root):
                p,rv=wf._load(root); rv['web_actions'][request_id]['done']=True; wf._save(root,p,rv)
            self.send_json(200,dict(ok=True))
        except (WorkflowError,ValueError,KeyError,TypeError) as exc:
            self.send_json(409,dict(error=str(exc)))


def serve_review(project_dir,port=8765):
    server=ReviewServer(project_dir,port); print('Trang duyệt: '+server.origin,flush=True)
    server.recover()
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()
