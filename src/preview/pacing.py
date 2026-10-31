"""Keep animation at native speed; wait on beat endpoints for natural speech."""
import math
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
    reference = source.get('storyboard')
    story = None
    if reference:
        path = store.project_path(root, reference['path'])
        if store.digest(path) != reference['sha256']:
            raise WorkflowError('ARTIFACT_CHANGED', 'Storyboard changed while building the preview.')
        story = store.read_json(path)
    tolerance = 1 / fps + 1e-6
    if source.get('clip'):
        info = probe(store.project_path(root, source['clip']['path']))
        video = next(s for s in info['streams'] if s['codec_type'] == 'video')
        numerator, denominator = map(float, video['avg_frame_rate'].split('/'))
        tolerance = max(tolerance, denominator / numerator + 1e-6)
    groups = _groups(story, phrases, seconds, tolerance)
    result, picture_start, output_start, phrase_start = [], 0, 0, 0
    picture_frames = round(seconds * fps)
    for index, group in enumerate(groups):
        picture_end = min(picture_frames, round(group['end'] * fps))
        if picture_end <= picture_start:
            raise WorkflowError('PREVIEW_TIMING', 'A storyboard beat has no encoded picture frames.')
        spoken = segments[phrase_start:group['phrase_end']]
        lead = .15 if index == 0 else 0
        tail = .15 if index == len(groups) - 1 else 0
        local = output_start / fps + lead
        for segment in spoken:
            segment['start'] = local
            local += segment['audio']['duration']
            segment['end'] = local
        voice_frames = math.ceil((local + tail) * fps - 1e-6) - output_start
        moving_frames = picture_end - picture_start
        length = max(moving_frames, voice_frames)
        result.append(dict(beat_id=group['id'], source_start_frame=picture_start,
                           source_end_frame=picture_end, start_frame=output_start,
                           end_frame=output_start + length, hold_frames=length - moving_frames))
        picture_start, output_start, phrase_start = picture_end, output_start + length, group['phrase_end']
    return result


def speech_track(root, segments, output, seconds):
    """Place cached speech unchanged; silence fills any waits for the picture."""
    args, filters = [binary('ffmpeg'), '-y', '-v', 'error'], []
    for index, segment in enumerate(segments):
        args += ['-i', str(store.project_path(root, segment['audio']['path']))]
        delay = round(segment['start'] * 48000)
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
