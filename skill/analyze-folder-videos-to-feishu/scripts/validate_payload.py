#!/usr/bin/env python3
"""Validate a 26-field payload against the packaged and live Feishu schema."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


class ValidationFailure(RuntimeError):
    pass


def read_json(path: Path) -> Dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationFailure(f"Cannot read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValidationFailure(f"JSON root must be an object: {path}")
    return value


def resolve_executable(
    explicit: Optional[str], env_name: str, candidates: Sequence[str]
) -> Optional[str]:
    requested = explicit or os.environ.get(env_name)
    if requested:
        path = Path(requested).expanduser()
        if path.is_file():
            return str(path.resolve())
        found = shutil.which(requested)
        return found
    for candidate in candidates:
        found = shutil.which(candidate)
        if found:
            return found
    return None


def run_json_command(argv: Sequence[str]) -> Dict[str, Any]:
    completed = subprocess.run(
        list(argv),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    combined = completed.stdout.strip() or completed.stderr.strip()
    if completed.returncode != 0:
        raise ValidationFailure(
            f"Command failed ({completed.returncode}): {' '.join(argv[:3])}\n{combined}"
        )
    try:
        envelope = json.loads(combined)
    except json.JSONDecodeError as exc:
        raise ValidationFailure(
            f"Command did not return JSON: {' '.join(argv[:3])}\n{combined}"
        ) from exc
    if not isinstance(envelope, dict) or envelope.get("ok") is False:
        raise ValidationFailure(f"Command returned an error: {combined}")
    return envelope


def writable_fields(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [field for field in config["fields"] if field["type"] != "attachment"]


def validate_payload(
    payload: Dict[str, Any], config: Dict[str, Any]
) -> Tuple[List[str], List[str]]:
    errors: List[str] = []
    warnings: List[str] = []
    expected = writable_fields(config)
    expected_names = [field["name"] for field in expected]
    actual_names = list(payload.keys())

    missing = [name for name in expected_names if name not in payload]
    extra = [name for name in actual_names if name not in expected_names]
    if missing:
        errors.append(f"Missing fields: {missing}")
    if extra:
        errors.append(f"Unknown fields: {extra}")
    if not missing and not extra and actual_names != expected_names:
        errors.append("Payload field order does not match the final 26 writable fields")

    for field in expected:
        name = field["name"]
        if name not in payload:
            continue
        value = payload[name]
        if field["type"] == "text":
            if not isinstance(value, str):
                errors.append(f"{name} must be text")
            elif not value.strip():
                errors.append(f"{name} cannot be empty")
        elif field["type"] == "select":
            allowed = set(field.get("options", []))
            if field.get("multiple"):
                if not isinstance(value, list) or not value:
                    errors.append(f"{name} must be a non-empty array")
                    continue
                if any(not isinstance(item, str) for item in value):
                    errors.append(f"{name} contains a non-string option")
                    continue
                if len(value) != len(set(value)):
                    errors.append(f"{name} contains duplicate options")
                invalid = [item for item in value if item not in allowed]
            else:
                if not isinstance(value, str) or not value:
                    errors.append(f"{name} must be one option string")
                    continue
                invalid = [] if value in allowed else [value]
            if invalid:
                errors.append(f"{name} contains unknown options: {invalid}")

    script = payload.get("原脚本文案")
    if isinstance(script, str):
        if not script.rstrip().endswith(("（口播）", "（混剪）")):
            errors.append("原脚本文案 must end with （口播） or （混剪）")
        if re.search(r"(?:\[|【)?\d{1,2}:\d{2}(?::\d{2})?(?:\]|】)?", script):
            errors.append("原脚本文案 contains timestamps")

    structure = payload.get("结构公式")
    if isinstance(structure, str) and structure.count("→") < 2:
        warnings.append("结构公式 should normally contain at least three chronological stages")

    distinct_pairs = [
        ("主卖点", "次卖点"),
        ("主痛点", "次痛点"),
        ("开头高光画面", "开头留人画面为什么留人"),
        ("开头高光画面", "开头文案为什么留人"),
    ]
    for left, right in distinct_pairs:
        if payload.get(left) and payload.get(left) == payload.get(right):
            errors.append(f"{left} and {right} must not be identical")

    for name in ("前三秒钩子", "不可照搬点"):
        value = payload.get(name)
        if isinstance(value, str) and len(value.strip()) < 12:
            warnings.append(f"{name} may be too vague for review")

    return errors, warnings


def get_live_fields(
    config: Dict[str, Any], lark_cli: Optional[str] = None
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    executable = resolve_executable(lark_cli, "LARK_CLI", ("lark-cli",))
    if not executable:
        raise ValidationFailure("lark-cli not found; install it or pass --lark-cli")
    envelope = run_json_command(
        [
            executable,
            "base",
            "+field-list",
            "--base-token",
            config["base_token"],
            "--table-id",
            config["table_id"],
            "--limit",
            "200",
            "--format",
            "json",
            "--as",
            "user",
        ]
    )
    fields = envelope.get("data", {}).get("fields")
    if not isinstance(fields, list):
        raise ValidationFailure("Live field-list response has no fields array")
    return fields, envelope


def validate_live_schema(
    config: Dict[str, Any], live_fields: List[Dict[str, Any]]
) -> Tuple[List[str], List[str]]:
    errors: List[str] = []
    warnings: List[str] = []
    expected_by_name = {field["name"]: field for field in config["fields"]}
    live_by_name = {field.get("name"): field for field in live_fields}

    missing = sorted(set(expected_by_name) - set(live_by_name))
    extra = sorted(set(live_by_name) - set(expected_by_name))
    if missing:
        errors.append(f"Live Base is missing final fields: {missing}")
    if extra:
        errors.append(f"Live Base has unexpected fields: {extra}")

    for name, expected in expected_by_name.items():
        live = live_by_name.get(name)
        if not live:
            continue
        if live.get("id") != expected.get("id"):
            errors.append(
                f"{name} field ID changed: expected {expected.get('id')}, got {live.get('id')}"
            )
        if live.get("type") != expected.get("type"):
            errors.append(
                f"{name} type changed: expected {expected.get('type')}, got {live.get('type')}"
            )
        if expected.get("type") == "select":
            if bool(live.get("multiple")) != bool(expected.get("multiple")):
                errors.append(f"{name} select multiplicity changed")
            expected_options = set(expected.get("options", []))
            live_options = {
                option.get("name")
                for option in live.get("options", [])
                if isinstance(option, dict)
            }
            removed = sorted(expected_options - live_options)
            added = sorted(live_options - expected_options)
            if removed:
                errors.append(f"{name} live options removed: {removed}")
            if added:
                warnings.append(f"{name} has new live options not in this package: {added}")

    attachment = live_by_name.get(config["attachment_field_name"])
    if attachment and attachment.get("id") != config["attachment_field_id"]:
        errors.append("Attachment field ID no longer matches the explicitly supplied config")
    return errors, warnings


def build_result(
    payload_path: Path,
    payload: Dict[str, Any],
    static_errors: List[str],
    static_warnings: List[str],
    live_errors: Optional[List[str]] = None,
    live_warnings: Optional[List[str]] = None,
) -> Dict[str, Any]:
    errors = static_errors + (live_errors or [])
    warnings = static_warnings + (live_warnings or [])
    return {
        "ok": not errors,
        "payload": str(payload_path.resolve()),
        "field_count": len(payload),
        "static_schema": "pass" if not static_errors else "fail",
        "live_schema": (
            "not_checked"
            if live_errors is None
            else ("pass" if not live_errors else "fail")
        ),
        "errors": errors,
        "warnings": warnings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("payload", type=Path)
    parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="explicit config for the selected legacy 27-field Base",
    )
    parser.add_argument("--live", action="store_true", help="also validate the live Base schema")
    parser.add_argument("--lark-cli", help="absolute path or command name for lark-cli")
    args = parser.parse_args()

    try:
        config = read_json(args.config)
        payload = read_json(args.payload)
        static_errors, static_warnings = validate_payload(payload, config)
        live_errors: Optional[List[str]] = None
        live_warnings: Optional[List[str]] = None
        if args.live:
            live_fields, _ = get_live_fields(config, args.lark_cli)
            live_errors, live_warnings = validate_live_schema(config, live_fields)
        result = build_result(
            args.payload,
            payload,
            static_errors,
            static_warnings,
            live_errors,
            live_warnings,
        )
    except ValidationFailure as exc:
        result = {"ok": False, "errors": [str(exc)], "warnings": []}

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
