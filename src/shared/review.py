"""Version-specific decisions, recorded in the workflow's shared transaction."""

import math
from src.shared import _workflow_store as store
from src.shared import workflow
from src.shared._workflow_store import WorkflowError


def record_review(project_dir, stage, version, decision, *, scene_id=None, feedback=None, read_seconds=None, expected_sha256=None, expected_preview_sha256=None):
    if decision not in ("approve", "request_changes"):
        raise WorkflowError("INVALID_DECISION", "decision must be approve or request_changes")
    if feedback is not None and not isinstance(feedback, str):
        raise WorkflowError("INVALID_INPUT", "feedback must be text")
    feedback = feedback.strip() if feedback else None
    feedback = feedback or None
    if decision == "request_changes" and not feedback:
        raise WorkflowError("FEEDBACK_REQUIRED", "request_changes requires nonempty feedback")
    workflow._identifier(version, "version")
    with store.locked_project(project_dir) as root:
        project, reviews = workflow._load(root)
        workflow._scope(project, stage, scene_id)
        key = stage, scene_id, version
        artifact = next((item for item in project["artifacts"] if workflow._key(item) == key), None)
        if artifact is None:
            if stage != project["stage"]:
                raise WorkflowError("WRONG_STAGE", f"Current stage is {project['stage']}, not {stage}")
            raise WorkflowError("REVIEW_NOT_FOUND", "No submitted artifact matches this stage, scene and version")
        if expected_sha256 is not None and artifact["sha256"] != expected_sha256:
            raise WorkflowError("STALE_REVIEW", "Reload the artifact before approving")
        from src.scene_design.scene_revision import guard_review, reconcile, manifest
        guard_review(reviews, artifact)
        request = workflow._request(reviews, artifact)
        if stage == "scene_design":
            if artifact.get("design_pipeline")==2 and decision=="approve":
                from src.scene_design.scene_pipeline import verify_quality
                verify_quality(root,artifact)
            ref=project.get("design_previews",{}).get(scene_id+":"+version)
            if artifact.get("render_engine")=="manim" and (not ref or ref.get("renderer")!="manim"):
                raise WorkflowError("STALE_REVIEW","Render the Manim design before review")
            if ref and ref.get("renderer")=="manim":
                from src.scene_design.design_preview import _verify as verify_preview
                verify_preview(root,ref)
                if ref["design_sha256"]!=artifact["sha256"]:
                    raise WorkflowError("STALE_REVIEW","Manim preview does not match this design")
                if decision=="approve": request["preview_sha256"]=ref["sha256"]
        if stage == "scene_design" and expected_preview_sha256:
            ref = project.get("design_previews", {}).get(scene_id+":"+version)
            if not ref or ref["design_sha256"] != artifact["sha256"] or ref["sha256"] != expected_preview_sha256 or store.digest(root/ref["path"]) != ref["sha256"]:
                raise WorkflowError("STALE_REVIEW", "Reload the motion preview")
            request["preview_sha256"] = expected_preview_sha256
        if read_seconds is not None:
            if artifact.get("origin") != "ai" or type(read_seconds) not in (int, float) or not math.isfinite(read_seconds) or read_seconds <= 0:
                raise WorkflowError("INVALID_READ_CHECK", "Read time must be finite, positive and refer to an AI script")
        if artifact.get("origin") == "ai" and decision == "approve":
            if read_seconds is None:
                raise WorkflowError("READ_DURATION_REQUIRED", "Read this script aloud and provide --read-seconds before approving")
            if not 30 <= read_seconds <= 60:
                raise WorkflowError("READ_DURATION_OUT_OF_RANGE", "Measured narration must last 30–60 seconds; request changes instead")
            brief = project.get('brief', {})
            if not brief.get('min_seconds',30) <= read_seconds <= brief.get('max_seconds',60):
                raise WorkflowError('READ_DURATION_OUT_OF_RANGE', 'Thời gian đọc thử nằm ngoài khoảng thời lượng bạn đã chọn; hãy yêu cầu sửa kịch bản.')
        if request["decision"] is not None:
            if (request["decision"] == decision and request["feedback"] == feedback
                and request.get("read_check", {}).get("seconds") == read_seconds):
                # A historical replay acknowledges the old decision only. It
                # cannot approve a replacement or advance the current stage.
                workflow._verify(root, artifact)
                return workflow._view(root, project, reviews)
            raise WorkflowError("REVIEW_CONFLICT", "This version already has a different decision; submit a new version")
        if stage != project["stage"]:
            raise WorkflowError("WRONG_STAGE", f"Current stage is {project['stage']}, not {stage}")
        if workflow._latest(project, stage, scene_id) != artifact or request["status"] != "pending":
            raise WorkflowError("STALE_REVIEW", "Only the current pending version can receive a decision")
        workflow._prerequisites(root, project, reviews)
        workflow._verify(root, artifact)
        if artifact.get("origin") == "c7":
            from src.preview.av_workflow import verify_inputs
            verify_inputs(root, project, artifact)
        request.update(status="approved" if decision == "approve" else "changes_requested",
                       decision=decision, feedback=feedback, decided_at=workflow._now())
        if read_seconds is not None:
            request["read_check"] = dict(seconds=read_seconds, sha256=artifact["sha256"])
        reconcile(project, reviews)
        final = None
        if decision == "approve" and workflow._all_approved(project, reviews, stage):
            for target in workflow._targets(project):
                workflow._verify(root, workflow._latest(project, stage, target))
            if stage == "final_video":
                # Check conflicts before committing the approval transaction.
                published = root / "final.mp4"
                if published.exists() or published.is_symlink():
                    if published.is_symlink() or not published.is_file() or store.digest(published) != artifact["sha256"]:
                        raise WorkflowError("ARTIFACT_CONFLICT", "final.mp4 already contains a different file")
                final = dict(path=artifact["path"], sha256=artifact["sha256"])
            order = workflow.stages(project)
            project["stage"] = order[order.index(stage) + 1]
            if stage == "scene_clip":
                manifest(root, project, reviews)
        workflow._save(root, project, reviews, publish_final=final)
        return workflow._view(root, project, reviews)
