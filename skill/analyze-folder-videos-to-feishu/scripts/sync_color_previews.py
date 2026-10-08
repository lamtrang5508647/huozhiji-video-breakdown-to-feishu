"""Idempotent, bot-only PNG/text sync for approved script-preview manifests.

No base/table/record identifiers are embedded here. The manifest is the sole
source of target coordinates. Default invocation is local validation/dry-run;
remote writes require --apply and the exact manifest SHA-256.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from typing import Any

WORKSPACE = Path.cwd().resolve()
SYNC_DIR = WORKSPACE / ".lark-preview-sync"
STATE_PATH = SYNC_DIR / "color-preview-sync-state.json"
LOG_PATH = SYNC_DIR / "color-preview-sync.log.jsonl"
REMOTE_DIR = SYNC_DIR / "remote-integrity"
TOOL_FILE_RE = re.compile(r"^scriptv4-preview--([0-9a-f]{64})\.png$")
BASE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
TABLE_ID_RE = re.compile(r"^tbl[A-Za-z0-9_-]{4,128}$")
RECORD_ID_RE = re.compile(r"^rec[A-Za-z0-9_-]{4,128}$")
MAIN_TEXT_ALLOW = {"标色原文", "可复用变量公式"}
SEGMENT_TEXT_ALLOW = {"标色原文"}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def workspace_path(value: str | Path) -> Path:
    p = Path(value)
    if not p.is_absolute():
        p = WORKSPACE / p
    p = p.resolve()
    if not p.is_relative_to(WORKSPACE):
        raise ValueError(f"Path escapes workspace: {p}")
    return p


def workspace_arg(path: Path) -> str:
    return path.resolve().relative_to(WORKSPACE).as_posix()


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: ("[REDACTED]" if any(x in k.lower() for x in ("token", "secret", "authorization")) else redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def parse_manifest(path: Path) -> tuple[dict, str]:
    raw = path.read_bytes()
    manifest_sha = sha256_bytes(raw)
    data = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(data.get("base_id"), str) or not data["base_id"].strip():
        raise ValueError("manifest.base_id is required")
    if not BASE_ID_RE.fullmatch(data["base_id"]):
        raise ValueError("manifest.base_id has an invalid shape")
    if not isinstance(data.get("records"), list) or not data["records"]:
        raise ValueError("manifest.records must be a non-empty array")
    seen: set[tuple[str, str]] = set()
    for index, row in enumerate(data["records"]):
        for key in ("section_id", "table_id", "record_id", "attachment_field", "preview_path", "preview_filename", "sha256", "fingerprint", "fingerprint_field", "text_updates"):
            if key not in row:
                raise ValueError(f"records[{index}] missing {key}")
        if not isinstance(row["section_id"], str) or not row["section_id"].strip():
            raise ValueError(f"invalid section_id in records[{index}]")
        if row["attachment_field"] != "彩色脚本预览" or row["fingerprint_field"] != "预览指纹":
            raise ValueError(f"Refusing non-dedicated attachment/fingerprint field in records[{index}]")
        ident = (row["table_id"], row["record_id"])
        if not TABLE_ID_RE.fullmatch(row["table_id"]) or not RECORD_ID_RE.fullmatch(row["record_id"]):
            raise ValueError(f"invalid table/record identifier in records[{index}]")
        if ident in seen:
            raise ValueError(f"duplicate target row: {ident}")
        seen.add(ident)
        image = workspace_path(row["preview_path"])
        if not image.is_file():
            raise ValueError(f"preview file missing: {image}")
        if image.stat().st_size <= 0 or image.stat().st_size >= 20 * 1024 * 1024:
            raise ValueError(f"preview PNG must be non-empty and smaller than 20 MiB: {image}")
        with image.open("rb") as png:
            if png.read(8) != b"\x89PNG\r\n\x1a\n":
                raise ValueError(f"preview file is not a PNG: {image}")
        actual_sha = sha256_file(image)
        if actual_sha != row["sha256"]:
            raise ValueError(f"local preview SHA mismatch for {row['record_id']}")
        m = TOOL_FILE_RE.fullmatch(row["preview_filename"])
        if not m or m.group(1) != row["sha256"]:
            raise ValueError(f"preview filename must embed the declared SHA-256: {row['preview_filename']}")
        if image.name != row["preview_filename"]:
            raise ValueError(f"preview filename/path mismatch for {row['record_id']}")
        if not isinstance(row["text_updates"], dict):
            raise ValueError(f"text_updates must be an object for {row['record_id']}")
        if not isinstance(row.get("expected_text_fields", {}), dict):
            raise ValueError(f"expected_text_fields must be an object for {row['record_id']}")
    return data, manifest_sha


def allowed_text_fields(row: dict, rows: list[dict]) -> set[str]:
    # The explicit new field marks the main-record payload. Existing formula
    # fields remain read-only even though their values are checked separately.
    return MAIN_TEXT_ALLOW if row["section_id"] == "main" else SEGMENT_TEXT_ALLOW


def validate_text_payload(manifest: dict) -> None:
    for row in manifest["records"]:
        allowed = allowed_text_fields(row, manifest["records"])
        unexpected = set(row["text_updates"]) - allowed
        if unexpected:
            raise ValueError(f"Refusing protected/unknown text update for {row['record_id']}: {sorted(unexpected)}")
        if not set(row["text_updates"]).issubset(allowed):
            raise ValueError(f"Invalid text update fields for {row['record_id']}")
        expected = row.get("expected_text_fields", {})
        if set(expected) & set(row["text_updates"]):
            raise ValueError(f"A field cannot be both written and preserved: {row['record_id']}")


class LarkCLI:
    def __init__(self, base_id: str, identity: str = "bot", timeout: int = 55):
        if identity != "bot":
            raise ValueError("This synchronizer supports bot identity only")
        self.base_id = base_id
        self.identity = identity
        self.timeout = min(max(timeout, 1), 55)
        self.executable = shutil.which("lark-cli")
        if not self.executable:
            raise RuntimeError("lark-cli not found")

    def call(self, command: str, table_id: str | None = None, *, args: list[str] | None = None, body: dict | None = None, file_args: list[Path] | None = None, timeout: int | None = None) -> dict:
        argv = [self.executable, "base", command, "--base-token", self.base_id, "--as", self.identity, "--format", "json"]
        if table_id:
            argv.extend(["--table-id", table_id])
        for path in file_args or []:
            argv.extend(["--file", workspace_arg(path)])
        if args:
            argv.extend(args)
        if body is not None:
            argv.extend(["--json", json.dumps(body, ensure_ascii=False, separators=(",", ":"))])
        env = dict(os.environ)
        for key in list(env):
            if key.upper() in {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"}:
                env.pop(key, None)
        env["LARKSUITE_CLI_NO_UPDATE_NOTIFIER"] = "1"
        env["LARKSUITE_CLI_NO_SKILLS_NOTIFIER"] = "1"
        started = time.time()
        try:
            result = subprocess.run(argv, cwd=str(WORKSPACE), env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=min(timeout or self.timeout, 55))
        except subprocess.TimeoutExpired as e:
            self._log(command, table_id, args, body, "timeout", time.time() - started)
            raise TimeoutError(f"{command} exceeded bounded timeout; reconcile by read before retry") from e
        raw = result.stdout.strip() or result.stderr.strip()
        try:
            envelope = json.loads(raw)
        except ValueError:
            envelope = {"ok": False, "error": {"message": raw[:1200]}}
        self._log(command, table_id, args, body, envelope, time.time() - started, result.returncode)
        if result.returncode or envelope.get("ok") is not True:
            err = envelope.get("error", envelope)
            raise RuntimeError(f"{command} failed: {json.dumps(redact(err), ensure_ascii=False)[:1400]}")
        if envelope.get("identity") not in (None, self.identity):
            raise RuntimeError("Unexpected CLI identity in response")
        return envelope.get("data", {})

    @staticmethod
    def _log(command: str, table_id: str | None, args: list[str] | None, body: dict | None, response: Any, elapsed: float, exit_code: int | None = None) -> None:
        SYNC_DIR.mkdir(parents=True, exist_ok=True)
        entry = {"time": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "command": command, "table_id": table_id, "identity": "bot", "args": redact(args or []), "body": redact(body), "response": redact(response), "elapsed_seconds": round(elapsed, 3), "exit_code": exit_code}
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def records_from(data: dict) -> list[dict]:
    if isinstance(data.get("records"), list):
        out = []
        for rec in data["records"]:
            rid = rec.get("record_id", rec.get("id"))
            out.append({"record_id": rid, "fields": rec.get("fields", {})})
        return out
    if isinstance(data.get("record"), dict):
        r = data["record"]
        return [{"record_id": r.get("record_id", r.get("id")), "fields": r.get("fields", {})}]
    ids = data.get("record_id_list")
    values = data.get("data")
    names = data.get("fields", data.get("field_id_list"))
    if isinstance(ids, list) and isinstance(values, list) and isinstance(names, list) and len(ids) == len(values):
        return [{"record_id": rid, "fields": dict(zip(names, row))} for rid, row in zip(ids, values)]
    raise RuntimeError("Unrecognized +record-get response shape")


def one_record(cli: LarkCLI, table_id: str, record_id: str) -> dict:
    data = cli.call("+record-get", table_id, args=["--record-id", record_id])
    found = [r for r in records_from(data) if r["record_id"] == record_id]
    if len(found) != 1:
        raise RuntimeError(f"Target record not uniquely present in table {table_id}: {record_id}")
    return found[0]["fields"]


def field_map(cli: LarkCLI, table_id: str) -> dict[str, dict]:
    data = cli.call("+field-list", table_id)
    fields = data.get("fields")
    if not isinstance(fields, list):
        raise RuntimeError(f"Unexpected field-list response for {table_id}")
    return {x["name"]: x for x in fields}


def ensure_fields(cli: LarkCLI, manifest: dict) -> dict[str, str]:
    field_ids: dict[str, str] = {}
    specs_by_table: dict[str, dict[str, str]] = {}
    for row in manifest["records"]:
        specs = specs_by_table.setdefault(row["table_id"], {})
        specs[row["attachment_field"]] = "attachment"
        specs[row["fingerprint_field"]] = "text"
        for name in row["text_updates"]:
            specs[name] = "text"
    precreate = {table_id: field_map(cli, table_id) for table_id in specs_by_table}
    save_json(SYNC_DIR / "schema-backup-before-preview-fields.json", {"identity": "bot", "base_id": manifest["base_id"], "tables": precreate})
    for table_id, specs in specs_by_table.items():
        expected = [{"name": name, "type": typ} for name, typ in specs.items()]
        current = precreate[table_id]
        for spec in expected:
            actual = current.get(spec["name"])
            if actual is None:
                cli.call("+field-create", table_id, body=spec)
                current = field_map(cli, table_id)
                actual = current.get(spec["name"])
            if not actual or actual.get("type") != spec["type"]:
                raise RuntimeError(f"Field schema mismatch: {table_id}.{spec['name']} expected {spec['type']}")
            field_ids[f"{table_id}:{spec['name']}"] = actual.get("id", spec["name"])
    return field_ids


def attachments_in(fields: dict, field_name: str) -> list[dict]:
    value = fields.get(field_name, [])
    if value is None:
        return []
    if isinstance(value, dict):
        value = value.get("value", value.get("attachments", [value]))
    if not isinstance(value, list):
        raise RuntimeError(f"Attachment field {field_name} has unsupported shape")
    return [x for x in value if isinstance(x, dict)]


def attachment_name(item: dict) -> str | None:
    return item.get("name") or item.get("file_name") or item.get("filename")


def attachment_token(item: dict) -> str | None:
    return item.get("file_token") or item.get("token")


def download_and_hash(cli: LarkCLI, table_id: str, record_id: str, token: str, destination: Path) -> str:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        destination.unlink()
    cli.call("+record-download-attachment", table_id, args=["--record-id", record_id, "--file-token", token, "--output", workspace_arg(destination)])
    if not destination.is_file():
        raise RuntimeError(f"Attachment download did not create expected file: {destination}")
    return sha256_file(destination)


def expected_preserved(row: dict, fields: dict) -> None:
    for field_name, expected in row.get("expected_text_fields", {}).items():
        actual = fields.get(field_name)
        if actual != expected:
            raise RuntimeError(f"Preserved field changed or differs: {row['record_id']}.{field_name}")


def verify_attachment(cli: LarkCLI, row: dict, fields: dict, expected_dir: Path) -> tuple[dict, str]:
    items = attachments_in(fields, row["attachment_field"])
    matches = [x for x in items if attachment_name(x) == row["preview_filename"]]
    if len(matches) != 1:
        raise RuntimeError(f"Expected exactly one matching preview attachment in {row['record_id']}, found {len(matches)}")
    token = attachment_token(matches[0])
    if not token:
        raise RuntimeError(f"Matching attachment has no download token in {row['record_id']}")
    dest = expected_dir / f"{row['record_id']}--{row['preview_filename']}"
    actual_sha = download_and_hash(cli, row["table_id"], row["record_id"], token, dest)
    if actual_sha != row["sha256"]:
        raise RuntimeError(f"Remote attachment SHA mismatch for {row['record_id']}")
    return matches[0], actual_sha


def current_preview(cli: LarkCLI, row: dict, fields: dict, expected_dir: Path) -> tuple[bool, dict | None, str | None]:
    items = attachments_in(fields, row["attachment_field"])
    matches = [x for x in items if attachment_name(x) == row["preview_filename"]]
    if len(matches) > 1:
        raise RuntimeError(f"Duplicate exact preview filenames in {row['record_id']}; refusing update")
    if not matches:
        return False, None, None
    token = attachment_token(matches[0])
    if not token:
        raise RuntimeError(f"Matching preview has no file token in {row['record_id']}")
    dest = expected_dir / f"{row['record_id']}--{row['preview_filename']}"
    actual = download_and_hash(cli, row["table_id"], row["record_id"], token, dest)
    if actual != row["sha256"]:
        raise RuntimeError(f"Filename/SHA collision or corrupt attachment for {row['record_id']}")
    return True, matches[0], actual


def ownership_path() -> Path:
    return SYNC_DIR / "preview-ownership.json"


def read_ownership(base_id: str) -> dict:
    path = ownership_path()
    if not path.exists():
        return {"base_id": base_id, "items": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("base_id") != base_id or not isinstance(data.get("items"), dict):
        raise RuntimeError("Ownership state belongs to another Base or has invalid structure")
    return data


def ownership_key(row: dict) -> str:
    return f"{row['table_id']}::{row['record_id']}::{row['attachment_field']}"


def save_owned(row: dict, item: dict, manifest: dict) -> None:
    data = read_ownership(manifest["base_id"])
    key = ownership_key(row)
    owned = data["items"].setdefault(key, [])
    token = attachment_token(item)
    name = attachment_name(item)
    if not token or not name:
        raise RuntimeError("Cannot track ownership without attachment token and filename")
    if not any(x.get("file_token") == token for x in owned):
        owned.append({"file_token": token, "filename": name, "sha256": row["sha256"], "verified_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")})
    save_json(ownership_path(), data)


def cleanup_owned(cli: LarkCLI, row: dict, manifest: dict, fields: dict, keep_token: str, expected_dir: Path) -> tuple[list[str], list[str]]:
    data = read_ownership(manifest["base_id"])
    key = ownership_key(row)
    entries = data["items"].get(key, [])
    attachments = attachments_in(fields, row["attachment_field"])
    removed: list[str] = []
    managed_tokens = {x.get("file_token") for x in entries}
    retain = []
    for entry in entries:
        token, name, expected_sha = entry.get("file_token"), entry.get("filename"), entry.get("sha256")
        if token == keep_token:
            retain.append(entry)
            continue
        matches = [x for x in attachments if attachment_token(x) == token and attachment_name(x) == name]
        if not matches:
            # Already absent; do not infer anything from similarly named unknown files.
            continue
        if len(matches) != 1 or not TOOL_FILE_RE.fullmatch(name or ""):
            raise RuntimeError(f"Owned old preview metadata is ambiguous in {row['record_id']}")
        target = expected_dir / f"old--{row['record_id']}--{name}"
        actual_sha = download_and_hash(cli, row["table_id"], row["record_id"], token, target)
        embedded = TOOL_FILE_RE.fullmatch(name)
        if actual_sha != expected_sha or not embedded or embedded.group(1) != actual_sha:
            raise RuntimeError(f"Owned old preview integrity check failed in {row['record_id']}; refusing remove")
        cli.call("+record-remove-attachment", row["table_id"], args=["--record-id", row["record_id"], "--field-id", row["attachment_field"], "--file-token", token, "--yes"])
        after = one_record(cli, row["table_id"], row["record_id"])
        if any(attachment_token(x) == token for x in attachments_in(after, row["attachment_field"])):
            raise RuntimeError(f"Old owned preview still present after remove in {row['record_id']}")
        removed.append(name)
    data["items"][key] = retain + [x for x in entries if x.get("file_token") == keep_token and x not in retain]
    save_json(ownership_path(), data)
    unowned = sorted({attachment_name(x) for x in attachments if attachment_name(x) and TOOL_FILE_RE.fullmatch(attachment_name(x)) and attachment_token(x) not in managed_tokens and attachment_token(x) != keep_token})
    return removed, unowned


def update_text(cli: LarkCLI, row: dict, values: dict) -> None:
    cli.call("+record-batch-update", row["table_id"], body={"update_records": {row["record_id"]: values}})


def sync_row(cli: LarkCLI, row: dict, manifest: dict, expected_dir: Path, *, apply: bool) -> dict:
    before = one_record(cli, row["table_id"], row["record_id"])
    expected_preserved(row, before)
    existing, existing_item, existing_sha = current_preview(cli, row, before, expected_dir)
    expected_text = dict(row["text_updates"])
    expected_text[row["fingerprint_field"]] = row["fingerprint"]
    text_matches = all(before.get(k) == v for k, v in expected_text.items())
    if existing and text_matches:
        if apply and existing_item:
            save_owned(row, existing_item, manifest)
            removed, unowned = cleanup_owned(cli, row, manifest, before, attachment_token(existing_item), expected_dir)
        else:
            removed, unowned = [], []
        return {"record_id": row["record_id"], "table_id": row["table_id"], "status": "already_verified", "remote_sha256": existing_sha, "filename": attachment_name(existing_item), "text_write": False, "upload": False, "removed_owned_previews": removed, "unowned_tool_previews_preserved": unowned}
    if not apply:
        return {"record_id": row["record_id"], "table_id": row["table_id"], "status": "would_upload" if not existing else "would_reuse_verified_attachment", "text_write": not text_matches, "upload": not existing}
    if not existing:
        local = workspace_path(row["preview_path"])
        try:
            cli.call("+record-upload-attachment", row["table_id"], args=["--record-id", row["record_id"], "--field-id", row["attachment_field"]], file_args=[local])
        except (TimeoutError, RuntimeError):
            # The request may have committed before the client failed. Reconcile once;
            # never replay the upload without proving absence.
            after_error = one_record(cli, row["table_id"], row["record_id"])
            appeared, _, _ = current_preview(cli, row, after_error, expected_dir)
            if not appeared:
                raise RuntimeError(f"Upload outcome unknown and matching attachment absent for {row['record_id']}; stopped without retry")
        after_upload = one_record(cli, row["table_id"], row["record_id"])
        _, remote_sha = verify_attachment(cli, row, after_upload, expected_dir)
    else:
        after_upload = before
        _, remote_sha = verify_attachment(cli, row, after_upload, expected_dir)
    # Text/fingerprint write follows a verified remote download only.
    update_text(cli, row, expected_text)
    final = one_record(cli, row["table_id"], row["record_id"])
    for field_name, expected in expected_text.items():
        if final.get(field_name) != expected:
            raise RuntimeError(f"Text readback mismatch for {row['record_id']}.{field_name}")
    expected_preserved(row, final)
    final_item, final_sha = verify_attachment(cli, row, final, expected_dir)
    save_owned(row, final_item, manifest)
    removed, unowned = cleanup_owned(cli, row, manifest, final, attachment_token(final_item), expected_dir)
    return {"record_id": row["record_id"], "table_id": row["table_id"], "status": "verified", "remote_sha256": final_sha, "text_write": True, "upload": not existing, "removed_owned_previews": removed, "unowned_tool_previews_preserved": unowned}


def preflight_records(cli: LarkCLI, manifest: dict) -> None:
    for row in manifest["records"]:
        fields = one_record(cli, row["table_id"], row["record_id"])
        expected_preserved(row, fields)


def auto_appended_fields(manifest: dict, table_id: str) -> list[str]:
    out: list[str] = []
    for row in manifest["records"]:
        if row["table_id"] != table_id:
            continue
        for name in [row["attachment_field"], row["fingerprint_field"], *row["text_updates"].keys()]:
            if name not in out:
                out.append(name)
    return out


def sync_views(cli: LarkCLI, views: list[dict], manifest: dict, *, apply: bool) -> list[dict]:
    result = []
    for spec in views:
        table_id, view_id = spec["table_id"], spec["view_id"]
        visible = cli.call("+view-get-visible-fields", table_id, args=["--view-id", view_id]).get("visible_fields")
        before, after = spec["visible_before"], spec["visible_after"]
        if visible == after:
            pass
        elif visible == before:
            if apply:
                cli.call("+view-set-visible-fields", table_id, args=["--view-id", view_id], body={"visible_fields": after})
                reread = cli.call("+view-get-visible-fields", table_id, args=["--view-id", view_id]).get("visible_fields")
                if reread != after:
                    raise RuntimeError(f"Visible fields readback mismatch for {view_id}")
                visible = reread
            else:
                visible = before
        elif visible == before + auto_appended_fields(manifest, table_id):
            # Creating a Base field can make it appear at the end of views whose
            # visible-fields list previously omitted it. Treat only this exact,
            # predictable schema-created intermediate as safe to normalize.
            if apply:
                cli.call("+view-set-visible-fields", table_id, args=["--view-id", view_id], body={"visible_fields": after})
                reread = cli.call("+view-get-visible-fields", table_id, args=["--view-id", view_id]).get("visible_fields")
                if reread != after:
                    raise RuntimeError(f"Visible fields readback mismatch for {view_id}")
                visible = reread
            else:
                visible = after
        else:
            raise RuntimeError(f"View {view_id} is neither expected before nor after state; refusing to overwrite")
        # Read-only preservation checks: these commands never alter filters/sorts/covers.
        filter_data = cli.call("+view-get-filter", table_id, args=["--view-id", view_id])
        if normalize_filter(filter_data) != normalize_filter(spec["preserve_filter"]):
            raise RuntimeError(f"Filter changed unexpectedly for {view_id}")
        if spec.get("preserve_sort") is not None:
            sort_data = cli.call("+view-get-sort", table_id, args=["--view-id", view_id])
            if normalize_sort(sort_data) != normalize_sort(spec["preserve_sort"]):
                raise RuntimeError(f"Sort changed unexpectedly for {view_id}")
        card = None
        if spec.get("kind") == "gallery":
            card = cli.call("+view-get-card", table_id, args=["--view-id", view_id]).get("card", {})
            if card.get("cover_field") != spec.get("preserve_card_cover_field_id"):
                raise RuntimeError(f"Gallery cover changed unexpectedly for {view_id}")
        result.append({"table_id": table_id, "view_id": view_id, "visible_fields": visible, "cover_field": card.get("cover_field") if card is not None else None, "status": "verified" if apply else "dry_run_checked"})
    return result


def normalize_filter(data: Any) -> Any:
    if isinstance(data, dict) and "filter" in data:
        return data["filter"]
    return data


def normalize_sort(data: Any) -> Any:
    if isinstance(data, dict) and "sort" in data:
        return data["sort"]
    return data


def load_view_plan(path_value: str | None, manifest: dict) -> list[dict]:
    if not path_value:
        return []
    plan_path = workspace_path(path_value)
    plan = json.loads(plan_path.read_text(encoding="utf-8-sig"))
    if plan.get("base_id") != manifest["base_id"]:
        raise ValueError("view plan base_id does not match manifest")
    return plan.get("views", [])


def run(args: argparse.Namespace) -> int:
    global WORKSPACE, SYNC_DIR, STATE_PATH, LOG_PATH, REMOTE_DIR
    WORKSPACE = Path(args.workspace).resolve()
    if not WORKSPACE.is_dir():
        raise ValueError(f"workspace is not a directory: {WORKSPACE}")
    SYNC_DIR = workspace_path(args.state_dir)
    SYNC_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH = SYNC_DIR / "color-preview-sync-state.json"
    LOG_PATH = SYNC_DIR / "color-preview-sync.log.jsonl"
    REMOTE_DIR = SYNC_DIR / "remote-integrity"
    manifest_path = workspace_path(args.manifest)
    manifest, manifest_sha = parse_manifest(manifest_path)
    validate_text_payload(manifest)
    view_specs = load_view_plan(args.view_plan, manifest)
    if args.apply and args.approved_manifest_sha256 != manifest_sha:
        raise ValueError("--approved-manifest-sha256 must exactly match current manifest bytes")
    if not args.apply and args.approved_manifest_sha256:
        raise ValueError("approval SHA is only accepted with --apply")
    output_path = workspace_path(args.output) if args.output else STATE_PATH
    summary = {"status": "VALIDATED", "mode": "apply" if args.apply else "dry-run", "manifest_path": workspace_arg(manifest_path), "manifest_sha256": manifest_sha, "record_count": len(manifest["records"]), "records": [], "views": []}
    if not args.apply and not args.remote_dry_run:
        # Local-only review mode intentionally performs no network/CLI calls.
        summary["schema_fields_required"] = sorted({(r["table_id"], r["attachment_field"], r["fingerprint_field"], *r["text_updates"].keys()) for r in manifest["records"]})
        summary["text_write_fields"] = {r["record_id"]: sorted(r["text_updates"]) for r in manifest["records"]}
        save_json(output_path, summary)
        print(json.dumps({"status": summary["status"], "mode": summary["mode"], "manifest_sha256": manifest_sha, "records": len(manifest["records"]), "remote_calls": 0, "output": workspace_arg(output_path)}, ensure_ascii=False))
        return 0
    cli = LarkCLI(manifest["base_id"], timeout=args.timeout)
    preflight_records(cli, manifest)
    field_ids = ensure_fields(cli, manifest) if args.apply else {"mode": "will ensure on apply"}
    summary["field_ids_or_plan"] = field_ids
    for row in manifest["records"]:
        result = sync_row(cli, row, manifest, REMOTE_DIR, apply=args.apply)
        summary["records"].append(result)
        save_json(output_path, summary)
        if args.apply:
            time.sleep(0.75)
    if view_specs:
        summary["views"] = sync_views(cli, view_specs, manifest, apply=args.apply)
    summary["status"] = "COMPLETED" if args.apply else "DRY_RUN_COMPLETE"
    save_json(output_path, summary)
    print(json.dumps({"status": summary["status"], "mode": summary["mode"], "manifest_sha256": manifest_sha, "records": len(summary["records"]), "uploads": sum(x.get("upload", False) for x in summary["records"]), "text_writes": sum(x.get("text_write", False) for x in summary["records"]), "views": len(summary["views"]), "output": workspace_arg(output_path)}, ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, help="workspace-contained manifest JSON")
    parser.add_argument("--workspace", default=str(Path.cwd()), help="explicit workspace root for all relative paths")
    parser.add_argument("--state-dir", default=".lark-preview-sync", help="workspace-contained job state/log/download directory")
    parser.add_argument("--output", help="optional workspace-contained status JSON path")
    parser.add_argument("--apply", action="store_true", help="perform writes; requires approved manifest SHA")
    parser.add_argument("--approved-manifest-sha256")
    parser.add_argument("--view-plan", help="optional workspace-contained view plan with before/after visible fields")
    parser.add_argument("--remote-dry-run", action="store_true", help="perform bounded reads and CLI dry-run validation only")
    parser.add_argument("--timeout", type=int, default=45)
    args = parser.parse_args()
    try:
        return run(args)
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
