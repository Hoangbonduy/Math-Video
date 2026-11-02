"""Keep animation at native speed; wait on beat endpoints for natural speech."""
import math
import re
from pathlib import Path
from src.shared import _workflow_store as store
from src.shared._workflow_store import WorkflowError
from src.shared.media_tools import binary, run, probe


def storyboard_reference(root, design):
    relative = design.get('storyboard_path')
    path = store.project_path(root, relative) if relative else store.project_path(root, design['path']).parent / 'storyboard.json'
    if path.is_file():
        return dict(path=str(path.relative_to(root)), sha256=store.digest(path))
    return None


# Bump when segmentation, silence or placement changes; raw TTS stays reusable.
VERSION = 2
LEAD_SECONDS = .05
TAIL_SECONDS = .06


def _story(root, source):
    reference = source.get('storyboard')
    if not reference:
        return None
    path = store.project_path(root, reference['path'])
    if store.digest(path) != reference['sha256']:
        raise WorkflowError('ARTIFACT_CHANGED', 'Storyboard changed while building speech.')
    return store.read_json(path)


def phrases(root, source, text):
    """Respect narration/beat boundaries before splitting long display phrases."""
    from src.preview import audio
    story = _story(root, source)
    if story is None:
        result = audio.chunks(text)
    else:
        excerpts = [beat['narration_excerpt'] for beat in story['beats']]
        if ' '.join(excerpts).split() != text.split():
            raise WorkflowError('PREVIEW_TIMING', 'Storyboard must match the complete narration in order.')
        result = [phrase for excerpt in excerpts for phrase in audio.chunks(excerpt)]
    if not result:
        raise WorkflowError('PREVIEW_INPUT', 'Cảnh thiếu lời dẫn.')
    return result


def pause_after(text):
    """Small extra breathing gaps after trimmed TTS audio."""
    ending = re.sub(r'[\s”’"\)\]»]+$', '', text)
    if ending.endswith(('.', '!', '?', '…')):
        return .14
    if ending.endswith((';', ':')):
        return .10
    if ending.endswith(','):
        return .06
    return .03


def schedule(segments, start=LEAD_SECONDS, trailing_pause=False):
    """Place complete audio, then breathing gaps, without estimating word time."""
    local = start
    for index, segment in enumerate(segments):
        seconds = segment['audio']['duration']
        if not math.isfinite(seconds) or seconds <= 0:
            raise WorkflowError('TTS_AUDIO', 'Speech duration must be positive and finite.')
        segment['start'] = local
        local += seconds
        segment['end'] = local
        gap = pause_after(segment['text']) if index < len(segments)-1 or trailing_pause else 0
        segment['pause_after'] = gap
        local += gap
    return local


def prepare(root, source, text, config, synthesizer, seconds=None, fps=30, retry_uncertain=None):
    """Shared speech preparation for listening samples and video previews."""
    texts = phrases(root, source, text)
    segments = []
    for text in texts:
        ref = (synthesizer(root, text, config) if retry_uncertain is None else
               synthesizer(root, text, config, retry_uncertain))
        segments.append(dict(text=text, audio=ref))
    beats = None
    if seconds is not None:
        beats = plan(root, source, texts, segments, seconds, fps)
        seconds = beats[-1]['end_frame'] / fps
    else:
        seconds = math.ceil((schedule(segments) + TAIL_SECONDS) * fps - 1e-6) / fps
    return dict(segments=segments, beats=beats, duration=seconds)


def _groups(story, phrases, seconds, tolerance):
    if story is None:
        return [dict(id='scene', end=seconds, phrase_end=len(phrases))]
    # Use declared narration boundaries, never guessed percentages of speech.
    words = [phrase.split() for phrase in phrases]
    cursor, previous, groups = 0, 0.0, []
    for beat in story['beats']:
        excerpt = beat['narration_excerpt'].split()
        collected, start = [], cursor
        while cursor < len(words) and len(collected) < len(excerpt):
            collected.extend(words[cursor]); cursor += 1
        if (not excerpt or collected != excerpt or cursor == start or
                abs(beat['start'] - previous) > 1e-6 or beat['end'] <= previous or
                beat['end'] > seconds + tolerance):
            raise WorkflowError('PREVIEW_TIMING', 'Storyboard beats must match complete narration phrases in order.')
        groups.append(dict(id=beat['id'], end=beat['end'], phrase_end=cursor))
        previous = beat['end']
    if cursor != len(words) or not groups or abs(previous - seconds) > tolerance:
        raise WorkflowError('PREVIEW_TIMING', 'Storyboard must cover the complete scene and narration.')
    # The encoded clip may extend the declared duration by a native frame.
    groups[-1]['end'] = seconds
    return groups


