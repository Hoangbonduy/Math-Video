"""Microsoft Edge online Vietnamese speech, no user API key or FPT fallback."""
import asyncio,hashlib,math,time,uuid
import array,json,re,sys,wave
from pathlib import Path
from src.shared import _workflow_store as store
from src.shared._workflow_store import WorkflowError
from src.shared.job_session import _json_bytes
from src.shared.media_tools import binary,run,duration


TRIM_VERSION = 1
MAX_TRIM_SECONDS = .35
KEEP_LEAD_SECONDS = .04
KEEP_TAIL_SECONDS = .08



def _voice_log(event, **fields):
    """Always flush voice diagnostics to the server terminal, including workers."""
    record = dict(time=time.strftime('%Y-%m-%d %H:%M:%S'), event=event, **fields)
    print('[voice.edge] ' + json.dumps(record, ensure_ascii=False, default=str), file=sys.stderr, flush=True)


def _error_details(exc):
    # HTTP exceptions can contain signed service URLs; keep the useful reason.
    detail = re.sub(r"(?:https?|wss?)://[^\s'\"]+", '<url>', str(exc))[:1000]
    result = dict(error=type(exc).__name__, detail=detail)
    if getattr(exc, 'status', None) is not None:
        result['http_status'] = exc.status
    cause = exc.__cause__ or exc.__context__
    if cause is not None:
        result['cause'] = type(cause).__name__
    return result


def _trim_cached(root, source):
    """Derive speech from immutable raw WAV; remove at most .35 s at its edges."""
    identity = dict(source=source['sha256'], source_key=source.get('key', source['path']), version=TRIM_VERSION,
                    maximum=MAX_TRIM_SECONDS, lead=KEEP_LEAD_SECONDS,
                    tail=KEEP_TAIL_SECONDS, threshold_db=-45, minimum_silence=.05)
    key = hashlib.sha256(_json_bytes(identity)).hexdigest()
    directory = root/'generation/checkpoint_7/edge_trimmed'
    directory.mkdir(parents=True, exist_ok=True)
    output, meta = directory/(key+'.wav'), directory/(key+'.json')
    if meta.exists():
        cached = store.read_json(meta)
        if not output.is_file() or store.digest(output) != cached['sha256']:
            raise WorkflowError('CACHE_CORRUPT', 'Processed Edge speech changed.')
        return cached
    with wave.open(str(store.project_path(root, source['path'])), 'rb') as wav:
        params = wav.getparams()
        if params.nchannels != 1 or params.sampwidth != 2 or params.comptype != 'NONE':
            raise WorkflowError('TTS_AUDIO', 'Edge speech must be mono PCM16 WAV.')
        frames = wav.readframes(wav.getnframes())
    samples = array.array('h', frames)
    if sys.byteorder != 'little':
        samples.byteswap()
    threshold = 32768 * 10**(-45/20)
    first = next((i for i, value in enumerate(samples) if abs(value) > threshold), None)
    left = right = 0
    if first is not None:
        last = next(i for i in range(len(samples)-1, -1, -1) if abs(samples[i]) > threshold)
        lead, tail = first, len(samples)-1-last
        minimum = math.ceil(.05*params.framerate)
        available_lead = max(0, lead-round(KEEP_LEAD_SECONDS*params.framerate)) if lead >= minimum else 0
        available_tail = max(0, tail-round(KEEP_TAIL_SECONDS*params.framerate)) if tail >= minimum else 0
        budget = math.floor(MAX_TRIM_SECONDS*params.framerate)
        # Prefer tail silence so the consonant onset keeps its original breathing room.
        right = min(budget, available_tail)
        left = min(budget-right, available_lead)
    temporary = output.with_suffix('.partial.wav')
    try:
        with wave.open(str(temporary), 'wb') as wav:
            wav.setparams(params)
            wav.writeframes(frames[left*2:(len(samples)-right)*2])
        temporary.replace(output)
        result = dict(source, path=str(output.relative_to(root)), sha256=store.digest(output),
                      duration=(len(samples)-left-right)/params.framerate,
                      trim_version=TRIM_VERSION, trim_source_sha256=source['sha256'],
                      untrimmed_path=source['path'], trimmed_seconds=(left+right)/params.framerate)
        store.atomic_write(meta, result)
        return result
    finally:
        temporary.unlink(missing_ok=True)


async def _download(text,voice,rate,path,boundary='WordBoundary'):
    try:import edge_tts
    except ImportError:raise WorkflowError('TTS_CONFIGURATION','Cài edge-tts trong môi trường Python chạy app.') from None
    # One synthesis attempt only. _with_retries owns backoff, metadata mode,
    # and attempt accounting so the ledger matches the Communicate calls.
    started = time.monotonic()
    key = path.name.split('.')[0][:12]
    _voice_log('download_start', key=key, library_version=getattr(edge_tts, '__version__', 'unknown'),
               voice=voice, rate=rate, boundary=boundary, characters=len(text),
               connect_timeout=15, receive_timeout=45)
    try:
        await edge_tts.Communicate(text,voice=voice,rate=rate,boundary=boundary,connect_timeout=15,receive_timeout=45).save(str(path))
    except BaseException as exc:
        _voice_log('download_error', key=key, boundary=boundary,
                   elapsed_seconds=round(time.monotonic()-started, 3),
                   audio_bytes=path.stat().st_size if path.exists() else 0, **_error_details(exc))
        raise
    _voice_log('download_complete', key=key, boundary=boundary,
               elapsed_seconds=round(time.monotonic()-started, 3),
               audio_bytes=path.stat().st_size if path.exists() else 0)


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
        _voice_log('attempt_start', call_id=call['id'], key=call['key'][:12], attempt=attempt+1, maximum_attempts=3, boundary=boundary)
        try:
            await _download(text,voice,rate,path,boundary=boundary)
            _voice_log('attempt_complete', call_id=call['id'], attempt=attempt+1, boundary=boundary)
            return
        except Exception as exc:
            if isinstance(exc,WorkflowError):raise
            code,message,retry=_failure(exc)
            call.update(error=type(exc).__name__,error_code=code)
            diag=dict(attempt=attempt+1,boundary=boundary,error=type(exc).__name__,code=code)
            if hasattr(exc,'status'):diag['status']=exc.status
            call.setdefault('attempt_diagnostics',[]).append(diag)
            _voice_log('attempt_error', call_id=call['id'], attempt=attempt+1, boundary=boundary, code=code, retryable=retry, **_error_details(exc))
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
            _voice_log('retry_wait', call_id=call['id'], next_attempt=attempt+2, next_boundary=boundary, delay_seconds=delay)
            await asyncio.sleep(delay)


