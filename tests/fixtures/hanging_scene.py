"""A real timeout, with a child process which must also be stopped."""

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from manim import Scene


class HangingScene(Scene):
    def construct(self):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(3600)"])

        def stop(signum, frame):
            raise SystemExit("timeout fixture received SIGTERM")

        signal.signal(signal.SIGTERM, stop)
        Path("timeout_processes.json").write_text(json.dumps({"worker": os.getpid(), "child": child.pid}))
        try:
            while True:
                time.sleep(0.1)
        finally:
            child.terminate()
            child.wait(timeout=1)
