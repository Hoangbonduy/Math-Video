"""Load local OpenRouter and FPT settings without executing shell commands."""

from src.shared.paths import PROJECT_ROOT

import os
from pathlib import Path
import shlex

from src.shared._workflow_store import WorkflowError


ENV_PATH = PROJECT_ROOT / ".env"


def load_local_env():
    """Load supported settings, preserving each explicit environment value."""
    names = {"OPENROUTER_API_KEY", "OPENROUTER_MODEL", "FPT_API_KEY", "MANIM_PYTHON", "SCENE_REVIEW_MODEL", "AI_BACKEND", "CODEX_MODEL", "CODEX_COMMAND", "CODEX_TIMEOUT_SECONDS", "SCENE_TRIAL_REPAIRS"}
    if names.issubset(os.environ):
        return
    try:
        lines = ENV_PATH.read_text(encoding="utf-8-sig").splitlines()
    except FileNotFoundError:
        return
    except (OSError, UnicodeError):
        raise WorkflowError("ENV_CONFIG", "Cannot read the project's .env file") from None

    pending = {}
    for number, line in enumerate(lines, 1):
        line = line.strip()
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, value = line.partition("=")
        name = key.strip()
        if not separator or name not in names or name in os.environ or name in pending:
            continue
        try:
            # Quotes and comments are supported; substitution is never executed.
            words = shlex.split(value, comments=True, posix=True)
            if len(words) > 1:
                raise ValueError("Expected one value")
        except ValueError:
            raise WorkflowError("ENV_CONFIG", f"Invalid {name} assignment in .env at line {number}") from None
        if words and words[0]:
            pending[name] = words[0]
    for name, value in pending.items():
        os.environ.setdefault(name, value)
