"""Voice regression coverage using local PCM tones; no external TTS calls."""
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
import wave
from unittest.mock import patch

from src.preview import audio, pacing, continuous_preview as preview, av_workflow as av
from src.shared import _workflow_store as store, workflow as wf
from src.shared.media_tools import binary, run
from src.shared.review import record_review
from tests.test_continuous_preview import fixture


class VoicePacingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def tone(self, root, text, config, retry=None):
        name = hashlib.sha256(text.encode()).hexdigest() + '.wav'
        path = root / name
        # Equal word counts deliberately have unequal real speech durations.
        seconds = .4 if text.startswith('Xin') else 1.1
        if not path.exists():
            run([binary('ffmpeg'), '-y', '-v', 'error', '-f', 'lavfi', '-i',
                 f'sine=frequency=440:duration={seconds}', '-ar', '48000', '-ac', '1', str(path)])
        return dict(path=name, sha256=store.digest(path), duration=seconds)

    def read_wave(self, path):
        with wave.open(str(path)) as wav:
            return wav.getnframes(), wav.readframes(wav.getnframes())

    def test_real_audio_durations_and_punctuation_gaps_preserve_pcm(self):
        text = 'Xin chào, hãy xem: giá trị là 3,14 km. Kết thúc!'
        prepared = pacing.prepare(self.root, {}, text, {}, self.tone)
        segments = prepared['segments']
        self.assertEqual([s['text'] for s in segments],
                         ['Xin chào,', 'hãy xem:', 'giá trị là 3,14 km.', 'Kết thúc!'])
        self.assertEqual([s['pause_after'] for s in segments], [.10, .16, .22, 0])
        output = self.root / 'speech.wav'
        pacing.speech_track(self.root, segments, output, prepared['duration'])
        frames, actual = self.read_wave(output)
        self.assertEqual(frames, round(prepared['duration'] * 48000))
        previous_end = 0
        for segment in segments:
            count, original = self.read_wave(self.root / segment['audio']['path'])
            start = round(segment['start'] * 48000)
            self.assertAlmostEqual(segment['end']-segment['start'], segment['audio']['duration'])
            self.assertEqual(actual[start*2:(start+count)*2], original)
            self.assertFalse(any(actual[previous_end*2:start*2]))
            previous_end = start + count
        self.assertFalse(any(actual[previous_end*2:]))

    def test_mismatched_slots_overlap_and_short_output_are_rejected(self):
        ref = self.tone(self.root, 'Xin chào.', {})
        good = dict(text='Xin chào.', start=.15, end=.55, audio=ref)
        cases = [([dict(good, end=.3)], 1),
                 ([good, dict(good, start=.3, end=.7)], 1),
                 ([good], .4)]
        with patch('src.preview.pacing.run') as render:
            for segments, seconds in cases:
                with self.subTest(segments=segments, seconds=seconds):
                    with self.assertRaisesRegex(store.WorkflowError, 'PREVIEW_TIMING'):
                        pacing.speech_track(self.root, segments, self.root/'bad.wav', seconds)
            render.assert_not_called()

    def test_storyboard_can_split_inside_a_sentence_without_word_time_estimates(self):
        story = self.root/'story.json'
        store.atomic_write(story, dict(beats=[
            dict(id='first', start=0, end=.5, narration_excerpt='Xin chào'),
            dict(id='second', start=.5, end=1, narration_excerpt='các bạn.')]))
        source = dict(storyboard=dict(path=story.name, sha256=store.digest(story)))
        prepared = pacing.prepare(self.root, source, 'Xin chào các bạn.', {}, self.tone, 1)
        self.assertEqual([s['text'] for s in prepared['segments']], ['Xin chào', 'các bạn.'])
        self.assertGreater(prepared['duration'], 1)
        self.assertEqual(len(prepared['beats']), 2)
        self.assertAlmostEqual(prepared['segments'][1]['end']-prepared['segments'][1]['start'], 1.1)
        with self.assertRaisesRegex(store.WorkflowError, 'PREVIEW_TIMING'):
            pacing.prepare(self.root, source, 'Nội dung khác.', {}, self.tone, 1)

    def test_sample_matches_first_scene_processed_audio_including_beat_waits(self):
        root = fixture(self.root/'project')
        # A long first beat forces a picture wait before the second sentence.
        story = root/'first-story.json'
        store.atomic_write(story, dict(beats=[
            dict(id='first', start=0, end=.6, narration_excerpt='Xin chào các bạn.'),
            dict(id='second', start=.6, end=.8, narration_excerpt='Đây là cảnh đầu.')]))
        with store.locked_project(root):
            project, reviews = wf._load(root)
            wf._latest(project, 'scene_design', 'first')['storyboard_path'] = story.name
            wf._save(root, project, reviews)
        with patch('src.preview.av_workflow.SYNTHESIZER', side_effect=self.tone), \
             patch('src.preview.continuous_preview.SYNTHESIZER', side_effect=self.tone):
            sample = av.sample(root)
            preview._generate_base(root, True)
        item = store.read_json(root/preview.BASE/'index.json')['audio']
        first = item['scenes'][0]
        self.assertAlmostEqual(sample['duration'], first['end']-first['start'])
        cues = [c for c in item['cues'] if c['scene_id']=='first']
        self.assertEqual([(c['start'], c['end'], c['text']) for c in cues],
                         [(s['start'], s['end'], s['text']) for s in sample['segments']])
        scene_wav = (root/item['path']).parent/'scene_000.wav'
        self.assertEqual(self.read_wave(root/sample['path']), self.read_wave(scene_wav))
        self.assertGreater(sample['segments'][1]['start']-sample['segments'][0]['end'], .19)
        self.assertEqual(wf.get_status(root)['voice_sample']['path'], sample['path'])
        with patch('src.preview.av_workflow.SYNTHESIZER', side_effect=self.tone), \
             patch('src.preview.pacing.speech_track', side_effect=AssertionError('sample cache')):
            self.assertEqual(av.sample(root), sample)
        (root/sample['path']).write_bytes(b'changed')
        with patch('src.preview.av_workflow.SYNTHESIZER', side_effect=self.tone):
            with self.assertRaisesRegex(store.WorkflowError, 'CACHE_CORRUPT'):
                av.sample(root)

    def test_sample_works_before_visuals_and_before_other_scenes_are_ready(self):
        root = fixture(self.root/'project')
        with store.locked_project(root):
            project, reviews = wf._load(root)
            for artifact in project['artifacts']:
                if artifact['stage'] in ('scene_design', 'scene_clip'):
                    artifact['invalidated'] = True
            project['stage'] = 'scene_design'
            wf._save(root, project, reviews)
        with patch('src.preview.av_workflow.SYNTHESIZER', side_effect=self.tone):
            sample = av.sample(root)
        self.assertEqual(len(sample['segments']), 2)
        self.assertTrue((root/sample['path']).is_file())

    def test_c7_scene_audio_and_subtitles_share_natural_pauses(self):
        # Enter C7 from approved local artifacts; no script-generation fixture.
        from src.shared.ai_config import MODEL
        from src.scene_design.design import DEFAULT_CONFIG
        from tests.test_av import TimingRouter
        root = fixture(self.root/'project')
        with store.locked_project(root):
            project, reviews = wf._load(root)
            script = wf._latest(project, 'script', None)
            payload_path = root/script['bundle']['script.json']['path']
            payload = store.read_json(payload_path)
            payload['brief'] = dict(min_seconds=30, max_seconds=60)
            store.atomic_write(payload_path, payload)
            script['bundle']['script.json']['sha256'] = store.digest(payload_path)
            design = wf._latest(project, 'scene_design', 'first')
            store.atomic_write(root/design['path'], dict(duration=.8, timeline=[
                dict(object_id='title', action='appear', start=0, duration=.3),
                dict(object_id='picture', action='move', start=.3, duration=.5)]))
            design['sha256'] = store.digest(root/design['path'])
            design['review_path'] = str(root/'first.md')
            (root/'first.md').write_text('Approved test design')
            wf._save(root, project, reviews)
        store.atomic_write(root/'data.json', {})
        store.atomic_write(root/'assets.json', dict(assets=[]))
        store.atomic_write(root/'scene_config.json', DEFAULT_CONFIG)
        for sid in ('first', 'second'):
            record_review(root, 'scene_clip', 'v001', 'approve', scene_id=sid)

        def render(root, directory, session, context):
            path, code = directory/'picture.mp4', directory/'scene.py'
            code.write_text('# Local test renderer')
            run([binary('ffmpeg'), '-y', '-v', 'error', '-f', 'lavfi', '-i',
                 'color=c=blue:s=320x180:r=30', '-t', str(context['target_seconds']),
                 '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', str(path)])
            return path, dict(code_path=str(code.relative_to(root)))

        with patch.dict(os.environ, dict(AI_BACKEND='openrouter', OPENROUTER_MODEL=MODEL)), \
             patch('src.preview.av_workflow.CLIENT_FACTORY', return_value=TimingRouter()), \
             patch('src.preview.av_workflow.SYNTHESIZER', side_effect=self.tone), \
             patch('src.preview.av_workflow.CLIP_RENDERER', side_effect=render):
            av.generate(root, scene_id='first')
        project, _ = wf._load(root)
        artifact = wf._latest(project, 'scene_av', 'first')
        timing = next(v for v in artifact['bundle'].values() if v['path'].endswith('/timing.json'))
        segments = store.read_json(root/timing['path'])['segments']
        self.assertAlmostEqual(segments[1]['start']-segments[0]['end'], .22)
        frames, actual = self.read_wave(root/artifact['audio_path'])
        self.assertEqual(frames, round(artifact['duration']*48000))
        for segment in segments:
            count, original = self.read_wave(root/segment['audio']['path'])
            start = round(segment['start']*48000)
            self.assertEqual(actual[start*2:(start+count)*2], original)
        captions = (root/artifact['subtitle_path']).read_text()
        from src.preview.export import stamp
        for segment in segments:
            self.assertIn(stamp(segment['start'])+' --> '+stamp(segment['end']), captions)

    def test_pacing_version_retires_old_audio_preview_fingerprint(self):
        root = fixture(self.root/'project')
        project, reviews = wf._load(root)
        inputs = preview.snapshot(root, project, reviews, True)
        original = preview.fingerprint(inputs)
        with patch('src.preview.pacing.VERSION', pacing.VERSION+1):
            self.assertNotEqual(original, preview.fingerprint(inputs))


if __name__ == '__main__':
    unittest.main()
