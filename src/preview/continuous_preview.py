"""Continuous local previews. Only an explicit with_audio request synthesizes speech.

Preview files are separate from workflow artifacts and never approve a review.
"""
import copy
import hashlib
import math
from pathlib import Path
from src.shared import _workflow_store as store
from src.shared import workflow as wf
from src.preview import audio
from src.preview import export
from src.shared._workflow_store import WorkflowError
from src.shared.job_session import _job_lock, _json_bytes
from src.shared.media_tools import binary, run, probe, validate

BASE = 'generation/continuous_preview'
SYNTHESIZER = audio.synthesize
FPS = 30


def snapshot(root, project, reviews, with_audio=False):
    script = wf._latest(project, 'script', None)
    if not script or wf._request(reviews, script)['status'] != 'approved':
        raise WorkflowError('PREVIEW_INPUT', 'Duyệt kịch bản trước khi xem bản dựng.')
    if 'script.json' not in script.get('bundle', {}):
        raise WorkflowError('PREVIEW_INPUT', 'Bản dựng cần lời dẫn từ kịch bản có dữ liệu từng cảnh.')
    from src.scene_design.scene_drafts import current as current_drafts, verify as verify_draft
    drafts=current_drafts(root)
    sources = []
    for sid in project['scene_ids']:
        design = wf._latest(project, 'scene_design', sid)
        clip = wf._latest(project, 'scene_clip', sid)
        draft=drafts.get(sid) if not clip else None
        is_draft=False
        if draft:
            verify_draft(root,draft)
            files={f['path']:f for f in draft['files']}
            version=draft['job_id']
            design=dict(files[draft['design_path']],version=version)
            clip=dict(path=draft['reference']['path'],sha256=draft['reference']['sha256'],version=version)
            is_draft=True
        elif design and not clip and design.get('manim_render'):
            ref=design['manim_render'];clip=dict(path=ref['path'],sha256=ref['sha256'],version=design['version']);is_draft=True
        if not design or not clip:
            raise WorkflowError('PREVIEW_INPUT', 'Chưa có đủ video cảnh. Tạo hoặc chạy lại các cảnh còn thiếu trong Thiết kế cảnh.')
        sources.append(dict(scene_id=sid, design=copy.deepcopy(design), clip=copy.deepcopy(clip),draft=is_draft))
    if not sources:
        raise WorkflowError('PREVIEW_INPUT', 'Chưa có cảnh để ghép.')
    voice = {key: audio.settings(root)[key] for key in ('provider', 'voice', 'speed')} if with_audio else None
    return dict(script=copy.deepcopy(script), scenes=sources, voice=voice)


def fingerprint(inputs):
    # UI action revisions and review decisions do not change preview media.
    ref = lambda a: dict(version=a['version'], sha256=a['sha256'], path=a['path'])
    identity = dict(script=ref(inputs['script']), scenes=[dict(scene_id=s['scene_id'],
                    design=ref(s['design']), clip=ref(s['clip'])) for s in inputs['scenes']], voice=inputs['voice'], format_version=1)
    return hashlib.sha256(_json_bytes(identity)).hexdigest()


def _base_status(root, project=None):
    root = Path(root)
    if project is None:
        project, reviews = wf._load(root)
    else:
        _, reviews = wf._load(root)
    result = dict(ready=False, silent=None, audio=None, missing=[])
    try:
        inputs = snapshot(root, project, reviews)
    except WorkflowError as exc:
        result['message'] = str(exc)
        from src.scene_design.scene_drafts import current as current_drafts
        drafts=current_drafts(root)
        result['missing'] = [sid for sid in project['scene_ids'] if not wf._latest(project, 'scene_clip', sid) and sid not in drafts and not (wf._latest(project,'scene_design',sid) or {}).get('manim_render')]
        return result
    result['ready'] = True
    result['draft'] = any(s.get('draft') for s in inputs['scenes'])
    path = root / BASE / 'index.json'
    index = store.read_json(path) if path.exists() else {}
    for mode in ('silent', 'audio'):
        current = dict(inputs, voice={key: audio.settings(root)[key] for key in ('provider', 'voice', 'speed')} if mode == 'audio' else None)
        item = index.get(mode)
        if item and item.get('fingerprint') == fingerprint(current):
            if all(store.project_path(root, item[key]).is_file() for key in ('path', 'subtitle_path')):
                result[mode] = item
    return result