def synthesize(root,text,config,retry_uncertain=None):
    from src.preview.audio import init_ledger
    root=Path(root)
    if not isinstance(text,str):raise WorkflowError('INVALID_INPUT','Lời đọc cần 3–5000 ký tự.')
    text=' '.join(text.split())
    if not 3<=len(text)<=5000:raise WorkflowError('INVALID_INPUT','Lời đọc cần 3–5000 ký tự.')
    key=hashlib.sha256(_json_bytes(dict(provider='edge',voice=config['voice'],speed=config['speed'],text=text,format='wav'))).hexdigest()
    _voice_log('synthesize_start', key=key[:12], project=str(root), voice=config['voice'], speed=config['speed'], characters=len(text), text=text[:200])
    ledger=init_ledger(root);directory=root/'generation/checkpoint_7';path=directory/(key+'.wav');meta=directory/(key+'.json');ledger_path=directory/'ledger.json'
    if meta.exists():
        cached=store.read_json(meta)
        if cached.get('status')=='complete':
            if not path.is_file() or store.digest(path)!=cached['sha256']:raise WorkflowError('CACHE_CORRUPT','Giọng Microsoft đã lưu bị thay đổi; chưa gửi lại yêu cầu.')
            _voice_log('cache_hit', key=key[:12], duration_seconds=cached['duration'], boundary=cached.get('boundary'))
            return _trim_cached(root,cached)
    previous=next((c for c in reversed(ledger['calls']) if c['key']==key),None)
    if previous and previous['status'] in ('in_flight','uncertain') and retry_uncertain!=previous['id']:
        raise WorkflowError('TTS_RESULT_UNKNOWN','Lượt giọng Microsoft chưa rõ kết quả. Chọn tiếp tục lượt đó trước khi gửi lại.')
    recent=max((c.get('completed_epoch') or c.get('started_epoch',0) for c in ledger.get('calls',[]) if c.get('provider')=='edge'),default=0)
    wait=max(0.0,recent+1.5-time.time())
    if wait>0:
        _voice_log('throttle_wait', key=key[:12], delay_seconds=round(min(wait,3.0),3))
        time.sleep(min(wait,3.0))
    call=dict(id=uuid.uuid4().hex,key=key,provider='edge',status='in_flight',started_epoch=time.time(),characters=len(text),text=text,voice=config['voice'],speed=config['speed'],reserved_usd=None,cost_status='no_api_key',price=None,exchange_rate=None)
    ledger['calls'].append(call);store.atomic_write(ledger_path,ledger)
    mp3=directory/(key+'.download.mp3');temporary=directory/(key+'.partial.wav')
    try:
        asyncio.run(asyncio.wait_for(_with_retries(text,config['voice'],f"{config['speed']*10:+d}%",mp3,call),timeout=75))
        _voice_log('convert_start', call_id=call['id'], audio_bytes=mp3.stat().st_size)
        run([binary('ffmpeg'),'-y','-v','error','-i',str(mp3),'-ar','48000','-ac','1',str(temporary)])
        seconds=duration(temporary)
        if not math.isfinite(seconds) or seconds<=0:raise WorkflowError('TTS_AUDIO','Microsoft chưa trả âm thanh hợp lệ.')
        temporary.replace(path)
        result=dict(status='complete',provider='edge',voice=config['voice'],boundary=call['boundary'],path=str(path.relative_to(root)),sha256=store.digest(path),duration=seconds,text=text,key=key)
        store.atomic_write(meta,result);call['status']='complete';call['completed_epoch']=time.time();call.pop('error',None);call.pop('error_code',None);store.atomic_write(ledger_path,ledger)
        trimmed = _trim_cached(root,result)
        _voice_log('synthesize_complete', call_id=call['id'], key=key[:12], attempts=call['attempts'], boundary=call['boundary'], duration_seconds=round(trimmed['duration'],3))
        return trimmed
    except Exception as exc:
        code,message,_=_failure(exc)
        _voice_log('synthesize_error', call_id=call['id'], key=key[:12], attempts=call.get('attempts',0), code=code, **_error_details(exc))
        call.update(status='failed',completed_epoch=time.time(),error=call.get('error',type(exc).__name__),error_code=code);store.atomic_write(ledger_path,ledger)
        if isinstance(exc,WorkflowError):raise
        raise WorkflowError(code,message) from None
    finally:
        mp3.unlink(missing_ok=True);temporary.unlink(missing_ok=True)
