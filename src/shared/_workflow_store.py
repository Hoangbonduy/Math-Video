"""Locked, recoverable storage shared by workflow and review (stdlib only)."""

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

import fcntl


JOURNAL = ".workflow-transaction.json"


class WorkflowError(Exception):
    def __init__(self, code, message):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


def digest(path):
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def project_path(root, relative):
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise WorkflowError("STATE_CORRUPT", "Artifact paths must stay inside the project")
    target = root / path
    if not target.resolve().is_relative_to(Path(root).resolve()):
        raise WorkflowError("STATE_CORRUPT", "Artifact path points outside the project")
    return target


def _sync_directory(directory):
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write(path, value):
    temporary = None
    try:
        with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                prefix=f".{path.name}.", suffix=".tmp", delete=False) as file:
            temporary = Path(file.name)
            json.dump(value, file, ensure_ascii=False, indent=2, allow_nan=False)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
        _sync_directory(path.parent)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise WorkflowError("STATE_MISSING", f"Missing state file: {path.name}") from exc
    except (ValueError, UnicodeError) as exc:
        raise WorkflowError("STATE_CORRUPT", f"Invalid JSON in {path.name}: {exc}") from exc


def _check_envelope(project, reviews):
    if not isinstance(project, dict) or not isinstance(reviews, dict):
        raise WorkflowError("STATE_CORRUPT", "State files must contain JSON objects")
    for key in ("schema_version", "project_id", "revision"):
        if key not in project or project[key] != reviews.get(key):
            raise WorkflowError("STATE_CORRUPT", f"State files disagree on {key}")
    if project["schema_version"] != 1 or type(project["revision"]) is not int or project["revision"] < 1:
        raise WorkflowError("STATE_CORRUPT", "Unsupported schema or invalid state revision")


def materialize(root, relative, *, source=None, content=None):
    """Publish an immutable file, or reuse identical output after interruption."""
    target = project_path(root, relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    expected = digest(source) if source is not None else hashlib.sha256(content).hexdigest()
    if target.exists() or target.is_symlink():
        if target.is_symlink() or not target.is_file() or digest(target) != expected:
            raise WorkflowError("ARTIFACT_CONFLICT", f"A different file already exists: {relative}")
        return expected
    temporary = None
    try:
        with NamedTemporaryFile("wb", dir=target.parent, prefix=".artifact-", delete=False) as out:
            temporary = Path(out.name)
            if source is None:
                out.write(content)
            else:
                with source.open("rb") as file:
                    while block := file.read(1024 * 1024):
                        out.write(block)
            out.flush()
            os.fsync(out.fileno())
        if digest(temporary) != expected:
            raise WorkflowError("ARTIFACT_CHANGED", "Source changed while copying; submit it again")
        try:
            os.link(temporary, target)
        except FileExistsError:
            if target.is_symlink() or not target.is_file() or digest(target) != expected:
                raise WorkflowError("ARTIFACT_CONFLICT", f"A different file already exists: {relative}")
        _sync_directory(target.parent)
        return expected
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _apply(root, bundle):
    if not isinstance(bundle, dict) or not {"project", "reviews"}.issubset(bundle):
        raise WorkflowError("STATE_CORRUPT", "Invalid transaction journal")
    project, reviews = bundle["project"], bundle["reviews"]
    _check_envelope(project, reviews)
    final = bundle.get("publish_final")
    if final is not None:
        if not isinstance(final, dict) or set(final) != {"path", "sha256"}:
            raise WorkflowError("STATE_CORRUPT", "Invalid final publication in journal")
        source = project_path(root, final["path"])
        if not source.is_file() or digest(source) != final["sha256"]:
            raise WorkflowError("ARTIFACT_CHANGED", "Approved final candidate is missing or changed")
        artifact = next((a for a in project["artifacts"] if a["stage"] == "final_video" and a["path"] == final["path"]), {})
        if artifact.get("origin") == "c7":
            for field, name in (("audio_path", "narration.wav"), ("subtitle_path", "final.srt"), ("manifest_path", "final_manifest.json")):
                sidecar = project_path(root, artifact[field])
                component = next((c for c in artifact["bundle"].values() if c["path"] == artifact[field]), None)
                if not component or digest(sidecar) != component["sha256"]:
                    raise WorkflowError("ARTIFACT_CHANGED", "Final companion file changed")
                materialize(root, name, source=sidecar)
        materialize(root, "final.mp4", source=source)
    atomic_write(root / "project.json", project)
    atomic_write(root / "reviews.json", reviews)
    (root / JOURNAL).unlink()
    _sync_directory(root)


def commit(root, project, reviews, *, publish_final=None):
    _check_envelope(project, reviews)
    bundle = dict(project=project, reviews=reviews, publish_final=publish_final)
    atomic_write(root / JOURNAL, bundle)
    _apply(root, bundle)


def load(root):
    project, reviews = read_json(root / "project.json"), read_json(root / "reviews.json")
    _check_envelope(project, reviews)
    return project, reviews


@contextmanager
def locked_project(project_dir, *, create=False):
    """The OS releases this lock even if a CLI process is killed."""
    try:
        root = Path(project_dir).expanduser().resolve()
        if create:
            root.mkdir(parents=True, exist_ok=True)
        if not root.is_dir():
            raise WorkflowError("PROJECT_NOT_FOUND", f"Project directory does not exist: {root}")
        with (root / ".workflow.lock").open("a+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                if (root / JOURNAL).exists():
                    _apply(root, read_json(root / JOURNAL))
                yield root
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
    except OSError as exc:
        raise WorkflowError("IO_ERROR", str(exc)) from exc
