#!/usr/bin/env python3
"""Initialize, inspect, demonstrate, or install the local video breakdown toolkit."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skill" / "analyze-folder-videos-to-feishu"


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def require_empty(path: Path) -> None:
    if path.exists() and (not path.is_dir() or any(path.iterdir())):
        raise ValueError(f"Destination must be new or empty; existing files are preserved: {path}")


def init_project(args) -> dict:
    root = args.project.expanduser().resolve()
    require_empty(root)
    config = {
        "schema_version": 1, "mode": "local", "identity": "bot",
        "base_id": None, "base_url": None, "app_id": None,
        "expected_base_owner_id": None, "document_owners": {}, "tables": {},
        "registry_path": "state/platform-document-registry.json",
        "video_index_path": "state/video-index.json",
        "network": {"ignore_proxy_env": False},
    }
    if args.feishu_config:
        supplied = json.loads(args.feishu_config.read_text(encoding="utf-8-sig"))
        if not isinstance(supplied, dict) or supplied.get("schema_version", 1) != 1:
            raise ValueError("--feishu-config must contain a schema_version 1 JSON object")
        config.update(supplied)
        config["mode"] = "feishu"
    if args.base_url:
        parsed = urlsplit(args.base_url)
        parts = parsed.path.strip("/").split("/")
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password or "base" not in parts:
            raise ValueError("--base-url must be an HTTPS Base URL without credentials")
        position = parts.index("base") + 1
        if position >= len(parts) or not parts[position]:
            raise ValueError("--base-url has no Base token")
        config.update(base_url=args.base_url, base_id=parts[position], mode="feishu")
    if args.app_id:
        config.update(app_id=args.app_id, mode="feishu")
    if args.identity:
        config["identity"] = args.identity
    # Credentials belong to the separately authenticated CLI, never this profile.
    forbidden = {"app_secret", "access_token", "refresh_token", "user_access_token", "tenant_access_token", "client_secret", "authorization", "cookie", "password"}
    def inspect(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key.lower() in forbidden:
                    raise ValueError(f"Do not store credentials in project configuration: {key}")
                inspect(child)
        elif isinstance(value, list):
            for child in value:
                inspect(child)
    inspect(config)
    if config.get("identity") not in ("bot", "user"):
        raise ValueError("identity must be bot or user")
    state_paths = []
    for key in ("registry_path", "video_index_path"):
        raw = config.get(key)
        if not isinstance(raw, str) or not raw:
            raise ValueError(f"Missing {key}")
        path = (root / raw).resolve()
        if Path(raw).is_absolute() or not path.is_relative_to(root) or path == root:
            raise ValueError(f"{key} must be a file path inside the project")
        state_paths.append(path)
    protected = [root / "config" / "project.json", *state_paths]
    if len(set(protected)) != len(protected) or any(a in b.parents for a in protected for b in protected if a != b):
        raise ValueError("Configuration and state file paths must be distinct and non-overlapping")
    write_json(protected[0], config)
    write_json(state_paths[0], {"schema_version": 1, "base_id": config.get("base_id"), "groups": {}})
    write_json(state_paths[1], {"schema_version": 1, "base_id": config.get("base_id"), "videos": {}})
    for directory in ("jobs", "evidence"):
        (root / directory).mkdir(exist_ok=True)
    return {"status": "initialized", "project": str(root), "mode": config["mode"],
            "remote_created": False, "remote_access_verified": False,
            "next": "Run doctor locally. Configure explicit table IDs and live field metadata before Feishu preflight or writes."}


def doctor(args) -> dict:
    selected = set(args.feature or [])
    def command(name):
        path = shutil.which(name)
        return {"available": bool(path), "path": path}
    commands = {name: command(name) for name in ("ffmpeg", "ffprobe", "node", "lark-cli", "whisper", "whisper-cli")}
    modules = {name: {"available": importlib.util.find_spec(name) is not None} for name in ("av", "PIL")}
    candidates = [args.browser, os.environ.get("SCRIPT_PREVIEW_BROWSER"),
                  "C:/Program Files/Google/Chrome/Application/chrome.exe",
                  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
                  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                  "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                  shutil.which("google-chrome"), shutil.which("chromium"), shutil.which("chromium-browser")]
    browser = next((str(Path(p).expanduser().resolve()) for p in candidates if p and Path(p).expanduser().is_file()), None)
    missing = []
    if sys.version_info < (3, 10):
        missing.append("Python >= 3.10")
    requirements = {"media": ("ffmpeg", "ffprobe"), "feishu": ("lark-cli",), "preview": ("node",)}
    for feature in selected:
        for name in requirements.get(feature, ()):
            if not commands[name]["available"]:
                missing.append(f"{feature}: {name}")
    if "evidence" in selected:
        missing.extend(f"evidence: {name}" for name in modules if not modules[name]["available"])
    if "preview" in selected and not browser:
        missing.append("preview: Chrome/Edge/Chromium (pass --browser)")
    if "asr" in selected and not (commands["whisper"]["available"] or commands["whisper-cli"]["available"]):
        missing.append("asr: whisper or whisper-cli; local model must also be configured")
    config_check = None
    if args.project:
        root = args.project.expanduser().resolve()
        config = json.loads((root / "config/project.json").read_text(encoding="utf-8-sig"))
        config_check = {"project": str(root), "mode": config.get("mode", "feishu"),
                        "state_files_present": all((root / config.get(key, "")).is_file() for key in ("registry_path", "video_index_path"))}
        if not config_check["state_files_present"]:
            missing.append("project: declared state files are missing")
    return {"status": "PASS" if not missing else "MISSING_DEPENDENCIES", "python": sys.version.split()[0],
            "selected_features": sorted(selected), "commands": commands, "python_modules": modules,
            "browser": browser, "project": config_check, "missing": missing,
            "scope": "Local executable/module discovery only; no network requests or authentication. Executable versions, codecs, models, remote access and UI remain unverified; run demo to verify media exports."}


def install_skill(args) -> dict:
    destination = args.dest.expanduser().resolve()
    if destination.exists():
        raise ValueError(f"Destination already exists; compare or choose another path: {destination}")
    if destination.is_relative_to(SKILL.resolve()) or SKILL.resolve().is_relative_to(destination):
        raise ValueError("Install destination must be separate from the repository skill source")
    if not (SKILL / "SKILL.md").is_file():
        raise ValueError("Bundled skill source is missing")
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", "local-project.json", ".DS_Store")
    shutil.copytree(SKILL, destination, ignore=ignore)
    count = 0
    for copied in destination.rglob("*"):
        if copied.is_file():
            source = SKILL / copied.relative_to(destination)
            if hashlib.sha256(copied.read_bytes()).digest() != hashlib.sha256(source.read_bytes()).digest():
                raise ValueError(f"Installed file failed readback: {copied.name}")
            count += 1
    return {"status": "installed_and_hash_verified", "destination": str(destination), "files": count,
            "runtime_installed": False, "credentials_included": False}


def demo(args) -> dict:
    path = ROOT / "examples/synthetic/build.py"
    spec = importlib.util.spec_from_file_location("synthetic_demo", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build(args.output.expanduser().resolve(), SKILL / "scripts", args.ffmpeg, args.ffprobe)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="Create a local project with empty state; no remote calls or invented resource IDs")
    init.add_argument("--project", required=True, type=Path, help="New or empty project directory")
    init.add_argument("--feishu-config", type=Path, help="Explicit user-owned JSON profile; IDs and schema are copied, not verified")
    init.add_argument("--base-url", help="Explicit HTTPS Base URL; tables still need configuration")
    init.add_argument("--app-id", help="Existing CLI application's public app ID, never an app secret")
    init.add_argument("--identity", choices=("bot", "user"), help="Existing CLI identity; defaults to bot")
    init.set_defaults(handler=init_project)
    check = commands.add_parser("doctor", help="Inspect local dependency layers; never log in or contact a service")
    check.add_argument("--project", type=Path, help="Also inspect local config/state existence")
    check.add_argument("--feature", action="append", choices=("media", "evidence", "preview", "feishu", "asr"), help="Require this optional dependency layer; repeat for multiple layers")
    check.add_argument("--browser", help="Explicit browser executable for the preview layer")
    check.set_defaults(handler=doctor)
    sample = commands.add_parser("demo", help="Generate and verify a four-second synthetic source, clips, HTML and XML offline")
    sample.add_argument("--output", required=True, type=Path, help="New or empty output directory; refuses existing artifacts")
    sample.add_argument("--ffmpeg", default="ffmpeg", help="FFmpeg command or executable path")
    sample.add_argument("--ffprobe", default="ffprobe", help="FFprobe command or executable path")
    sample.set_defaults(handler=demo)
    install = commands.add_parser("install-skill", help="Copy and verify the bundled Skill; does not install runtimes or overwrite an existing Skill")
    install.add_argument("--dest", required=True, type=Path, help="Exact destination Skill directory, e.g. ~/.codex/skills/analyze-folder-videos-to-feishu")
    install.set_defaults(handler=install_skill)
    return result


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        result = args.handler(args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("status") != "MISSING_DEPENDENCIES" else 1
    except (ValueError, OSError, RuntimeError) as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
