"""Select and probe the Python environment used by every Manim worker."""
from functools import lru_cache
import json
import os
from pathlib import Path
import subprocess
import sys

from src.shared._workflow_store import WorkflowError


def python_executable():
    configured = os.environ.get('MANIM_PYTHON')
    if configured:
        candidate = Path(configured).expanduser()
        if not candidate.is_file():
            raise WorkflowError('DEPENDENCY_ERROR', 'MANIM_PYTHON không trỏ tới Python hợp lệ.')
        return str(candidate.resolve())
    # Prefer the dedicated environment the application was started in.
    current = Path(sys.executable)
    if 'video' in current.parts:
        return str(current)
    for prefix in (Path.home()/'.conda/envs/video', Path.home()/'miniconda3/envs/video',
                   Path.home()/'anaconda3/envs/video'):
        for suffix in ('bin/python', 'python.exe'):
            candidate = prefix/suffix
            if candidate.is_file():
                return str(candidate)
    return str(current)


@lru_cache(maxsize=8)
def _probe(executable, modified):
    env = os.environ.copy()
    for key in ('OPENROUTER_API_KEY', 'FPT_API_KEY'):
        env.pop(key, None)
    try:
        result = subprocess.run([executable, '-c',
            'import json,sys,manim,av; print(json.dumps(dict(python=sys.executable,version=manim.__version__)))'],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=20, env=env)
        if result.returncode:
            raise ValueError(result.stderr[-1000:])
        value = json.loads(result.stdout.strip().splitlines()[-1])
        return dict(available=True, python=value['python'], version=value['version'], renderer='cairo')
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        raise WorkflowError('DEPENDENCY_ERROR',
            'Không chạy được Manim trong môi trường video. Kiểm tra MANIM_PYTHON. ' + str(exc)) from None


def info():
    executable = python_executable()
    return dict(_probe(executable, Path(executable).stat().st_mtime_ns))
