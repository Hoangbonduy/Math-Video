"""Local, allowlisted background music and deterministic full-video mixing."""
import copy
import hashlib
import json
import math
from pathlib import Path

from src.shared import _workflow_store as store
from src.shared._workflow_store import WorkflowError
from src.shared.job_session import _job_lock, _json_bytes
from src.shared.media_tools import binary, run, duration, validate
from src.shared.paths import PROJECT_ROOT

MUSIC_ROOT = PROJECT_ROOT / 'music'
DEFAULTS = dict(track_id=None, volume=10, ducking=True)
RECIPE_VERSION = 1


def catalog():
    path = MUSIC_ROOT / 'catalog.json'
    if not path.is_file():
        return []
    return store.read_json(path)['tracks']


def track(track_id):
    item = next((item for item in catalog() if item['id'] == track_id), None)
    if item is None:
        raise WorkflowError('MUSIC_NOT_FOUND', 'Bài nhạc không có trong thư viện.')
    path = MUSIC_ROOT / item['filename']
    if path.is_symlink() or path.resolve().parent != MUSIC_ROOT.resolve() or not path.is_file():
        raise WorkflowError('MUSIC_NOT_FOUND', 'Không tìm thấy tệp nhạc hợp lệ trong thư viện.')
    if store.digest(path) != item['sha256']:
        raise WorkflowError('MUSIC_CHANGED', 'Tệp nhạc đã thay đổi. Kiểm tra lại thư viện nhạc.')
    return item, path


def settings(root, project=None):
    if project is None:
        project = store.read_json(Path(root) / 'project.json')
    return dict(DEFAULTS, **project.get('music_config', {}))


def validate_settings(config):
    if not isinstance(config, dict) or set(config) != set(DEFAULTS):
        raise WorkflowError('INVALID_INPUT', 'Cấu hình nhạc không hợp lệ.')
    volume = config['volume']
    if type(volume) not in (int, float) or not math.isfinite(volume) or not 0 <= volume <= 30 or type(config['ducking']) is not bool:
        raise WorkflowError('INVALID_INPUT', 'Âm lượng nhạc cần từ 0 đến 30%; tự hạ nhạc phải là boolean.')
    if config['track_id'] is not None:
        if not isinstance(config['track_id'], str):
            raise WorkflowError('INVALID_INPUT', 'Mã bài nhạc không hợp lệ.')
        track(config['track_id'])


def snapshot(root, project=None):
    config = settings(root, project)
    validate_settings(config)
    item = track(config['track_id'])[0] if config['track_id'] else None
    return dict(config=config, track=copy.deepcopy(item), recipe_version=RECIPE_VERSION)


def state(root):
    return dict(config=settings(root), tracks=catalog())


def configure(root, config):
    from src.shared import workflow as wf
    root = Path(root).resolve()
    validate_settings(config)
    with _job_lock(root), store.locked_project(root):
        project, reviews = wf._load(root)
        if project['stage'] == 'completed':
            raise WorkflowError('WRONG_STAGE', 'Dự án đã hoàn tất chỉ được xem.')
        if settings(root, project) == config:
            return
        # A music edit affects only the whole-video mix, never scene approvals.
        for artifact in project['artifacts']:
            if artifact['stage'] == 'final_video' and not artifact.get('invalidated'):
                artifact['invalidated'] = True
                request = wf._request(reviews, artifact)
                if request['status'] == 'pending':
                    request['status'] = 'superseded'
        project.pop('av_manifest', None)
        project['music_config'] = dict(config)
        wf._save(root, project, reviews)


def _normalize(source, target):
    """Two-pass loudness measurement keeps quiet music/voices comparable."""
    import subprocess
    result = subprocess.run([binary('ffmpeg'), '-nostdin', '-v', 'info', '-i', str(source),
        '-vn', '-af', 'loudnorm=I=-16:TP=-1:LRA=11:print_format=json', '-f', 'null', '-'],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=600, check=True)
    report = result.stderr.decode(errors='replace')
    measured = json.loads(report[report.rfind('{'):report.rfind('}') + 1])
    values = {key: float(measured[key]) for key in ('input_i', 'input_tp', 'input_lra', 'input_thresh', 'target_offset')}
    if all(math.isfinite(value) for value in values.values()):
        filt = ('loudnorm=I=-16:TP=-1:LRA=11:linear=true'
                f":measured_I={values['input_i']}:measured_TP={values['input_tp']}"
                f":measured_LRA={values['input_lra']}:measured_thresh={values['input_thresh']}"
                f":offset={values['target_offset']}")
    else:
        # Silence has -inf loudness; preserve it rather than generating invalid filters.
        filt = 'anull'
    run([binary('ffmpeg'), '-y', '-v', 'error', '-i', str(source), '-vn', '-af', filt,
         '-ar', '48000', '-ac', '2', '-c:a', 'pcm_s16le', str(target)])


