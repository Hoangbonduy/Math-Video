"""Render one named Manim scene for an agent. See docs/checkpoint_2.md."""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
from tempfile import NamedTemporaryFile, TemporaryDirectory
import traceback


_QUALITIES = {
    "l": ("low_quality", 854, 480, 15),
    "m": ("medium_quality", 1280, 720, 30),
    "h": ("high_quality", 1920, 1080, 60),
    "p": ("production_quality", 2560, 1440, 60),
    "k": ("fourk_quality", 3840, 2160, 60),
}


class RenderFailure(Exception):
    """An operational failure which can be returned to the calling agent."""

    def __init__(self, code, message):
        super().__init__(f"{code}: {message}")


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def _result(scene_id, version, error=None):
    return dict(status="failed", scene_id=scene_id, version=version,
                video_path=None, log_path=None, error=error)


def _write_json_atomic(path, value):
    temporary = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                prefix=f".{path.name}.", suffix=".tmp", delete=False) as file:
            temporary = Path(file.name)
            json.dump(value, file, ensure_ascii=False, indent=2, allow_nan=False)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _settings(render_config):
    if not isinstance(render_config, Mapping):
        raise ValueError("render_config must be a mapping")
    unknown = set(render_config) - {"quality", "resolution", "fps", "timeout_seconds", "target_duration"}
    if unknown:
        raise ValueError(f"Unknown render_config keys: {list(map(str, unknown))}")
    quality = render_config.get("quality", "l")
    if not isinstance(quality, str) or quality not in _QUALITIES:
        raise ValueError("quality must be one of: l, m, h, p, k")
    preset, width, height, fps = _QUALITIES[quality]
    resolution = render_config.get("resolution", [width, height])
    if not isinstance(resolution, (list, tuple)) or len(resolution) != 2:
        raise ValueError("resolution must be [width, height]")
    if any(type(value) is not int or value <= 0 or value % 2 for value in resolution):
        raise ValueError("resolution dimensions must be positive even integers for MP4")
    fps = render_config.get("fps", fps)
    timeout = render_config.get("timeout_seconds", 300)
    for name, value in (("fps", fps), ("timeout_seconds", timeout)):
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"{name} must be a positive finite number")
    settings = {
        "quality": preset, "pixel_width": resolution[0], "pixel_height": resolution[1],
        "frame_rate": fps, "frame_height": 8.0,
        "frame_width": 8.0 * resolution[0] / resolution[1],
        "renderer": "cairo", "format": "mp4", "write_to_movie": True,
        "save_last_frame": False, "save_pngs": False, "save_sections": False,
        "preview": False, "show_in_file_browser": False, "log_to_file": False,
        "dry_run": False, "disable_caching": True, "progress_bar": "none",
    }
    effective = dict(quality=quality, resolution=list(resolution), fps=fps,
                     timeout_seconds=timeout, renderer="cairo")
    if render_config.get("target_duration") is not None:
        target = render_config["target_duration"]
        if type(target) not in (int, float) or not math.isfinite(target) or target <= 0:
            raise ValueError("target_duration must be a positive finite number")
        effective["target_duration"] = target
    return settings, effective


def _stop_process_group(process):
    """Reap the worker and stop its children, including any ffmpeg processes."""
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass
        # The worker may have exited while a child ignored SIGTERM.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    elif process.poll() is None:
        # taskkill /T includes descendants on Windows. Linux/WSL uses killpg.
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=10, check=False)
        if process.poll() is None:
            process.kill()
    process.wait()


