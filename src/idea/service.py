"""Persist the opening form and use it as the input of a new workflow cycle."""

from src.shared.paths import PROJECT_ROOT
import copy
import re

from src.shared import _workflow_store as store
from src.shared import workflow as wf
from src.shared._workflow_store import WorkflowError
from src.shared.ai_config import normalize_brief
from src.scene_design.design import DEFAULT_CONFIG

FIELDS = ('topic', 'goal', 'audience', 'duration', 'expression', 'style', 'format', 'reference')


def draft_values(values):
    if not isinstance(values, dict) or set(values) - set(FIELDS):
        raise WorkflowError('INVALID_INPUT', 'Dữ liệu ý tưởng không hợp lệ.')
    result = {}
    for key, value in values.items():
        if not isinstance(value, str) or len(value) > 2000:
            raise WorkflowError('INVALID_INPUT', 'Mỗi mục ý tưởng tối đa 2.000 ký tự.')
        result[key] = value.strip()
    return result


def default_values():
    from pathlib import Path
    path = PROJECT_ROOT / 'settings' / 'idea_defaults.json'
    values = dict(topic='', duration='30–60 giây', format='landscape')
    if path.exists():
        values.update(draft_values(store.read_json(path)))
    return values


def inputs(values):
    idea = draft_values(values)
    if not idea.get('topic'):
        raise WorkflowError('INVALID_INPUT', 'Hãy nhập chủ đề / câu hỏi mở đầu.')
    match = re.fullmatch(r'(\d+)\s*[-–—]\s*(\d+)\s*(?:giây|s)?', idea.get('duration', '30–60 giây'))
    if not match:
        raise WorkflowError('INVALID_INPUT', 'Độ dài cần có dạng 30–60 giây.')
    requirements = '\n'.join(text for text in (
        'Ý tưởng kịch bản (6 dòng):\n' + idea['goal'] if idea.get('goal') else '',
        'Cách thể hiện: ' + idea['expression'] if idea.get('expression') else '',
        'Cách hiện bằng Manim (2 dòng):\n' + idea['style'] if idea.get('style') else '',
    ) if text)
    brief = normalize_brief(dict(min_seconds=int(match[1]), max_seconds=int(match[2]),
                                 **({'audience': idea['audience']} if idea.get('audience') else {}),
                                 **({'requirements': requirements} if requirements else {})))
    if idea.get('format', 'landscape') not in ('landscape', 'portrait'):
        raise WorkflowError('INVALID_INPUT', 'Hãy chọn video ngang hoặc dọc.')
    idea.setdefault('format', 'landscape')
    return idea, brief


def save_draft(project_dir, values):
    idea = draft_values(values)
    with store.locked_project(project_dir) as root:
        project, reviews = wf._load(root)
        if project.get('idea_draft') != idea:
            project['idea_draft'] = idea
            wf._save(root, project, reviews)


def apply_idea(project_dir, values, *, force_new=False):
    idea, brief = inputs(values)
    with store.locked_project(project_dir) as root:
        project, reviews = wf._load(root)
        if project.get('idea') == idea and not force_new:
            if project.get('idea_draft') != idea:
                project['idea_draft'] = idea
                wf._save(root, project, reviews)
            return  # Double clicks/retries keep the current generation and approvals.
        project.setdefault('idea_history', []).append(dict(
            idea=project.get('idea'), topic=project['topic'], brief=project.get('brief'),
            revision=project['revision'], archived_at=wf._now()))
        for artifact in project['artifacts']:
            artifact['invalidated'] = True
            request = wf._request(reviews, artifact)
            if request['status'] == 'pending':
                request['status'] = 'superseded'
        for revision in reviews.get('scene_revisions', []):
            if revision['status'] not in ('completed', 'cancelled'):
                revision['status'] = 'cancelled'
        # Keep artifacts, versions and paid-call ledgers for history and accounting.
        project.update(topic=idea['topic'], brief=brief, idea=idea, idea_draft=idea,
                       stage='script', scene_ids=[], c7_enabled=True)
        for key in ('generation', 'assembly_manifest', 'av_manifest', 'voice_sample', 'design_previews', 'shared_resources'):
            project.pop(key, None)
        config_path = root / 'scene_config.json'
        config = store.read_json(config_path) if config_path.exists() else copy.deepcopy(DEFAULT_CONFIG)
        width, height = sorted(config['render']['resolution'], reverse=True)
        config['render']['resolution'] = [height, width] if idea['format'] == 'portrait' else [width, height]
        store.atomic_write(config_path, config)
        published = root / 'final.mp4'
        if published.exists():
            archive = root / 'finals' / f'published_before_idea_{project["revision"]}.mp4'
            archive.parent.mkdir(exist_ok=True)
            published.rename(archive)
        wf._save(root, project, reviews)


def final_resolution(project):
    return [1080, 1920] if project.get('idea', {}).get('format') == 'portrait' else [1920, 1080]


