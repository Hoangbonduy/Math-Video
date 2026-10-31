"""Real ffmpeg media, fake speech; no API calls or user approvals."""
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.request
import uuid
import wave
from unittest.mock import patch
from src.preview import continuous_preview as preview
from src.shared import workflow as wf
from src.shared import _workflow_store as store
from src.preview import audio, pacing
from src.shared.media_tools import binary, run, probe
from src.shared.review import record_review
from src.shared.review_server import ReviewServer


def fixture(root, portrait=False):
    wf.create_project(root, 'Kiểm thử bản dựng')
    script = root / 'input.md'; script.write_text('Kịch bản thử đã duyệt.', encoding='utf-8')
    wf.submit_artifact(root, 'script', 'v001', script, scene_ids=['first', 'second'])
    payload = root / 'script.json'
    payload.write_text(json.dumps(dict(script=dict(title='Video thử', scenes=[
        dict(scene_id='first', title='Cảnh đỏ', narration='Xin chào các bạn. Đây là cảnh đầu.'),
        dict(scene_id='second', title='Cảnh xanh', narration='Tiếp theo là cảnh thứ hai. Cảm ơn bạn đã xem.')
    ])), ensure_ascii=False), encoding='utf-8')
    with store.locked_project(root):
        p, r = wf._load(root); p['artifacts'][0]['bundle']={'script.json':dict(path='script.json', sha256=store.digest(payload))}; wf._save(root,p,r)
    record_review(root,'script','v001','approve')
    for sid in ('first','second'):
        design=root/(sid+'.json'); design.write_text('{}')
        wf.submit_artifact(root,'scene_design','v001',design,scene_id=sid)
        record_review(root,'scene_design','v001','approve',scene_id=sid)
    for sid,color,seconds in [('first','red',.8),('second','blue',1.2)]:
        path=root/(sid+'.mp4'); size='180x320' if portrait else '320x180'
        run([binary('ffmpeg'),'-y','-v','error','-f','lavfi','-i',f'color=c={color}:s={size}:r=30','-t',str(seconds),'-c:v','libx264','-preset','ultrafast','-pix_fmt','yuv420p',str(path)])
        wf.submit_artifact(root,'scene_clip','v001',path,scene_id=sid)
    with store.locked_project(root):
        p,r=wf._load(root); p['audio_config']=dict(audio.DEFAULTS,provider='fpt',voice='leminh',vnd_per_million_chars=333334,vnd_per_usd=25000);wf._save(root,p,r)
    return root


