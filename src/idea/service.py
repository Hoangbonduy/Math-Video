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
        'Mục tiêu người xem: ' + idea['goal'] if idea.get('goal') else '',
        'Cách thể hiện: ' + idea['expression'] if idea.get('expression') else '',
        'Phong cách: ' + idea['style'] if idea.get('style') else '',
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
