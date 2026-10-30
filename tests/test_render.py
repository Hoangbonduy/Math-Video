"""Checkpoint 2: contract, stored versions, real rendering and error recovery."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

from src.scene_design.render import render_scene, _run_worker


FIXTURES = Path(__file__).parent / "fixtures"
CONFIG = {"quality": "l", "resolution": [640, 360], "fps": 12}
HAS_MANIM = importlib.util.find_spec("manim") is not None
RESULT_KEYS = {"status", "scene_id", "version", "video_path", "log_path", "error"}


class RenderTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix="render project ")
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)

    def render(self, **overrides):
        args = dict(project_dir=self.project, scene_id="scene_01", version="v001",
                    scene_file=(FIXTURES / "moving_dot.py").resolve(),
                    scene_name="MovingDot", render_config=CONFIG)
        args.update(overrides)
        return render_scene(**args)

    def record(self, result):
        return json.loads(Path(result["log_path"]).with_suffix(".json").read_text())

    def assert_failed(self, result, code, has_log=True):
        self.assertEqual(set(result), RESULT_KEYS)
        self.assertEqual(result["status"], "failed")
        self.assertIsNone(result["video_path"])
        self.assertTrue(result["error"].startswith(code + ":"), result)
        if has_log:
            self.assertIn("FAILED", Path(result["log_path"]).read_text())
            record = self.record(result)
            self.assertEqual(record["status"], "failed")
            self.assertIsNone(record["video_path"])
            self.assertTrue(record["finished_at"])
            self.assertEqual(record["error"], result["error"])
        else:
            self.assertIsNone(result["log_path"])
        self.assertFalse(list(self.project.glob(".render-*")))


class RenderUnitTests(RenderTestCase):
    def test_unsafe_ids_rejected_without_starting_worker(self):
        for overrides in ({"scene_id": "../escape"}, {"version": "../../v001"}):
            with self.subTest(overrides=overrides), patch("src.scene_design.render.subprocess.Popen") as popen:
                self.assert_failed(self.render(**overrides), "INVALID_INPUT", has_log=False)
                popen.assert_not_called()

    def test_invalid_config_and_scene_name_have_logs(self):
        for index, overrides in enumerate((
            {"scene_name": "--write_all"}, {"render_config": {"fps": 0}},
            {"render_config": {"resolution": [641, 360]}},
            {"render_config": {"fps": float("nan")}},
            {"render_config": {"fpz": 30}}, {"render_config": None},
        )):
            with self.subTest(overrides=overrides), patch("src.scene_design.render.subprocess.Popen") as popen:
                self.assert_failed(self.render(version=f"v{index:03}", **overrides), "INVALID_INPUT")
                popen.assert_not_called()

    def test_missing_runtime_returns_dependency_error_with_log(self):
        from src.shared._workflow_store import WorkflowError
        with patch('src.scene_design.manim_runtime.info',side_effect=WorkflowError('DEPENDENCY_ERROR','video environment unavailable')):
            self.assert_failed(self.render(),'DEPENDENCY_ERROR')

    def test_missing_source_resolves_against_project(self):
        result = self.render(scene_file="scenes/missing.py")
        self.assert_failed(result, "SOURCE_NOT_FOUND")
        self.assertIn(str(self.project / "scenes/missing.py"), result["error"])

    def test_exit_zero_without_report_is_failure(self):
        with patch("src.scene_design.render._run_worker", return_value=0):
            self.assert_failed(self.render(), "RENDER_FAILED")
        self.assertFalse(list((self.project / "previews").glob("*.mp4")))

    def test_abrupt_worker_exit_keeps_stdout(self):
        def fail(request_path, project, log, timeout):
            log.write("worker terminated unexpectedly\n")
            return -9

        with patch("src.scene_design.render._run_worker", side_effect=fail):
            result = self.render()
        self.assert_failed(result, "RENDER_FAILED")
        self.assertIn("code -9", result["error"])
        self.assertIn("worker terminated unexpectedly", Path(result["log_path"]).read_text())

    def test_existing_clip_log_or_metadata_preserved(self):
        for index, (folder, extension) in enumerate((("previews", "mp4"), ("logs", "log"), ("logs", "json"))):
            version = f"v{index:03}"
            path = self.project / folder / f"scene_01_{version}.{extension}"
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(b"previous artifact")
            with self.subTest(path=path), patch("src.scene_design.render.subprocess.Popen") as popen:
                self.assert_failed(self.render(version=version), "VERSION_EXISTS", has_log=False)
                self.assertEqual(path.read_bytes(), b"previous artifact")
                popen.assert_not_called()

    def test_cannot_create_project_returns_io_error(self):
        project_file = self.project / "not_a_directory"
        project_file.write_text("occupied")
        self.assert_failed(self.render(project_dir=project_file), "IO_ERROR", has_log=False)

    @unittest.skipUnless(os.name == "posix", "Process groups require POSIX")
    def test_timeout_escalates_when_worker_ignores_sigterm(self):
        script = self.project / "ignore_term.py"
        script.write_text("import signal, time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\nprint('ready', flush=True)\ntime.sleep(3600)\n")
        with script.with_suffix(".log").open("w+") as log:
            # Exercise the supervisor with a real process, without requiring Manim.
            with patch("src.scene_design.render.Path.with_name", return_value=script):
                start = time.monotonic()
                with self.assertRaisesRegex(Exception, "TIMEOUT"):
                    _run_worker(self.project / "unused.json", self.project, log, 0.5)
                self.assertLess(time.monotonic() - start, 4)
            log.seek(0)
            self.assertIn("ready", log.read())

    def test_cli_config_error_is_machine_readable(self):
        completed = subprocess.run([
            sys.executable, "-m", "src.scene_design.render", "--project-dir", str(self.project),
            "--scene-id", "scene_01", "--version", "v001", "--scene-file", "missing.py",
            "--scene-name", "MovingDot", "--render-config", str(self.project / "missing.json"),
        ], text=True, capture_output=True, check=False)
        self.assertEqual(completed.returncode, 1)
        self.assert_failed(json.loads(completed.stdout), "INVALID_INPUT", has_log=False)


@unittest.skipUnless(HAS_MANIM, "Requires Python with Manim installed")
class RenderIntegrationTests(RenderTestCase):
    def assert_video(self, result):
        import av

        self.assertEqual(set(result), RESULT_KEYS)
        detail = Path(result["log_path"]).read_text() if result["log_path"] else result
        self.assertEqual(result["status"], "success", detail)
        self.assertIsNone(result["error"])
        self.assertEqual(Path(result["video_path"]).parent, Path(result["log_path"]).parent.parent / "previews")
        record = self.record(result)
        self.assertEqual(record["status"], "success")
        self.assertEqual(record["video_path"], result["video_path"])
        self.assertEqual(record["render_config"]["resolution"], CONFIG["resolution"])
        self.assertEqual(record["render_config"]["fps"], CONFIG["fps"])
        self.assertLessEqual(record["started_at"], record["finished_at"])
        with av.open(result["video_path"]) as container:
            stream = container.streams.video[0]
            self.assertEqual((stream.width, stream.height), tuple(CONFIG["resolution"]))
            self.assertEqual(float(stream.average_rate), CONFIG["fps"])
            self.assertGreater(float(stream.duration * stream.time_base), 0)
            frames = list(container.decode(video=0))
            self.assertGreater(len(frames), 1)
            self.assertEqual(record["video"]["frame_count"], len(frames))
            # Confirm the frames contain a visible shape and actual animation.
            first, last = (frame.to_ndarray(format="rgb24") for frame in (frames[0], frames[-1]))
            self.assertGreater(first.max(), 100)
            self.assertFalse((first == last).all())
        self.assertFalse(list(Path(result["video_path"]).parent.parent.glob(".render-*")))

    def test_two_scenes_versions_and_projects(self):
        scenes = self.project / "scenes"
        scenes.mkdir()
        (scenes / "dot.py").write_text((FIXTURES / "moving_dot.py").read_text())
        first = self.render(scene_file="scenes/dot.py")
        self.assert_video(first)
        originals = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in (
            Path(first["video_path"]), Path(first["log_path"]), Path(first["log_path"]).with_suffix(".json"),
        )}
        second = self.render(version="v002", scene_file="scenes/dot.py")
        self.assert_video(second)
        self.assertNotEqual(first["video_path"], second["video_path"])
        for path, digest in originals.items():
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)
        rectangle = self.render(scene_id="scene_02", scene_name="ResizingRectangle",
                                scene_file=(FIXTURES / "resizing_rectangle.py").resolve())
        self.assert_video(rectangle)
        other = self.render(project_dir=self.project / "other_project")
        self.assert_video(other)
        self.assertNotEqual(first["video_path"], other["video_path"])
        self.assertNotEqual(self.record(first)["project_dir"], self.record(other)["project_dir"])
        self.assert_failed(self.render(), "VERSION_EXISTS", has_log=False)

    def test_python_errors_and_wrong_class_then_success(self):
        for index, (file, name, code, detail) in enumerate((
            ("syntax_error.py", "BrokenScene", "PYTHON_SYNTAX_ERROR", "syntax_error.py"),
            ("runtime_error.py", "BrokenScene", "RENDER_FAILED", "intentional scene failure"),
            ("moving_dot.py", "WrongName", "SCENE_NOT_FOUND", "WrongName"),
        )):
            with self.subTest(file=file, name=name):
                result = self.render(version=f"v{index:03}", scene_file=(FIXTURES / file).resolve(), scene_name=name)
                self.assert_failed(result, code)
                self.assertIn(detail, result["error"])
                self.assertIn("Traceback", Path(result["log_path"]).read_text())
        self.assertFalse(list((self.project / "previews").glob("*.mp4")))
        self.assert_video(self.render(version="v010"))

    def test_missing_or_corrupt_video_never_succeeds(self):
        for index, name in enumerate(("MissingOutput", "CorruptOutput")):
            with self.subTest(name=name):
                self.assert_failed(self.render(version=f"v{index:03}", scene_name=name,
                                   scene_file=(FIXTURES / "invalid_output.py").resolve()), "INVALID_VIDEO")
        self.assertFalse(list((self.project / "previews").glob("*.mp4")))

    def test_failed_metadata_commit_removes_only_new_clip(self):
        from src.scene_design.render import _write_json_atomic

        def fail_success(path, value):
            if path.name == "scene_01_v001.json" and value.get("status") == "success":
                raise OSError("metadata disk failure")
            return _write_json_atomic(path, value)

        with patch("src.scene_design.render._write_json_atomic", side_effect=fail_success):
            result = self.render()
        self.assert_failed(result, "IO_ERROR")
        self.assertFalse(list((self.project / "previews").glob("*.mp4")))

    @unittest.skipUnless(os.name == "posix", "Process-tree check uses Linux /proc")
    def test_real_timeout_stops_worker_and_child_then_recovers(self):
        start = time.monotonic()
        result = self.render(scene_file=(FIXTURES / "hanging_scene.py").resolve(),
                             scene_name="HangingScene", render_config=dict(CONFIG, timeout_seconds=5))
        self.assertLess(time.monotonic() - start, 9)
        self.assert_failed(result, "TIMEOUT")
        pids = json.loads((self.project / "timeout_processes.json").read_text())
        for pid in pids.values():
            stat = Path(f"/proc/{pid}/stat")
            self.assertFalse(stat.exists(), f"Process {pid} was not reaped")
        self.assertFalse(list((self.project / "previews").glob("*.mp4")))
        self.assert_video(self.render(version="v002"))

    def test_cli_success_has_only_json_on_stdout(self):
        completed = subprocess.run([
            sys.executable, "-m", "src.scene_design.render", "--project-dir", str(self.project),
            "--scene-id", "scene_01", "--version", "v001",
            "--scene-file", str((FIXTURES / "moving_dot.py").resolve()), "--scene-name", "MovingDot",
            "--render-config", str((FIXTURES / "render_config.json").resolve()),
        ], text=True, capture_output=True, timeout=30, check=False)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assert_video(json.loads(completed.stdout))

    def test_validator_rejects_wrong_resolution_and_fps(self):
        import av
        import numpy as np
        from src.scene_design._render_worker import validate_video, WorkerFailure

        # A genuine, decodable MP4 whose metadata disagrees with the project.
        video = self.project / "wrong.mp4"
        with av.open(str(video), "w") as container:
            stream = container.add_stream("libx264", rate=6)
            stream.width, stream.height, stream.pix_fmt = 320, 180, "yuv420p"
            for _ in range(3):
                frame = av.VideoFrame.from_ndarray(np.zeros((180, 320, 3), dtype=np.uint8), format="rgb24")
                for packet in stream.encode(frame):
                    container.mux(packet)
            for packet in stream.encode():
                container.mux(packet)
        with self.assertRaisesRegex(WorkerFailure, "INVALID_VIDEO:.*Resolution"):
            validate_video(video, {"pixel_width": 640, "pixel_height": 360, "frame_rate": 12})
        with self.assertRaisesRegex(WorkerFailure, "INVALID_VIDEO:.*FPS"):
            validate_video(video, {"pixel_width": 320, "pixel_height": 180, "frame_rate": 12})


if __name__ == "__main__":
    unittest.main()
