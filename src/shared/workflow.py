"""Checkpoint 3: persistent approval gates, with no AI or rendering dependency."""

from datetime import datetime, timezone
import math
import re
import uuid

from src.shared import _workflow_store as store
from src.shared._workflow_store import WorkflowError


STAGES = ("script", "scene_design", "scene_clip", "scene_av", "final_video", "completed")
SCENE_STAGES = ("scene_design", "scene_clip", "scene_av")

def stages(project):
    return STAGES if project.get("c7_enabled") else tuple(s for s in STAGES if s != "scene_av")
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*\Z")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _identifier(value, name):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise WorkflowError("INVALID_INPUT", f"{name} must contain letters, digits, underscores or hyphens")


def _scene_list(scene_ids):
    if not isinstance(scene_ids, list) or not scene_ids:
        raise WorkflowError("INVALID_INPUT", "scene_ids must be a nonempty ordered list of unique IDs")
    for scene_id in scene_ids:
        _identifier(scene_id, "scene_id")
    if len(set(scene_ids)) != len(scene_ids):
        raise WorkflowError("INVALID_INPUT", "scene_ids must be unique")
    return list(scene_ids)


def _key(item):
    return item["stage"], item["scene_id"], item["version"]


def _scope(project, stage, scene_id):
    if stage not in STAGES[:-1]:
        raise WorkflowError("INVALID_STAGE", f"Unknown review stage: {stage}")
    if stage in SCENE_STAGES:
        if scene_id not in project["scene_ids"]:
            raise WorkflowError("SCENE_NOT_FOUND", f"Scene is not in the approved script: {scene_id}")
    elif scene_id is not None:
        raise WorkflowError("INVALID_INPUT", f"{stage} does not accept scene_id")


def _latest(project, stage, scene_id):
    return next((item for item in reversed(project["artifacts"])
                 if item["stage"] == stage and item["scene_id"] == scene_id and not item.get("invalidated")), None)


def _request(reviews, artifact):
    return next(item for item in reviews["requests"] if _key(item) == _key(artifact))


def _targets(project, stage=None):
    stage = stage or project["stage"]
    return project["scene_ids"] if stage in SCENE_STAGES else [None]


def _all_approved(project, reviews, stage):
    if stage == "scene_clip" and any(r["status"] not in ("completed", "cancelled") for r in reviews.get("scene_revisions", [])):
        return False
    return all((item := _latest(project, stage, scene_id)) is not None
               and _request(reviews, item)["status"] == "approved"
               for scene_id in _targets(project, stage))


def _stage_status(project, reviews):
    if project["stage"] == "completed":
        return "completed"
    if any(r["status"] not in ("completed", "cancelled", "awaiting_clip") for r in reviews.get("scene_revisions", [])):
        return "needs_revision"
    requests = [(_request(reviews, item) if (item := _latest(project, project["stage"], scene_id)) else None)
                for scene_id in _targets(project)]
    if any(item and item["status"] == "changes_requested" for item in requests):
        return "needs_revision"
    if any(item is None for item in requests):
        return "ready"
    return "awaiting_review"


