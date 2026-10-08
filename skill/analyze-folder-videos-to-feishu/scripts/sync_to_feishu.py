#!/usr/bin/env python3
"""Validate, write, attach, read back, and share one Feishu Base record."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from validate_payload import (
    ValidationFailure,
    get_live_fields,
    read_json,
    resolve_executable,
    validate_live_schema,
    validate_payload,
)


class SyncFailure(RuntimeError):
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.details = details or {}


def run_lark(executable: str, args: Sequence[str]) -> Dict[str, Any]:
    argv = [executable, *args]
    completed = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    output = completed.stdout.strip() or completed.stderr.strip()
    try:
        envelope = json.loads(output) if output else {}
    except json.JSONDecodeError:
        envelope = {"raw_output": output}
    if completed.returncode != 0 or envelope.get("ok") is False:
        raise SyncFailure(
            f"lark-cli command failed ({completed.returncode})",
            {"command": args[:2], "response": envelope},
        )
    if not isinstance(envelope, dict):
        raise SyncFailure("lark-cli returned a non-object JSON response")
    return envelope


def user_auth_ready(executable: str) -> Dict[str, Any]:
    envelope = run_lark(executable, ("auth", "status"))
    identities = envelope.get("identities") or envelope.get("data", {}).get("identities") or {}
    status = identities.get("user", {}).get("status")
    if status != "ready":
        raise SyncFailure(
            "Feishu user identity is not ready",
            {"recovery": "lark-cli auth login --domain base", "status": status},
        )
    return envelope


def notices_from(envelopes: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    notices: List[Dict[str, Any]] = []
    for envelope in envelopes:
        notice = envelope.get("_notice")
        if isinstance(notice, dict) and notice not in notices:
            notices.append(notice)
    return notices


def search_exact_title(
    executable: str, config: Dict[str, Any], title: str
) -> Tuple[List[str], Dict[str, Any]]:
    envelope = run_lark(
        executable,
        (
            "base",
            "+record-search",
            "--base-token",
            config["base_token"],
            "--table-id",
            config["table_id"],
            "--keyword",
            title,
            "--search-field",
            "案例标题",
            "--field-id",
            "案例标题",
            "--limit",
            "200",
            "--format",
            "json",
            "--as",
            "user",
        ),
    )
    data = envelope.get("data", {})
    if data.get("has_more"):
        raise SyncFailure("Duplicate search returned more than 200 candidates; narrow manually")
    fields = data.get("fields", [])
    rows = data.get("data", [])
    record_ids = data.get("record_id_list", [])
    try:
        title_index = fields.index("案例标题")
    except ValueError:
        return [], envelope
    exact = [
        record_id
        for record_id, row in zip(record_ids, rows)
        if isinstance(row, list)
        and title_index < len(row)
        and row[title_index] == title
    ]
    return exact, envelope


def resolve_new_record_id(
    executable: str,
    config: Dict[str, Any],
    title: str,
    before_ids: Sequence[str],
    attempts: int,
    delay: float,
) -> Tuple[str, List[Dict[str, Any]]]:
    envelopes: List[Dict[str, Any]] = []
    latest_ids: List[str] = []
    for attempt in range(attempts):
        latest_ids, envelope = search_exact_title(executable, config, title)
        envelopes.append(envelope)
        new_ids = [record_id for record_id in latest_ids if record_id not in before_ids]
        if len(new_ids) == 1:
            return new_ids[0], envelopes
        if len(new_ids) > 1:
            break
        if attempt + 1 < attempts and delay > 0:
            time.sleep(delay)
    raise SyncFailure(
        "Record was written but its ID could not be resolved uniquely",
        {"before_ids": list(before_ids), "after_ids": latest_ids, "attempts": attempts},
    )


def find_record_id(value: Any) -> Optional[str]:
    if isinstance(value, dict):
        for key in ("record_id", "id"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.startswith("rec"):
                return candidate
        for child in value.values():
            found = find_record_id(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = find_record_id(child)
            if found:
                return found
    return None


def read_record(
    executable: str,
    config: Dict[str, Any],
    record_id: str,
    field_names: Sequence[str],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    args: List[str] = [
        "base",
        "+record-get",
        "--base-token",
        config["base_token"],
        "--table-id",
        config["table_id"],
        "--record-id",
        record_id,
    ]
    for name in field_names:
        args.extend(("--field-id", name))
    args.extend(("--format", "json", "--as", "user"))
    envelope = run_lark(executable, args)
    data = envelope.get("data", {})
    fields = data.get("fields", [])
    rows = data.get("data", [])
    ids = data.get("record_id_list", [])
    if not rows or record_id not in ids:
        raise SyncFailure("Created/updated record cannot be read back", {"record_id": record_id})
    row = rows[ids.index(record_id)]
    if not isinstance(row, list) or len(row) != len(fields):
        raise SyncFailure("Record readback matrix is malformed", {"record_id": record_id})
    return dict(zip(fields, row)), envelope


def values_equal(field: Dict[str, Any], expected: Any, actual: Any) -> bool:
    if field["type"] == "text":
        return expected == actual
    if field["type"] == "select" and field.get("multiple"):
        return isinstance(actual, list) and expected == actual
    if field["type"] == "select":
        return actual == expected or actual == [expected]
    return expected == actual


def verify_readback(
    config: Dict[str, Any],
    payload: Dict[str, Any],
    readback: Dict[str, Any],
    attachments: Sequence[Path],
) -> List[str]:
    errors: List[str] = []
    for field in config["fields"]:
        name = field["name"]
        if field["type"] == "attachment":
            continue
        if name not in readback:
            errors.append(f"Readback is missing {name}")
            continue
        if not values_equal(field, payload[name], readback[name]):
            errors.append(
                f"Readback mismatch for {name}: expected {payload[name]!r}, got {readback[name]!r}"
            )

    actual_attachments = readback.get(config["attachment_field_name"])
    if not isinstance(actual_attachments, list):
        errors.append("Attachment readback is missing or malformed")
        return errors
    actual_keys = {
        (item.get("name"), item.get("size"))
        for item in actual_attachments
        if isinstance(item, dict)
    }
    for path in attachments:
        expected_key = (path.name, path.stat().st_size)
        if expected_key not in actual_keys:
            errors.append(f"Attachment readback is missing {path.name} ({path.stat().st_size} bytes)")
    return errors


def verify_readback_with_retry(
    executable: str,
    config: Dict[str, Any],
    record_id: str,
    payload: Dict[str, Any],
    attachments: Sequence[Path],
    attempts: int,
    delay: float,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[str]]:
    envelopes: List[Dict[str, Any]] = []
    latest_readback: Dict[str, Any] = {}
    latest_errors: List[str] = []
    all_field_names = [field["name"] for field in config["fields"]]
    for attempt in range(attempts):
        try:
            latest_readback, envelope = read_record(
                executable, config, record_id, all_field_names
            )
            envelopes.append(envelope)
            latest_errors = verify_readback(
                config, payload, latest_readback, attachments
            )
            if not latest_errors:
                return latest_readback, envelopes, []
        except SyncFailure as exc:
            latest_errors = [str(exc)]
        if attempt + 1 < attempts and delay > 0:
            time.sleep(delay)
    return latest_readback, envelopes, latest_errors


def missing_attachments(
    existing: Any, requested: Sequence[Path]
) -> List[Path]:
    entries = existing if isinstance(existing, list) else []
    existing_keys = {
        (item.get("name"), item.get("size"))
        for item in entries
        if isinstance(item, dict)
    }
    size_by_name = {
        item.get("name"): item.get("size")
        for item in entries
        if isinstance(item, dict) and item.get("name")
    }
    missing: List[Path] = []
    for path in requested:
        key = (path.name, path.stat().st_size)
        if key in existing_keys:
            continue
        if path.name in size_by_name and size_by_name[path.name] != path.stat().st_size:
            raise SyncFailure(
                "Attachment with the same name but a different size already exists",
                {
                    "name": path.name,
                    "existing_size": size_by_name[path.name],
                    "new_size": path.stat().st_size,
                },
            )
        missing.append(path)
    return missing


def write_result(path: Optional[Path], result: Dict[str, Any]) -> None:
    if not path:
        return
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="explicit config for the selected legacy 27-field Base",
    )
    parser.add_argument("--lark-cli", help="absolute path or command name")
    parser.add_argument("--record-id", help="update/resume a known record instead of creating")
    parser.add_argument("--allow-duplicate", action="store_true")
    parser.add_argument("--write", action="store_true", help="perform writes; omitted means dry run")
    parser.add_argument("--no-share", action="store_true")
    parser.add_argument("--result", type=Path, help="also save the result JSON")
    parser.add_argument("--post-write-delay", type=float, default=0.75)
    parser.add_argument("--readback-attempts", type=int, default=6)
    parser.add_argument("--retry-delay", type=float, default=0.5)
    args = parser.parse_args()

    if args.readback_attempts < 1:
        parser.error("--readback-attempts must be at least 1")
    if args.retry_delay < 0 or args.post_write_delay < 0:
        parser.error("retry and post-write delays cannot be negative")

    partial_record_id: Optional[str] = args.record_id
    envelopes: List[Dict[str, Any]] = []
    try:
        config = read_json(args.config)
        payload = read_json(args.payload)
        video = args.video.expanduser().resolve()
        report = args.report.expanduser().resolve()
        for path, label in ((video, "video"), (report, "report")):
            if not path.is_file():
                raise SyncFailure(f"{label} file does not exist", {"path": str(path)})

        static_errors, warnings = validate_payload(payload, config)
        if static_errors:
            raise SyncFailure("Static payload validation failed", {"errors": static_errors})

        executable = resolve_executable(args.lark_cli, "LARK_CLI", ("lark-cli",))
        if not executable:
            raise SyncFailure("lark-cli not found; install it or pass --lark-cli")
        envelopes.append(user_auth_ready(executable))

        try:
            live_fields, live_envelope = get_live_fields(config, executable)
        except ValidationFailure as exc:
            raise SyncFailure(str(exc)) from exc
        envelopes.append(live_envelope)
        live_errors, live_warnings = validate_live_schema(config, live_fields)
        warnings.extend(live_warnings)
        if live_errors:
            raise SyncFailure("Live schema validation failed", {"errors": live_errors})

        title = payload["案例标题"]
        before_ids, duplicate_envelope = search_exact_title(executable, config, title)
        envelopes.append(duplicate_envelope)
        conflicts = [record_id for record_id in before_ids if record_id != args.record_id]
        if conflicts and not args.allow_duplicate:
            raise SyncFailure(
                "Exact title already exists; duplicate write blocked",
                {"title": title, "record_ids": conflicts},
            )

        upsert_args: List[str] = [
            "base",
            "+record-upsert",
            "--base-token",
            config["base_token"],
            "--table-id",
            config["table_id"],
            "--json",
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            "--format",
            "json",
            "--as",
            "user",
        ]
        if args.record_id:
            upsert_args.extend(("--record-id", args.record_id))

        if not args.write:
            preview = run_lark(executable, (*upsert_args, "--dry-run"))
            envelopes.append(preview)
            result = {
                "ok": True,
                "mode": "dry_run",
                "title": title,
                "target": {
                    "base": config["base_name"],
                    "table": config["table_name"],
                    "base_url": config["base_url"],
                },
                "field_count": len(payload),
                "attachments": [str(video), str(report)],
                "warnings": warnings,
                "notices": notices_from(envelopes),
                "preview": preview.get("data", preview),
            }
            write_result(args.result, result)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0

        upsert_envelope = run_lark(executable, upsert_args)
        envelopes.append(upsert_envelope)
        record_id = args.record_id or find_record_id(upsert_envelope)
        if not record_id:
            record_id, search_envelopes = resolve_new_record_id(
                executable,
                config,
                title,
                before_ids,
                args.readback_attempts,
                args.retry_delay,
            )
            envelopes.extend(search_envelopes)
        partial_record_id = record_id

        attachment_name = config["attachment_field_name"]
        current: Dict[str, Any] = {attachment_name: []}
        if args.record_id:
            last_read_error: Optional[SyncFailure] = None
            for attempt in range(args.readback_attempts):
                try:
                    current, current_envelope = read_record(
                        executable, config, record_id, (attachment_name,)
                    )
                    envelopes.append(current_envelope)
                    last_read_error = None
                    break
                except SyncFailure as exc:
                    last_read_error = exc
                    if attempt + 1 < args.readback_attempts and args.retry_delay > 0:
                        time.sleep(args.retry_delay)
            if last_read_error:
                raise last_read_error
        requested_attachments = (video, report)
        to_upload = missing_attachments(current.get(attachment_name), requested_attachments)
        if to_upload:
            upload_args: List[str] = [
                "base",
                "+record-upload-attachment",
                "--base-token",
                config["base_token"],
                "--table-id",
                config["table_id"],
                "--record-id",
                record_id,
                "--field-id",
                config["attachment_field_id"],
            ]
            for path in to_upload:
                upload_args.extend(("--file", str(path)))
            upload_args.extend(("--format", "json", "--as", "user"))
            upload_envelope = run_lark(executable, upload_args)
            envelopes.append(upload_envelope)

        readback, readback_envelopes, readback_errors = verify_readback_with_retry(
            executable,
            config,
            record_id,
            payload,
            requested_attachments,
            args.readback_attempts,
            args.retry_delay,
        )
        envelopes.extend(readback_envelopes)
        if readback_errors:
            raise SyncFailure(
                "Record readback validation failed",
                {"record_id": record_id, "errors": readback_errors},
            )

        share_link: Optional[str] = None
        if not args.no_share:
            share_envelope = run_lark(
                executable,
                (
                    "base",
                    "+record-share-link-create",
                    "--base-token",
                    config["base_token"],
                    "--table-id",
                    config["table_id"],
                    "--record-ids",
                    record_id,
                    "--format",
                    "json",
                    "--as",
                    "user",
                ),
            )
            envelopes.append(share_envelope)
            share_link = (
                share_envelope.get("data", {})
                .get("record_share_links", {})
                .get(record_id)
            )
            if not share_link:
                raise SyncFailure(
                    "Record passed readback but share link creation failed",
                    {"record_id": record_id, "response": share_envelope},
                )

        result = {
            "ok": True,
            "mode": "write",
            "record_id": record_id,
            "title": title,
            "record_link": share_link,
            "field_count": len(payload),
            "attachment_count_verified": 2,
            "attachments": [path.name for path in requested_attachments],
            "readback": "pass",
            "warnings": warnings,
            "notices": notices_from(envelopes),
        }
        write_result(args.result, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.post_write_delay > 0:
            time.sleep(args.post_write_delay)
        return 0
    except (SyncFailure, ValidationFailure) as exc:
        details = exc.details if isinstance(exc, SyncFailure) else {}
        result = {
            "ok": False,
            "mode": "write" if args.write else "dry_run",
            "error": str(exc),
            "partial_record_id": partial_record_id,
            "details": details,
            "recovery": (
                "If partial_record_id is present, fix the cause and resume with --record-id; "
                "do not create another record."
            ),
        }
        write_result(args.result, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    sys.exit(main())
