#!/usr/bin/env python3
"""Check cross-platform dependencies, Feishu login, and the live Base schema."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from validate_payload import (
    ValidationFailure,
    get_live_fields,
    read_json,
    resolve_executable,
    run_json_command,
    validate_live_schema,
)


def command_version(executable: str, args: Sequence[str]) -> str:
    completed = subprocess.run(
        [executable, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    output = (completed.stdout or completed.stderr).strip().splitlines()
    return output[0] if output else "unknown"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="explicit config for the selected legacy 27-field Base",
    )
    parser.add_argument("--ffmpeg", help="absolute path or command name")
    parser.add_argument("--lark-cli", help="absolute path or command name")
    parser.add_argument("--live", action="store_true", help="require login and validate live Base")
    args = parser.parse_args()

    errors: List[str] = []
    warnings: List[str] = []
    result: Dict[str, Any] = {
        "ok": False,
        "platform": platform.platform(),
        "python": {
            "version": platform.python_version(),
            "executable": sys.executable,
            "supported": sys.version_info >= (3, 9),
        },
    }
    if sys.version_info < (3, 9):
        errors.append("Python 3.9 or newer is required")

    ffmpeg = resolve_executable(args.ffmpeg, "FFMPEG_BIN", ("ffmpeg",))
    result["ffmpeg"] = {
        "found": bool(ffmpeg),
        "path": ffmpeg,
        "version": command_version(ffmpeg, ("-version",)) if ffmpeg else None,
    }
    if not ffmpeg:
        errors.append("ffmpeg not found; install it or pass --ffmpeg")

    lark_cli = resolve_executable(args.lark_cli, "LARK_CLI", ("lark-cli",))
    result["lark_cli"] = {
        "found": bool(lark_cli),
        "path": lark_cli,
        "version": command_version(lark_cli, ("--version",)) if lark_cli else None,
    }
    if args.live and not lark_cli:
        errors.append("lark-cli not found; install it or pass --lark-cli")

    asr = {
        "openai_whisper": shutil.which("whisper"),
        "whisper_cpp": shutil.which("whisper-cli") or shutil.which("main"),
    }
    result["offline_asr"] = asr
    if not any(asr.values()):
        warnings.append("No offline ASR found; videos without readable subtitles must be skipped")

    if args.live and lark_cli:
        try:
            auth = run_json_command([lark_cli, "auth", "status"])
            user_status = (
                auth.get("identities", {}).get("user", {}).get("status")
                or auth.get("data", {}).get("identities", {}).get("user", {}).get("status")
            )
            result["feishu_user_auth"] = user_status or "unknown"
            if user_status != "ready":
                errors.append("Feishu user identity is not ready; run lark-cli auth login --domain base")

            config = read_json(args.config)
            live_fields, field_envelope = get_live_fields(config, lark_cli)
            live_errors, live_warnings = validate_live_schema(config, live_fields)
            errors.extend(live_errors)
            warnings.extend(live_warnings)
            result["live_schema"] = {
                "status": "pass" if not live_errors else "fail",
                "field_count": len(live_fields),
                "identity": field_envelope.get("identity"),
            }
        except ValidationFailure as exc:
            errors.append(str(exc))

    result["errors"] = errors
    result["warnings"] = warnings
    result["ok"] = not errors
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
