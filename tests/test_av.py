"""C7: real local media, fake paid providers, exact dependencies and recovery."""
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import wave
from src.preview import audio
from src.preview import av_workflow as av
from src.shared import workflow as wf
from src.shared import _workflow_store as store
from src.shared.review import record_review
from src.shared.media_tools import binary,run,probe,duration
from tests import test_scenes as scene_fixtures
SceneRouter=scene_fixtures.SceneRouter
from src.shared.ai_config import MODEL


def wave_bytes(seconds):
    stream=io.BytesIO()
    with wave.open(stream,'wb') as w:
        w.setnchannels(1);w.setsampwidth(2);w.setframerate(48000);w.writeframes(b'\0\0'*round(seconds*48000))
    return stream.getvalue()

class AudioTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.config=dict(audio.DEFAULTS,provider='fpt',voice='leminh',vnd_per_million_chars=333334,vnd_per_usd=25000)
        patch.dict(os.environ,{'FPT_API_KEY':'private-test-fpt'}).start();self.addCleanup(patch.stopall)
    def response(self):
        return io.BytesIO(json.dumps(dict(error=0,request_id='request-1',async_='unused')).replace('async_', 'async').replace('unused','https://s3-ap-southeast-1.amazonaws.com/text2speech-v4/file.wav').encode())
    def test_async_resume_cache_and_no_duplicate_post(self):
        with patch('src.preview.audio.urllib.request.urlopen',return_value=self.response()) as post,patch('src.preview.audio._download',return_value=wave_bytes(.8)),patch('src.preview.audio.time.sleep'):
            a=audio.synthesize(self.root,'Xin chào các bạn.',self.config)
            b=audio.synthesize(self.root,'Xin chào các bạn.',self.config)
        self.assertEqual(a,b);self.assertEqual(post.call_count,1);self.assertAlmostEqual(a['duration'],.8,places=2)
        self.assertNotIn('private-test-fpt',json.dumps(audio.summary(self.root)))
        (self.root/a['path']).write_bytes(b'changed')
        with self.assertRaisesRegex(store.WorkflowError,'CACHE_CORRUPT'): audio.synthesize(self.root,'Xin chào các bạn.',self.config)
    def test_uncertain_is_not_resent_and_missing_price_blocks(self):
        with patch('src.preview.audio.urllib.request.urlopen',side_effect=OSError('timeout')) as post:
            with self.assertRaisesRegex(store.WorkflowError,'API_RESULT_UNKNOWN'): audio.synthesize(self.root,'Xin chào các bạn.',self.config)
            with self.assertRaisesRegex(store.WorkflowError,'API_RESULT_UNKNOWN'): audio.synthesize(self.root,'Xin chào các bạn.',self.config)
            self.assertEqual(post.call_count,1)
        with self.assertRaisesRegex(store.WorkflowError,'TTS_PRICING_REQUIRED'): audio.synthesize(self.root,'Một câu khác.',dict(audio.DEFAULTS,provider='fpt',voice='leminh'))
    def test_budget_blocks_before_post(self):
        config=dict(self.config,vnd_per_million_chars=1e10)
        with patch('src.preview.audio.urllib.request.urlopen') as post:
            with self.assertRaisesRegex(store.WorkflowError,'BUDGET_EXCEEDED'): audio.synthesize(self.root,'Xin chào các bạn.',config)
            post.assert_not_called()
    def test_pending_download_recovers_accepted_request(self):
        with patch('src.preview.audio.urllib.request.urlopen',return_value=self.response()) as post,patch('src.preview.audio._download',side_effect=__import__('urllib').error.URLError('wait')),patch('src.preview.audio.time.sleep'):
            with self.assertRaisesRegex(store.WorkflowError,'TTS_PENDING'): audio.synthesize(self.root,'Xin chào các bạn.',self.config)
        with patch('src.preview.audio.urllib.request.urlopen') as post,patch('src.preview.audio._download',return_value=wave_bytes(.5)):
            audio.synthesize(self.root,'Xin chào các bạn.',self.config);post.assert_not_called()
    def test_missing_ledger_cannot_reset_budget(self):
        with patch('src.preview.audio.urllib.request.urlopen',return_value=self.response()),patch('src.preview.audio._download',return_value=wave_bytes(.5)):
            audio.synthesize(self.root,'Xin chào các bạn.',self.config)
        (self.root/'generation/checkpoint_7/ledger.json').unlink()
        with self.assertRaisesRegex(store.WorkflowError,'STATE_MISSING'): audio.synthesize(self.root,'Một câu khác.',self.config)
    def test_explicit_unpriced_mode_records_unknown_cost_and_caches_real_audio(self):
        config=dict(audio.DEFAULTS,provider='fpt',voice='leminh',allow_unpriced=True)
        with patch('src.preview.audio.urllib.request.urlopen',return_value=self.response()) as post,patch('src.preview.audio._download',return_value=wave_bytes(.5)):
            result=audio.synthesize(self.root,'Xin chào các bạn.',config)
            self.assertEqual(result,audio.synthesize(self.root,'Xin chào các bạn.',config));self.assertEqual(post.call_count,1)
        summary=audio.summary(self.root);self.assertTrue(summary['cost_is_unknown']);self.assertGreater(summary['unpriced_characters'],0)
        self.assertIsNone(summary['calls'][0]['reserved_usd']);self.assertEqual(summary['calls'][0]['cost_status'],'unknown')
        self.assertIsNone(summary['calls'][0]['price']);self.assertIsNone(summary['calls'][0]['exchange_rate'])

    def test_rate_limit_waits_then_resumes_without_duplicate_completed_audio(self):
        import urllib.error
        error=urllib.error.HTTPError(audio.URL,429,'rate limited',{'Retry-After':'1'},None)
        with patch('src.preview.audio.urllib.request.urlopen',side_effect=[error,self.response()]) as post,patch('src.preview.audio._download',return_value=wave_bytes(.5)),patch('src.preview.audio.time.sleep') as wait:
            result=audio.synthesize(self.root,'Xin chào các bạn.',self.config)
            audio.synthesize(self.root,'Xin chào các bạn.',self.config)
        self.assertEqual(post.call_count,2);self.assertTrue(wait.called)
        calls=audio.summary(self.root)['calls'];self.assertEqual([c['status'] for c in calls],['rejected','complete']);self.assertEqual(calls[0]['http_status'],429)

    def test_persistent_rate_limit_stops_after_three_posts(self):
        import urllib.error
        error=urllib.error.HTTPError(audio.URL,429,'rate limited',{},None)
        with patch('src.preview.audio.urllib.request.urlopen',side_effect=error) as post,patch('src.preview.audio.time.sleep'):
            with self.assertRaisesRegex(store.WorkflowError,'TTS_RATE_LIMIT'):audio.synthesize(self.root,'Xin chào các bạn.',self.config)
        self.assertEqual(post.call_count,3)
        self.assertTrue(all(c['status']=='rejected' for c in audio.summary(self.root)['calls']))

    def test_redaction(self):
        from src.shared.openrouter_client import redact
        self.assertEqual(redact('key private-test-fpt'),'key [REDACTED]');self.assertEqual(redact({'api-key':'secret'}),{})