def mix_audio(narration, output, seconds, selection):
    """Normalize stems; crossfade music loops, duck under speech, limit peaks."""
    import tempfile
    config = selection['config']
    with tempfile.TemporaryDirectory(prefix='video_music_') as directory:
        directory = Path(directory)
        voice = directory / 'voice.wav'
        _normalize(narration, voice)
        args = [binary('ffmpeg'), '-y', '-v', 'error', '-i', str(voice)]
        if selection['track'] and config['volume'] > 0:
            item, source = track(config['track_id'])
            if item['sha256'] != selection['track']['sha256']:
                raise WorkflowError('STALE_JOB', 'Bài nhạc đã thay đổi trong khi phối.')
            normalized = directory / 'music.wav'
            _normalize(source, normalized)
            span = duration(normalized)
            if span <= 0:
                raise WorkflowError('MUSIC_AUDIO', 'Bài nhạc không có âm thanh.')
            overlap = min(.5, span / 4)
            # Cache one seamless cycle: body followed by the overlapped tail/head.
            cycle = directory / 'cycle.wav'
            graph = (f'[0:a]asplit=3[x][y][z];[x]atrim={overlap}:{span-overlap},asetpts=PTS-STARTPTS[body];'
                     f'[y]atrim={span-overlap}:{span},asetpts=PTS-STARTPTS[tail];'
                     f'[z]atrim=0:{overlap},asetpts=PTS-STARTPTS[head];'
                     f'[tail][head]acrossfade=d={overlap}:c1=tri:c2=tri[join];'
                     '[body][join]concat=n=2:v=0:a=1[cycle]')
            run([binary('ffmpeg'), '-y', '-v', 'error', '-i', str(normalized), '-filter_complex', graph,
                 '-map', '[cycle]', '-c:a', 'pcm_s16le', str(cycle)])
            args += ['-stream_loop', '-1', '-i', str(cycle)]
            fade_in, fade_out = min(1, seconds/2), min(2, seconds/2)
            filters = [f'[1:a]atrim=duration={seconds},asetpts=PTS-STARTPTS,volume={config["volume"]/100},'
                       f'afade=t=in:d={fade_in},afade=t=out:st={seconds-fade_out}:d={fade_out}[bed]']
            if config['ducking']:
                filters += ['[0:a]asplit=2[voice][side]',
                            '[bed][side]sidechaincompress=threshold=0.02:ratio=8:attack=20:release=500[duck]',
                            '[voice][duck]amix=inputs=2:duration=first:normalize=0[mix]']
            else:
                filters += ['[0:a][bed]amix=inputs=2:duration=first:normalize=0[mix]']
            filters += ['[mix]alimiter=limit=0.89125:level=false:latency=true,apad[out]']
            args += ['-filter_complex', ';'.join(filters), '-map', '[out]']
        else:
            args += ['-map', '0:a:0', '-af', 'alimiter=limit=0.89125:level=false:latency=true,apad']
        run(args + ['-t', str(seconds), '-ar', '48000', '-ac', '2', '-c:a', 'pcm_s16le', str(output)])


def apply(root, base, selection):
    """Remix a cached speech movie without synthesizing or re-encoding its picture."""
    root = Path(root)
    identity = dict(base=base['sha256'], narration=base.get('narration_sha256'), music=selection, recipe_version=RECIPE_VERSION)
    key = hashlib.sha256(_json_bytes(identity)).hexdigest()
    directory = root / 'generation/music_mixes' / key
    directory.mkdir(parents=True, exist_ok=True)
    meta = directory / 'result.json'
    if meta.exists():
        result = store.read_json(meta)
        for name in ('path', 'audio_path', 'narration_path', 'subtitle_path'):
            checksum = name.removesuffix('_path') + '_sha256' if name != 'path' else 'sha256'
            if not (root/result[name]).is_file() or store.digest(root/result[name]) != result[checksum]:
                raise WorkflowError('CACHE_CORRUPT', 'Bản phối đã lưu bị thay đổi.')
        # Subtitle paths can differ between versions of the same final picture.
        return dict(result, subtitle_path=base['subtitle_path'], subtitle_sha256=base['subtitle_sha256'])
    narration = directory / 'narration.wav'
    if base.get('narration_path'):
        import shutil
        source = root/base['narration_path']
        if not source.is_file() or store.digest(source) != base['narration_sha256']:
            raise WorkflowError('CACHE_CORRUPT', 'Lời đọc gốc đã thay đổi.')
        shutil.copyfile(source, narration)
    else:
        run([binary('ffmpeg'), '-y', '-v', 'error', '-i', str(root/base['path']), '-vn',
             '-c:a', 'pcm_s16le', '-ar', '48000', str(narration)])
    mixed = directory / 'mix.wav'
    mix_audio(narration, mixed, base['duration'], selection)
    output, temporary = directory / 'preview.mp4', directory / 'preview.partial.mp4'
    try:
        run([binary('ffmpeg'), '-y', '-v', 'error', '-i', str(root/base['path']), '-i', str(mixed),
             '-map', '0:v:0', '-map', '1:a:0', '-map', '0:s?', '-c:v', 'copy', '-c:a', 'aac',
             '-b:a', '192k', '-ar', '48000', '-c:s', 'copy', '-t', str(base['duration']),
             '-movflags', '+faststart', str(temporary)])
        validate(temporary, audio=True, resolution=base['resolution'], fps=30)
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    result = dict(base, path=str(output.relative_to(root)), sha256=store.digest(output),
                  audio_path=str(mixed.relative_to(root)), audio_sha256=store.digest(mixed),
                  narration_path=str(narration.relative_to(root)), narration_sha256=store.digest(narration),
                  fingerprint=key, base_fingerprint=base['fingerprint'], music=selection)
    store.atomic_write(directory/'manifest.json', identity)
    result['manifest_path'] = str((directory/'manifest.json').relative_to(root))
    store.atomic_write(meta, result)
    return result