def _validate(root, project, reviews):
    """Reject broken snapshots instead of inventing missing state or approvals."""
    try:
        if not isinstance(project["topic"], str) or not project["topic"].strip():
            raise ValueError("Missing project topic")
        if project["stage"] not in STAGES or not isinstance(project["artifacts"], list):
            raise ValueError("Invalid stage or artifact list")
        if not isinstance(reviews["requests"], list):
            raise ValueError("Invalid review request list")
        if project["scene_ids"]:
            _scene_list(project["scene_ids"])
        elif project["scene_ids"] != [] or project["stage"] != "script":
            raise ValueError("Scene stages require an approved scene list")
        artifacts = {}
        for item in project["artifacts"]:
            if item.get("invalidated") and item["stage"] in SCENE_STAGES:
                _identifier(item["scene_id"], "scene_id")
            else:
                _scope(project, item["stage"], item["scene_id"])
            _identifier(item["version"], "version")
            if STAGES.index(item["stage"]) > STAGES.index(project["stage"]) and not item.get("invalidated") and not (project.get("c7_enabled") and item["stage"] == "scene_av"):
                raise ValueError("Artifact belongs to a future stage")
            if _key(item) in artifacts:
                raise ValueError("Duplicate artifact version")
            artifacts[_key(item)] = item
            store.project_path(root, item["path"])
            if not re.fullmatch(r"[0-9a-f]{64}", item["sha256"]):
                raise ValueError("Invalid artifact checksum")
            if item["stage"] == "script":
                _scene_list(item["scene_ids"])
            if item.get("origin") in ("ai", "ai_scene"):
                if item.get("origin") == "ai" and (item["stage"] != "script" or set(item["bundle"]) != {"script.json", "research.json", "generation.json"}):
                    raise ValueError("Invalid AI script bundle")
                if item.get("origin") == "ai_scene" and (item["stage"] not in SCENE_STAGES or not item.get("bundle")):
                    raise ValueError("Invalid AI scene bundle")
                for component in item["bundle"].values():
                    store.project_path(root, component["path"])
                    if not re.fullmatch(r"[0-9a-f]{64}", component["sha256"]):
                        raise ValueError("Invalid AI bundle checksum")
        requests = {}
        for request in reviews["requests"]:
            key = _key(request)
            if key not in artifacts or key in requests:
                raise ValueError("Review does not identify exactly one artifact")
            requests[key] = request
            status, decision = request["status"], request["decision"]
            if status in ("pending", "superseded"):
                if decision is not None or request["feedback"] is not None or request["decided_at"] is not None:
                    raise ValueError("Undecided review has a decision")
            elif status in ("approved", "changes_requested"):
                expected = "approve" if status == "approved" else "request_changes"
                if decision != expected or not request["decided_at"]:
                    raise ValueError("Review status disagrees with decision")
                if status == "changes_requested" and not request["feedback"]:
                    raise ValueError("Revision decision requires feedback")
                if status == "approved" and artifacts[key].get("origin") == "ai":
                    check = request["read_check"]
                    seconds = check["seconds"]
                    if (type(seconds) not in (int, float) or not math.isfinite(seconds) or not 30 <= seconds <= 60
                        or check["sha256"] != artifacts[key]["sha256"]):
                        raise ValueError("AI approval requires a measured read check for this exact script")
            else:
                raise ValueError("Invalid review status")
        if artifacts.keys() != requests.keys():
            raise ValueError("Artifact is missing its review request")
        for stage, scene_id, _ in artifacts:
            latest = _latest(project, stage, scene_id)
            if latest and requests[_key(latest)]["status"] == "superseded":
                raise ValueError("The current artifact cannot have a superseded review")
        for stage in stages(project)[:stages(project).index(project["stage"])]:
            if not _all_approved(project, reviews, stage):
                raise ValueError("A required earlier review gate was bypassed")
        script = _latest(project, "script", None)
        if script and script["scene_ids"] != project["scene_ids"]:
            raise ValueError("Scene order disagrees with current script")
        if script is None and project["scene_ids"]:
            raise ValueError("Scene order requires a submitted script")
        if project["status"] != _stage_status(project, reviews):
            raise ValueError("Project status disagrees with review state")
        if project["stage"] != "completed" and _all_approved(project, reviews, project["stage"]):
            raise ValueError("Approved stage was not advanced")
    except (KeyError, TypeError, ValueError, StopIteration, WorkflowError) as exc:
        raise WorkflowError("STATE_CORRUPT", f"Invalid workflow state: {exc}") from exc


def _load(root):
    project, reviews = store.load(root)
    _validate(root, project, reviews)
    return project, reviews


