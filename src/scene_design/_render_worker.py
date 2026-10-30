"""Private subprocess entrypoint; scene failures never execute inside the agent."""

import importlib.util
import json
import math
from pathlib import Path
import sys
import traceback


class WorkerFailure(Exception):
    def __init__(self, code, message):
        super().__init__(f"{code}: {message}")


def validate_video(video, settings):
    """Decode the complete MP4 before it can become an agent-visible result."""
    import av

    if not video.is_file() or video.stat().st_size == 0:
        raise WorkerFailure("INVALID_VIDEO", f"Manim did not produce a nonempty MP4: {video}")
    try:
        with av.open(str(video)) as container:
            if container.format.name != "mov,mp4,m4a,3gp,3g2,mj2":
                raise ValueError("Output is not an MP4 container")
            stream = container.streams.video[0]
            width, height = stream.width, stream.height
            if (width, height) != (settings["pixel_width"], settings["pixel_height"]):
                raise ValueError(f"Resolution {width}x{height} does not match project settings")
            fps = float(stream.average_rate) if stream.average_rate else 0
            if not math.isfinite(fps) or abs(fps - settings["frame_rate"]) > 0.01:
                raise ValueError(f"FPS {fps} does not match project settings")
            duration = float(stream.duration * stream.time_base) if stream.duration is not None else (
                container.duration / av.time_base if container.duration is not None else 0
            )
            if not math.isfinite(duration) or duration <= 0:
                raise ValueError("Video duration must be greater than zero")
            frames = sum(1 for _ in container.decode(video=0))
            if frames == 0:
                raise ValueError("Video contains no decodable frames")
            return dict(width=width, height=height, fps=fps,
                        duration_seconds=duration, frame_count=frames)
    except Exception as exc:
        raise WorkerFailure("INVALID_VIDEO", f"{type(exc).__name__}: {exc}") from exc


def compatible_camera():
    """Use an actual RGBA canvas for image compositing, including Pillow 13+."""
    import math
    import numpy as np
    from PIL import Image
    from manim.camera.camera import Camera

    class PreviewCamera(Camera):
        def display_image_mobject(self, image_mobject, pixel_array):
            upper_left,upper_right,lower_left,_=self.points_to_pixel_coords(image_mobject,image_mobject.points)
            right=upper_right-upper_left;down=lower_left-upper_left
            center=upper_left+(right+down)/2
            image=Image.fromarray(image_mobject.get_pixel_array())
            size=(max(int(np.linalg.norm(right)),1),max(int(np.linalg.norm(down)),1))
            image=image.resize(size,resample=image_mobject.resampling_algorithm)
            angle=-int(360*math.atan2(right[1],right[0])/(2*math.pi))
            if angle:image=image.rotate(angle,resample=image_mobject.resampling_algorithm,expand=True)
            # Manim 0.19 uses a float 2-D array with mode=RGBA here. New Pillow
            # rejects that conversion; an explicit transparent RGBA image is valid.
            canvas=Image.new('RGBA',(self.pixel_width,self.pixel_height),(0,0,0,0))
            corner=(center-np.array(image.size)/2).astype(int)
            canvas.paste(image,(int(corner[0]),int(corner[1])))
            self.overlay_PIL_image(pixel_array,canvas)
    return PreviewCamera


def render(request):
    try:
        from manim import Scene, tempconfig
    except ImportError as exc:
        raise WorkerFailure("DEPENDENCY_ERROR", f"Cannot import Manim in this Python environment: {exc}") from exc

    # The worker is launched with an artifact directory as cwd. Make the SDK
    # available under both imports admitted by the scene AST policy.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from src.scene_design import manim_kit
    sys.modules["manim_kit"] = manim_kit
    source = Path(request["scene_file"])
    sys.path.insert(0, str(source.parent))
    spec = importlib.util.spec_from_file_location("_agent_render_scene", source)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except SyntaxError as exc:
        raise WorkerFailure("PYTHON_SYNTAX_ERROR", f"{exc.filename}:{exc.lineno}: {exc.msg}") from exc
    except (Exception, SystemExit) as exc:
        raise WorkerFailure("SCENE_LOAD_ERROR", f"{type(exc).__name__}: {exc}") from exc

    scene_class = getattr(module, request["scene_name"], None)
    if not isinstance(scene_class, type) or not issubclass(scene_class, Scene) or scene_class is Scene:
        raise WorkerFailure("SCENE_NOT_FOUND", f"Scene class not found or not a Manim Scene: {request['scene_name']}")
    try:
        # A scene file may change global config at import time; project settings
        # still determine output resolution, FPS and output location.
        with tempconfig(request["settings"]):
            scene=scene_class()
            scene.camera.display_image_mobject=compatible_camera().display_image_mobject.__get__(scene.camera)
            original_wait = scene.wait

            def safe_wait(duration=1.0, *args, **kwargs):
                if duration is None or float(duration) <= 1e-4:
                    return
                return original_wait(float(duration), *args, **kwargs)

            scene.wait = safe_wait
            target_duration = request.get("target_duration")
            if target_duration is not None:
                target_duration = float(target_duration)
                if not math.isfinite(target_duration) or target_duration <= 0:
                    raise ValueError("target_duration must be positive and finite")
                original_construct = scene.construct

                def construct_with_padding(*args, **kwargs):
                    result = original_construct(*args, **kwargs)
                    remaining = target_duration - float(scene.time)
                    if 1e-3 < remaining <= 5.0:
                        # Frozen waits floor duration*FPS. Snap upward to the
                        # frame grid so floating-point drift cannot drop a frame.
                        fps = float(request["settings"]["frame_rate"])
                        frames = math.ceil(remaining * fps - 1e-7)
                        safe_wait(math.nextafter(frames / fps, math.inf))
                    return result

                scene.construct = construct_with_padding
            scene.render()
    except (Exception, SystemExit) as exc:
        raise WorkerFailure("RENDER_FAILED", f"{type(exc).__name__}: {exc}") from exc
    result=validate_video(Path(request["expected_video"]), request["settings"])
    audit=getattr(scene,'design_audit',None)
    if audit is not None:
        encoded=json.dumps(audit,allow_nan=False)
        # The contract permits 400 motion milestones. The separate image
        # evidence budget is 64; it must not truncate the geometric audit.
        if not isinstance(audit,list) or len(audit)>400 or len(encoded)>1_000_000:
            raise WorkerFailure('INVALID_VIDEO','Invalid or excessive scene relation audit')
        result['design_audit']=audit
    return result


def main():
    request = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    try:
        report = dict(status="success", video=render(request))
    except (Exception, SystemExit) as exc:
        traceback.print_exc()
        message = str(exc) if isinstance(exc, WorkerFailure) else f"RENDER_FAILED: {type(exc).__name__}: {exc}"
        report = dict(status="failed", error=message)
    Path(request["report_path"]).write_text(json.dumps(report), encoding="utf-8")
    return 0 if report["status"] == "success" else 1


if __name__ == "__main__":
    exit_code=main()
    if exit_code:
        # A failed Manim animation may leave its writer thread waiting forever.
        # The result has been closed on disk; the parent reaps the process group.
        import os
        os._exit(exit_code)
    raise SystemExit(exit_code)
