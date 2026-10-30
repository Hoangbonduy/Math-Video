"""Common filesystem and CLI helpers for checkpoint 3 tests."""

import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from src.shared._workflow_store import WorkflowError
from src.shared.review import record_review
from src.shared.workflow import create_project, get_status, run_workflow


REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures" / "workflow"


class WorkflowCase(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix="workflow project ")
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name) / "video"

    def snapshots(self):
        return {name: (self.project / name).read_bytes() for name in ("project.json", "reviews.json")}

    def error(self, code, function, *args, **kwargs):
        with self.assertRaises(WorkflowError) as raised:
            function(*args, **kwargs)
        self.assertEqual(raised.exception.code, code, str(raised.exception))

    def bootstrap(self):
        create_project(self.project, "Điểm chuyển động và hình chữ nhật")
        return run_workflow(self.project, mock=True)

    def reach(self, stage):
        """Set up the stage; actual gate behavior is asserted in review tests."""
        self.bootstrap()
        while (state := get_status(self.project))["stage"] != stage:
            for item in state["pending_reviews"]:
                record_review(self.project, item["stage"], item["version"], "approve", scene_id=item["scene_id"])
            run_workflow(self.project, mock=True)
        return run_workflow(self.project, mock=True)

    def cli(self, command, *args, expected=0):
        # -S excludes third-party packages, including Manim, from the CLI test.
        result = subprocess.run([sys.executable, "-S", str(REPO / "main.py"), command,
                                 "--project-dir", str(self.project), *args],
                                capture_output=True, text=True, timeout=15, cwd=REPO)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        self.assertFalse(result.stderr, result.stderr)
        return json.loads(result.stdout)
