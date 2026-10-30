"""Public generation settings; credentials never belong in these settings."""

from src.shared.paths import PROJECT_ROOT

import math
import os

from src.shared._workflow_store import WorkflowError

MODEL = "anthropic/claude-haiku-5.5"
BUDGET_USD = 0.50
MAX_REQUESTS = 6
MAX_OUTPUT_TOKENS = 4096
TIMEOUT_SECONDS = 180
# Published Exa auto rate, verified 2026-10-23. Reserve conservatively above it.
SEARCH_RESERVE_USD = 0.02  # two searches at $0.007 each, plus price headroom
DEFAULT_BRIEF = dict(language="vi", audience="Người xem phổ thông, không có kiến thức chuyên môn",
                     min_seconds=30, max_seconds=60, read_units_per_second=3.5,
                     requirements="Một ý chính, một ví dụ trực quan, lời dẫn tự nhiên và liền mạch")


def provider_settings():
    import json
    from pathlib import Path
    path = PROJECT_ROOT / 'settings' / 'ai_provider.json'
    try:
        value = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    except (OSError, ValueError) as exc:
        raise WorkflowError('AI_CONFIG', 'Không đọc được settings/ai_provider.json') from exc
    if not isinstance(value, dict):
        raise WorkflowError('AI_CONFIG', 'Cấu hình AI phải là object')
    return value


def get_backend():
    backend = os.environ.get('AI_BACKEND', '').strip() or provider_settings().get('backend', 'openrouter')
    if backend not in ('codex', 'openrouter'):
        raise WorkflowError('AI_CONFIG', 'AI_BACKEND phải là codex hoặc openrouter')
    return backend


def codex_settings():
    value = provider_settings().get('codex', {})
    if not isinstance(value, dict):
        raise WorkflowError('AI_CONFIG', 'Cấu hình Codex phải là object')
    return value


def get_model():
    if get_backend() == 'codex':
        return os.environ.get('CODEX_MODEL', '').strip() or codex_settings().get('model') or 'codex/default'
    return os.environ.get('OPENROUTER_MODEL', '').strip() or MODEL


def normalize_brief(value=None):
    if value is not None and not isinstance(value, dict):
        raise WorkflowError("INVALID_BRIEF", "Brief must be a JSON object")
    value = value or {}
    if set(value) - set(DEFAULT_BRIEF):
        raise WorkflowError("INVALID_BRIEF", "Brief contains unsupported fields")
    brief = dict(DEFAULT_BRIEF, **value)
    for name in ("language", "audience", "requirements"):
        if not isinstance(brief[name], str) or not brief[name].strip() or len(brief[name]) > 2000:
            raise WorkflowError("INVALID_BRIEF", f"Invalid {name}")
    if brief["language"] != "vi":
        raise WorkflowError("INVALID_BRIEF", "Checkpoint 4 uses Vietnamese narration")
    for name in ("min_seconds", "max_seconds", "read_units_per_second"):
        value = brief[name]
        if type(value) not in (int, float) or not math.isfinite(value):
            raise WorkflowError("INVALID_BRIEF", f"Invalid {name}")
    if not 30 <= brief["min_seconds"] < brief["max_seconds"] <= 60 or not 0.5 <= brief["read_units_per_second"] <= 10:
        raise WorkflowError("INVALID_BRIEF", "Target duration must stay within 30–60 seconds; invalid reading rate")
    return brief


def scene_trial_enabled():
    import json
    from pathlib import Path
    path = PROJECT_ROOT / 'settings' / 'scene_generation.json'
    try:
        value = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    except (OSError, ValueError) as exc:
        raise WorkflowError('AI_CONFIG', 'Không đọc được settings/scene_generation.json') from exc
    if not isinstance(value, dict) or type(value.get('trial_mode', False)) is not bool:
        raise WorkflowError('AI_CONFIG', 'trial_mode phải là true hoặc false')
    return value.get('trial_mode', False)


def set_backend(backend):
    if backend not in ('codex',):
        raise WorkflowError('AI_CONFIG', 'Chọn Codex trong ứng dụng.')
    from pathlib import Path
    from src.shared._workflow_store import atomic_write
    config = provider_settings()
    config['backend'] = backend
    atomic_write(PROJECT_ROOT / 'settings/ai_provider.json', config)
    os.environ['AI_BACKEND'] = backend
    return dict(backend=backend, model=get_model())
