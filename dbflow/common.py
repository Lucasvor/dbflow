import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path


class FlowError(Exception):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    if not isinstance(value, bytes):
        value = value.encode("utf-8") if isinstance(value, str) else encoded(value).encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def identifier(value, label="identifier"):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,119}", value) or ".." in value:
        raise FlowError(f"Invalid {label}: use letters, numbers, dot, underscore or hyphen.")
    return value


def required_text(value, label, maximum=1000000):
    if not isinstance(value, str) or not value.strip() or len(value.encode("utf-8")) > maximum:
        raise FlowError(f"{label} must be nonempty text, at most {maximum} bytes.")
    return value


def safe_path(root, relative):
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute() or "\\" in relative:
        raise FlowError("Expected a project-relative path using forward slashes.")
    root = Path(root).resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or path == root:
        raise FlowError("Path escapes project root.")
    return path


def atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = data.encode("utf-8") if isinstance(data, str) else data
    # tempfile.mkstemp may retry PermissionError thousands of times on Windows
    # protected directories. A unique exclusive create fails promptly instead.
    temporary = path.parent / ('.dbflow-' + uuid.uuid4().hex)
    fd = os.open(temporary, os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0), 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_config(root):
    path = Path(root) / ".db-control/project.json"
    try:
        config = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise FlowError("Project is not configured. Run the installer first.") from exc
    identifier(config.get("project_key"), "project_key")
    if config.get("version") != 1:
        raise FlowError("Unsupported project configuration version.")
    return config


def target(config, database, environment=None):
    identifier(database, "database")
    entry = config.get("databases", {}).get(database)
    if not entry:
        raise FlowError(f"Database {database} is not mapped in .db-control/project.json.")
    if environment is not None and environment not in entry.get("environments", {}):
        raise FlowError(f"Environment {environment} is not mapped for {database}.")
    return entry
