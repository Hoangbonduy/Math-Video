"""Browse and delete script versions persisted in the local project."""
from src.shared import _workflow_store as store
from src.shared import workflow as wf
from src.shared._workflow_store import WorkflowError

MAX_SCRIPT_BYTES = 2_000_000


def _file(root, path):
    target = store.project_path(root, path)
    if target.is_symlink() or not target.is_file():
        raise WorkflowError('ARTIFACT_MISSING', 'Không tìm thấy tệp kịch bản đã lưu.')
    if target.stat().st_size > MAX_SCRIPT_BYTES:
        raise WorkflowError('INVALID_INPUT', 'Tệp kịch bản quá lớn để xem trên giao diện.')
    return target


def _json(root, artifact, name):
    component = artifact.get('bundle', {}).get(name)
    if not component:
        return {}
    value = store.read_json(_file(root, component['path']))
    return value if isinstance(value, dict) else {}


def _entry(root, artifact, reviews, current):
    review = wf._request(reviews, artifact)
    title = artifact.get('title') or 'Kịch bản ' + artifact['version']
    error = None
    try:
        wf._verify(root, artifact)
        content = _json(root, artifact, 'script.json')
        title = content.get('script', {}).get('title') or title
        if not content:
            first = _file(root, artifact['path']).read_text(encoding='utf-8').splitlines()
            title = next((line.lstrip('# ').strip() for line in first if line.startswith('# ')), title)
    except (WorkflowError, OSError, UnicodeError) as exc:
        error = str(exc)
    return dict(version=artifact['version'], title=title, created_at=artifact.get('created_at'),
                status=review['status'], current=artifact['version'] == current,
                archived=bool(artifact.get('invalidated')), origin=artifact.get('origin', 'mock' if artifact.get('mock') else 'manual'),
                path=artifact['path'], sha256=artifact['sha256'], available=error is None, error=error)


def history(project_dir):
    with store.locked_project(project_dir) as root:
        project, reviews = wf._load(root)
        current = wf._latest(project, 'script', None)
        items = [_entry(root, a, reviews, current['version'] if current else None)
                 for a in reversed(project['artifacts']) if a['stage'] == 'script' and not a.get('deleted_at')]
        return dict(items=items, project_dir=str(root), storage_path=str(root / 'scripts'),
                    project_id=project['project_id'])


def detail(project_dir, version):
    with store.locked_project(project_dir) as root:
        project, reviews = wf._load(root)
        artifact = next((a for a in project['artifacts']
                         if a['stage'] == 'script' and a['version'] == version and not a.get('deleted_at')), None)
        if artifact is None:
            raise WorkflowError('SCRIPT_NOT_FOUND', 'Không tìm thấy phiên bản kịch bản này.')
        current = wf._latest(project, 'script', None)
        item = _entry(root, artifact, reviews, current['version'] if current else None)
        if not item['available']:
            raise WorkflowError('ARTIFACT_CHANGED', item['error'])
        content = _json(root, artifact, 'script.json')
        generation = _json(root, artifact, 'generation.json')
        research = _json(root, artifact, 'research.json')
        item.update(script=content.get('script', {}), brief=content.get('brief', {}),
                    duration_estimate=content.get('duration_estimate', {}),
                    markdown=_file(root, artifact['path']).read_text(encoding='utf-8'),
                    sources=research.get('sources', []), model=generation.get('model'),
                    json_path=artifact.get('bundle', {}).get('script.json', {}).get('path'))
        return item


def delete(project_dir, version, expected_sha256):
    """Keep a version tombstone and billing records; remove the saved script files.

    The state change is durable before unlinking files. Repeating the same delete
    finishes cleanup after interruption without restoring or reusing the version.
    """
    with store.locked_project(project_dir) as root:
        project, reviews = wf._load(root)
        artifact = next((a for a in project['artifacts']
                         if a['stage'] == 'script' and a['version'] == version), None)
        if artifact is None:
            raise WorkflowError('SCRIPT_NOT_FOUND', 'Không tìm thấy phiên bản kịch bản này.')
        if not isinstance(expected_sha256, str) or expected_sha256 != artifact['sha256']:
            raise WorkflowError('ARTIFACT_CHANGED', 'Kịch bản đã thay đổi. Tải lại lịch sử trước khi xóa.')

        # Only unlink the registered script content, never a directory or an
        # arbitrary client-supplied path. Source and paid-call logs stay intact.
        paths = {artifact['path']}
        script = artifact.get('bundle', {}).get('script.json')
        if script:
            paths.add(script['path'])
            from pathlib import Path
            job_id = artifact.get('job_id')
            if (isinstance(job_id, str) and Path(job_id).name == job_id
                    and Path(script['path']).parent == Path('generation') / job_id):
                paths.add(str(Path('generation') / job_id / 'script.md'))
        shared = {a['path'] for a in project['artifacts'] if a is not artifact}
        shared.update(c['path'] for a in project['artifacts'] if a is not artifact
                      for c in a.get('bundle', {}).values())
        targets = []
        for path in paths - shared:
            target = store.project_path(root, path)
            if any(p.is_symlink() for p in (target, *target.parents) if p != root and p.is_relative_to(root)):
                raise WorkflowError('INVALID_INPUT', 'Không xóa kịch bản qua liên kết tệp.')
            if target.exists() and not target.is_file():
                raise WorkflowError('INVALID_INPUT', 'Đường dẫn kịch bản không phải tệp.')
            targets.append(target)

        current = wf._latest(project, 'script', None)
        was_current = current is artifact
        if not artifact.get('deleted_at'):
            if was_current:
                # Reset the workflow without making an older script current or
                # erasing already rendered videos and their review records.
                for item in project['artifacts']:
                    item['invalidated'] = True
                    request = wf._request(reviews, item)
                    if request['status'] == 'pending':
                        request['status'] = 'superseded'
                for revision in reviews.get('scene_revisions', []):
                    if revision['status'] not in ('completed', 'cancelled'):
                        revision['status'] = 'cancelled'
                project.update(stage='script', scene_ids=[])
                for key in ('generation', 'assembly_manifest', 'av_manifest', 'voice_sample',
                            'design_previews', 'shared_resources'):
                    project.pop(key, None)
            artifact.update(deleted_at=wf._now(), invalidated=True)
            request = wf._request(reviews, artifact)
            if request['status'] == 'pending':
                request['status'] = 'superseded'
            wf._save(root, project, reviews)
        for target in targets:
            target.unlink(missing_ok=True)
        return dict(ok=True, version=version, reset_to_script=was_current)