def _run_worker(request_path, project, log, timeout):
    from src.scene_design.manim_runtime import python_executable
    executable = python_executable()
    env = os.environ.copy()
    env.pop("OPENROUTER_API_KEY", None)
    env.pop("FPT_API_KEY", None)
    env["PATH"] = str(Path(executable).parent) + os.pathsep + env.get("PATH", "")
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    worker = Path(__file__).with_name("_render_worker.py").resolve()
    process = subprocess.Popen(
        [executable, str(worker), str(request_path)], cwd=project, env=env,
        stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
        start_new_session=(os.name == "posix"),
    )
    try:
        return process.wait(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise RenderFailure("TIMEOUT", f"Render exceeded {timeout:g} seconds; worker and child processes stopped") from exc
    finally:
        _stop_process_group(process)


def _render_to_temp(work, project, source, scene_name, stem, settings, timeout, log, target_duration=None):
    rendered = work / "videos" / f"{stem}.mp4"
    report_path = work / "worker_result.json"
    settings = dict(settings, media_dir=str(work / "media"),
                    video_dir=str(rendered.parent), output_file=stem)
    request = dict(scene_file=str(source), scene_name=scene_name, settings=settings,
                   expected_video=str(rendered), report_path=str(report_path))
    if target_duration is not None:
        request["target_duration"] = target_duration
    request_path = work / "request.json"
    _write_json_atomic(request_path, request)
    returncode = _run_worker(request_path, project, log, timeout)
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RenderFailure("RENDER_FAILED", f"Worker exited with code {returncode} without a valid result; see log_path") from exc
    if not isinstance(report, dict):
        raise RenderFailure("RENDER_FAILED", "Worker returned an invalid result; see log_path")
    if report.get("status") == "failed" and isinstance(report.get("error"), str):
        raise _worker_failure(report["error"])
    if returncode != 0 or report.get("status") != "success":
        raise RenderFailure("RENDER_FAILED", f"Worker exited with code {returncode}; see log_path")
    if not rendered.is_file() or rendered.stat().st_size == 0 or not isinstance(report.get("video"), dict):
        raise RenderFailure("INVALID_VIDEO", "Worker did not produce a validated nonempty MP4")
    return rendered, report["video"]


def _worker_failure(message):
    code, separator, detail = message.partition(": ")
    return RenderFailure(code, detail) if separator else RenderFailure("RENDER_FAILED", message)


def render_scene(project_dir, scene_id, version, scene_file, scene_name, render_config) -> dict:
    """Render a scene, preserving prior versions and returning the 2.1 contract.

    Relative scene_file paths are resolved against project_dir. Returned paths
    are absolute. The active Python interpreter must have Manim installed.
    Clip/log/metadata names are reserved; use a new version to render again.
    """
    result = _result(scene_id, version)
    started_at = _utc_now()
    log = metadata = metadata_path = video = None
    published = False
    try:
        for name, value in (("scene_id", scene_id), ("version", version)):
            if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", value):
                raise RenderFailure("INVALID_INPUT", f"{name} must contain only letters, digits, underscores or hyphens")
        project = Path(project_dir).expanduser().resolve()
        stem = f"{scene_id}_{version}"
        video = project / "previews" / f"{stem}.mp4"
        log_path = project / "logs" / f"{stem}.log"
        metadata_path = log_path.with_suffix(".json")
        video.parent.mkdir(parents=True, exist_ok=True)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        if any(path.exists() or path.is_symlink() for path in (video, log_path, metadata_path)):
            raise RenderFailure("VERSION_EXISTS", f"Clip, log or metadata already exists for {stem}; use a new version")
        try:
            log = log_path.open("x", encoding="utf-8")
        except FileExistsError as exc:
            raise RenderFailure("VERSION_EXISTS", f"Another render reserved {stem}; use a new version") from exc
        result["log_path"] = str(log_path)
        metadata = dict(project_dir=str(project), scene_id=scene_id, version=version,
                        scene_file=None, scene_name=scene_name if isinstance(scene_name, str) else None,
                        render_config=None, started_at=started_at, finished_at=None,
                        status="running", video_path=None, log_path=str(log_path), error=None, video=None)
        if not isinstance(scene_name, str) or not scene_name.isidentifier():
            raise RenderFailure("INVALID_INPUT", "scene_name must be a Python class name")
        try:
            source = Path(scene_file).expanduser()
            source = (source if source.is_absolute() else project / source).resolve()
            settings, effective = _settings(render_config)
        except (TypeError, ValueError, OverflowError) as exc:
            raise RenderFailure("INVALID_INPUT", str(exc)) from exc
        metadata.update(scene_file=str(source), render_config=effective)
        from src.scene_design.manim_runtime import info as runtime_info
        try:
            metadata.update(runtime=runtime_info())
        except Exception as exc:
            from src.shared._workflow_store import WorkflowError
            if isinstance(exc,WorkflowError):
                raise RenderFailure(exc.code,exc.message) from None
            raise
        _write_json_atomic(metadata_path, metadata)
        log.write(json.dumps(metadata, ensure_ascii=False) + "\n")
        log.flush()
        if source.suffix != ".py" or not source.is_file():
            raise RenderFailure("SOURCE_NOT_FOUND", f"Python scene file not found: {source}")
        with TemporaryDirectory(prefix=".render-", dir=project) as work_dir:
            rendered, video_info = _render_to_temp(
                Path(work_dir), project, source, scene_name, stem, settings,
                effective["timeout_seconds"], log, effective.get("target_duration"),
            )
            # Both files are on the project filesystem: publish atomically and
            # exclusively, so no incomplete clip is exposed or old clip replaced.
            os.link(rendered, video)
            published = True
        metadata.update(status="success", finished_at=_utc_now(), video_path=str(video), video=video_info)
        _write_json_atomic(metadata_path, metadata)
        log.write(f"\nSUCCESS: {video}\n")
        log.flush()
        log.close()
        log = None
        result.update(status="success", video_path=str(video))
    except Exception as exc:
        if isinstance(exc, RenderFailure):
            error = str(exc)
        elif isinstance(exc, OSError):
            error = f"IO_ERROR: {type(exc).__name__}: {exc}"
        elif isinstance(exc, (TypeError, ValueError)):
            error = f"INVALID_INPUT: {type(exc).__name__}: {exc}"
        else:
            error = f"INTERNAL_ERROR: {type(exc).__name__}: {exc}"
        result["error"] = error
        if published:
            try:
                video.unlink(missing_ok=True)
            except OSError as cleanup_error:
                result["error"] += f"; could not remove unpublished result: {cleanup_error}"
        if metadata is not None:
            metadata.update(status="failed", finished_at=_utc_now(), video_path=None,
                            video=None, error=result["error"])
            try:
                _write_json_atomic(metadata_path, metadata)
            except OSError as metadata_error:
                result["error"] += f"; metadata could not be saved: {metadata_error}"
        if log is not None and not log.closed:
            try:
                traceback.print_exc(file=log)
                log.write(f"\nFAILED: {result['error']}\n")
                log.close()
            except OSError as log_error:
                result["error"] += f"; log could not be completed: {log_error}"
        elif result["log_path"]:
            # A final metadata write or log close can fail after rendering.
            try:
                with Path(result["log_path"]).open("a", encoding="utf-8") as failed_log:
                    failed_log.write(f"\nFAILED: {result['error']}\n")
            except OSError:
                pass
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", required=True)
    parser.add_argument("--scene-id", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--scene-file", required=True)
    parser.add_argument("--scene-name", required=True)
    parser.add_argument("--render-config", type=Path, required=True,
                        help="JSON file containing shared project render settings")
    args = parser.parse_args()
    try:
        render_config = json.loads(args.render_config.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        result = _result(args.scene_id, args.version, f"INVALID_INPUT: {exc}")
    else:
        result = render_scene(args.project_dir, args.scene_id, args.version,
                              args.scene_file, args.scene_name, render_config)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