def _save(root, project, reviews, *, publish_final=None):
    project["revision"] += 1
    reviews["revision"] = project["revision"]
    project["updated_at"] = _now()
    project["status"] = _stage_status(project, reviews)
    _validate(root, project, reviews)
    store.commit(root, project, reviews, publish_final=publish_final)


def _verify(root, artifact):
    path = store.project_path(root, artifact["path"])
    if not path.is_file():
        raise WorkflowError("ARTIFACT_MISSING", f"Registered artifact is missing: {artifact['path']}")
    if store.digest(path) != artifact["sha256"]:
        raise WorkflowError("ARTIFACT_CHANGED", f"File changed after submission: {artifact['path']}; submit a new version at the current stage")
    for component in artifact.get("bundle", {}).values():
        path = store.project_path(root, component["path"])
        if not path.is_file() or store.digest(path) != component["sha256"]:
            raise WorkflowError("ARTIFACT_CHANGED", f"Script bundle is missing or changed: {component['path']}")


def _prerequisites(root, project, reviews):
    for stage in stages(project)[:stages(project).index(project["stage"])]:
        for scene_id in _targets(project, stage):
            _verify(root, _latest(project, stage, scene_id))


def _view(root, project, reviews):
    pending, revisions, current, actions, issues = [], [], [], [], []
    if project["stage"] != "completed":
        for scene_id in _targets(project):
            artifact = _latest(project, project["stage"], scene_id)
            if artifact is None:
                actions.append(dict(action="submit", stage=project["stage"], scene_id=scene_id,
                                    hint="Register a file, or run --mock for fixed test data"))
                continue
            request = _request(reviews, artifact)
            item = dict(artifact, path=str(root / artifact["path"]), review_status=request["status"],
                        feedback=request["feedback"])
            current.append(item)
            if request["status"] == "pending":
                pending.append(item)
                actions.append(dict(action="review", stage=item["stage"], scene_id=scene_id,
                                    version=item["version"], path=item["path"]))
            elif request["status"] == "changes_requested":
                revisions.append(item)
                actions.append(dict(action="submit_revision", stage=item["stage"], scene_id=scene_id,
                                    feedback=request["feedback"], hint="Use a new version, or run --mock"))
    for stage in stages(project)[:min(stages(project).index(project["stage"]) + 1, len(stages(project))-1)]:
        for scene_id in _targets(project, stage):
            artifact = _latest(project, stage, scene_id)
            if artifact:
                try:
                    _verify(root, artifact)
                except WorkflowError as exc:
                    issues.append(dict(stage=stage, scene_id=scene_id, version=artifact["version"], error=str(exc)))
    final_path = str(root / "final.mp4") if project["stage"] == "completed" else None
    if final_path:
        final = _latest(project, "final_video", None)
        if not (root / "final.mp4").is_file() or store.digest(root / "final.mp4") != final["sha256"]:
            issues.append(dict(stage="final_video", error="Published final.mp4 is missing or changed"))
    result = dict(project_id=project["project_id"], project_dir=str(root), topic=project["topic"],
                stage=project["stage"], status=project["status"], revision=project["revision"],
                scene_ids=list(project["scene_ids"]),
                artifacts=[dict(item, path=str(root / item["path"])) for item in project["artifacts"] if not item.get('deleted_at')],
                current_artifacts=current, pending_reviews=pending,
                needs_revision=revisions, review_history=reviews["requests"], next_actions=actions,
                integrity_issues=issues, final_path=final_path)
    result["scene_revisions"] = reviews.get("scene_revisions", [])
    result["assembly_manifest"] = project.get("assembly_manifest")
    result["design_previews"] = project.get("design_previews", {})
    result["c7_enabled"] = project.get("c7_enabled", False)
    from src.preview.audio import summary as audio_summary
    result["audio_generation"] = audio_summary(root)
    result["voice_sample"] = project.get("voice_sample")
    if "brief" in project:
        result["brief"] = project["brief"]
    for key in ("idea", "idea_draft"):
        if key in project:
            result[key] = project[key]
    if "generation" in project:
        from src.idea.generation import summary
        result["generation"] = summary(root, project["generation"])
    if "scene_generation" in project:
        from src.scene_design.scene_generation import summary as scene_summary
        result["scene_generation"] = scene_summary(root)
    from src.shared.ai_config import get_backend, get_model
    result['ai_backend']=get_backend()
    result['ai_model']=get_model()
    return result


