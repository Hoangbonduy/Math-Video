"""Deterministic local test data. Never used unless --mock is explicit."""

from src.shared.paths import PROJECT_ROOT

from pathlib import Path

from src.shared._workflow_store import WorkflowError


FIXTURES = PROJECT_ROOT / "tests" / "fixtures" / "workflow"
SCENE_IDS = ["scene_01", "scene_02"]


def prepare(stage, scene_id):
    """Return a fixture path and script scene order, without changing any files."""
    if stage == "script":
        return FIXTURES / "script.md", list(SCENE_IDS)
    if scene_id not in SCENE_IDS and stage in ("scene_design", "scene_clip"):
        raise WorkflowError("MOCK_UNAVAILABLE", f"No fixed mock content for {scene_id}; use submit")
    name = {
        "scene_design": f"{scene_id}.json",
        "scene_clip": f"{scene_id}.mp4",
        "final_video": "scene_01.mp4",
    }.get(stage)
    if name is None:
        raise WorkflowError("INVALID_STAGE", f"No mock producer for {stage}")
    return FIXTURES / name, None