def plan(root, source, phrases, segments, seconds, fps):
    story = _story(root, source)
    tolerance = 1 / fps + 1e-6
    if source.get('clip'):
        info = probe(store.project_path(root, source['clip']['path']))
        video = next(s for s in info['streams'] if s['codec_type'] == 'video')
        numerator, denominator = map(float, video['avg_frame_rate'].split('/'))
        tolerance = max(tolerance, denominator / numerator + 1e-6)
    groups = _groups(story, phrases, seconds, tolerance)
    result, picture_start, output_start, phrase_start = [], 0, 0, 0
    picture_frames = round(seconds * fps)
    voice_cursor = LEAD_SECONDS
    for index, group in enumerate(groups):
        picture_end = min(picture_frames, round(group['end'] * fps))
        if picture_end <= picture_start:
            raise WorkflowError('PREVIEW_TIMING', 'A storyboard beat has no encoded picture frames.')
        spoken = segments[phrase_start:group['phrase_end']]
        tail = TAIL_SECONDS if index == len(groups) - 1 else 0
        beat_start_sec = output_start / fps + (LEAD_SECONDS if index == 0 else 0)
        start_sec = max(voice_cursor, beat_start_sec - (0.30 if index > 0 else 0.0))
        local = schedule(spoken, start_sec,
                         trailing_pause=index < len(groups)-1)
        voice_cursor = local
        voice_frames = max(0, math.ceil((local + tail) * fps - 1e-6) - output_start)
        moving_frames = picture_end - picture_start
        length = max(moving_frames, voice_frames)
        result.append(dict(beat_id=group['id'], source_start_frame=picture_start,
                           source_end_frame=picture_end, start_frame=output_start,
                           end_frame=output_start + length, hold_frames=length - moving_frames))
        picture_start, output_start, phrase_start = picture_end, output_start + length, group['phrase_end']
    return result


def speech_track(root, segments, output, seconds):
    """Place unmodified speech. Refuse overlapping or undersized output windows."""
    if not segments or not math.isfinite(seconds) or seconds <= 0:
        raise WorkflowError('PREVIEW_TIMING', 'Speech needs segments and a positive output duration.')
    args, filters = [binary('ffmpeg'), '-y', '-v', 'error'], []
    previous_end = 0.0
    for index, segment in enumerate(segments):
        start, end, dur = segment['start'], segment['end'], segment['audio']['duration']
        if (not all(math.isfinite(t) for t in (start, end, dur)) or dur <= 0 or
                start < previous_end - 1/48000 or end-start < dur - 1/48000 or
                start+dur > seconds + 1/48000):
            raise WorkflowError('PREVIEW_TIMING', 'Speech does not fit: extend the picture instead of speeding up or truncating audio.')
        previous_end = start + dur
        args += ['-i', str(store.project_path(root, segment['audio']['path']))]
        delay = round(start * 48000)
        filters.append(f'[{index}:a]aresample=48000,asetpts=PTS-STARTPTS,adelay={delay}S:all=1[a{index}]')
    inputs = ''.join(f'[a{i}]' for i in range(len(segments)))
    filters.append(inputs + f'amix=inputs={len(segments)}:normalize=0:dropout_transition=0,'
                   f'apad,atrim=end_sample={round(seconds * 48000)}[speech]')
    run(args + ['-filter_complex', ';'.join(filters), '-map', '[speech]',
                '-ar', '48000', '-ac', '1', '-c:a', 'pcm_s16le', str(output)])


def video_filter(base, beats, fps):
    """Insert time gaps at declared beat boundaries, then clone only those gaps."""
    additions = ''.join(f'+{beat["hold_frames"]}*gte(N\\,{beat["source_end_frame"]})'
                        for beat in beats[:-1] if beat['hold_frames'])
    # Some FFmpeg builds give tpad clones repeated PTS. Reset to an integer
    # frame time base and bound the tail by the planned output frame count.
    frames = beats[-1]['end_frame']
    return (f'[0:v]{base},tpad=stop_mode=clone:stop=-1,settb=1/{fps},'
            f'setpts=N{additions},fps={fps},trim=end_frame={frames}[picture]')
