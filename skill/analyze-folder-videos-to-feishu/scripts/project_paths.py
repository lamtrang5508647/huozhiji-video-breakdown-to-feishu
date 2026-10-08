"""Resolve configurable project-owned paths without escaping the project root."""
from pathlib import Path


def registry_path(project: Path, profile: dict) -> Path:
    """Honor an explicit registry_path; retain the historical default if absent."""
    root = Path(project).resolve()
    value = profile.get("registry_path", "state/platform-document-registry.json")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("registry_path must be a nonempty path inside the project")
    path = (root / value).resolve()
    if path == root or not path.is_relative_to(root):
        raise ValueError("registry_path escapes the project root or names the root directory")
    return path