def _seconds(path):
    data = probe(path)
    stream = next(s for s in data['streams'] if s['codec_type'] == 'video')
    return float(stream.get('duration', data['format']['duration']))


def _encode(source, output, seconds, resolution, speech=None):
    original = _seconds(source)
    width, height = resolution
    vf = (f'setpts={seconds / original:.12f}*(PTS-STARTPTS),'
          f'scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2,'
          f'pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=0x0D1726,setsar=1,fps={FPS},'
          f'tpad=stop_mode=clone:stop_duration={seconds},trim=duration={seconds}')
    args = [binary('ffmpeg'), '-y', '-v', 'error', '-i', str(source)]
    if speech:
        args += ['-i', str(speech)]
    args += ['-vf', vf, '-map', '0:v:0', '-c:v', 'libx264', '-preset', 'fast', '-crf', '20', '-pix_fmt', 'yuv420p']
    if speech:
        args += ['-map', '1:a:0', '-af', 'apad', '-c:a', 'aac', '-b:a', '128k', '-ar', '48000', '-ac', '1']
    else:
        args += ['-an']
    run(args + ['-t', str(seconds), '-movflags', '+faststart', str(output)])
    validate(output, audio=bool(speech), resolution=list(resolution), fps=FPS)


def _generate_base(project_dir, with_audio=False):
    if type(with_audio) is not bool:
        raise WorkflowError('INVALID_INPUT', 'with_audio phải là boolean.')
    root = Path(project_dir).resolve()
    mode = 'audio' if with_audio else 'silent'
    with _job_lock(root):
        with store.locked_project(root):
            project, reviews = wf._load(root)
            inputs = snapshot(root, project, reviews, with_audio)
            for item in [inputs['script']] + [a for s in inputs['scenes'] for a in (s['design'], s['clip'])]:
                wf._verify(root, item)
            cached = _base_status(root, project).get(mode)
            if cached:
                if store.digest(root / cached['path']) != cached['sha256'] or store.digest(root / cached['subtitle_path']) != cached['subtitle_sha256']:
                    raise WorkflowError('CACHE_CORRUPT', 'Bản xem trước đã bị thay đổi. Kiểm tra tệp trên máy.')
                return cached
            payload = store.read_json(store.project_path(root, inputs['script'].get('bundle', {}).get('script.json', {}).get('path', '')))
            narration = {s['scene_id']: s for s in payload['script']['scenes']}
            config = audio.settings(root)
            if with_audio:
                audio.validate_settings(config)
                if config['provider']=='fpt' and not config['allow_unpriced'] and (not config['vnd_per_million_chars'] or not config['vnd_per_usd']):
                    raise WorkflowError('TTS_PRICING_REQUIRED', 'Mở Cấu hình giọng đọc bên dưới, điền đơn giá FPT và tỷ giá rồi lưu trước khi thêm tiếng.')
        key = fingerprint(inputs)
        directory = root / BASE / key
        directory.mkdir(parents=True, exist_ok=True)
        first = probe(store.project_path(root, inputs['scenes'][0]['clip']['path']))
        video = next(s for s in first['streams'] if s['codec_type'] == 'video')
        resolution = (360, 640) if video['width'] < video['height'] else (640, 360)
        scenes, cues, clips, offset = [], [], [], 0.0
        for index, source in enumerate(inputs['scenes']):
            sid = source['scene_id']
            text = narration[sid]['narration']
            phrases = audio.chunks(text)
            if not phrases:
                raise WorkflowError('PREVIEW_INPUT', 'Cảnh thiếu lời dẫn: ' + sid)
            source_path = store.project_path(root, source['clip']['path'])
            speech = None
            if with_audio:
                segments, local = [], .15
                for phrase in phrases:
                    ref = SYNTHESIZER(root, phrase, config)
                    segments.append(dict(text=phrase, start=local, end=local + ref['duration'], audio=ref))
                    local += ref['duration']
                seconds = math.ceil((local + .15) * FPS) / FPS
                speech = directory / f'scene_{index:03}.wav'
                export.concat_audio([root / s['audio']['path'] for s in segments], speech, seconds)
            else:
                seconds = math.ceil(_seconds(source_path) * FPS - 1e-6) / FPS
                total = sum(len(t.split()) for t in phrases)
                segments, local = [], 0.0
                for phrase in phrases:
                    end = local + seconds * len(phrase.split()) / total
                    segments.append(dict(text=phrase, start=local, end=end))
                    local = end
            cues.extend(dict(text=s['text'], start=offset+s['start'], end=offset+s['end'], scene_id=sid) for s in segments)
            clip = directory / f'scene_{index:03}.mp4'
            _encode(source_path, clip, seconds, resolution, speech)
            clips.append(clip)
            scenes.append(dict(scene_id=sid, title=narration[sid].get('title') or f'Cảnh {index+1:02}', start=offset, end=offset+seconds))
            offset += seconds
        captions = directory / 'captions.srt'
        captions.write_text('\n\n'.join(f'{i}\n{export.stamp(c["start"])} --> {export.stamp(c["end"])}\n{c["text"]}' for i, c in enumerate(cues, 1)) + '\n', encoding='utf-8')
        listing = directory / 'clips.txt'
        # Generated filenames only; no user text is interpolated into the concat list.
        listing.write_text(''.join(f"file '{p.name}'\n" for p in clips), encoding='utf-8')
        output = directory / 'preview.mp4'
        temporary = directory / 'preview.partial.mp4'
        try:
            run([binary('ffmpeg'), '-y', '-v', 'error', '-f', 'concat', '-safe', '1', '-i', str(listing),
                 '-i', str(captions), '-map', '0:v:0', '-map', '0:a?', '-map', '1:0', '-c:v', 'copy', '-c:a', 'copy',
                 '-c:s', 'mov_text', '-metadata:s:s:0', 'language=vie', '-t', str(offset), '-movflags', '+faststart', str(temporary)])
            validate(temporary, audio=with_audio, resolution=list(resolution), fps=FPS)
            temporary.replace(output)
        finally:
            temporary.unlink(missing_ok=True)
        result = dict(path=str(output.relative_to(root)), sha256=store.digest(output),
                      subtitle_path=str(captions.relative_to(root)), subtitle_sha256=store.digest(captions),
                      fingerprint=key, with_audio=with_audio, draft=any(s.get('draft') for s in inputs['scenes']), duration=offset, scenes=scenes, cues=cues,
                      caption_timing='speech_phrases' if with_audio else 'estimated', resolution=list(resolution))
        if with_audio:
            narration_wav=directory/'narration.wav'
            export.concat_audio([directory/f'scene_{i:03}.wav' for i in range(len(scenes))],narration_wav)
            result.update(narration_path=str(narration_wav.relative_to(root)),narration_sha256=store.digest(narration_wav))
        with store.locked_project(root):
            project, reviews = wf._load(root)
            if fingerprint(snapshot(root, project, reviews, with_audio)) != key:
                raise WorkflowError('STALE_JOB', 'Kịch bản, cảnh hoặc giọng đọc đã đổi. Hãy dựng lại bản xem trước.')
            path = root / BASE / 'index.json'
            index = store.read_json(path) if path.exists() else {}
            index[mode] = result
            store.atomic_write(path, index)
        return result


