import copy,json,os,subprocess,tempfile,unittest
from pathlib import Path
from unittest.mock import patch, AsyncMock
from src.preview import audio
from src.preview import av_workflow
from src.preview import continuous_preview
from src.shared import _workflow_store as store
from src.shared.media_tools import binary,probe
from tests.test_continuous_preview import fixture

class EdgeSpeechTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.config=dict(audio.DEFAULTS,provider='edge',voice='vi-VN-HoaiMyNeural',vnd_per_million_chars=None,vnd_per_usd=None,allow_unpriced=False)
    async def download(self,text,voice,rate,path,boundary='WordBoundary'):
        subprocess.run([binary('ffmpeg'),'-y','-v','error','-f','lavfi','-i','sine=frequency=500:duration=0.5','-c:a','libmp3lame',str(path)],check=True)
    def test_default_is_nam_minh_edge_and_wrong_provider_voices_rejected(self):
        self.assertEqual(audio.settings(self.root)['provider'],'edge');self.assertEqual(audio.settings(self.root)['voice'],'vi-VN-NamMinhNeural')
        for values in [dict(provider='fpt'),dict(voice='leminh'),dict(provider='unsupported')]:
            with self.assertRaises(store.WorkflowError):audio.validate_settings(dict(self.config,**values))
    def test_no_key_or_fpt_price_required_cache_and_voice_identity(self):
        with patch.dict(os.environ,{'FPT_API_KEY':''}),patch('src.preview.audio.urllib.request.urlopen',side_effect=AssertionError('FPT forbidden')),patch('src.preview.edge_speech._download',side_effect=self.download) as download:
            first=audio.synthesize(self.root,'Xin chào các bạn.',self.config);self.assertEqual(first,audio.synthesize(self.root,'Xin chào các bạn.',self.config));self.assertEqual(download.call_count,1)
            second=audio.synthesize(self.root,'Xin chào các bạn.',dict(self.config,voice='vi-VN-NamMinhNeural'));self.assertNotEqual(first['key'],second['key'])
        self.assertEqual(first['provider'],'edge');self.assertGreater(first['duration'],0)
        summary=audio.summary(self.root);self.assertFalse(summary['cost_is_unknown']);self.assertTrue(all(c['provider']=='edge' and c['cost_status']=='no_api_key' for c in summary['calls']))
        (self.root/first['path']).write_bytes(b'changed')
        with self.assertRaisesRegex(store.WorkflowError,'CACHE_CORRUPT'):audio.synthesize(self.root,'Xin chào các bạn.',self.config)
    def test_network_failure_keeps_diagnostic_and_never_falls_back_to_fpt(self):
        with patch('src.preview.edge_speech._download',side_effect=ConnectionError('network')),patch('src.preview.edge_speech.asyncio.sleep',new_callable=AsyncMock),patch('src.preview.audio.urllib.request.urlopen',side_effect=AssertionError('FPT forbidden')):
            with self.assertRaisesRegex(store.WorkflowError,'TTS_EDGE_NETWORK'):audio.synthesize(self.root,'Xin chào các bạn.',self.config)
        self.assertEqual(audio.summary(self.root)['calls'][0]['status'],'failed')
        with patch('src.preview.edge_speech._download',side_effect=self.download):self.assertEqual(audio.synthesize(self.root,'Xin chào các bạn.',self.config)['status'],'complete')
        self.assertEqual(len(audio.summary(self.root)['calls']),2)
    def test_full_preview_has_audio_without_keys_prices_or_review_changes(self):
        project=fixture(self.root/'project');av_workflow.configure(project,self.config)
        before=[(project/n).read_bytes() for n in ['project.json','reviews.json']]
        with patch.dict(os.environ,{'FPT_API_KEY':''}),patch('src.preview.audio.urllib.request.urlopen',side_effect=AssertionError('FPT forbidden')),patch('src.preview.edge_speech._download',side_effect=self.download):
            result=continuous_preview.generate(project,True)
            self.assertEqual(result,continuous_preview.generate(project,True))
        self.assertTrue(result['with_audio']);self.assertTrue(any(s['codec_type']=='audio' for s in probe(project/result['path'])['streams']))
        self.assertEqual(before,[(project/n).read_bytes() for n in ['project.json','reviews.json']])
        self.assertTrue(all(c['provider']=='edge' for c in audio.summary(project)['calls']))

    def test_adapter_uses_word_boundary_and_selected_nam_minh(self):
        import asyncio
        from src.preview import edge_speech
        with patch('edge_tts.Communicate') as communicate:
            communicate.return_value.save=AsyncMock()
            asyncio.run(edge_speech._download('Câu kiểm tra âm thanh.','vi-VN-NamMinhNeural','+0%',self.root/'test.mp3'))
        self.assertEqual(communicate.call_args.kwargs['boundary'],'WordBoundary')
        self.assertEqual(communicate.call_args.kwargs['voice'],'vi-VN-NamMinhNeural')

    def test_no_audio_retry_delay_success_and_cache(self):
        from edge_tts.exceptions import NoAudioReceived
        attempts=[]
        async def flaky(*args,**kwargs):
            attempts.append(1)
            if len(attempts)<3:raise NoAudioReceived('empty')
            await self.download(*args,**kwargs)
        with patch('src.preview.edge_speech._download',side_effect=flaky) as download,patch('src.preview.edge_speech.asyncio.sleep',new_callable=AsyncMock) as sleep:
            first=audio.synthesize(self.root,'Đây là câu kiểm tra.',self.config)
            self.assertEqual(audio.synthesize(self.root,'Đây là câu kiểm tra.',self.config),first)
        self.assertEqual(download.call_count,3)
        self.assertEqual([c.kwargs['boundary'] for c in download.call_args_list],['WordBoundary','SentenceBoundary','WordBoundary'])
        self.assertEqual([call.args[0] for call in sleep.await_args_list],[2,5])
        call=audio.summary(self.root)['calls'][0]
        self.assertEqual(call['attempts'],3);self.assertNotIn('error',call)

    def test_no_audio_failure_has_specific_diagnostic_and_three_attempt_limit(self):
        from edge_tts.exceptions import NoAudioReceived
        with patch('src.preview.edge_speech._download',side_effect=NoAudioReceived('empty')) as download,patch('src.preview.edge_speech.asyncio.sleep',new_callable=AsyncMock):
            with self.assertRaisesRegex(store.WorkflowError,'TTS_EDGE_NO_AUDIO'):audio.synthesize(self.root,'Đây là câu kiểm tra.',self.config)
        self.assertEqual(download.call_count,3)
        self.assertEqual(audio.summary(self.root)['calls'][0]['error'],'NoAudioReceived')

    def test_rate_limit_auth_and_timeout_classification(self):
        from src.preview.edge_speech import _failure
        class HttpError(Exception):
            def __init__(self,status):self.status=status
        for status,code,retry in [(429,'TTS_EDGE_RATE_LIMIT',True),(403,'TTS_EDGE_CONFIGURATION',False),(503,'TTS_EDGE_SERVICE',True)]:
            result=_failure(HttpError(status));self.assertEqual((result[0],result[2]),(code,retry))
        self.assertEqual(_failure(TimeoutError())[0],'TTS_EDGE_NETWORK')

if __name__=='__main__':unittest.main()