def create_project(project_dir, topic, *, brief=None):
    if not isinstance(topic, str) or not topic.strip():
        raise WorkflowError("INVALID_INPUT", "A nonempty project topic is required")
    if brief is not None:
        from src.shared.ai_config import normalize_brief
        brief = normalize_brief(brief)
    with store.locked_project(project_dir, create=True) as root:
        if any(path.name != ".workflow.lock" for path in root.iterdir()):
            raise WorkflowError("PROJECT_EXISTS", "Project directory is not empty; use a new directory")
        project = dict(schema_version=1, project_id=uuid.uuid4().hex, revision=0,
                       topic=topic.strip(), stage="script", status="ready", scene_ids=[],
                       artifacts=[], created_at=_now(), updated_at=_now())
        if brief is not None:
            project["brief"] = brief
        reviews = dict(schema_version=1, project_id=project["project_id"], revision=0, requests=[])
        _save(root, project, reviews)
        return _view(root, project, reviews)


def get_status(project_dir):
    with store.locked_project(project_dir) as root:
        project, reviews = _load(root)
        return _view(root, project, reviews)


def _artifact_path(stage, scene_id, version):
    if stage == "script":
        return f"scripts/script_{version}.md"
    if stage == "scene_design":
        return f"designs/{scene_id}_{version}.json"
    if stage == "scene_clip":
        return f"previews/{scene_id}_{version}.mp4"
    if stage == "scene_av":
        return f"av_previews/{scene_id}_{version}.mp4"
    return f"finals/final_{version}.mp4"


def _submit(root, project, reviews, stage, version, source, scene_id, scene_ids, *, mock=False):
    _scope(project, stage, scene_id)
    _identifier(version, "version")
    if stage != project["stage"]:
        raise WorkflowError("WRONG_STAGE", f"Current stage is {project['stage']}, not {stage}")
    _prerequisites(root, project, reviews)
    if stage == "script":
        scene_ids = _scene_list(scene_ids)
    elif scene_ids is not None:
        raise WorkflowError("INVALID_INPUT", "scene_ids is only accepted for a script")
    if not source.is_file() or source.stat().st_size == 0:
        raise WorkflowError("ARTIFACT_MISSING", f"Artifact source is missing or empty: {source}")
    existing = next((item for item in project["artifacts"] if _key(item) == (stage, scene_id, version)), None)
    if existing:
        if existing["sha256"] == store.digest(source) and (stage != "script" or existing["scene_ids"] == scene_ids):
            _verify(root, existing)
            return False
        raise WorkflowError("VERSION_EXISTS", "This version is already registered; use a new version")
    relative = _artifact_path(stage, scene_id, version)
    checksum = store.materialize(root, relative, source=source)
    previous = _latest(project, stage, scene_id)
    if previous:
        request = _request(reviews, previous)
        if request["status"] == "pending":
            request["status"] = "superseded"
    artifact = dict(stage=stage, scene_id=scene_id, version=version, path=relative,
                    sha256=checksum, created_at=_now(), mock=mock)
    if stage == "script":
        artifact["scene_ids"] = scene_ids
        project["scene_ids"] = scene_ids
    project["artifacts"].append(artifact)
    reviews["requests"].append(dict(stage=stage, scene_id=scene_id, version=version,
                                    status="pending", decision=None, feedback=None,
                                    requested_at=_now(), decided_at=None))
    return True