def status(root, project=None):
    from src.preview import music
    root = Path(root)
    result = _base_status(root, project)
    base = result['audio']
    result['voice_ready'] = bool(base)
    result['audio'] = None
    if base:
        path = root / BASE / 'mix_index.json'
        item = store.read_json(path) if path.exists() else None
        try:
            selection = music.snapshot(root, project)
        except WorkflowError as exc:
            result['music_error'] = str(exc)
            return result
        if item and item.get('base_fingerprint') == base['fingerprint'] and item.get('music') == selection:
            if all((root/item[key]).is_file() for key in ('path','audio_path','narration_path','subtitle_path')):
                result['audio'] = item
    return result


def generate(project_dir, with_audio=False):
    from src.preview import music
    root = Path(project_dir).resolve()
    base = _generate_base(root, with_audio)
    if not with_audio:
        return base
    with _job_lock(root):
        with store.locked_project(root):
            project, reviews = wf._load(root)
            if fingerprint(snapshot(root,project,reviews,True)) != base['fingerprint']:
                raise WorkflowError('STALE_JOB','Giọng hoặc cảnh đã đổi; hãy dựng lại bản có tiếng.')
            selection = music.snapshot(root, project)
        result = music.apply(root, base, selection)
        with store.locked_project(root):
            project, reviews = wf._load(root)
            if music.snapshot(root,project) != selection or fingerprint(snapshot(root,project,reviews,True)) != base['fingerprint']:
                raise WorkflowError('STALE_JOB','Cấu hình đã đổi trong khi phối nhạc.')
            store.atomic_write(root/BASE/'mix_index.json', result)
        return result
