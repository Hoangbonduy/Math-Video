"""Cached Microsoft Edge / FPT speech; provider selection never silently falls back."""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from src.shared import _workflow_store as store
from src.shared._workflow_store import WorkflowError
from src.shared.job_session import _json_bytes
from src.shared.media_tools import binary,run,duration

DEFAULTS=dict(provider='edge',voice='vi-VN-NamMinhNeural',speed=0,budget_usd=.50,vnd_per_million_chars=None,vnd_per_usd=None,allow_unpriced=False)
URL='https://api.fpt.ai/hmi/tts/v5'


def settings(root):
    root=Path(root);project=root/'project.json'
    if project.exists():
        config=store.read_json(project).get('audio_config')
        if config is not None: return dict(DEFAULTS,**config)
    path=root/'audio_config.json'
    return dict(DEFAULTS,**(store.read_json(path) if path.exists() else {}))


def validate_settings(config):
    voices={'fpt':('leminh','giahuy'),'edge':('vi-VN-HoaiMyNeural','vi-VN-NamMinhNeural')}
    if set(config)!=set(DEFAULTS) or config['provider'] not in voices or config['voice'] not in voices[config['provider']] or type(config['speed']) is not int or not -3<=config['speed']<=3:
        raise WorkflowError('INVALID_INPUT','Nguồn giọng đọc, giọng hoặc tốc độ không hợp lệ')
    for key in ('vnd_per_million_chars','vnd_per_usd'):
        value=config[key]
        if value is not None and (type(value) not in (int,float) or not math.isfinite(value) or value<=0): raise WorkflowError('INVALID_INPUT','Price and exchange rate must be positive')
    if type(config['allow_unpriced']) is not bool:raise WorkflowError('INVALID_INPUT','allow_unpriced phải là boolean')
    if config['budget_usd']!=.5: raise WorkflowError('INVALID_INPUT','C7 budget is fixed at 0.50 USD')


def chunks(text):
    """Lossless display spans, protecting unit/formula tokens; short TTS phrases."""
    result=[]; start=0
    # Sentence/clause boundaries, then whitespace when a clause is long.
    for match in re.finditer(r'.+?(?:[.!?;,:](?=\s|$)|$)',text,re.S):
        value=match.group().strip()
        if not value: continue
        while len(value)>84:
            stops=[m.start() for m in re.finditer(r'\s+',value[:85]) if m.start()>30 and not re.search(r'\d$',value[:m.start()])]
            if not stops: break
            cut=stops[-1];result.append(value[:cut].strip());value=value[cut:].strip()
        if value: result.append(value)
    if not result and text.strip(): result=[text.strip()]
    merged=[]
    for value in result:
        if len(value)<3 and merged: merged[-1]+=' '+value
        else: merged.append(value)
    return merged


def init_ledger(root):
    directory=Path(root)/'generation/checkpoint_7';path=directory/'ledger.json'
    if path.exists(): return store.read_json(path)
    if directory.exists() and any(directory.iterdir()):
        raise WorkflowError('STATE_MISSING','C7 ledger missing; refusing to reset its budget')
    directory.mkdir(parents=True,exist_ok=True)
    ledger=dict(calls=[],jobs=[]);store.atomic_write(path,ledger);return ledger


def summary(root):
    path=Path(root)/'generation/checkpoint_7/ledger.json'
    ledger=store.read_json(path) if path.exists() else dict(calls=[])
    from src.preview.av_workflow import spent
    jobs=[store.read_json(p) for p in (Path(root)/'generation/checkpoint_7/jobs').glob('*/job.json')]
    return dict(budget_usd=.5,reserved_usd=spent(root),calls=ledger['calls'],jobs=[dict(job_id=j['job_id'],scene_id=j['input']['inputs']['clip']['scene_id'],status=j['status'],last_error=j.get('last_error'),uncertain_requests=[c['call_id'] for c in j['calls'] if c['status'] in ('in_flight','uncertain')]) for j in jobs],config=settings(root),cost_is_estimate=True,cost_is_unknown=any(c.get('provider','fpt')=='fpt' and c.get('reserved_usd') is None and c['status']!='rejected' for c in ledger['calls']),unpriced_characters=sum(c['characters'] for c in ledger['calls'] if c.get('provider','fpt')=='fpt' and c.get('reserved_usd') is None and c['status']!='rejected'))