def submit_artifact(project_dir, stage, version, artifact_path, *, scene_id=None, scene_ids=None):
    from pathlib import Path

    with store.locked_project(project_dir) as root:
        project, reviews = _load(root)
        source = Path(artifact_path).expanduser()
        source = source if source.is_absolute() else root / source
        if _submit(root, project, reviews, stage, version, source, scene_id, scene_ids):
            _save(root, project, reviews)
        return _view(root, project, reviews)


def _next_version(project, stage, scene_id):
    numbers = [int(item["version"][1:]) for item in project["artifacts"]
               if item["stage"] == stage and item["scene_id"] == scene_id
               and re.fullmatch(r"v[0-9]+", item["version"])]
    return f"v{max(numbers, default=0) + 1:03}"


def run_workflow(project_dir, *, mock=False, ai=False, retry_uncertain=None, scene_id=None, scene_trial=False, attempt_id=None, auto_continue=False):
    if attempt_id is not None and (not ai or not isinstance(attempt_id,str) or not attempt_id or len(attempt_id)>128 or retry_uncertain):
        raise WorkflowError("INVALID_INPUT", "Lượt mới cần mã hợp lệ và không dùng chung với retry-uncertain")
    if type(auto_continue) is not bool or auto_continue and not ai:
        raise WorkflowError("INVALID_INPUT", "auto_continue requires an AI workflow")
    if scene_trial and not ai:
        raise WorkflowError("INVALID_INPUT", "--scene-trial requires --ai")
    if mock and ai or retry_uncertain is not None and not ai:
        raise WorkflowError("INVALID_INPUT", "Choose --ai or --mock; --retry-uncertain requires --ai")
    if ai:
        with store.locked_project(project_dir) as root:
            project, reviews = _load(root)
            stage = project["stage"]
        if project.get("c7_enabled") and stage in ("scene_av", "final_video"):
            from src.preview.av_workflow import generate
            return generate(project_dir, scene_id=scene_id, retry_uncertain=retry_uncertain)
        if stage in SCENE_STAGES:
            from src.scene_design.scene_revision import active, resume_revision
            with store.locked_project(project_dir) as root:
                _, reviews = _load(root)
                pending = [r for r in active(reviews) if scene_id is None or scene_id in r["affected"]]
            if pending:
                if scene_trial:
                    raise WorkflowError("REVISION_PENDING", "Hoàn tất lượt chỉnh sửa đang mở trước khi chạy thử cảnh.")
                for revision in pending:
                    resume_revision(project_dir, revision["id"], retry_uncertain=retry_uncertain)
                return get_status(project_dir)
            from src.scene_design.scene_generation import run_scenes
            return run_scenes(project_dir, scene_id=scene_id, retry_uncertain=retry_uncertain, **(dict(trial_mode=True) if scene_trial and stage=="scene_design" else {}), **(dict(attempt_id=attempt_id) if attempt_id else {}), **(dict(auto_continue=True) if auto_continue else {}))
        if scene_id is not None:
            raise WorkflowError("INVALID_INPUT", "--scene-id requires an AI scene stage")
        from src.idea.generation import run_ai
        return run_ai(project_dir, retry_uncertain=retry_uncertain)
    if scene_id is not None:
        raise WorkflowError("INVALID_INPUT", "--scene-id requires --ai")
    with store.locked_project(project_dir) as root:
        project, reviews = _load(root)
        _prerequisites(root, project, reviews)
        changed = False
        if project["stage"] != "completed":
            for scene_id in _targets(project):
                item = _latest(project, project["stage"], scene_id)
                if item:
                    _verify(root, item)
                    if _request(reviews, item)["status"] != "changes_requested":
                        continue
                if not mock:
                    continue
                from src.shared.mock_content import prepare

                source, scene_ids = prepare(project["stage"], scene_id)
                changed |= _submit(root, project, reviews, project["stage"],
                                   _next_version(project, project["stage"], scene_id),
                                   source, scene_id, scene_ids, mock=True)
        if changed:
            _save(root, project, reviews)
        return _view(root, project, reviews)