def _write_text(path, text):
    from tempfile import NamedTemporaryFile
    from pathlib import Path
    import os
    temporary = None
    try:
        with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as f:
            temporary = Path(f.name)
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
        store._sync_directory(path.parent)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def edit_script(project_dir, version=None, scenes=None, *, scene_id=None, narration=None, visual_intent=None, expected_sha256=None):
    from src.idea.content import markdown

    if scenes is None:
        scenes = []
    if scene_id is not None:
        item = dict(scene_id=scene_id)
        if narration is not None:
            item['narration'] = narration
        if visual_intent is not None:
            item['visual_intent'] = visual_intent
        scenes = [item]

    if not isinstance(scenes, list) or not scenes:
        raise WorkflowError('INVALID_INPUT', 'Cần cung cấp nội dung cảnh để chỉnh sửa.')

    with store.locked_project(project_dir) as root:
        project, reviews = wf._load(root)
        if project['stage'] != 'script':
            raise WorkflowError('WRONG_STAGE', f"Hiện đang ở bước {project['stage']}, không thể chỉnh sửa kịch bản.")
        artifact = wf._latest(project, 'script', None)
        if not artifact or artifact.get('deleted_at') or artifact.get('invalidated'):
            raise WorkflowError('SCRIPT_NOT_FOUND', 'Không tìm thấy kịch bản hiện tại.')
        if version and artifact['version'] != version:
            raise WorkflowError('STALE_REVIEW', 'Phiên bản kịch bản không khớp. Hãy tải lại trang.')
        if expected_sha256 and artifact['sha256'] != expected_sha256:
            raise WorkflowError('STALE_REVIEW', 'Kịch bản đã thay đổi. Hãy tải lại trang.')
        review = wf._request(reviews, artifact)
        if review['status'] not in ('pending', 'changes_requested'):
            raise WorkflowError('REVIEW_LOCKED', 'Kịch bản đã được duyệt hoặc xử lý, không thể chỉnh sửa trực tiếp.')
        if 'script.json' not in artifact.get('bundle', {}):
            raise WorkflowError('STRUCTURED_SCRIPT_REQUIRED', 'Không tìm thấy tệp script.json để chỉnh sửa.')

        wf._verify(root, artifact)

        script_rel = artifact['bundle']['script.json']['path']
        script_path = store.project_path(root, script_rel)
        payload = store.read_json(script_path)
        script_data = payload.get('script', {})
        current_scenes = script_data.get('scenes', [])
        scene_map = {s['scene_id']: s for s in current_scenes}

        for update in scenes:
            if not isinstance(update, dict):
                raise WorkflowError('INVALID_INPUT', 'Dữ liệu chỉnh sửa cảnh không hợp lệ.')
            sid = update.get('scene_id')
            if not sid or sid not in scene_map:
                raise WorkflowError('INVALID_INPUT', f'Không tìm thấy cảnh {sid}.')
            target = scene_map[sid]
            if 'narration' in update:
                narr = update['narration']
                if not isinstance(narr, str) or not narr.strip():
                    raise WorkflowError('INVALID_INPUT', f'Lời đọc của cảnh {sid} không được để trống.')
                target['narration'] = narr.strip()
            if 'visual_intent' in update:
                vis = update['visual_intent']
                if not isinstance(vis, str) or not vis.strip():
                    raise WorkflowError('INVALID_INPUT', f'Mô tả hình ảnh của cảnh {sid} không được để trống.')
                target['visual_intent'] = vis.strip()

        brief = project.get('brief') or payload.get('brief', {})
        rate = brief.get('read_units_per_second') or payload.get('duration_estimate', {}).get('read_units_per_second') or 3.5
        all_narration = " ".join(s['narration'].strip() for s in current_scenes)
        units = len(all_narration.split())
        seconds = units / rate
        duration = dict(
            narration=all_narration,
            estimated_seconds=seconds,
            whitespace_units=units,
            read_units_per_second=rate,
            estimate_only=True
        )
        payload['duration_estimate'] = duration
        payload['script'] = script_data

        store.atomic_write(script_path, payload)
        artifact['bundle']['script.json']['sha256'] = store.digest(script_path)

        sources = []
        research_rel = artifact.get('bundle', {}).get('research.json', {}).get('path')
        if research_rel:
            research_path = store.project_path(root, research_rel)
            if research_path.is_file():
                sources = store.read_json(research_path).get('sources', [])

        md_text = markdown(script_data, sources, duration)
        md_path = store.project_path(root, artifact['path'])
        _write_text(md_path, md_text)
        artifact['sha256'] = store.digest(md_path)

        if artifact.get('job_id'):
            gen_md = store.project_path(root, f"generation/{artifact['job_id']}/script.md")
            if gen_md.exists():
                _write_text(gen_md, md_text)

        artifact['duration_estimate'] = duration
        wf._save(root, project, reviews)
        return dict(ok=True, version=artifact['version'], sha256=artifact['sha256'], duration_estimate=duration)
