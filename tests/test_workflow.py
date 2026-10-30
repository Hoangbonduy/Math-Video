"""Persistence, restart, mock isolation and CLI integration for checkpoint 3."""

import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

from src.shared import _workflow_store as store
from src.shared.review import record_review
from src.shared.workflow import create_project, get_status, run_workflow, submit_artifact
from tests.workflow_support import FIXTURES, REPO, WorkflowCase


class WorkflowTests(WorkflowCase):
    def test_create_project_and_reload(self):
        state = create_project(self.project, "  Chủ đề mới  ")
        self.assertEqual(state["topic"], "Chủ đề mới")
        self.assertEqual((state["stage"], state["status"]), ("script", "ready"))
        self.assertEqual(state["scene_ids"], [])
        self.assertEqual(state["artifacts"], [])
        self.assertEqual(state["next_actions"][0]["action"], "submit")
        self.assertEqual(get_status(self.project), state)

    def test_cannot_replace_project_or_legacy_directory(self):
        create_project(self.project, "Original")
        before = self.snapshots()
        self.error("PROJECT_EXISTS", create_project, self.project, "Replacement")
        self.assertEqual(self.snapshots(), before)
        legacy = self.project.parent / "legacy"
        legacy.mkdir()
        (legacy / "project.json").write_bytes(b"")
        self.error("PROJECT_EXISTS", create_project, legacy, "Replacement")
        self.assertEqual((legacy / "project.json").read_bytes(), b"")
        self.assertFalse((legacy / "reviews.json").exists())

    def test_default_mode_never_invokes_mock_producer(self):
        create_project(self.project, "Manual")
        before = self.snapshots()
        with patch("src.shared.mock_content.prepare") as prepare:
            state = run_workflow(self.project)
            prepare.assert_not_called()
        self.assertEqual(state["status"], "ready")
        self.assertEqual(self.snapshots(), before)
        self.assertFalse((self.project / "scripts").exists())

    def test_repeated_run_waits_and_keeps_existing_data(self):
        state = self.bootstrap()
        artifact = Path(state["pending_reviews"][0]["path"])
        before = self.snapshots()
        fingerprint = artifact.read_bytes(), artifact.stat().st_mtime_ns
        for _ in range(3):
            state = run_workflow(self.project, mock=True)
        self.assertEqual(state["status"], "awaiting_review")
        self.assertEqual(self.snapshots(), before)
        self.assertEqual((artifact.read_bytes(), artifact.stat().st_mtime_ns), fingerprint)
        self.assertFalse((self.project / "designs").exists())
        self.assertFalse((self.project / "previews").exists())

    def test_manual_submit_copies_into_project_and_preserves_scene_order(self):
        create_project(self.project, "Manual")
        state = submit_artifact(self.project, "script", "draft_a", FIXTURES / "script.md",
                                scene_ids=["second", "first"])
        item = state["pending_reviews"][0]
        self.assertEqual(state["scene_ids"], ["second", "first"])
        self.assertEqual(Path(item["path"]), self.project / "scripts/script_draft_a.md")
        before = self.snapshots()
        submit_artifact(self.project, "script", "draft_a", FIXTURES / "script.md", scene_ids=["second", "first"])
        self.assertEqual(self.snapshots(), before)
        state = record_review(self.project, "script", "draft_a", "approve")
        self.assertEqual(state["stage"], "scene_design")
        self.assertEqual(state["scene_ids"], ["second", "first"])

    def test_register_clip_already_produced_in_previews(self):
        self.reach("scene_clip")
        # Register the next version at the canonical renderer output path.
        destination = self.project / "previews/scene_01_v002.mp4"
        destination.write_bytes((FIXTURES / "scene_01.mp4").read_bytes())
        original_time = destination.stat().st_mtime_ns
        state = submit_artifact(self.project, "scene_clip", "v002", "previews/scene_01_v002.mp4", scene_id="scene_01")
        self.assertEqual(destination.stat().st_mtime_ns, original_time)
        self.assertEqual(next(item for item in state["pending_reviews"] if item["scene_id"] == "scene_01")["version"], "v002")

    def test_invalid_submissions_do_not_change_state(self):
        self.bootstrap()
        before = self.snapshots()
        for code, kwargs in (
            ("INVALID_INPUT", dict(version="../escape", scene_ids=["one"])),
            ("INVALID_INPUT", dict(scene_ids=[])),
            ("INVALID_INPUT", dict(scene_ids=["one", "one"])),
            ("INVALID_INPUT", dict(scene_ids=[{}])),
            ("INVALID_INPUT", dict(scene_ids=["../escape"])),
        ):
            with self.subTest(kwargs=kwargs):
                args = dict(version="v002", scene_ids=["one"])
                args.update(kwargs)
                self.error(code, submit_artifact, self.project, "script", artifact_path=FIXTURES / "script.md", **args)
                self.assertEqual(self.snapshots(), before)
        self.error("ARTIFACT_MISSING", submit_artifact, self.project, "script", "v002", "missing.md", scene_ids=["one"])
        self.assertEqual(self.snapshots(), before)

    def test_missing_and_corrupt_state_are_not_reset(self):
        self.bootstrap()
        original = self.snapshots()
        for broken in (b"", b"{bad", b"[]"):
            with self.subTest(broken=broken):
                (self.project / "project.json").write_bytes(broken)
                self.error("STATE_CORRUPT", get_status, self.project)
                self.assertEqual((self.project / "project.json").read_bytes(), broken)
                (self.project / "project.json").write_bytes(original["project.json"])
        (self.project / "reviews.json").unlink()
        self.error("STATE_MISSING", get_status, self.project)
        self.assertFalse((self.project / "reviews.json").exists())

    def test_cannot_bypass_gate_by_setting_later_stage_in_state(self):
        self.bootstrap()
        project = json.loads((self.project / "project.json").read_text())
        project["stage"], project["status"] = "scene_design", "ready"
        (self.project / "project.json").write_text(json.dumps(project))
        self.error("STATE_CORRUPT", run_workflow, self.project, mock=True)
        self.assertFalse((self.project / "designs").exists())

    def test_interrupted_two_file_commit_is_recovered(self):
        create_project(self.project, "Recovery")
        original_reviews = (self.project / "reviews.json").read_bytes()
        real_write = store.atomic_write

        def crash(path, value):
            if path.name == "reviews.json":
                raise OSError("simulated interruption between snapshot writes")
            real_write(path, value)

        with patch.object(store, "atomic_write", side_effect=crash):
            self.error("IO_ERROR", run_workflow, self.project, mock=True)
        self.assertTrue((self.project / store.JOURNAL).exists())
        self.assertEqual((self.project / "reviews.json").read_bytes(), original_reviews)
        artifact = self.project / "scripts/script_v001.md"
        mtime = artifact.stat().st_mtime_ns
        recovered = get_status(self.project)
        self.assertEqual(recovered["status"], "awaiting_review")
        self.assertFalse((self.project / store.JOURNAL).exists())
        self.assertEqual(artifact.stat().st_mtime_ns, mtime)
        self.assertEqual(len(recovered["review_history"]), 1)
        self.assertEqual(run_workflow(self.project, mock=True), recovered)

    def test_mock_file_created_before_state_commit_is_reused(self):
        create_project(self.project, "Orphan recovery")
        before = self.snapshots()
        with patch("src.shared.workflow._save", side_effect=OSError("interrupted before journal")):
            self.error("IO_ERROR", run_workflow, self.project, mock=True)
        artifact = self.project / "scripts/script_v001.md"
        fingerprint = artifact.read_bytes(), artifact.stat().st_mtime_ns
        self.assertEqual(self.snapshots(), before)
        result = run_workflow(self.project, mock=True)
        self.assertEqual(result["pending_reviews"][0]["version"], "v001")
        self.assertEqual((artifact.read_bytes(), artifact.stat().st_mtime_ns), fingerprint)

    def test_abrupt_process_death_releases_lock_and_replays_journal(self):
        create_project(self.project, "Abrupt restart")
        code = """
import os
import sys
from src.shared import _workflow_store as store
from src.shared.workflow import run_workflow
write = store.atomic_write
def crash(path, value):
    if path.name == 'reviews.json':
        os._exit(73)
    write(path, value)
store.atomic_write = crash
run_workflow(sys.argv[1], mock=True)
"""
        result = subprocess.run([sys.executable, "-S", "-c", code, str(self.project)],
                                cwd=REPO, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 73, result.stderr)
        self.assertTrue((self.project / store.JOURNAL).exists())
        recovered = self.cli("status")["project"]
        self.assertEqual(recovered["status"], "awaiting_review")
        self.assertEqual(recovered["pending_reviews"][0]["version"], "v001")
        self.assertFalse((self.project / store.JOURNAL).exists())
        self.assertEqual(self.cli("run", "--mock")["project"], recovered)

    def test_conflicting_orphan_output_is_not_overwritten(self):
        create_project(self.project, "Orphan conflict")
        artifact = self.project / "scripts/script_v001.md"
        artifact.parent.mkdir()
        artifact.write_text("unrelated content")
        before = self.snapshots()
        self.error("ARTIFACT_CONFLICT", run_workflow, self.project, mock=True)
        self.assertEqual(artifact.read_text(), "unrelated content")
        self.assertEqual(self.snapshots(), before)

    def test_restart_finishes_final_publication_transaction(self):
        self.reach("final_video")
        real_write = store.atomic_write

        def crash(path, value):
            if path.name == "reviews.json":
                raise OSError("crash after final publication")
            real_write(path, value)

        with patch.object(store, "atomic_write", side_effect=crash):
            self.error("IO_ERROR", record_review, self.project, "final_video", "v001", "approve")
        self.assertTrue((self.project / "final.mp4").is_file())
        result = get_status(self.project)
        self.assertEqual((result["stage"], result["status"]), ("completed", "completed"))
        self.assertEqual(result["integrity_issues"], [])
        self.assertEqual(record_review(self.project, "final_video", "v001", "approve"), result)
        self.assertEqual(run_workflow(self.project, mock=True), result)

    def test_two_projects_remain_independent(self):
        first = self.bootstrap()
        other = self.project.parent / "other"
        create_project(other, "Other")
        self.assertNotEqual(first["project_id"], get_status(other)["project_id"])
        record_review(self.project, "script", "v001", "approve")
        self.assertEqual(get_status(other)["status"], "ready")

    def test_concurrent_mock_runs_create_only_one_version(self):
        create_project(self.project, "Concurrent")
        command = [sys.executable, "-S", str(REPO / "main.py"), "run", "--project-dir", str(self.project), "--mock"]
        processes = [subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
        for process in processes:
            out, err = process.communicate(timeout=15)
            self.assertEqual(process.returncode, 0, out + err)
        state = get_status(self.project)
        self.assertEqual(len(state["artifacts"]), 1)
        self.assertEqual(len(state["review_history"]), 1)
        self.assertEqual(state["revision"], 2)


class WorkflowCliTests(WorkflowCase):
    def test_full_cli_flow_across_restarts_and_all_gates(self):
        self.cli("create", "--topic", "Kiểm thử CLI")
        self.assertEqual(self.cli("run")["project"]["status"], "ready")
        waiting = self.cli("run", "--mock")["project"]
        self.assertEqual(self.cli("run", "--mock")["project"], waiting)
        self.cli("review", "--stage", "script", "--version", "v001", "--decision", "request_changes", "--feedback", "Làm rõ ví dụ")
        self.cli("run", "--mock")
        resumed = self.cli("status")["project"]
        self.assertEqual(resumed["pending_reviews"][0]["version"], "v002")
        self.assertEqual(resumed["review_history"][0]["feedback"], "Làm rõ ví dụ")
        self.cli("review", "--stage", "script", "--version", "v001", "--decision", "approve", expected=1)
        approved = self.cli("review", "--stage", "script", "--version", "v002", "--decision", "approve")["project"]
        self.assertEqual(approved["stage"], "scene_design")
        self.assertFalse((self.project / "designs").exists())
        for stage in ("scene_design", "scene_clip"):
            state = self.cli("run", "--mock")["project"]
            self.assertEqual(state["stage"], stage)
            self.cli("review", "--stage", stage, "--scene-id", "scene_01", "--version", "v001", "--decision", "approve")
            self.assertEqual(self.cli("status")["project"]["stage"], stage)
            self.cli("review", "--stage", stage, "--scene-id", "scene_02", "--version", "v001", "--decision", "approve")
        self.assertFalse((self.project / "final.mp4").exists())
        self.cli("run", "--mock")
        done = self.cli("review", "--stage", "final_video", "--version", "v001", "--decision", "approve")["project"]
        self.assertEqual(done["status"], "completed")
        self.assertTrue(Path(done["final_path"]).is_file())
        self.assertEqual(self.cli("review", "--stage", "final_video", "--version", "v001", "--decision", "approve")["project"], done)
        self.assertEqual(self.cli("run", "--mock")["project"], done)
        self.assertEqual(self.cli("status")["project"], done)

    def test_cli_submit_and_error_are_json(self):
        self.cli("create", "--topic", "Manual CLI")
        self.cli("submit", "--stage", "script", "--version", "v001", "--file", str(FIXTURES / "script.md"), "--scene-ids", "scene_01", "scene_02")
        before = self.snapshots()
        result = self.cli("review", "--stage", "script", "--version", "v001", "--decision", "request_changes", expected=1)
        self.assertEqual(result["error"]["code"], "FEEDBACK_REQUIRED")
        result = self.cli("submit", expected=1)
        self.assertEqual(result["error"]["code"], "INVALID_INPUT")
        self.assertEqual(self.snapshots(), before)