def _download(url):
    parsed=urllib.parse.urlsplit(url)
    if parsed.scheme!='https' or not parsed.hostname or not (parsed.hostname=='api.fpt.ai' or parsed.hostname.endswith('.amazonaws.com') or parsed.hostname.endswith('.fpt.ai')):
        raise WorkflowError('TTS_URL','Unexpected FPT audio host')
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args): return None
    with urllib.request.build_opener(NoRedirect).open(url,timeout=20) as response:
        data=response.read(20_000_001)
    if len(data)>20_000_000: raise WorkflowError('TTS_AUDIO','Audio exceeds size limit')
    return data


def synthesize(root,text,config=None,retry_uncertain=None,*,_rate_retries=0):
    """Caller holds the project generation lock, never the state lock."""
    root=Path(root);config=config or settings(root);validate_settings(config)
    if config['provider']=='edge':
        from src.preview.edge_speech import synthesize as edge_synthesize
        return edge_synthesize(root,text,config,retry_uncertain)
    key=hashlib.sha256(_json_bytes(dict(text=text,voice=config['voice'],speed=config['speed'],provider='fpt',format='wav'))).hexdigest()
    ledger=init_ledger(root)
    directory=root/'generation/checkpoint_7'
    output=directory/(key+'.wav');meta=directory/(key+'.json');ledger_path=directory/'ledger.json'
    cached=store.read_json(meta) if meta.exists() else None
    if cached and cached.get('status')=='complete':
        if not output.exists() or store.digest(output)!=cached['sha256']: raise WorkflowError('CACHE_CORRUPT','Cached speech changed; not sending again')
        return cached
    call=next((c for c in reversed(ledger['calls']) if c['key']==key),None)
    if call and call['status'] in ('in_flight','uncertain'):
        if retry_uncertain!=call['id']: raise WorkflowError('API_RESULT_UNKNOWN','FPT request may be billed; explicit retry required: '+call['id'])
        call=None
    if not call or call['status']=='rejected':
        if not os.environ.get('FPT_API_KEY'): raise WorkflowError('TTS_CONFIGURATION','Set FPT_API_KEY in .env')
        priced=bool(config['vnd_per_million_chars'] and config['vnd_per_usd'])
        if not priced and not config['allow_unpriced']: raise WorkflowError('TTS_PRICING_REQUIRED','Set the FPT package unit price and VND/USD rate or explicitly allow unpriced synthesis')
        if not 3<=len(text)<=5000: raise WorkflowError('INVALID_INPUT','FPT input must contain 3–5000 characters')
        reserve=len(text)*config['vnd_per_million_chars']/1_000_000/config['vnd_per_usd']*1.1 if priced else None
        from src.preview.av_workflow import spent
        if reserve is not None and spent(root)+reserve>.5: raise WorkflowError('BUDGET_EXCEEDED','C7 cannot reserve the next FPT request')
        recent=max((c.get('started_epoch',0) for c in ledger['calls']),default=0)
        not_before=max((c.get('not_before_epoch',0) for c in ledger['calls']),default=0)
        wait=max(0,recent+5-time.time(),not_before-time.time())
        if wait>60:raise WorkflowError('TTS_RATE_LIMIT','FPT đang giới hạn tần suất. Chờ rồi bấm Thêm tiếng để tiếp tục các đoạn còn thiếu.')
        if wait:time.sleep(wait)
        import uuid
        call=dict(id=uuid.uuid4().hex,key=key,provider='fpt',status='in_flight',started_epoch=time.time(),characters=len(text),text=text,reserved_usd=reserve,cost_status='estimated' if priced else 'unknown',unpriced_authorized=config['allow_unpriced'],voice=config['voice'],speed=config['speed'],price=config['vnd_per_million_chars'],exchange_rate=config['vnd_per_usd'])
        ledger['calls'].append(call);store.atomic_write(ledger_path,ledger)
        req=urllib.request.Request(URL,data=text.encode('utf-8'),headers={'api-key':os.environ['FPT_API_KEY'],'api_key':os.environ['FPT_API_KEY'],'voice':config['voice'],'speed':str(config['speed']),'format':'wav','Content-Type':'text/plain; charset=utf-8'})
        try:
            with urllib.request.urlopen(req,timeout=60) as response: data=json.load(response)
            if data.get('error')!=0:
                call['status']='rejected';store.atomic_write(ledger_path,ledger)
                raise WorkflowError('TTS_REJECTED','FPT rejected synthesis, code '+str(data.get('error')))
            call.update(status='accepted',url=data['async'],request_id=data.get('request_id'));store.atomic_write(ledger_path,ledger)
        except urllib.error.HTTPError as exc:
            call['status']='rejected' if exc.code in (400,401,403,422,429) else 'uncertain'
            call['http_status']=exc.code
            try:
                from src.shared.openrouter_client import redact
                call['server_message']=redact(exc.read(2000).decode('utf-8',errors='replace'))
            except (OSError,AttributeError,TypeError):pass
            if exc.code==429:
                delay=20*(2**_rate_retries)
                header=exc.headers.get('Retry-After','') if exc.headers else ''
                try:delay=max(delay,float(header))
                except ValueError:
                    try:
                        from email.utils import parsedate_to_datetime
                        delay=max(delay,parsedate_to_datetime(header).timestamp()-time.time())
                    except (ValueError,TypeError,OverflowError):pass
                call.update(retry_after_seconds=delay,not_before_epoch=time.time()+delay)
                store.atomic_write(ledger_path,ledger)
                if _rate_retries<2 and delay<=60:
                    time.sleep(delay)
                    return synthesize(root,text,config,_rate_retries=_rate_retries+1)
                raise WorkflowError('TTS_RATE_LIMIT','FPT vẫn giới hạn tần suất sau khi chờ. Đoạn đã tạo được giữ lại; bấm Thêm tiếng để tiếp tục sau.') from None
            store.atomic_write(ledger_path,ledger)
            raise WorkflowError('TTS_HTTP',f'FPT HTTP {exc.code}') from None
        except (OSError,ValueError,KeyError):
            call['status']='uncertain';store.atomic_write(ledger_path,ledger)
            raise WorkflowError('API_RESULT_UNKNOWN','FPT result unknown: '+call['id']) from None
    temporary=output.with_suffix('.download')
    for attempt in range(18):
        try:
            temporary.write_bytes(_download(call['url']))
            run([binary('ffmpeg'),'-y','-v','error','-i',str(temporary),'-ar','48000','-ac','1',str(output)])
            seconds=duration(output)
            if seconds<=0: raise WorkflowError('TTS_AUDIO','Empty speech')
            info=dict(status='complete',path=str(output.relative_to(root)),sha256=store.digest(output),duration=seconds,text=text,key=key)
            store.atomic_write(meta,info);call['status']='complete';store.atomic_write(ledger_path,ledger);return info
        except urllib.error.HTTPError as exc:
            if exc.code not in (403,404): raise WorkflowError('TTS_DOWNLOAD',f'Audio download HTTP {exc.code}') from None
        except urllib.error.URLError: pass
        finally: temporary.unlink(missing_ok=True)
        if attempt<17: time.sleep(min(1+attempt,8))
    raise WorkflowError('TTS_PENDING','Speech still pending; resume downloads without resending synthesis')
