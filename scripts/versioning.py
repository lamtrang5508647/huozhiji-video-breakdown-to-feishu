#!/usr/bin/env python3
"""Check or prepare one synchronized repository/Skill release; never publish it."""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ("analyze-folder-videos-to-feishu", "video-link-extractor")
START = "<!-- release-version:start -->"
END = "<!-- release-version:end -->"


def read_release(root: Path = ROOT) -> dict:
    value = json.loads((root / "VERSION.json").read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict) or set(value) != {"schema_version", "version", "iteration", "released_on"}:
        raise ValueError("VERSION.json must contain exactly schema_version, version, iteration and released_on")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ValueError("Unsupported release schema_version")
    if not isinstance(value["version"], str) or not re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", value["version"]):
        raise ValueError("version must be a stable SemVer X.Y.Z")
    if type(value["iteration"]) is not int or value["iteration"] < 1:
        raise ValueError("iteration must be a positive integer")
    validate_date(value["released_on"])
    return value


def validate_date(value: str) -> date:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise ValueError("Release date must be YYYY-MM-DD")
    return date.fromisoformat(value)


def heading(release: dict) -> str:
    return f"## v{release['version']} · 第 {release['iteration']:03d} 次正式版本迭代 · {release['released_on']}"


def readme_block(release: dict) -> str:
    return (f"{START}\n当前版本 **v{release['version']}** · 第 **{release['iteration']:03d}** 次正式版本迭代"
            f"（{release['released_on']}）。[更新日志](CHANGELOG.md) · [版本说明](docs/versioning.md) · "
            "[下载与历史版本](https://github.com/lamtrang5508647/huozhiji-video-breakdown-to-feishu/releases)\n"
            f"{END}")


def version_paths(root: Path) -> list[Path]:
    return [root / "VERSION.json", *[root / "skill" / name / "VERSION.json" for name in SKILLS]]


def check(root: Path = ROOT, tag: str | None = None) -> dict:
    release = read_release(root)
    for name in SKILLS:
        if read_release(root / "skill" / name) != release:
            raise ValueError(f"Skill version mismatch: {name}")
    readme = (root / "README.md").read_text(encoding="utf-8")
    if readme.count(START) != 1 or readme.count(END) != 1 or readme_block(release) not in readme:
        raise ValueError("README release marker is missing or stale")
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    headings = re.findall(r"^## .+$", changelog, re.MULTILINE)
    if not headings or headings[0] != heading(release):
        raise ValueError("CHANGELOG first release must match VERSION.json")
    if tag is not None and tag != "v" + release["version"]:
        raise ValueError("Tag does not match VERSION.json")
    return release


def bump(root: Path, part: str, released_on: str, notes: str) -> dict:
    current = check(root)
    if part not in ("patch", "minor", "major"):
        raise ValueError("Unknown SemVer part")
    if validate_date(released_on) < validate_date(current["released_on"]):
        raise ValueError("Release date cannot precede the previous release")
    if not notes.strip() or re.search(r"^#{1,2}(?:\s|$)", notes, re.MULTILINE):
        raise ValueError("Release notes must be nonempty and cannot contain H1/H2 headings")
    major, minor, patch = map(int, current["version"].split("."))
    version = {"patch": (major, minor, patch + 1), "minor": (major, minor + 1, 0), "major": (major + 1, 0, 0)}[part]
    release = dict(current, version=".".join(map(str, version)), iteration=current["iteration"] + 1, released_on=released_on)
    serialized = json.dumps(release, ensure_ascii=False, indent=2) + "\n"
    changes = {path: serialized for path in version_paths(root)}
    readme = (root / "README.md").read_text(encoding="utf-8")
    changes[root / "README.md"] = readme.replace(readme_block(current), readme_block(release), 1)
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    changes[root / "CHANGELOG.md"] = changelog.replace(heading(current), heading(release) + "\n\n" + notes.strip() + "\n\n" + heading(current), 1)
    originals = {path: path.read_bytes() for path in changes}
    changed = []
    try:
        for path, content in changes.items():
            changed.append(path)
            path.write_text(content, encoding="utf-8", newline="\n")
        check(root)
    except Exception as original_error:
        # Restore the release set on an ordinary write/check failure. A power
        # loss is still detectable by check; no Git commits or tags are made.
        restore_errors = []
        for path in reversed(changed):
            try:
                if path.read_bytes() != originals[path]:
                    path.write_bytes(originals[path])
            except OSError as exc:
                restore_errors.append(f"{path.name}: {exc}")
        if restore_errors:
            raise RuntimeError("Release preparation failed; some files could not be restored: " + "; ".join(restore_errors)) from original_error
        raise
    return release


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    verify = commands.add_parser("check", help="Verify release metadata, Skill copies and visible documentation")
    verify.add_argument("--tag", help="Optional expected tag; does not query or create remote tags")
    prepare = commands.add_parser("bump", help="Prepare a local release; no commit, tag, upload or deployment")
    prepare.add_argument("--part", choices=("patch", "minor", "major"), required=True)
    prepare.add_argument("--date", required=True, help="Explicit release date YYYY-MM-DD")
    prepare.add_argument("--notes-file", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        release = check(ROOT, args.tag) if args.command == "check" else bump(ROOT, args.part, args.date, args.notes_file.read_text(encoding="utf-8-sig"))
        print(json.dumps({"status": "PASS" if args.command == "check" else "prepared_locally", "release": release, "published": False}, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, RuntimeError) as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
