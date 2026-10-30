"""Real audio/video regression tests; no speech service or music network calls."""
import array
import json
import math
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import uuid
import wave
from unittest.mock import patch

from src.preview import music, continuous_preview as preview
from src.shared import _workflow_store as store
from src.shared import workflow as wf
from src.shared.media_tools import binary, run, duration, probe
from src.shared.review_server import ReviewServer
from tests.test_continuous_preview import fixture, fake_speech


class MusicTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.directory=Path(self.temp.name);self.root=fixture(self.directory/'project')
        library=self.directory/'music';library.mkdir()
        self.addCleanup(patch.stopall);patch('src.preview.music.MUSIC_ROOT',library).start()
        tracks=[]
        for name,seconds in [('short',.8),('long',8)]:
            path=library/(name+'.wav')
            run([binary('ffmpeg'),'-y','-v','error','-f','lavfi','-i',f'sine=frequency=1000:duration={seconds}', '-ar','48000',str(path)])
            tracks.append(dict(id=name,title=name,artist='Test',filename=path.name,sha256=store.digest(path),duration=seconds,license_url='https://example.test/license'))
        store.atomic_write(library/'catalog.json',dict(tracks=tracks))

    def config(self,name='short',volume=10,ducking=True):
        return dict(track_id=name,volume=volume,ducking=ducking)

    def test_short_and_long_music_preserve_duration_subtitles_and_cached_picture(self):
        with patch('src.preview.continuous_preview.SYNTHESIZER',side_effect=fake_speech):
            dry=preview.generate(self.root,True)
        reviews=json.loads((self.root/'reviews.json').read_text())['requests']
        for selection in [self.config('short'),self.config('long'),self.config('long',20,False),self.config(None)]:
            music.configure(self.root,selection)
            with patch('src.preview.continuous_preview.SYNTHESIZER',side_effect=AssertionError('speech cache must be reused')),patch('src.preview.continuous_preview._encode',side_effect=AssertionError('picture must be reused')):
                result=preview.generate(self.root,True)
                self.assertEqual(preview.generate(self.root,True),result)
            self.assertEqual(result['cues'],dry['cues'])
            self.assertEqual(result['subtitle_sha256'],dry['subtitle_sha256'])
            self.assertLess(abs(duration(self.root/result['audio_path'])-result['duration']),1/30)
            info=probe(self.root/result['path'])
            self.assertTrue(any(s['codec_type']=='subtitle' for s in info['streams']))
            self.assertEqual(preview.status(self.root)['audio']['sha256'],result['sha256'])
        self.assertEqual(json.loads((self.root/'reviews.json').read_text())['requests'],reviews)

    def test_settings_media_allowlist_and_changed_file(self):
        for config in [self.config('../project.json'),self.config(volume=31),self.config(volume=True),self.config(volume=float('nan')),self.config(ducking='true')]:
            with self.assertRaises(store.WorkflowError):music.configure(self.root,config)
        item,path=music.track('short');path.write_bytes(b'changed')
        with self.assertRaisesRegex(store.WorkflowError,'MUSIC_CHANGED'):music.track('short')

    def test_mix_cache_rejects_changed_wav(self):
        with patch('src.preview.continuous_preview.SYNTHESIZER',side_effect=fake_speech):result=preview.generate(self.root,True)
        (self.root/result['audio_path']).write_bytes(b'changed')
        with self.assertRaisesRegex(store.WorkflowError,'CACHE_CORRUPT'):preview.generate(self.root,True)

    def test_ducking_reduces_music_keeps_voice_and_limits_peak(self):
        voice=self.directory/'voice.wav'
        run([binary('ffmpeg'),'-y','-v','error','-f','lavfi','-i','sine=frequency=300:duration=5',
             '-af',"volume='if(between(t,1,3),1,0)':eval=frame",'-ar','48000',str(voice)])
        outputs=[]
        for duck in (False,True):
            music.configure(self.root,self.config('short',10,duck))
            out=self.directory/('duck.wav' if duck else 'plain.wav')
            music.mix_audio(voice,out,5,music.snapshot(self.root));outputs.append(out)
        def levels(path):
            with wave.open(str(path)) as wav:
                wav.setpos(int(1.7*48000));samples=array.array('h',wav.readframes(24000));mono=samples[::2]
            def amplitude(f):
                real=sum(x*math.cos(2*math.pi*f*i/48000) for i,x in enumerate(mono))
                imaginary=sum(x*math.sin(2*math.pi*f*i/48000) for i,x in enumerate(mono))
                return math.hypot(real,imaginary)*2/len(mono)
            return amplitude(1000),amplitude(300),max(abs(v) for v in samples)/32768
        plain,duck=map(levels,outputs)
        self.assertLess(duck[0],plain[0]*.7)
        self.assertGreater(duck[1],plain[1]*.9)
        self.assertLessEqual(duck[2],.892)
        self.assertAlmostEqual(duration(outputs[1]),5,places=3)

    def test_music_api_requires_session_and_serves_only_registered_ids_with_ranges(self):
        server=ReviewServer(self.root,0);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        state=json.load(urllib.request.urlopen(server.origin+'/api/state'))
        self.assertEqual(len(state['project']['music']['tracks']),2)
        payload=json.dumps(dict(config=self.config(),request_id=uuid.uuid4().hex)).encode()
        bad=urllib.request.Request(server.origin+'/api/music-settings',data=payload,headers={'Origin':server.origin})
        with self.assertRaises(urllib.error.HTTPError):urllib.request.urlopen(bad)
        good=urllib.request.Request(server.origin+'/api/music-settings',data=payload,headers={'Origin':server.origin,'X-Review-Token':state['token'],'Content-Type':'application/json'})
        self.assertTrue(json.load(urllib.request.urlopen(good))['ok'])
        req=urllib.request.Request(server.origin+'/music-media?id=short',headers={'Range':'bytes=0-99'})
        with urllib.request.urlopen(req) as response:self.assertEqual(response.status,206);self.assertEqual(len(response.read()),100)
        for value in ('../project.json','unknown','/etc/passwd'):
            with self.assertRaises(urllib.error.HTTPError):urllib.request.urlopen(server.origin+'/music-media?id='+value)


if __name__=='__main__':unittest.main()
