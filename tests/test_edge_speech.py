import copy,io,json,os,subprocess,tempfile,unittest
from contextlib import redirect_stderr
import math,struct,wave
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
    def make_pcm(self, name, lead, tail, tone=True):
        path = self.root/name
        samples = [0]*round(lead*48000)
        samples += [round(9000*math.sin(2*math.pi*500*i/48000)) if tone else 0 for i in range(24000)]
        samples += [0]*round(tail*48000)
        frames = struct.pack('<'+'h'*len(samples), *samples)
        with wave.open(str(path), 'wb') as wav:
            wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(48000)
            wav.writeframes(frames)
        return dict(path=path.name, sha256=store.digest(path), duration=len(samples)/48000), frames

    def test_edge_trim_caps_total_and_preserves_spoken_samples_and_raw_cache(self):
        from src.preview import edge_speech as edge
        source, frames = self.make_pcm('raw.wav', .2, .3)
        original = (self.root/source['path']).read_bytes()
        result = edge._trim_cached(self.root, source)
        self.assertAlmostEqual(result['trimmed_seconds'], .35)
        self.assertAlmostEqual(source['duration']-result['duration'], .35)
        with wave.open(str(self.root/result['path']), 'rb') as wav:
            actual = wav.readframes(wav.getnframes())
        start = frames.find(actual)
        self.assertGreaterEqual(start, 0)
        self.assertEqual(start % 2, 0)
        self.assertGreaterEqual(.2-start/2/48000, .04)
        removed_tail = len(frames)-start-len(actual)
        self.assertGreaterEqual(.3-removed_tail/2/48000, .08-1/48000)
        self.assertEqual((self.root/source['path']).read_bytes(), original)
        self.assertEqual(edge._trim_cached(self.root, source), result)
        (self.root/result['path']).write_bytes(b'changed')
        with self.assertRaisesRegex(store.WorkflowError, 'CACHE_CORRUPT'):
            edge._trim_cached(self.root, source)

    def test_edge_trim_keeps_short_padding_and_all_silent_audio(self):
        from src.preview import edge_speech as edge
        for name, lead, tail, tone in [('short.wav', .02, .03, True), ('silent.wav', .2, .3, False)]:
            source, frames = self.make_pcm(name, lead, tail, tone)
            result = edge._trim_cached(self.root, source)
            self.assertEqual(result['trimmed_seconds'], 0)
            with wave.open(str(self.root/result['path']), 'rb') as wav:
                self.assertEqual(wav.readframes(wav.getnframes()), frames)

    def test_processing_version_retires_only_edge_audio_preview(self):
        from src.shared import workflow as wf
        from src.preview import edge_speech as edge
        root = fixture(self.root/'project')
        av_workflow.configure(root, self.config)
        project, reviews = wf._load(root)
        voiced = continuous_preview.snapshot(root, project, reviews, True)
        silent = continuous_preview.snapshot(root, project, reviews, False)
        before_audio = continuous_preview.fingerprint(voiced)
        before_silent = continuous_preview.fingerprint(silent)
        with patch.object(edge, 'TRIM_VERSION', edge.TRIM_VERSION+1):
            self.assertNotEqual(continuous_preview.fingerprint(voiced), before_audio)
            self.assertEqual(continuous_preview.fingerprint(silent), before_silent)

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
        saves = []
        async def flaky(filename):
            path = Path(filename)
            # Failed downloads must be removed before starting another request.
            self.assertFalse(path.exists())
            saves.append(filename)
            if len(saves) < 3:
                path.write_bytes(b'incomplete mp3')
                raise NoAudioReceived('empty')
            await self.download('Test speech.', self.config['voice'], '+0%', path)
        with patch('edge_tts.Communicate') as communicate, patch('src.preview.edge_speech.asyncio.sleep', new_callable=AsyncMock) as sleep:
            communicate.return_value.save = AsyncMock(side_effect=flaky)
            first = audio.synthesize(self.root, 'Test speech.', self.config)
            self.assertEqual(audio.synthesize(self.root, 'Test speech.', self.config), first)
        self.assertEqual(communicate.call_count, 3)
        self.assertEqual([c.kwargs['boundary'] for c in communicate.call_args_list], ['WordBoundary', 'SentenceBoundary', 'WordBoundary'])
        self.assertEqual([call.args[0] for call in sleep.await_args_list], [2, 5])
        call = audio.summary(self.root)['calls'][0]
        self.assertEqual(call['attempts'], 3)
        self.assertEqual(call['boundary'], first['boundary'])
        self.assertNotIn('error', call)

    def test_no_audio_failure_has_specific_diagnostic_and_three_attempt_limit(self):
        from edge_tts.exceptions import NoAudioReceived
        terminal = io.StringIO()
        with redirect_stderr(terminal), patch('edge_tts.Communicate') as communicate, patch('src.preview.edge_speech.asyncio.sleep', new_callable=AsyncMock) as sleep:
            communicate.return_value.save = AsyncMock(side_effect=NoAudioReceived('empty'))
            with self.assertRaisesRegex(store.WorkflowError, 'TTS_EDGE_NO_AUDIO'):
                audio.synthesize(self.root, 'Test speech.', self.config)
        self.assertEqual(communicate.call_count, 3)
        self.assertEqual(communicate.return_value.save.await_count, 3)
        self.assertEqual([call.args[0] for call in sleep.await_args_list], [2, 5])
        call = audio.summary(self.root)['calls'][0]
        self.assertEqual(call['error'], 'NoAudioReceived')
        self.assertEqual(call['attempts'], 3)
        self.assertEqual([d['boundary'] for d in call['attempt_diagnostics']], [c.kwargs['boundary'] for c in communicate.call_args_list])
        self.assertFalse(list((self.root/'generation/checkpoint_7').glob('*.download.mp3')))
        logs = [json.loads(line.removeprefix('[voice.edge] ')) for line in terminal.getvalue().splitlines()]
        errors = [row for row in logs if row['event'] == 'download_error']
        self.assertEqual(len(errors), 3)
        self.assertTrue(all(row['error'] == 'NoAudioReceived' and row['audio_bytes'] == 0 and 'elapsed_seconds' in row for row in errors))
        self.assertEqual([row['delay_seconds'] for row in logs if row['event'] == 'retry_wait'], [2, 5])
        self.assertEqual(logs[-1]['event'], 'synthesize_error')
        self.assertEqual(logs[-1]['code'], 'TTS_EDGE_NO_AUDIO')

    def test_alternate_boundary_success_records_actual_mode_in_ledger_and_cache(self):
        from edge_tts.exceptions import NoAudioReceived
        saves = []
        async def flaky(filename):
            saves.append(filename)
            if len(saves) == 1:
                raise NoAudioReceived('empty')
            await self.download('Test speech.', self.config['voice'], '+0%', Path(filename))
        terminal = io.StringIO()
        with redirect_stderr(terminal), patch('edge_tts.Communicate') as communicate, patch('src.preview.edge_speech.asyncio.sleep', new_callable=AsyncMock) as sleep:
            communicate.return_value.save = AsyncMock(side_effect=flaky)
            result = audio.synthesize(self.root, 'Test speech.', self.config)
            self.assertEqual(audio.synthesize(self.root, 'Test speech.', self.config), result)
        self.assertEqual(communicate.call_count, 2)
        self.assertEqual([c.kwargs['boundary'] for c in communicate.call_args_list], ['WordBoundary', 'SentenceBoundary'])
        self.assertTrue(all(c.kwargs['voice'] == self.config['voice'] and c.kwargs['rate'] == '+0%' for c in communicate.call_args_list))
        sleep.assert_awaited_once_with(2)
        call = audio.summary(self.root)['calls'][0]
        self.assertEqual(call['status'], 'complete')
        self.assertEqual(call['attempts'], 2)
        self.assertEqual(call['boundary'], 'SentenceBoundary')
        self.assertNotIn('error', call)
        self.assertNotIn('error_code', call)
        self.assertEqual(result['boundary'], 'SentenceBoundary')
        cached = store.read_json(self.root/'generation/checkpoint_7'/(result['key']+'.json'))
        self.assertEqual(cached['boundary'], 'SentenceBoundary')
        logs = [json.loads(line.removeprefix('[voice.edge] ')) for line in terminal.getvalue().splitlines()]
        completed = next(row for row in logs if row['event'] == 'download_complete')
        self.assertGreater(completed['audio_bytes'], 0)
        self.assertEqual(completed['boundary'], 'SentenceBoundary')
        self.assertEqual(sum(row['event'] == 'cache_hit' for row in logs), 1)

    def test_rate_limit_auth_and_timeout_classification(self):
        from src.preview.edge_speech import _failure
        class HttpError(Exception):
            def __init__(self,status):self.status=status
        for status,code,retry in [(429,'TTS_EDGE_RATE_LIMIT',True),(403,'TTS_EDGE_CONFIGURATION',False),(503,'TTS_EDGE_SERVICE',True)]:
            result=_failure(HttpError(status));self.assertEqual((result[0],result[2]),(code,retry))
        self.assertEqual(_failure(TimeoutError())[0],'TTS_EDGE_NETWORK')

if __name__=='__main__':unittest.main()
