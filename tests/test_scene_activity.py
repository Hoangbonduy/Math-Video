import json
import os
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch
import pytest
from src.scene_design.scene_activity import snapshot,public_event
from src.shared import _workflow_store as store
from src.shared.codex_client import CodexClient
from src.shared.review_server import ReviewServer


def make_job(root):
    directory=root/'generation/checkpoint_5/scene_job'
    directory.mkdir(parents=True)
    store.atomic_write(root/'generation/checkpoint_5/ledger.json',dict(jobs=['generation/checkpoint_5/scene_job/job.json']))
    job=dict(job_id='scene_job',input=dict(scene_id='s1'),status='working',progress='writing_code',
        calls=[dict(call_id='call_1',phase='v3_code_0',status='in_flight')])
    store.atomic_write(directory/'job.json',job)
    return directory,job


def test_activity_filters_private_reasoning_and_scopes_to_job(tmp_path):
    directory,job=make_job(tmp_path)
    trace=directory/'codex_calls/call_1';trace.mkdir(parents=True)
    events=[dict(type='item.completed',item=dict(type='reasoning',text='PRIVATE_REASONING')),
        dict(type='item.started',item=dict(id='tool1',type='web_search',query='public query')),
        dict(type='item.completed',item=dict(type='agent_message',text=json.dumps(dict(source='class GeneratedScene(Scene): pass'))))]
    (trace/'events.jsonl').write_text('\n'.join(json.dumps(e) for e in events)+'\n{partial')
    (trace/'prompt.txt').write_text('PRIVATE_PROMPT')
    result=snapshot(tmp_path,'s1','scene_job')
    text=json.dumps(result)
    assert 'PRIVATE_REASONING' not in text and 'PRIVATE_PROMPT' not in text
    assert result['events'][0]['kind']=='tool'
    assert result['source'].startswith('class GeneratedScene')
    assert result['streaming']
    with pytest.raises(store.WorkflowError):snapshot(tmp_path,'s2','scene_job')
    with pytest.raises(store.WorkflowError):snapshot(tmp_path,'s1','../outside')


def test_activity_endpoint_requires_session(tmp_path):
    from urllib.request import Request,urlopen
    from urllib.error import HTTPError
    from src.shared import workflow as wf
    wf.create_project(tmp_path,'Activity test')
    directory,job=make_job(tmp_path)
    server=ReviewServer(tmp_path,0);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        url=server.origin+'/api/scene-activity?scene_id=s1&job_id=scene_job'
        with pytest.raises(HTTPError) as exc:urlopen(url,timeout=5)
        assert exc.value.code==403
        with urlopen(Request(url,headers={'X-Review-Token':server.token}),timeout=5) as response:
            result=json.load(response)
        assert result['job_id']=='scene_job'
    finally:
        server.shutdown();server.server_close();thread.join()


def test_cli_events_are_visible_before_call_completes(tmp_path):
    directory,job=make_job(tmp_path)
    fake=tmp_path/'fake_codex'
    fake.write_text('#!'+sys.executable+'\n'+r"""
import json,sys,time
from pathlib import Path
args=sys.argv
final=Path(args[args.index('--output-last-message')+1])
trace=final.parent
sys.stdin.read()
print(json.dumps(dict(type='turn.started')),flush=True)
print(json.dumps(dict(type='item.completed',item=dict(type='reasoning',text='PRIVATE_REASONING'))),flush=True)
print(json.dumps(dict(type='item.started',item=dict(type='web_search',query='a public query'))),flush=True)
(trace/'ready').write_text('ready')
for i in range(500):
    if (trace/'release').exists():break
    time.sleep(.01)
final.write_text(json.dumps(dict(source='from manim import *')))
print(json.dumps(dict(type='turn.completed',usage=dict(input_tokens=1,output_tokens=1,cached_input_tokens=0))),flush=True)
""")
    fake.chmod(0o700)
    errors=[];results=[]
    def call():
        try:results.append(CodexClient().complete(dict(messages=[],schema=dict(type='object'),_execution=dict(directory=str(directory),call_id='call_1'))))
        except Exception as exc:errors.append(exc)
    trace=directory/'codex_calls/call_1'
    with patch('src.shared.codex_client.executable',return_value=str(fake)),patch.dict(os.environ,dict(CODEX_TIMEOUT_SECONDS='10')):
        worker=threading.Thread(target=call);worker.start()
        try:
            deadline=time.monotonic()+5
            while not (trace/'ready').exists() and time.monotonic()<deadline:time.sleep(.01)
            assert (trace/'ready').exists(),errors
            result=snapshot(tmp_path,'s1','scene_job')
            assert worker.is_alive()
            assert any(e['kind']=='tool' for e in result['events'])
            assert 'PRIVATE_REASONING' not in json.dumps(result)
        finally:
            trace.mkdir(parents=True,exist_ok=True);(trace/'release').write_text('release');worker.join(timeout=10)
    assert not worker.is_alive() and not errors
    assert results


def test_query_routes_work_before_and_after_activity_request(tmp_path):
    from urllib.request import Request,urlopen
    from urllib.error import HTTPError
    from src.shared import workflow as wf
    wf.create_project(tmp_path,'Query route regression')
    make_job(tmp_path)
    server=ReviewServer(tmp_path,0)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        def get(path,**headers):
            return urlopen(Request(server.origin+path,headers=headers),timeout=5)
        for attempt in range(2):
            # These routes must parse queries regardless of which branch ran.
            with get('/api/script-history') as response:
                assert response.status==200
                assert isinstance(json.load(response),dict)
            with pytest.raises(HTTPError) as exc:
                get('/api/script-history?version=missing')
            assert exc.value.code==404
            with pytest.raises(HTTPError) as exc:
                get('/media?path=unregistered.mp4')
            assert exc.value.code==403
            with pytest.raises(HTTPError) as exc:
                get('/music-media?id=missing')
            assert exc.value.code==403
            with get('/api/scene-activity?scene_id=s1&job_id=scene_job',
                     **{'X-Review-Token':server.token}) as response:
                assert json.load(response)['job_id']=='scene_job'
    finally:
        server.shutdown();server.server_close();thread.join()
