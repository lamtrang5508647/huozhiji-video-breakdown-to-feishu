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
import subprocess
import sys
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from versioning import read_release

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skill" / "analyze-folder-videos-to-feishu"
ACQUIRE_SKILL = ROOT / "skill" / "video-link-extractor"
SKILL_NAMES = ("analyze-folder-videos-to-feishu", "video-link-extractor")


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
    commands = {name: command(name) for name in ("ffmpeg", "ffprobe", "node", "lark-cli", "whisper", "whisper-cli", "yt-dlp")}
    modules = {name: {"available": importlib.util.find_spec(name) is not None} for name in ("av", "PIL")}
    candidates = [args.browser, os.environ.get("SCRIPT_PREVIEW_BROWSER"),
                  "C:/Program Files/Google/Chrome/Application/chrome.exe",
                  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
                  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                  "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                  "/Applications/Chromium.app/Contents/MacOS/Chromium",
                  *[str(Path.home() / "Applications" / app / "Contents/MacOS" / executable)
                    for app, executable in (("Google Chrome.app", "Google Chrome"),
                                            ("Microsoft Edge.app", "Microsoft Edge"), ("Chromium.app", "Chromium"))],
                  shutil.which("google-chrome"), shutil.which("chromium"), shutil.which("chromium-browser")]
    browser = next((str(Path(p).expanduser().resolve()) for p in candidates if p and Path(p).expanduser().is_file()), None)
    playwright_path = ACQUIRE_SKILL / "vendor/video-batch-download/node_modules/playwright/package.json"
    playwright = {"available": playwright_path.is_file(), "path": str(playwright_path),
                  "scope": "Local package presence only; import and browser launch are unverified"}
    if "acquire" in selected and not browser and playwright["available"] and commands["node"]["available"]:
        # Asking an installed Playwright package for its executable path neither
        # installs Chromium nor launches a browser or contacts a platform.
        try:
            probe = subprocess.run([commands["node"]["path"], "-e",
                                    "process.stdout.write(JSON.stringify({executable:require('playwright').chromium.executablePath()}))"],
                                   cwd=playwright_path.parents[2], capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", timeout=5, check=False)
            path = json.loads(probe.stdout).get("executable") if probe.returncode == 0 else None
            if isinstance(path, str) and Path(path).is_file():
                browser = str(Path(path).resolve())
        except (OSError, subprocess.SubprocessError, ValueError, AttributeError):
            pass
    missing = []
    if sys.version_info < (3, 10):
        missing.append("Python >= 3.10")
    requirements = {"media": ("ffmpeg", "ffprobe"), "feishu": ("lark-cli",), "preview": ("node",),
                    "acquire": ("node", "ffmpeg", "ffprobe")}
    for feature in selected:
        for name in requirements.get(feature, ()):
            if not commands[name]["available"]:
                missing.append(f"{feature}: {name}")
    if "evidence" in selected:
        missing.extend(f"evidence: {name}" for name in modules if not modules[name]["available"])
    for feature in ("preview", "acquire"):
        if feature in selected and not browser:
            missing.append(f"{feature}: Chrome/Edge/Chromium (pass --browser)")
    if "acquire" in selected and not playwright["available"]:
        missing.append("acquire: Playwright package in the extractor's vendor/video-batch-download/node_modules")
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
    return {"status": "PASS" if not missing else "MISSING_DEPENDENCIES", "release": read_release(ROOT), "python": sys.version.split()[0],
            "selected_features": sorted(selected), "commands": commands, "python_modules": modules,
            "node_modules": {"playwright": playwright},
            "browser": browser, "project": config_check, "missing": missing,
            "scope": "Local executable/module discovery only; no network requests or authentication. Executable versions, codecs, models, remote access and UI remain unverified; run demo to verify media exports."}


def install_skill(args) -> dict:
    name = getattr(args, "skill", SKILL_NAMES[0])
    if name not in SKILL_NAMES:
        raise ValueError("Unknown bundled Skill: " + str(name))
    source_root = SKILL if name == SKILL_NAMES[0] else ACQUIRE_SKILL
    destination = args.dest.expanduser().resolve()
    if destination.exists():
        raise ValueError(f"Destination already exists; compare or choose another path: {destination}")
    if destination.is_relative_to(source_root.resolve()) or source_root.resolve().is_relative_to(destination):
        raise ValueError("Install destination must be separate from the repository skill source")
    if not (source_root / "SKILL.md").is_file():
        raise ValueError("Bundled skill source is missing")
    release = read_release(ROOT)
    if read_release(source_root) != release:
        raise ValueError("Skill version mismatch; run scripts/versioning.py check before installing")
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", "local-project.json", ".DS_Store",
                                    "node_modules", "browser-state*", "storage-state*", "cookies*", "credentials*", ".env*",
                                    "cache", "caches", ".cache", "*-cache", ".pytest_cache", ".mypy_cache", ".ruff_cache",
                                    ".venv", "venv", "__pypackages__", "ms-playwright", "runtime", "runtimes",
                                    "*.exe", "*.dll", "*.dylib", "*.so", "*.so.*", "node", "ffmpeg", "ffprobe", "python", "python3")
    shutil.copytree(source_root, destination, ignore=ignore)
    count = 0
    for copied in destination.rglob("*"):
        if copied.is_file():
            source = source_root / copied.relative_to(destination)
            if hashlib.sha256(copied.read_bytes()).digest() != hashlib.sha256(source.read_bytes()).digest():
                raise ValueError(f"Installed file failed readback: {copied.name}")
            count += 1
    return {"status": "installed_and_hash_verified", "skill": name, "destination": str(destination), "files": count,
            "release": release, "runtime_installed": False, "credentials_included": False}