class TimingRouter(SceneRouter):
    def complete(self,payload):
        props=payload['response_format']['json_schema']['schema']['properties']
        if 'kind' in props:
            ctx=json.loads(payload['messages'][1]['content']);return dict(id='feedback',model=MODEL,usage=dict(cost=.0001,prompt_tokens=100,completion_tokens=100),choices=[dict(message=dict(content=json.dumps(dict(kind='timing',spoken=ctx['phrases'],subtitle_font_size=44))))])
        if 'beats' not in props:return super().complete(payload)
        self.requests.append(copy.deepcopy(payload));ctx=json.loads(payload['messages'][1]['content'])
        result=dict(beats=[dict(index=i,segment=min(i,len(ctx['segments'])-1),start=.05,end=.8) for i,_ in enumerate(ctx['design']['timeline'])])
        return dict(id='timing',model=MODEL,usage=dict(cost=.0001,prompt_tokens=100,completion_tokens=100),choices=[dict(message=dict(content=json.dumps(result)))])

class AVTests(unittest.TestCase):
    def setUp(self):
        self.flow=scene_fixtures.SceneFlowTests('test_two_topics_different_scene_counts_and_all_gates');self.flow.setUp();self.addCleanup(self.flow.doCleanups)
        self.root=self.flow.root;self.flow.approved(2)
        state=wf.run_workflow(self.root,ai=True)
        for a in state['pending_reviews']:record_review(self.root,'scene_design',a['version'],'approve',scene_id=a['scene_id'])
        state=wf.run_workflow(self.root,ai=True)
        for a in state['pending_reviews']:record_review(self.root,'scene_clip',a['version'],'approve',scene_id=a['scene_id'])
        self.router=TimingRouter();patch('src.preview.av_workflow.CLIENT_FACTORY',return_value=self.router).start()
        self.synthesis=patch('src.preview.av_workflow.SYNTHESIZER',side_effect=self.synth).start()
        patch('src.preview.av_workflow.CLIP_RENDERER',side_effect=self.clip).start()
        av.configure(self.root,dict(vnd_per_million_chars=333334,vnd_per_usd=25000))
    def synth(self,root,text,config,retry=None):
        key=hashlib.sha256(text.encode()).hexdigest()[:16];path=root/'audio'/f'{key}.wav';path.parent.mkdir(exist_ok=True)
        seconds=len(text.split())/4.4
        if not path.exists():path.write_bytes(wave_bytes(seconds))
        return dict(path=str(path.relative_to(root)),sha256=store.digest(path),duration=len(text.split())/4.4,text=text)
    def clip(self,root,directory,session,context):
        path=directory/'visual.mp4';code=directory/'scene.py';code.write_text('# Fake paid code generation; real native 1080p encoding\n')
        if not path.exists():run([binary('ffmpeg'),'-y','-v','error','-f','lavfi','-i','color=c=0x193d4e:s=1920x1080:r=30','-t',str(context['target_seconds']),'-c:v','libx264','-preset','ultrafast','-pix_fmt','yuv420p',str(path)])
        return path,dict(code_path=str(code.relative_to(root)))
    def test_full_real_media_gates_reopen_and_publication(self):
        state=av.generate(self.root)
        self.assertEqual(state['stage'],'scene_av');self.assertEqual(len(state['pending_reviews']),2)
        before=self.synthesis.call_count;av.generate(self.root);self.assertEqual(self.synthesis.call_count,before)
        first=state['pending_reviews'][0]
        record_review(self.root,'scene_av',first['version'],'approve',scene_id=first['scene_id'],expected_sha256=first['sha256'])
        with self.assertRaisesRegex(store.WorkflowError,'C7_INPUT'):
            with store.locked_project(self.root):p,r=wf._load(self.root);av._assemble(self.root,p,r)
        last=state['pending_reviews'][1];state=record_review(self.root,'scene_av',last['version'],'approve',scene_id=last['scene_id'])
        self.assertEqual(state['stage'],'final_video')
        real_assemble=av.export.assemble
        def during_assembly(clips,output):
            real_assemble(clips,output)
            with store.locked_project(self.root):
                p,r=wf._load(self.root);r.setdefault('web_actions',{})['concurrent-ui']=dict(done=True);wf._save(self.root,p,r)
        with patch('src.preview.av_workflow.export.assemble',side_effect=during_assembly):
            state=av.generate(self.root)
        final=state['pending_reviews'][0]
        from src.scene_design import scene_revision as rev
        silent=wf._latest(wf._load(self.root)[0],'scene_clip',first['scene_id'])
        cycle=rev.request_revision(self.root,first['scene_id'],silent['version'],silent['sha256'],'Kiểm tra rồi hủy')
        with store.locked_project(self.root):
            p,r=wf._load(self.root);r['scene_revisions'][-1]['status']='failed';wf._save(self.root,p,r)
        rev.cancel_revision(self.root,cycle['id'])
        self.assertEqual(wf.get_status(self.root)['stage'],'final_video')
        self.assertEqual(wf.get_status(self.root)['pending_reviews'][0]['sha256'],final['sha256'])
        self.assertFalse((self.root/'final.mp4').exists());info=probe(final['path']);self.assertEqual(info['streams'][0]['width'],1920);self.assertEqual(info['streams'][0]['avg_frame_rate'],'30/1')
        preserved=next(a for a in state['artifacts'] if a['stage']=='scene_av' and a['scene_id']==last['scene_id'])
        av.reopen(self.root,first['scene_id'],first['version'],first['sha256'],'Chậm lại ở phần minh họa')
        state=av.generate(self.root,first['scene_id']);new=state['pending_reviews'][0]
        self.assertEqual(new['scene_id'],first['scene_id']);self.assertEqual(next(a for a in state['artifacts'] if a['stage']=='scene_av' and a['scene_id']==last['scene_id']),preserved)
        with self.assertRaisesRegex(store.WorkflowError,'STALE_REVIEW|WRONG_STAGE'):record_review(self.root,'final_video',final['version'],'approve')
        record_review(self.root,'scene_av',new['version'],'approve',scene_id=new['scene_id'])
        final=av.generate(self.root)['pending_reviews'][0];state=record_review(self.root,'final_video',final['version'],'approve',expected_sha256=final['sha256'])
        self.assertEqual(state['stage'],'completed');self.assertTrue((self.root/'final.mp4').exists())
        self.assertTrue((self.root/'final.srt').exists());self.assertTrue((self.root/'narration.wav').exists())
    def test_setting_change_and_script_integrity(self):
        state=av.generate(self.root,scene_id='scene_1');item=state['pending_reviews'][0]
        av.configure(self.root,dict(speed=1));state=wf.get_status(self.root)
        self.assertTrue(next(a for a in state['artifacts'] if a['stage']=='scene_av')['invalidated'])
        with self.assertRaisesRegex(store.WorkflowError,'STALE_REVIEW'):record_review(self.root,'scene_av',item['version'],'approve',scene_id='scene_1')
    def test_manim_preview_reference_is_cached_and_matches_design(self):
        from src.scene_design.design_preview import generate
        state=wf.get_status(self.root);a=next(a for a in state['artifacts'] if a['stage']=='scene_design')
        result=generate(self.root,a['scene_id'],a['version'],a['sha256']);path=self.root/result['path'];stamp=path.stat().st_mtime_ns
        self.assertEqual(generate(self.root,a['scene_id'],a['version'],a['sha256']),result);self.assertEqual(path.stat().st_mtime_ns,stamp)
        self.assertEqual(result['renderer'],'manim');self.assertEqual(result['design_sha256'],a['sha256'])
        with self.assertRaisesRegex(store.WorkflowError,'STALE_REVIEW'):generate(self.root,a['scene_id'],a['version'],'bad')
    def test_music_change_reuses_final_picture_speech_and_scene_approvals(self):
        from src.preview import music
        state=av.generate(self.root)
        for item in state['pending_reviews']:
            record_review(self.root,'scene_av',item['version'],'approve',scene_id=item['scene_id'])
        first=av.generate(self.root)['pending_reviews'][0]
        before=[r for r in wf._load(self.root)[1]['requests'] if r['stage']=='scene_av']
        library=self.root/'test_music';library.mkdir()
        path=library/'test.wav'
        run([binary('ffmpeg'),'-y','-v','error','-f','lavfi','-i','sine=frequency=800:duration=1','-ar','48000',str(path)])
        store.atomic_write(library/'catalog.json',dict(tracks=[dict(id='test',title='Test',artist='Test',filename='test.wav',sha256=store.digest(path))]))
        with patch('src.preview.music.MUSIC_ROOT',library):
            music.configure(self.root,dict(track_id='test',volume=10,ducking=True))
            with patch('src.preview.av_workflow.SYNTHESIZER',side_effect=AssertionError('No repeat TTS')),patch('src.preview.av_workflow.CLIP_RENDERER',side_effect=AssertionError('No repeat render')),patch('src.preview.av_workflow.export.assemble',side_effect=AssertionError('No repeat picture assembly')):
                second=av.generate(self.root)['pending_reviews'][0]
            project,reviews=wf._load(self.root);artifact=wf._latest(project,'final_video',None)
            self.assertNotEqual(first['version'],second['version'])
            self.assertEqual([r for r in reviews['requests'] if r['stage']=='scene_av'],before)
            self.assertEqual(store.read_json(self.root/artifact['manifest_path'])['music']['track']['id'],'test')
            self.assertLess(abs(duration(self.root/artifact['audio_path'])-duration(self.root/artifact['path'])),1/30)

    def test_timing_validation(self):
        design=dict(timeline=[dict(object_id='a',action='appear',start=0,duration=1)])
        with self.assertRaisesRegex(store.WorkflowError,'TIMING_PLAN'):av.timing(design,[dict(start=0,end=1)],dict(beats=[]))
