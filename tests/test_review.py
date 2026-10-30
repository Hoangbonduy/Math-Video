"""Exact-version decisions and mandatory per-scene approval gates."""

from pathlib import Path

from src.shared.review import record_review
from src.shared.workflow import get_status, run_workflow, submit_artifact
from tests.workflow_support import FIXTURES, WorkflowCase


class ReviewTests(WorkflowCase):
    def test_valid_script_approval_advances_without_producing_design(self):
        self.bootstrap()
        state = record_review(self.project, "script", "v001", "approve")
        self.assertEqual((state["stage"], state["status"]), ("scene_design", "ready"))
        self.assertFalse((self.project / "designs").exists())
        self.assertEqual(state["review_history"][0]["decision"], "approve")

    def test_changes_feedback_and_new_version_need_fresh_approval(self):
        self.bootstrap()
        rejected = record_review(self.project, "script", "v001", "request_changes", feedback="  Làm rõ mở bài  ")
        self.assertEqual((rejected["stage"], rejected["status"]), ("script", "needs_revision"))
        self.assertEqual(rejected["needs_revision"][0]["feedback"], "Làm rõ mở bài")
        old = self.project / "scripts/script_v001.md"
        fingerprint = old.read_bytes(), old.stat().st_mtime_ns
        revised = run_workflow(self.project, mock=True)
        self.assertEqual(revised["pending_reviews"][0]["version"], "v002")
        # Even identical bytes must not inherit the old version's decision.
        self.assertEqual(revised["pending_reviews"][0]["sha256"], rejected["needs_revision"][0]["sha256"])
        self.assertEqual(revised["status"], "awaiting_review")
        before = self.snapshots()
        self.error("REVIEW_CONFLICT", record_review, self.project, "script", "v001", "approve")
        self.assertEqual(self.snapshots(), before)
        self.assertEqual(record_review(self.project, "script", "v001", "request_changes", feedback="Làm rõ mở bài"), revised)
        self.assertEqual((old.read_bytes(), old.stat().st_mtime_ns), fingerprint)
        self.assertEqual(record_review(self.project, "script", "v002", "approve")["stage"], "scene_design")

    def test_superseded_pending_version_cannot_be_approved(self):
        self.bootstrap()
        submit_artifact(self.project, "script", "v002", FIXTURES / "script.md", scene_ids=["scene_01", "scene_02"])
        before = self.snapshots()
        self.error("STALE_REVIEW", record_review, self.project, "script", "v001", "approve")
        self.assertEqual(self.snapshots(), before)
        state = get_status(self.project)
        self.assertEqual(state["review_history"][0]["status"], "superseded")
        self.assertEqual(state["pending_reviews"][0]["version"], "v002")

    def test_duplicate_approval_does_not_reactivate_a_completed_step(self):
        self.bootstrap()
        first = record_review(self.project, "script", "v001", "approve")
        before = self.snapshots()
        self.assertEqual(record_review(self.project, "script", "v001", "approve"), first)
        self.assertEqual(self.snapshots(), before)
        run_workflow(self.project, mock=True)
        current = get_status(self.project)
        before = self.snapshots()
        self.assertEqual(record_review(self.project, "script", "v001", "approve"), current)
        self.assertEqual(self.snapshots(), before)
        self.error("REVIEW_CONFLICT", record_review, self.project, "script", "v001", "request_changes", feedback="Different decision")
        self.assertEqual(self.snapshots(), before)

    def test_design_reviews_are_independent_and_gate_all_scenes(self):
        self.reach("scene_design")
        first = record_review(self.project, "scene_design", "v001", "approve", scene_id="scene_01")
        self.assertEqual(first["stage"], "scene_design")
        first_path = self.project / "designs/scene_01_v001.json"
        fingerprint = first_path.read_bytes(), first_path.stat().st_mtime_ns
        changed = record_review(self.project, "scene_design", "v001", "request_changes", scene_id="scene_02", feedback="Tăng chiều rộng rõ hơn")
        self.assertEqual(changed["status"], "needs_revision")
        revised = run_workflow(self.project, mock=True)
        self.assertEqual([(item["scene_id"], item["version"]) for item in revised["pending_reviews"]], [("scene_02", "v002")])
        self.assertEqual((first_path.read_bytes(), first_path.stat().st_mtime_ns), fingerprint)
        self.assertFalse((self.project / "previews").exists())
        self.assertEqual(record_review(self.project, "scene_design", "v001", "approve", scene_id="scene_01"), revised)
        state = record_review(self.project, "scene_design", "v002", "approve", scene_id="scene_02")
        self.assertEqual((state["stage"], state["status"]), ("scene_clip", "ready"))
        self.assertFalse((self.project / "previews").exists())

    def test_old_scene_approval_cannot_approve_replacement_at_same_stage(self):
        self.reach("scene_design")
        record_review(self.project, "scene_design", "v001", "approve", scene_id="scene_01")
        revised = submit_artifact(self.project, "scene_design", "v002", FIXTURES / "scene_01.json", scene_id="scene_01")
        before = self.snapshots()
        self.assertEqual(record_review(self.project, "scene_design", "v001", "approve", scene_id="scene_01"), revised)
        self.assertEqual(self.snapshots(), before)
        self.assertEqual(len(revised["pending_reviews"]), 2)
        record_review(self.project, "scene_design", "v001", "approve", scene_id="scene_02")
        self.assertEqual(get_status(self.project)["stage"], "scene_design")
        self.assertEqual(record_review(self.project, "scene_design", "v002", "approve", scene_id="scene_01")["stage"], "scene_clip")

    def test_clip_reviews_are_independent_and_final_waits_for_both(self):
        self.reach("scene_clip")
        record_review(self.project, "scene_clip", "v001", "approve", scene_id="scene_01")
        record_review(self.project, "scene_clip", "v001", "request_changes", scene_id="scene_02", feedback="Dựng lại cảnh 2")
        state = run_workflow(self.project, mock=True)
        self.assertEqual(state["stage"], "scene_clip")
        self.assertEqual([(item["scene_id"], item["version"]) for item in state["pending_reviews"]], [("scene_02", "v002")])
        self.assertFalse((self.project / "finals").exists())
        self.assertFalse((self.project / "final.mp4").exists())
        state = record_review(self.project, "scene_clip", "v002", "approve", scene_id="scene_02")
        self.assertEqual(state["stage"], "final_video")
        self.assertFalse((self.project / "finals").exists())

    def test_wrong_scene_stage_version_or_feedback_preserve_state(self):
        self.bootstrap()
        before = self.snapshots()
        for code, args, kwargs in (
            ("WRONG_STAGE", ("final_video", "v001", "approve"), {}),
            ("SCENE_NOT_FOUND", ("scene_design", "v001", "approve"), {"scene_id": "missing"}),
            ("REVIEW_NOT_FOUND", ("script", "v999", "approve"), {}),
            ("FEEDBACK_REQUIRED", ("script", "v001", "request_changes"), {"feedback": " "}),
            ("INVALID_DECISION", ("script", "v001", "skip"), {}),
        ):
            with self.subTest(code=code):
                self.error(code, record_review, self.project, *args, **kwargs)
                self.assertEqual(self.snapshots(), before)

    def test_changed_current_file_blocks_approval_and_new_submission_repairs_it(self):
        state = self.bootstrap()
        path = Path(state["pending_reviews"][0]["path"])
        path.write_text("changed behind the workflow")
        before = self.snapshots()
        self.error("ARTIFACT_CHANGED", record_review, self.project, "script", "v001", "approve")
        self.assertEqual(self.snapshots(), before)
        self.assertTrue(get_status(self.project)["integrity_issues"])
        submit_artifact(self.project, "script", "v002", FIXTURES / "script.md", scene_ids=["scene_01", "scene_02"])
        self.assertEqual(record_review(self.project, "script", "v002", "approve")["stage"], "scene_design")

    def test_missing_artifact_blocks_review(self):
        state = self.bootstrap()
        Path(state["pending_reviews"][0]["path"]).unlink()
        before = self.snapshots()
        self.error("ARTIFACT_MISSING", record_review, self.project, "script", "v001", "approve")
        self.assertEqual(self.snapshots(), before)

    def test_completed_stage_cannot_be_replaced_and_changed_prerequisite_blocks_run(self):
        self.reach("scene_design")
        before = self.snapshots()
        self.error("WRONG_STAGE", submit_artifact, self.project, "script", "v002", FIXTURES / "script.md", scene_ids=["scene_01", "scene_02"])
        self.assertEqual(self.snapshots(), before)
        (self.project / "scripts/script_v001.md").write_text("changed approved script")
        self.error("ARTIFACT_CHANGED", run_workflow, self.project, mock=True)
        self.assertEqual(self.snapshots(), before)

    def test_final_publication_only_after_current_version_is_approved(self):
        self.reach("final_video")
        record_review(self.project, "final_video", "v001", "request_changes", feedback="Kiểm tra bản cuối")
        run_workflow(self.project, mock=True)
        self.assertFalse((self.project / "final.mp4").exists())
        state = record_review(self.project, "final_video", "v002", "approve")
        self.assertEqual(state["status"], "completed")
        candidate = self.project / "finals/final_v002.mp4"
        published = self.project / "final.mp4"
        self.assertEqual(candidate.read_bytes(), published.read_bytes())
        # The published copy must not be a writable hardlink to the candidate.
        published.write_bytes(b"changed publication")
        self.assertNotEqual(candidate.read_bytes(), published.read_bytes())
        self.assertTrue(get_status(self.project)["integrity_issues"])

    def test_conflicting_final_file_does_not_record_approval(self):
        self.reach("final_video")
        (self.project / "final.mp4").write_bytes(b"previous unrelated file")
        before = self.snapshots()
        self.error("ARTIFACT_CONFLICT", record_review, self.project, "final_video", "v001", "approve")
        self.assertEqual(self.snapshots(), before)
        self.assertEqual((self.project / "final.mp4").read_bytes(), b"previous unrelated file")