def demo(args) -> dict:
    path = ROOT / "examples/synthetic/build.py"
    spec = importlib.util.spec_from_file_location("synthetic_demo", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build(args.output.expanduser().resolve(), SKILL / "scripts", args.ffmpeg, args.ffprobe)


def acquire(arguments: list[str]) -> int:
    """Delegate untouched arguments and terminal streams to the extractor CLI."""
    entrypoint = ACQUIRE_SKILL / "scripts/acquire.py"
    if not entrypoint.is_file():
        raise ValueError("Bundled acquisition entrypoint is missing: " + str(entrypoint))
    return subprocess.run([sys.executable, "-B", str(entrypoint), *arguments], check=False).returncode


WORKFLOW_COMMANDS = {'creative': 'creative_workflow.py', 'feedback': 'performance_feedback.py', 'batch': 'workflow_batch.py'}


def workflow(command: str, arguments: list[str]) -> int:
    entrypoint = SKILL / 'scripts' / WORKFLOW_COMMANDS[command]
    if not entrypoint.is_file():
        raise ValueError('Bundled workflow entrypoint is missing: ' + str(entrypoint))
    return subprocess.run([sys.executable, '-B', str(entrypoint), *arguments], check=False).returncode


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--version", action="store_true", help="Print release version and iteration")
    commands = result.add_subparsers(dest="command", required=True)
    for name, filename in WORKFLOW_COMMANDS.items():
        commands.add_parser(name, help='Open the ' + name + ' workflow; use ' + name + ' --help for subcommands')
    version = commands.add_parser("version", help="Print the authoritative release metadata as JSON")
    version.set_defaults(handler=lambda args: {"status": "PASS", "release": read_release(ROOT)})
    init = commands.add_parser("init", help="Create a local project with empty state; no remote calls or invented resource IDs")
    init.add_argument("--project", required=True, type=Path, help="New or empty project directory")
    init.add_argument("--feishu-config", type=Path, help="Explicit user-owned JSON profile; IDs and schema are copied, not verified")
    init.add_argument("--base-url", help="Explicit HTTPS Base URL; tables still need configuration")
    init.add_argument("--app-id", help="Existing CLI application's public app ID, never an app secret")
    init.add_argument("--identity", choices=("bot", "user"), help="Existing CLI identity; defaults to bot")
    init.set_defaults(handler=init_project)
    check = commands.add_parser("doctor", help="Inspect local dependency layers; never log in or contact a service")
    check.add_argument("--project", type=Path, help="Also inspect local config/state existence")
    check.add_argument("--feature", action="append", choices=("media", "evidence", "preview", "feishu", "asr", "acquire"), help="Require this optional dependency layer; repeat for multiple layers")
    check.add_argument("--browser", help="Explicit browser executable for the preview layer")
    check.set_defaults(handler=doctor)
    sample = commands.add_parser("demo", help="Generate and verify a four-second synthetic source, clips, HTML and XML offline")
    sample.add_argument("--output", required=True, type=Path, help="New or empty output directory; refuses existing artifacts")
    sample.add_argument("--ffmpeg", default="ffmpeg", help="FFmpeg command or executable path")
    sample.add_argument("--ffprobe", default="ffprobe", help="FFprobe command or executable path")
    sample.set_defaults(handler=demo)
    install = commands.add_parser("install-skill", help="Copy and verify the bundled Skill; does not install runtimes or overwrite an existing Skill")
    install.add_argument("--skill", choices=SKILL_NAMES, default=SKILL_NAMES[0], help="Bundled Skill to install; defaults to the video breakdown Skill")
    install.add_argument("--dest", required=True, type=Path, help="Exact destination Skill directory, e.g. ~/.codex/skills/analyze-folder-videos-to-feishu")
    install.set_defaults(handler=install_skill)
    grab = commands.add_parser("acquire", help="Forward acquisition arguments to the video-link-extractor Skill",
                               description="Acquire an authorized source through video-link-extractor. Arguments, terminal output and exit status are forwarded unchanged.")
    grab.add_argument("--url", required=True, help="Source URL or copied share text")
    grab.add_argument("--output", required=True, help="New acquisition output directory")
    grab.add_argument("--platform", choices=("auto", "bilibili", "douyin", "xiaohongshu", "kuaishou", "wechat_channels", "generic"), default="auto")
    action = grab.add_mutually_exclusive_group()
    action.add_argument("--metadata-only", action="store_true")
    action.add_argument("--download", action="store_true")
    grab.add_argument("--browser", help="Installed browser executable")
    grab.add_argument("--storage-state", help="Explicit local browser storage state")
    grab.add_argument("--headed", action="store_true", help="Explicitly allow a visible browser")
    grab.add_argument("--wechat-browser", action="store_true", help="Use the Tencent browser flow for a Channels source")
    grab.add_argument("--allow-tencent-parse", action="store_true", help="Allow this one Channels link to be sent to Tencent Yuanbao")
    grab.add_argument("--manual-login", action="store_true", help="Wait for normal login in a visible browser; never export the session")
    grab.add_argument("--backend-url", help="Explicit acquisition backend URL")
    grab.add_argument("--online-endpoint", help="Explicit online parser endpoint")
    grab.add_argument("--allow-online-parser", action="store_true")
    grab.add_argument("--ffmpeg", help="FFmpeg command or executable")
    grab.add_argument("--ffprobe", help="FFprobe command or executable")
    grab.add_argument("--ytdlp", help="Optional yt-dlp fallback command or executable")
    return result


def main(argv=None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        if arguments == ["--version"]:
            release = read_release(ROOT)
            print(f"v{release['version']} (iteration {release['iteration']:03d}, {release['released_on']})")
            return 0
        if arguments and arguments[0] in WORKFLOW_COMMANDS:
            return workflow(arguments[0], arguments[1:])
        # Local help remains available while the independent provider is absent.
        if arguments and arguments[0] == "acquire":
            if "--help" in arguments[1:] or "-h" in arguments[1:]:
                parser().parse_args(["acquire", "--help"])
            return acquire(arguments[1:])
        args = parser().parse_args(arguments)
        result = args.handler(args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("status") != "MISSING_DEPENDENCIES" else 1
    except (ValueError, OSError, RuntimeError) as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
