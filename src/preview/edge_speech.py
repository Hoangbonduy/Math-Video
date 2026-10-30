"""Microsoft Edge online Vietnamese speech, no user API key or FPT fallback."""
import asyncio,hashlib,math,uuid
from pathlib import Path
from src.shared import _workflow_store as store
from src.shared._workflow_store import WorkflowError
from src.shared.job_session import _json_bytes
from src.shared.media_tools import binary,run,duration


async def _download(text,voice,rate,path,boundary='WordBoundary'):
    try:import edge_tts
    except ImportError:raise WorkflowError('TTS_CONFIGURATION','Cài edge-tts trong môi trường Python chạy app.') from None
    await edge_tts.Communicate(text,voice=voice,rate=rate,boundary=boundary,connect_timeout=15,receive_timeout=45).save(str(path))


def _failure(exc):
    status=getattr(exc,'status',None)
    if isinstance(exc,WorkflowError):return exc.code,str(exc),False
    if status==429:return 'TTS_EDGE_RATE_LIMIT','Microsoft đang giới hạn tần suất tạo giọng. Các đoạn đã hoàn tất được giữ lại.',True
    if status in (400,401,403,404):return 'TTS_EDGE_CONFIGURATION','Microsoft từ chối yêu cầu tạo giọng (HTTP '+str(status)+'). Kiểm tra giọng và phiên bản edge-tts; không cần API key.',False
    if type(exc).__name__=='NoAudioReceived':return 'TTS_EDGE_NO_AUDIO','Đã kết nối Microsoft nhưng dịch vụ chưa trả âm thanh cho đoạn này. Đoạn đã tạo được giữ lại; bấm Thêm tiếng để tiếp tục.',True
    if isinstance(exc,(TimeoutError,ConnectionError,OSError)) or type(exc).__name__ in ('ClientConnectorError','ClientConnectionError','ServerDisconnectedError','ClientConnectorDNSError','ClientConnectorCertificateError'):
        return 'TTS_EDGE_NETWORK','Kết nối tới dịch vụ giọng Microsoft bị gián đoạn hoặc quá thời gian. Kiểm tra kết nối tới dịch vụ rồi thử lại.',True
    if status and status>=500:return 'TTS_EDGE_SERVICE','Dịch vụ giọng Microsoft tạm lỗi (HTTP '+str(status)+'). Thử lại sau.',True
    return 'TTS_EDGE_FAILED','Chưa tạo được giọng Microsoft ('+type(exc).__name__+'). Các đoạn đã hoàn tất được giữ lại.',False


async def _with_retries(text,voice,rate,path,call):
    boundary='WordBoundary'
    for attempt in range(3):
        path.unlink(missing_ok=True)
        call.update(attempts=attempt+1,boundary=boundary)
        try:
            await _download(text,voice,rate,path,boundary=boundary)
            return
        except Exception as exc:
            if isinstance(exc,WorkflowError):raise
            code,message,retry=_failure(exc)
            call.update(error=type(exc).__name__,error_code=code)
            call.setdefault('attempt_diagnostics',[]).append(dict(attempt=attempt+1,boundary=boundary,error=type(exc).__name__,code=code))
            if not retry or attempt==2:raise WorkflowError(code,message) from None
            # Nam Minh can return no audio for one metadata mode but work in
            # the other with the same text/voice. Never change the user's voice.
            if type(exc).__name__=='NoAudioReceived':
                boundary='SentenceBoundary' if boundary=='WordBoundary' else 'WordBoundary'
            delay=(2,5)[attempt]
            if getattr(exc,'status',None)==429:
                try:delay=max(delay,float((getattr(exc,'headers',None) or {}).get('Retry-After',delay)))
                except (ValueError,TypeError):pass
                if delay>10:raise WorkflowError(code,message) from None
            await asyncio.sleep(delay)


def synthesize(root,text,config,retry_uncertain=None):
    from src.preview.audio import init_ledger
    root=Path(root)
    if not isinstance(text,str) or not 3<=len(text)<=5000:raise WorkflowError('INVALID_INPUT','Lời đọc cần 3–5000 ký tự.')
    key=hashlib.sha256(_json_bytes(dict(provider='edge',voice=config['voice'],speed=config['speed'],text=text,format='wav'))).hexdigest()
    ledger=init_ledger(root);directory=root/'generation/checkpoint_7';path=directory/(key+'.wav');meta=directory/(key+'.json');ledger_path=directory/'ledger.json'
    if meta.exists():
        cached=store.read_json(meta)
        if cached.get('status')=='complete':
            if not path.is_file() or store.digest(path)!=cached['sha256']:raise WorkflowError('CACHE_CORRUPT','Giọng Microsoft đã lưu bị thay đổi; chưa gửi lại yêu cầu.')
            return cached
    previous=next((c for c in reversed(ledger['calls']) if c['key']==key),None)
    if previous and previous['status'] in ('in_flight','uncertain') and retry_uncertain!=previous['id']:
        raise WorkflowError('TTS_RESULT_UNKNOWN','Lượt giọng Microsoft chưa rõ kết quả. Chọn tiếp tục lượt đó trước khi gửi lại.')
    call=dict(id=uuid.uuid4().hex,key=key,provider='edge',status='in_flight',characters=len(text),text=text,voice=config['voice'],speed=config['speed'],reserved_usd=None,cost_status='no_api_key',price=None,exchange_rate=None)
    ledger['calls'].append(call);store.atomic_write(ledger_path,ledger)
    mp3=directory/(key+'.download.mp3');temporary=directory/(key+'.partial.wav')
    try:
        asyncio.run(asyncio.wait_for(_with_retries(text,config['voice'],f"{config['speed']*10:+d}%",mp3,call),timeout=75))
        run([binary('ffmpeg'),'-y','-v','error','-i',str(mp3),'-ar','48000','-ac','1',str(temporary)])
        seconds=duration(temporary)
        if not math.isfinite(seconds) or seconds<=0:raise WorkflowError('TTS_AUDIO','Microsoft chưa trả âm thanh hợp lệ.')
        temporary.replace(path)
        result=dict(status='complete',provider='edge',voice=config['voice'],boundary=call['boundary'],path=str(path.relative_to(root)),sha256=store.digest(path),duration=seconds,text=text,key=key)
        store.atomic_write(meta,result);call['status']='complete';call.pop('error',None);call.pop('error_code',None);store.atomic_write(ledger_path,ledger)
        return result
    except Exception as exc:
        code,message,_=_failure(exc)
        call.update(status='failed',error=call.get('error',type(exc).__name__),error_code=code);store.atomic_write(ledger_path,ledger)
        if isinstance(exc,WorkflowError):raise
        raise WorkflowError(code,message) from None
    finally:
        mp3.unlink(missing_ok=True);temporary.unlink(missing_ok=True)