def fake_speech(root,text,config):
    path=root/(hashlib.sha256(text.encode()).hexdigest()+'.wav')
    with wave.open(str(path),'wb') as file:
        file.setnchannels(1);file.setsampwidth(2);file.setframerate(48000);file.writeframes(b'\0\0'*28800)
    return dict(path=str(path.relative_to(root)),sha256=store.digest(path),duration=.6)


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=fixture(Path(self.temp.name))

    def test_silent_order_captions_cache_and_unchanged_reviews(self):
        before=[(self.root/name).read_bytes() for name in ('project.json','reviews.json')]
        with patch('src.preview.continuous_preview.SYNTHESIZER',side_effect=AssertionError('No paid calls')):
            item=preview.generate(self.root)
            self.assertEqual(preview.generate(self.root),item)
        self.assertEqual([s['scene_id'] for s in item['scenes']],['first','second'])
        self.assertAlmostEqual(item['duration'],2,places=2)
        self.assertEqual(item['scenes'][1]['start'],item['scenes'][0]['end'])
        self.assertEqual(preview.status(self.root)['silent'],item)
        info=probe(self.root/item['path']); self.assertFalse(any(s['codec_type']=='audio' for s in info['streams']))
        self.assertTrue(any(s['codec_type']=='subtitle' for s in info['streams']))
        # Decode pixels across the real splice to verify source order, not metadata alone.
        for time,color in [(.3,0),(1.3,2)]:
            pixel=run([binary('ffmpeg'),'-v','error','-ss',str(time),'-i',str(self.root/item['path']),'-frames:v','1','-vf','scale=1:1','-pix_fmt','rgb24','-f','rawvideo','-'])
            self.assertGreater(pixel[color],180)
        self.assertEqual(before,[(self.root/name).read_bytes() for name in ('project.json','reviews.json')])
        text=(self.root/item['subtitle_path']).read_text();self.assertIn('Xin chào',text);self.assertIn('Cảm ơn',text)

    def test_audio_phrases_duration_cache_and_invalidation(self):
        with patch('src.preview.continuous_preview.SYNTHESIZER',side_effect=fake_speech) as synth:
            item=preview.generate(self.root,True);count=synth.call_count
            preview.generate(self.root,True);self.assertEqual(synth.call_count,count)
        silent=preview.generate(self.root)
        self.assertGreater(item['duration'],silent['duration'])
        self.assertTrue(all(s['hold_seconds']>0 for s in item['scenes']))
        self.assertTrue(all(abs(c['end']-c['start']-.6)<1e-6 for c in item['cues']))
        for scene in item['scenes']:
            self.assertTrue(all(scene['start']<=c['start']<c['end']<=scene['end'] for c in item['cues'] if c['scene_id']==scene['scene_id']))
        info=probe(self.root/item['path']);sound=next(s for s in info['streams'] if s['codec_type']=='audio');video=next(s for s in info['streams'] if s['codec_type']=='video')
        self.assertLess(abs(float(sound['duration'])-float(video['duration'])),.034)
        self.assertEqual(len(item['cues']),count)
        for a,b in zip(item['cues'],item['cues'][1:]):self.assertLessEqual(a['end'],b['start']+.00001)
        with store.locked_project(self.root):
            p,r=wf._load(self.root);p['audio_config']['speed']=1;wf._save(self.root,p,r)
        self.assertIsNone(preview.status(self.root)['audio'])
        # A new design version must also retire the cached movie.
        with store.locked_project(self.root):
            p,r=wf._load(self.root);a=wf._latest(p,'scene_design','first');a['sha256']='f'*64;wf._save(self.root,p,r)
        with self.assertRaisesRegex(store.WorkflowError,'ARTIFACT_CHANGED'):preview.generate(self.root)

    def test_speech_stays_unchanged_across_picture_waits(self):
        import numpy as np
        segments=[]
        originals=[]
        for index,frequency in enumerate((440,880)):
            path=self.root/f'tone{index}.wav'
            run([binary('ffmpeg'),'-y','-v','error','-f','lavfi','-i',
                 f'sine=frequency={frequency}:duration=1','-ar','48000',str(path)])
            with wave.open(str(path),'rb') as wav:originals.append(wav.readframes(wav.getnframes()))
            segments.append(dict(text=str(index),start=.15+index*1.3,end=1.15+index*1.3,
                                 audio=dict(path=path.name,duration=1)))
        output=self.root/'natural.wav'
        pacing.speech_track(self.root,segments,output,2.8)
        with wave.open(str(output),'rb') as wav:
            self.assertEqual(wav.getnframes(),134400)
            samples=np.frombuffer(wav.readframes(wav.getnframes()),dtype='<i2')
        for original,segment in zip(originals,segments):
            start=round(segment['start']*48000)
            self.assertEqual(samples[start:start+48000].tobytes(),original)
        self.assertFalse(samples[55200:69600].any())  # Silence while waiting for picture.

    def test_short_speech_waits_for_picture_without_speed_change(self):
        ref=fake_speech(self.root,'Short speech',{})
        segments=[dict(text='Short speech',audio=ref)]
        beats=pacing.plan(self.root,{},['Short speech'],segments,2,30)
        self.assertEqual(beats[-1]['end_frame'],60)
        self.assertEqual(beats[0]['hold_frames'],0)
        self.assertAlmostEqual(segments[0]['start'],.15)
        self.assertAlmostEqual(segments[0]['end'],.75)

    def test_12fps_motion_preserved_with_internal_and_final_holds(self):
        import av
        import numpy as np
        source=self.root/'motion.mp4'
        run([binary('ffmpeg'),'-y','-v','error','-f','lavfi','-i',
             'testsrc2=s=320x180:r=12','-t','1','-c:v','libx264','-pix_fmt','yuv420p',str(source)])
        story=self.root/'storyboard.json'
        store.atomic_write(story,dict(beats=[
            dict(id='one',start=0,end=.5,narration_excerpt='First.'),
            dict(id='two',start=.5,end=.95,narration_excerpt='Second.')]))
        segments=[dict(text=text,audio=dict(fake_speech(self.root,text,{}),duration=.8)) for text in ['First.','Second.']]
        beats=pacing.plan(self.root,dict(clip=dict(path=source.name),storyboard=dict(path=story.name,sha256=store.digest(story))),
                          ['First.','Second.'],segments,1,30)
        self.assertEqual([b['hold_frames'] for b in beats],[14,14])
        self.assertAlmostEqual(segments[1]['start'],29/30)
        silent,voiced=self.root/'silent.mp4',self.root/'holds.mp4'
        preview._encode(source,silent,1,(320,180))
        preview._encode(source,voiced,58/30,(320,180),beats=beats)
        def frames(path):
            with av.open(str(path)) as container:
                return [frame.to_ndarray(format='gray').astype(np.int16) for frame in container.decode(video=0)]
        native,actual=frames(silent),frames(voiced)
        expected=native[:15]+[native[14]]*14+native[15:]+[native[-1]]*14
        self.assertEqual(len(actual),len(expected))
        self.assertEqual(len(native),30)
        self.assertLess(max(np.abs(a-b).mean() for a,b in zip(actual,expected)),1.5)
        self.assertGreater(np.abs(actual[14]-actual[29]).mean(),1)

    def test_storyboard_changes_invalidate_preview_cache(self):
        project,reviews=wf._load(self.root)
        inputs=preview.snapshot(self.root,project,reviews,True)
        initial=preview.fingerprint(inputs)
        design=store.project_path(self.root,inputs['scenes'][0]['design']['path'])
        story=design.parent/'storyboard.json'
        store.atomic_write(story,dict(beats=[]))
        changed=preview.snapshot(self.root,project,reviews,True)
        self.assertNotEqual(initial,preview.fingerprint(changed))

    def test_storyboard_mismatch_is_rejected(self):
        story=self.root/'bad-story.json'
        store.atomic_write(story,dict(beats=[dict(id='wrong',start=0,end=1,narration_excerpt='Different.')]))
        with self.assertRaisesRegex(store.WorkflowError,'PREVIEW_TIMING'):
            pacing.plan(self.root,dict(storyboard=dict(path=story.name,sha256=store.digest(story))),
                        ['Original.'],[dict(text='Original.',audio=dict(duration=.6))],1,30)

    def test_incomplete_and_missing_price_do_not_synthesize(self):
        with store.locked_project(self.root):
            p,r=wf._load(self.root);p['audio_config']['vnd_per_million_chars']=None;wf._save(self.root,p,r)
        with patch('src.preview.continuous_preview.SYNTHESIZER') as synth:
            with self.assertRaisesRegex(store.WorkflowError,'TTS_PRICING_REQUIRED'):preview.generate(self.root,True)
            synth.assert_not_called()
        with store.locked_project(self.root):
            p,r=wf._load(self.root);clip=wf._latest(p,'scene_clip','second');clip['invalidated']=True;wf._save(self.root,p,r)
        self.assertFalse(preview.status(self.root)['ready'])
        with self.assertRaisesRegex(store.WorkflowError,'PREVIEW_INPUT'):preview.generate(self.root)

    def test_server_registered_media_ranges_and_protected_build(self):
        item=preview.generate(self.root)
        server=ReviewServer(self.root,0);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        def get(path):return urllib.request.urlopen(server.origin+path)
        state=json.load(get('/api/state'));self.assertIn('continuous_preview',state['capabilities'])
        self.assertEqual(state['project']['continuous_preview']['silent']['sha256'],item['sha256'])
        from urllib.parse import quote
        request=urllib.request.Request(server.origin+'/media?path='+quote(item['path']),headers={'Range':'bytes=0-99'})
        with urllib.request.urlopen(request) as response:self.assertEqual(response.status,206);self.assertEqual(len(response.read()),100)
        with self.assertRaises(urllib.error.HTTPError):get('/media?path=project.json')
        payload=json.dumps(dict(request_id=uuid.uuid4().hex,with_audio=False)).encode()
        request=urllib.request.Request(server.origin+'/api/preview-build',data=payload,headers={'Origin':server.origin,'X-Review-Token':state['token'],'Content-Type':'application/json'})
        self.assertTrue(json.load(urllib.request.urlopen(request))['ok'])
        bad=urllib.request.Request(server.origin+'/api/preview-build',data=payload,headers={'Origin':server.origin})
        with self.assertRaises(urllib.error.HTTPError):urllib.request.urlopen(bad)

    def test_portrait(self):
        with tempfile.TemporaryDirectory() as directory:
            root=fixture(Path(directory),portrait=True);item=preview.generate(root)
            self.assertEqual(item['resolution'],[360,640])

if __name__=='__main__': unittest.main()
