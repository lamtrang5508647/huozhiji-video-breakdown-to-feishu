#!/usr/bin/env python3
"""Conservatively accelerate one reviewed video sync using the same plan/state.

The default mode performs live read-only schema/row preflight. Writes require
--apply and an exact SHA-256 approval of the input plan. This helper never
creates schema/views, changes source script text, deletes attachments, or edits
permissions. It is a deterministic execution helper, not an analysis/upload bot.
Validated upsert responses replace redundant exact reads when complete. Attachment
pre-reads reuse this run's checked row; every upload still has its own readback and
one-operation pending journal. --stop-file stops only at a committed checkpoint.
"""
from __future__ import annotations

import argparse
import atexit
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any

from source_identity import canonical_schema, source_identity, verify_source


TIMEOUT_SECONDS = 50
WRITE_INTERVAL_SECONDS = 0.8
MAX_ATTACHMENT_BYTES = 2 * 1024 * 1024 * 1024
READ_ONLY_TYPES = {"formula", "lookup", "auto_number", "created_at", "updated_at", "created_by", "updated_by"}
SOURCE_SCRIPT_FIELD = "视频脚本"


class SyncError(RuntimeError):
    pass


class StoppedSafely(Exception):
    def __init__(self, report: dict[str, Any]):
        self.report = report


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SyncError(f"Cannot read JSON: {path.name}") from exc


def require_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SyncError(f"{label} must be a non-empty string")
    return value


def exact_object(value: Any, required: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SyncError(f"{label} must be a JSON object")
    missing = required - value.keys()
    if missing:
        raise SyncError(f"{label} is missing required keys: {', '.join(sorted(missing))}")
    return value


def plan_digest(raw: bytes) -> str:
    return sha256_bytes(raw)


def id_list(value: Any, label: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise SyncError(f"{label} must be a link-cell array")
    result: list[str] = []
    for item in value:
        rid = item.get("id", item.get("record_id")) if isinstance(item, dict) else None
        if not isinstance(rid, str) or not rid.startswith("rec"):
            raise SyncError(f"{label} contains an invalid linked record ID")
        if rid in result:
            raise SyncError(f"{label} repeats a linked record ID")
        result.append(rid)
    return result


def cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, list):
        return "".join(cell_text(item) for item in value)
    if isinstance(value, dict):
        for key in ("text", "name", "value", "link"):
            if key in value and isinstance(value[key], (str, int, float)):
                return str(value[key])
        return ""
    return str(value)


def normalize_option(value: Any) -> str:
    return cell_text(value).strip()


def collect_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [part for child in value.values() for part in collect_strings(child)]
    if isinstance(value, list):
        return [part for child in value for part in collect_strings(child)]
    return []


def normalize_field_name(key: str, schema: dict[str, dict[str, Any]]) -> str:
    for name, meta in schema.items():
        if key in {meta.get("id"), meta.get("name")}:
            return name
    return key


def decode_records(data: dict[str, Any], schema: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Decode both list/get CLI envelopes used by the installed CLI versions."""
    if not isinstance(data, dict):
        raise SyncError("Unexpected Base records response")
    if "record_id_list" in data:
        names = data.get("fields", data.get("field_id_list", []))
        values = data.get("data", [])
        ids = data.get("record_id_list", [])
        if not isinstance(names, list) or not isinstance(values, list) or len(values) != len(ids):
            raise SyncError("Base records response has inconsistent row arrays")
        result = []
        for rid, row in zip(ids, values):
            if isinstance(row, dict):
                raw_fields = row
            elif isinstance(row, list) and len(row) == len(names):
                raw_fields = dict(zip(names, row))
            else:
                raise SyncError("Base records response contains an unsupported row shape")
            result.append({"record_id": rid, "fields": {normalize_field_name(k, schema): v for k, v in raw_fields.items()}})
        return result
    records = data.get("records")
    if records is None and "record" in data:
        records = [data["record"]]
    if isinstance(records, list):
        result = []
        for row in records:
            if not isinstance(row, dict) or not isinstance(row.get("fields"), dict):
                raise SyncError("Base records response contains an unsupported record")
            result.append({
                "record_id": row.get("id", row.get("record_id")),
                "fields": {normalize_field_name(k, schema): v for k, v in row["fields"].items()},
            })
        return result
    raise SyncError("Unknown Base records response envelope")


def safe_error_detail(error: Any) -> str:
    """Expose actionable CLI errors without copying credentials or signed URLs."""
    if isinstance(error, dict):
        value = "; ".join(f"{key}={error[key]}" for key in ("code", "kind", "type", "message", "msg")
                          if isinstance(error.get(key), (str, int)))
    else:
        value = str(error) if error else ""
    value = re.sub(r"https?://\S+", "<url>", value)
    value = re.sub(r"(?i)(bearer\s+|(?:token|secret|password|authorization|cookie)[\s:=\"']+)\S+",
                   r"\1<redacted>", value)
    value = re.sub(r"[A-Za-z0-9_+/=-]{24,}", "<redacted-id>", value)
    return value.replace("\r", " ").replace("\n", " ")[:500]


def unwrap_cli(raw: str, exit_code: int, expected_identity: str, command: str) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        envelope = json.loads(raw)
    except json.JSONDecodeError as exc:
        detail = safe_error_detail(raw)
        raise SyncError(f"lark-cli returned non-JSON for {command} (exit {exit_code}); {detail}") from exc
    if not isinstance(envelope, dict):
        raise SyncError(f"lark-cli returned an invalid envelope for {command}")
    if exit_code != 0 or envelope.get("ok") is False:
        error = envelope.get("error", {})
        detail = safe_error_detail(error)
        suffix = f"; {detail}" if detail else ""
        raise SyncError(f"lark-cli {command} failed (exit {exit_code}){suffix}")
    actual_identity = envelope.get("identity")
    if actual_identity != expected_identity:
        raise SyncError("lark-cli identity does not match the selected project profile")
    data = envelope.get("data", envelope)
    if not isinstance(data, dict):
        raise SyncError(f"lark-cli returned invalid data for {command}")
    return data, envelope


class Lark:
    def __init__(self, base_token: str, identity: str, workspace: Path, ignore_proxy_env: bool):
        executable = shutil.which("lark-cli")
        if not executable:
            raise SyncError("lark-cli is unavailable")
        self.executable = executable
        self.base_token = base_token
        self.identity = identity
        self.workspace = workspace
        self.ignore_proxy_env = ignore_proxy_env
        self.last_write = 0.0
        self.schemas: dict[str, dict] = {}

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        if self.ignore_proxy_env:
            for key in list(env):
                if key.upper() in {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"}:
                    env.pop(key, None)
        env["LARKSUITE_CLI_NO_UPDATE_NOTIFIER"] = "1"
        env["LARKSUITE_CLI_NO_SKILLS_NOTIFIER"] = "1"
        return env

    def _call(self, command: str, table_id: str, args: list[str] | None = None, *, write: bool = False) -> tuple[dict[str, Any], dict[str, Any]]:
        argv = [self.executable, "base", command, "--base-token", self.base_token,
                "--table-id", table_id, "--as", self.identity, "--format", "json"]
        argv.extend(args or [])
        if write:
            wait_for = WRITE_INTERVAL_SECONDS - (time.monotonic() - self.last_write)
            if self.last_write and wait_for > 0:
                time.sleep(wait_for)
        try:
            done = subprocess.run(argv, cwd=str(self.workspace), env=self._env(),
                                  capture_output=True, text=True, encoding="utf-8", errors="replace",
                                  timeout=TIMEOUT_SECONDS, check=False)
        except subprocess.TimeoutExpired as exc:
            raise SyncError(f"lark-cli {command} timed out after {TIMEOUT_SECONDS}s; reconcile live state before retry") from exc
        raw = done.stdout.strip() or done.stderr.strip()
        # Some CLI upload failures print progress to stdout and the JSON error
        # envelope to stderr. Preserve that error rather than reporting only a
        # JSON parse failure of the progress stream.
        try:
            json.loads(raw)
        except json.JSONDecodeError:
            try:
                json.loads(done.stderr)
                raw = done.stderr.strip()
            except json.JSONDecodeError:
                if done.stderr.strip() and done.stderr.strip() != raw:
                    raw += "\n" + done.stderr.strip()
        data, envelope = unwrap_cli(raw, done.returncode, self.identity, command)
        if write:
            self.last_write = time.monotonic()
        return data, envelope

    def write_json_file(self, value: Any) -> tuple[str, Path]:
        handle = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".json",
                                             prefix=".sync-video-", dir=self.workspace,
                                             delete=False, newline="\n")
        path = Path(handle.name)
        try:
            with handle:
                json.dump(value, handle, ensure_ascii=False, separators=(",", ":"))
                handle.write("\n")
            return "@" + path.name, path
        except Exception:
            path.unlink(missing_ok=True)
            raise

    def fields(self, table_id: str) -> dict[str, dict[str, Any]]:
        data, _ = self._call("+field-list", table_id)
        raw = data.get("fields")
        if not isinstance(raw, list):
            raise SyncError("Live +field-list response has no fields array")
        result: dict[str, dict[str, Any]] = {}
        for item in raw:
            if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                raise SyncError("Live schema contains an invalid field entry")
            if item["name"] in result:
                raise SyncError("Live schema contains duplicate field names")
            if item.get("type") == "select" and item.get("truncated"):
                # Some CLI versions trim select options to 50 even in field-get.
                # Fetch the complete live option list before validating a write.
                options = []
                offset = 0
                while True:
                    page, _ = self._call("+field-search-options", table_id,
                                         ["--field-id", item["id"], "--limit", "200", "--offset", str(offset)])
                    batch = page.get("options")
                    total = page.get("total")
                    if not isinstance(batch, list) or not isinstance(total, int):
                        raise SyncError("Select option pagination has no options/total")
                    options.extend(batch)
                    offset += len(batch)
                    if offset >= total:
                        if offset != total:
                            raise SyncError("Select option count changed during pagination")
                        break
                    if not batch:
                        raise SyncError("Select option pagination stopped before total")
                if len({opt.get("name") for opt in options}) != len(options):
                    raise SyncError("Live select field contains duplicate option names")
                item = dict(item, options=options)
                for flag in ("truncated", "truncated_options_count", "remaining_options_count"):
                    item.pop(flag, None)
            result[item["name"]] = item
        return result

    def get(self, table_id: str, record_id: str, field_names: list[str], schema: dict[str, dict[str, Any]]) -> dict[str, Any]:
        args = ["--record-id", record_id]
        for name in field_names:
            args.extend(["--field-id", schema[name]["id"]])
        data, _ = self._call("+record-get", table_id, args)
        rows = decode_records(data, schema)
        if len(rows) != 1 or rows[0].get("record_id") != record_id:
            raise SyncError("Base record readback did not return the requested record")
        return rows[0]

    def list_exact(self, table_id: str, field_name: str, key: str, field_names: list[str], schema: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        filt, temp = self.write_json_file({"logic": "and", "conditions": [[schema[field_name]["id"], "==", key]]})
        try:
            args = ["--filter-json", filt, "--limit", "2"]
            for name in field_names:
                args.extend(["--field-id", schema[name]["id"]])
            data, envelope = self._call("+record-list", table_id, args)
        finally:
            temp.unlink(missing_ok=True)
        rows = decode_records(data, schema)
        has_more = data.get("has_more", envelope.get("has_more", False))
        if has_more or len(rows) > 1:
            raise SyncError(f"Duplicate business key in {field_name}; refusing to guess")
        return rows

    def list_video_business_rows(self, table_id: str, field_name: str, video_id: str,
                                 field_names: list[str], schema: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        """Fetch all rows whose text business key contains this video ID.

        This read-only preflight accelerator still requires exact local key matching.
        """
        filt, temp = self.write_json_file({"logic": "and", "conditions": [[schema[field_name]["id"], "intersects", video_id]]})
        rows: list[dict[str, Any]] = []
        seen_record_ids: set[str] = set()
        offset = 0
        try:
            while True:
                args = ["--filter-json", filt, "--limit", "200", "--offset", str(offset)]
                for name in field_names:
                    args.extend(["--field-id", schema[name]["id"]])
                data, envelope = self._call("+record-list", table_id, args)
                page = decode_records(data, schema)
                if len(page) > 200:
                    raise SyncError("Base returned more than the requested page size")
                has_more = data.get("has_more", envelope.get("has_more"))
                if not isinstance(has_more, bool):
                    raise SyncError("Paged Base response is missing boolean has_more; refusing an incomplete preflight")
                for row in page:
                    record_id = row.get("record_id")
                    if not isinstance(record_id, str) or not record_id:
                        raise SyncError("Paged Base response contains a row without a record ID")
                    if record_id in seen_record_ids:
                        raise SyncError("Paged Base response repeated a record ID; refusing an ambiguous preflight")
                    seen_record_ids.add(record_id)
                rows.extend(page)
                if not has_more:
                    break
                if not page:
                    raise SyncError("Paged Base response says has_more but returned no rows")
                offset += len(page)
        finally:
            temp.unlink(missing_ok=True)
        return rows

    def upsert(self, table_id: str, record_id: str | None, fields: dict[str, Any], *, apply: bool) -> dict[str, Any]:
        schema = self.schemas.get(table_id)
        if not schema or any(name not in schema for name in fields):
            raise SyncError("Write field mapping was not verified for this table")
        payload = {schema[name]["id"]: value for name, value in fields.items()}
        json_arg, temp = self.write_json_file(payload)
        try:
            args = ["--json", json_arg]
            if record_id:
                args.extend(["--record-id", record_id])
            if not apply:
                args.append("--dry-run")
            data, _ = self._call("+record-upsert", table_id, args, write=apply)
            return data
        finally:
            temp.unlink(missing_ok=True)

    def upload(self, table_id: str, record_id: str, field_id: str, file_path: Path, cli_path: str, *, apply: bool) -> dict[str, Any]:
        args = ["--record-id", record_id, "--field-id", field_id, "--file", cli_path]
        if not apply:
            args.append("--dry-run")
        data, _ = self._call("+record-upload-attachment", table_id, args, write=apply)
        return data


def validate_project(project: dict[str, Any], project_root: Path) -> dict[str, Any]:
    exact_object(project, {"base_id", "base_url", "identity", "tables"}, "project profile")
    base_id = require_text(project["base_id"], "project.base_id")
    base_url = require_text(project["base_url"], "project.base_url")
    if base_id not in base_url:
        raise SyncError("Project base_url does not identify the configured base_id")
    identity = project["identity"]
    if identity not in {"user", "bot"}:
        raise SyncError("Project identity must be user or bot")
    tables = project["tables"]
    exact_object(tables, {"videos", "materials", "segments", "products", "platforms"}, "project.tables")
    for key in ("videos", "materials", "segments", "products", "platforms"):
        table = exact_object(tables[key], {"id", "name", "fields_snapshot", "fields"}, f"project.tables.{key}")
        if not str(table["id"]).startswith("tbl") or not isinstance(table["fields"], dict):
            raise SyncError(f"Project table profile is invalid: {key}")
        snapshot_path = (project_root / table["fields_snapshot"]).resolve()
        if not snapshot_path.is_relative_to(project_root.resolve()) or not snapshot_path.is_file():
            raise SyncError(f"Missing or unsafe schema snapshot for table {key}")
        snapshot = read_json(snapshot_path)
        if snapshot.get("ok") is not True or not isinstance(snapshot.get("data"), dict):
            raise SyncError(f"Schema snapshot is not a successful CLI response: {key}")
        snap_fields = snapshot["data"].get("fields")
        if not isinstance(snap_fields, list):
            raise SyncError(f"Schema snapshot has no field list: {key}")
        snap_by_name = {f.get("name"): f for f in snap_fields if isinstance(f, dict)}
        try:
            canonical_schema(snap_by_name, table["fields"], key)
        except ValueError as exc:
            raise SyncError(str(exc)) from exc
    return project


def schema_for_live(api: Lark, project: dict[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    result: dict[str, dict[str, dict[str, Any]]] = {}
    api.schemas = {}
    for logical in ("videos", "materials", "segments", "products", "platforms"):
        table = project["tables"][logical]
        live = api.fields(table["id"])
        try:
            result[logical] = canonical_schema(live, table["fields"], logical)
        except ValueError as exc:
            raise SyncError(str(exc)) from exc
        api.schemas[table["id"]] = result[logical]
    expected_link_targets = {
        ("videos", "所属产品"): "products",
        ("videos", "画面素材"): "materials",
        ("videos", "脚本段"): "segments",
        ("materials", "原视频关联"): "videos",
        ("materials", "所属脚本段"): "segments",
        ("segments", "原视频"): "videos",
        ("segments", "所属产品"): "products",
        ("segments", "关联原画面"): "materials",
    }
    for (logical, field_name), target_logical in expected_link_targets.items():
        meta = result[logical].get(field_name, {})
        if meta.get("type") != "link" or meta.get("link_table") != project["tables"][target_logical]["id"]:
            raise SyncError(f"Live link target differs from project profile: {logical}/{field_name}")
    return result


def check_field_value(name: str, value: Any, schema: dict[str, dict[str, Any]], label: str) -> None:
    meta = schema.get(name)
    if meta is None:
        raise SyncError(f"Unknown field {label}/{name}")
    field_type = meta.get("type")
    if field_type == "attachment" or field_type in READ_ONLY_TYPES:
        raise SyncError(f"Field {label}/{name} is not writable through ordinary fields")
    if value is None or value == "" or value == []:
        raise SyncError(f"Empty values are rejected to prevent accidental clearing: {label}/{name}")
    if field_type == "text":
        if not isinstance(value, str):
            raise SyncError(f"Expected text for {label}/{name}")
    elif field_type == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise SyncError(f"Expected finite JSON number for {label}/{name}")
    elif field_type == "select":
        multi = bool(meta.get("multiple"))
        values = value if multi else [value]
        if (multi and (not isinstance(value, list) or not value)) or (not multi and not isinstance(value, str)):
            raise SyncError(f"Select shape does not match live multiplicity: {label}/{name}")
        allowed = {option.get("name") for option in meta.get("options", []) if isinstance(option, dict)}
        if not all(isinstance(item, str) and item in allowed for item in values):
            raise SyncError(f"Select option is not present in the live schema: {label}/{name}")
    elif field_type == "link":
        links = id_list(value, f"{label}/{name}")
        if not links:
            raise SyncError(f"Empty link values are rejected: {label}/{name}")
        if len(links) != len(value):
            raise SyncError(f"Duplicate link IDs are not allowed: {label}/{name}")
    else:
        raise SyncError(f"Unsupported writable field type {field_type!r}: {label}/{name}")


def normalize_for_compare(value: Any, meta: dict[str, Any]) -> Any:
    kind = meta.get("type")
    if kind == "text":
        result = cell_text(value)
        if meta.get('style', {}).get('type') == 'url':
            match = re.fullmatch(r'\[[^\]]*\]\((https?://[^\s]+)\)', result)
            if match:
                return match.group(1)
        return result
    if kind == "number":
        if value is None or isinstance(value, bool):
            return value
        return float(value)
    if kind == "select":
        multi = bool(meta.get("multiple"))
        if multi:
            raw = value if isinstance(value, list) else [value] if value is not None else []
            values = [normalize_option(v) for v in raw]
            return sorted(values)
        return normalize_option(value)
    if kind == "link":
        return sorted(id_list(value, "readback link"))
    return value


def field_matches(actual: Any, expected: Any, meta: dict[str, Any]) -> bool:
    left = normalize_for_compare(actual, meta)
    right = normalize_for_compare(expected, meta)
    if meta.get("type") == "number":
        return isinstance(left, float) and isinstance(right, float) and abs(left - right) <= 1e-6
    return left == right


def validate_fields(fields: Any, schema: dict[str, dict[str, Any]], label: str, required: set[str] = frozenset()) -> dict[str, Any]:
    if not isinstance(fields, dict):
        raise SyncError(f"{label}.fields must be a JSON object")
    if required - fields.keys():
        raise SyncError(f"{label}.fields lacks required fields: {', '.join(sorted(required - fields.keys()))}")
    normalized: dict[str, Any] = {}
    for name, value in fields.items():
        if name in normalized:
            raise SyncError(f"Duplicate field key: {label}/{name}")
        check_field_value(name, value, schema, label)
        normalized[name] = value
    return normalized


def attachment_plan(items: Any, allowed_fields: set[str], workspace: Path) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        raise SyncError("attachments must be an array")
    result = []
    seen: set[tuple[str, str]] = set()
    for index, item in enumerate(items):
        exact_object(item, {"field", "path", "sha256"}, f"attachments[{index}]")
        field = require_text(item["field"], "attachment.field")
        if field not in allowed_fields:
            raise SyncError(f"Attachment field is not allowed here: {field}")
        path_text = require_text(item["path"], "attachment.path")
        raw_path = Path(path_text)
        if raw_path.is_absolute():
            path = raw_path.resolve()
            cli_path = str(path)
        else:
            path = (workspace / raw_path).resolve()
            if not path.is_relative_to(workspace.resolve()):
                raise SyncError("Workspace-relative attachment path escapes --workspace")
            cli_path = str(path.relative_to(workspace.resolve()))
        expected_sha = require_text(item["sha256"], "attachment.sha256").lower()
        if not re.fullmatch(r"[0-9a-f]{64}", expected_sha):
            raise SyncError("attachment.sha256 must be a 64-character SHA-256 hex digest")
        if not path.is_file():
            raise SyncError(f"Attachment file is missing: {path.name}")
        size = path.stat().st_size
        if size <= 0 or size > MAX_ATTACHMENT_BYTES:
            raise SyncError(f"Attachment size is outside supported range: {path.name}")
        actual_sha = sha256_file(path)
        if actual_sha != expected_sha:
            raise SyncError(f"Attachment SHA-256 mismatch: {path.name}")
        dedup = (field, path.name)
        if dedup in seen:
            raise SyncError(f"Duplicate attachment plan for {field}/{path.name}")
        seen.add(dedup)
        result.append({"field": field, "path": path, "cli_path": cli_path,
                       "sha256": expected_sha, "size": size, "name": path.name})
    return result


def verify_plan_provenance(plan: dict, workspace: Path, project_root: Path | None) -> None:
    """Rebuild locally from receipt-bound inputs before trusting a saved plan."""
    import build_video_base_plan as builder
    provenance = plan.get('export_provenance')
    if not isinstance(provenance, dict) or not isinstance(provenance.get('input_paths'), dict):
        raise SyncError('Plan lacks export_provenance; rebuild it with the current plan builder')
    if project_root is None:
        raise SyncError('Project root is required to verify the plan against reviewed exporter inputs')
    if not isinstance(plan.get('source_identity'), dict):
        raise SyncError('Plan source_identity must be an object')
    required = {'manifest', 'review_receipt', 'export_manifest', 'segment_index', 'timeline'}
    paths = provenance['input_paths']
    if not required <= set(paths) or set(paths) - required - {'doc_links'}:
        raise SyncError('Plan provenance has missing or unsupported input paths')
    resolved = {}
    for name, relative in paths.items():
        if not isinstance(relative, str) or Path(relative).is_absolute():
            raise SyncError('Plan provenance paths must be workspace-relative')
        target = (workspace / relative).resolve()
        if not target.is_relative_to(workspace.resolve()) or not target.is_file():
            raise SyncError('Plan provenance input is missing or escapes the workspace')
        resolved[name] = target
    args = argparse.Namespace(**resolved, project=project_root, workspace=workspace,
                              platform=plan.get('source_identity', {}).get('platform'),
                              source_record_id=require_text(plan.get('source_record_id'), 'source_record_id'),
                              product_record_id=require_text(plan.get('product_record_id'), 'product_record_id'))
    if 'doc_links' not in resolved:
        args.doc_links = None
    try:
        rebuilt = builder.build_plan(args)
    except (builder.PlanError, OSError, ValueError, KeyError, TypeError) as exc:
        raise SyncError('Plan exporter provenance no longer validates: ' + str(exc)) from exc
    if rebuilt != plan:
        raise SyncError('Saved plan differs from a fresh rebuild of the reviewed exporter inputs')


def parse_plan(raw: bytes, workspace: Path, schemas: dict[str, dict[str, dict[str, Any]]], project: dict[str, Any], project_root: Path | None = None) -> dict[str, Any]:
    try:
        plan = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SyncError("Plan must be valid UTF-8 JSON") from exc
    required = {"base_id", "video_id", "source_record_id", "product_record_id", "source_sha256", "revision", "main_fields", "materials", "segments"}
    exact_object(plan, required, "plan")
    verify_plan_provenance(plan, workspace, project_root)
    if plan["base_id"] != project["base_id"]:
        raise SyncError("Plan base_id does not match the selected project profile")
    video_id = require_text(plan["video_id"], "plan.video_id")
    for key in ("source_record_id", "product_record_id"):
        if not isinstance(plan[key], str) or not plan[key].startswith("rec"):
            raise SyncError(f"plan.{key} must be a Base record ID")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", str(plan["source_sha256"])):
        raise SyncError("plan.source_sha256 must be a 64-character SHA-256 digest")
    revision = require_text(plan["revision"], "plan.revision")
    claimed = plan.get("source_identity")
    source = claimed if isinstance(claimed, dict) else {
        "platform": plan.get("main_fields", {}).get("来源平台"),
        "url": plan.get("main_fields", {}).get("来源链接")}
    if claimed is None and source.get("platform") != "B站":
        raise SyncError("Non-Bilibili plans require an explicit source_identity")
    try:
        identity = source_identity(video_id, source,
                                   id_field=project.get("source_identity", {}).get("id_field"))
    except ValueError as exc:
        raise SyncError(str(exc)) from exc
    if claimed is not None and claimed != identity:
        raise SyncError("Plan source_identity differs from the configured identity contract")
    if (plan.get("main_fields", {}).get("来源平台") != identity["platform"]
            or plan.get("main_fields", {}).get("来源链接") != identity["url"]):
        raise SyncError("Plan source platform/URL fields conflict with its source_identity")

    main_schema = schemas["videos"]
    if SOURCE_SCRIPT_FIELD in (plan["main_fields"] if isinstance(plan["main_fields"], dict) else {}):
        raise SyncError("Plan must never write 视频脚本")
    main_required = {"所属产品"}
    main_fields = validate_fields(plan["main_fields"], main_schema, "main_fields", main_required)
    if any(main_schema[name].get("type") == "link" and name not in {"所属产品", "脚本段", "画面素材"} for name in main_fields):
        raise SyncError("main_fields contains an unsupported/unverified link field")
    if main_fields["所属产品"] != [{"id": plan["product_record_id"]}]:
        if id_list(main_fields["所属产品"], "main_fields/所属产品") != [plan["product_record_id"]]:
            raise SyncError("main_fields 所属产品 must explicitly link the supplied product_record_id")

    if not isinstance(plan["materials"], list) or not isinstance(plan["segments"], list):
        raise SyncError("materials and segments must be arrays")
    material_map: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(plan["materials"]):
        exact_object(item, {"key", "fields", "attachments"}, f"materials[{index}]")
        key = require_text(item["key"], f"materials[{index}].key")
        if key in material_map:
            raise SyncError(f"Duplicate material plan key: {key}")
        fields = validate_fields(item["fields"], schemas["materials"], f"materials[{index}].fields",
                                 {"素材ID", "来源记录", "原视频关联"})
        if "所属脚本段" in fields:
            raise SyncError("Material 所属脚本段 is reciprocal; derive it from segment 关联原画面 instead")
        if any(schemas["materials"][name].get("type") == "link" and name != "原视频关联" for name in fields):
            raise SyncError(f"Material {key} contains an unverified link field")
        if fields["素材ID"] != key or fields["来源记录"] != plan["source_record_id"]:
            raise SyncError(f"Material key/source fields do not match for {key}")
        if id_list(fields["原视频关联"], f"materials[{index}].原视频关联") != [plan["source_record_id"]]:
            raise SyncError(f"Material source link does not match for {key}")
        material_map[key] = {"key": key, "fields": fields,
                             "attachments": attachment_plan(item["attachments"], {"素材附件", "关键帧"}, workspace)}

    segment_map: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(plan["segments"]):
        exact_object(item, {"key", "fields", "source_shot_keys", "attachments"}, f"segments[{index}]")
        key = require_text(item["key"], f"segments[{index}].key")
        if key in segment_map:
            raise SyncError(f"Duplicate segment plan key: {key}")
        if not isinstance(item["source_shot_keys"], list) or not item["source_shot_keys"]:
            raise SyncError(f"Segment {key} must contain source_shot_keys")
        shot_keys = [require_text(k, f"segments[{index}].source_shot_keys") for k in item["source_shot_keys"]]
        if len(set(shot_keys)) != len(shot_keys):
            raise SyncError(f"Segment {key} repeats a source shot key")
        missing = set(shot_keys) - material_map.keys()
        if missing:
            raise SyncError(f"Segment {key} references material keys absent from this plan")
        fields = validate_fields(item["fields"], schemas["segments"], f"segments[{index}].fields",
                                 {"脚本段键", "原视频", "所属产品"})
        if any(schemas["segments"][name].get("type") == "link" and name not in {"原视频", "所属产品"} for name in fields):
            raise SyncError(f"Segment {key} contains a link field that must be generated or preserved by the synchronizer")
        if fields["脚本段键"] != key:
            raise SyncError(f"Segment key field does not match {key}")
        if id_list(fields["原视频"], f"segments[{index}].原视频") != [plan["source_record_id"]]:
            raise SyncError(f"Segment source link does not match for {key}")
        if id_list(fields["所属产品"], f"segments[{index}].所属产品") != [plan["product_record_id"]]:
            raise SyncError(f"Segment product link does not match for {key}")
        if "关联原画面" in fields:
            raise SyncError("Segment 关联原画面 is generated from source_shot_keys; do not supply it in fields")
        segment_map[key] = {"key": key, "fields": fields, "source_shot_keys": shot_keys,
                            "attachments": attachment_plan(item["attachments"], {"段落视频"}, workspace)}
    if not material_map or not segment_map:
        raise SyncError("Plan must include at least one material and one segment")
    return {"plan": plan, "identity": identity, "video_id": video_id, "revision": revision,
            "main_fields": main_fields, "materials": material_map, "segments": segment_map,
            "source_sha256": str(plan["source_sha256"]).lower()}


def verify_live_profile_schema(profile_table: dict[str, Any], live: dict[str, dict[str, Any]], logical: str) -> None:
    try:
        canonical_schema({meta["name"]: meta for meta in live.values()}, profile_table["fields"], logical)
    except ValueError as exc:
        raise SyncError(str(exc)) from exc


def is_single_link(value: Any, expected_id: str) -> bool:
    return id_list(value, "link cell") == [expected_id]


def extract_video_ids(value: Any) -> set[str]:
    urls = [url for part in collect_strings(value) for url in re.findall(r"https?://[^\s)<>]+", part)]
    return {match for url in urls for match in re.findall(r"BV[0-9A-Za-z]{10,12}", url)}


def acquire_apply_lock(state_path: Path) -> Any:
    """Prevent concurrent processes from racing key lookup and record creation."""
    lock_path = state_path.with_name(state_path.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+b")
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        handle.close()
        raise SyncError("Another apply process holds this video's local sync lock") from exc
    atexit.register(handle.close)
    return handle


def verify_source_and_product(api: Lark, project: dict[str, Any], schemas: dict[str, dict[str, dict[str, Any]]], plan: dict[str, Any]) -> tuple[dict[str, Any], str]:
    source_table = project["tables"]["videos"]["id"]
    source_schema = schemas["videos"]
    identity = plan["identity"]
    url_fields = [name for name in ("来源链接", "B站链接") if name in source_schema]
    required_fields = ["来源平台", "所属产品", SOURCE_SCRIPT_FIELD]
    if identity.get("id_field"):
        required_fields.append(identity["id_field"])
    if not url_fields or any(name not in source_schema for name in required_fields):
        raise SyncError("Source identity/script fields are missing from the configured field mapping")
    source_fields = list(dict.fromkeys(required_fields + url_fields +
                         [name for name in ("标题", "脚本段", "画面素材") if name in source_schema]))
    source_row = api.get(source_table, plan["plan"]["source_record_id"], source_fields, source_schema)
    fields = source_row["fields"]
    if not is_single_link(fields.get("所属产品"), plan["plan"]["product_record_id"]):
        raise SyncError("Source video record does not link to the plan's product record")
    urls = [url for name in url_fields for part in collect_strings(fields.get(name))
            for url in re.findall(r"https?://[^\s)<>]+", part)]
    try:
        verify_source(identity, platform=normalize_option(fields.get("来源平台")), urls=urls,
                      stable_id=cell_text(fields.get(identity["id_field"])) if identity.get("id_field") else None)
    except ValueError as exc:
        raise SyncError(str(exc)) from exc
    source_script = fields.get(SOURCE_SCRIPT_FIELD)
    if not isinstance(source_script, str):
        source_script = cell_text(source_script)
    source_script_hash = sha256_bytes(source_script.encode("utf-8"))
    product_table = project["tables"]["products"]["id"]
    product_schema = schemas["products"]
    product_row = api.get(product_table, plan["plan"]["product_record_id"], ["产品编码", "产品业务名"], product_schema)
    if product_row["record_id"] != plan["plan"]["product_record_id"]:
        raise SyncError("Product record readback did not match the requested product_record_id")
    return source_row, source_script_hash


def exact_row(api: Lark, logical: str, key: str, project: dict[str, Any], schemas: dict[str, dict[str, dict[str, Any]]]) -> dict[str, Any] | None:
    table = project["tables"][logical]["id"]
    key_field = "素材ID" if logical == "materials" else "脚本段键"
    names = list(project["tables"][logical]["fields"].keys())
    names = list(dict.fromkeys(names + (["素材附件", "关键帧"] if logical == "materials" else ["段落视频"])))
    rows = api.list_exact(table, key_field, key, names, schemas[logical])
    if not rows:
        return None
    if rows[0].get("fields", {}).get(key_field) != key:
        raise SyncError(f"Exact-key query returned a non-matching record for {logical}/{key}")
    return rows[0]


def index_prefetched_rows(logical: str, rows: list[dict[str, Any]], requested_keys: set[str]) -> dict[str, dict[str, Any]]:
    """Select requested exact keys from a broad, video-scoped read; reject duplicates."""
    key_field = "素材ID" if logical == "materials" else "脚本段键"
    matches: dict[str, dict[str, Any]] = {}
    for row in rows:
        fields = row.get("fields")
        if not isinstance(fields, dict):
            raise SyncError(f"Prefetched {logical} row has no fields object")
        key = fields.get(key_field)
        if not isinstance(key, str) or key not in requested_keys:
            continue
        if key in matches:
            raise SyncError(f"Duplicate business key in {key_field}; refusing to guess")
        matches[key] = row
    return matches


def verify_child_owner(logical: str, record: dict[str, Any], plan: dict[str, Any]) -> None:
    fields = record["fields"]
    if logical == "materials":
        if fields.get("来源记录") != plan["plan"]["source_record_id"]:
            raise SyncError("Existing material business key belongs to a different source record")
        if not is_single_link(fields.get("原视频关联"), plan["plan"]["source_record_id"]):
            raise SyncError("Existing material business key has a different source link")
    else:
        if not is_single_link(fields.get("原视频"), plan["plan"]["source_record_id"]):
            raise SyncError("Existing segment business key belongs to a different source record")
        if not is_single_link(fields.get("所属产品"), plan["plan"]["product_record_id"]):
            raise SyncError("Existing segment business key has a different product link")


def verified_upsert_row(data: Any, logical: str, key: str, expected: dict[str, Any],
                        previous: dict[str, Any] | None, plan: dict[str, Any],
                        schema: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    """Reuse only a complete, positively verified mutation response.

    Missing response fields fall back to the existing exact-key read. Contradictory
    IDs, business keys, ownership or values fail closed and keep pending intact.
    Previously read attachment cells survive sparse normal-field update responses.
    """
    if not isinstance(data, dict):
        return None
    try:
        rows = decode_records(data, schema)
    except SyncError:
        return None
    if len(rows) != 1:
        return None
    row = rows[0]
    rid = row.get("record_id")
    if not isinstance(rid, str) or not rid.startswith("rec"):
        return None
    if previous and rid != previous["record_id"]:
        raise SyncError("Upsert returned a different record ID; pending write needs reconciliation")
    key_field = "素材ID" if logical == "materials" else "脚本段键"
    fields = row["fields"]
    if key_field in fields and fields[key_field] != key:
        raise SyncError("Upsert returned a different business key")
    owners = ["来源记录", "原视频关联"] if logical == "materials" else ["原视频", "所属产品"]
    # Reject explicit ownership contradictions even if other response fields are absent.
    expected_owners = ({"来源记录": plan["plan"]["source_record_id"],
                       "原视频关联": [{"id": plan["plan"]["source_record_id"]}]}
                      if logical == "materials" else
                      {"原视频": [{"id": plan["plan"]["source_record_id"]}],
                       "所属产品": [{"id": plan["plan"]["product_record_id"]}]})
    for name, value in expected_owners.items():
        if name in fields and not field_matches(fields[name], value, schema[name]):
            raise SyncError("Upsert returned conflicting source/product ownership")
    for name, value in expected.items():
        if name in fields and not field_matches(fields[name], value, schema[name]):
            raise SyncError(f"Upsert response field mismatch: {key}/{name}")
    if any(name not in fields for name in set(expected) | set(owners) | {key_field}):
        return None
    verify_child_owner(logical, row, plan)
    if previous:
        merged = dict(previous["fields"])
        merged.update(fields)
        row["fields"] = merged
    # A created record did not receive any ordinary attachment CellValue. Missing
    # attachment cells are therefore empty; updates retain previously read cells.
    return row


def attachment_cell(value: Any, field: str) -> list[dict[str, Any]]:
    if value is None or value == "":
        return []
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise SyncError(f"Attachment readback has unsupported shape: {field}")
    return value


def matching_attachment(items: list[dict[str, Any]], attachment: dict[str, Any]) -> dict[str, Any] | None:
    matches = [item for item in items if item.get("name", item.get("file_name")) == attachment["name"]]
    if len(matches) > 1:
        raise SyncError(f"Duplicate same-name attachment; refusing to append or replace: {attachment['name']}")
    if not matches:
        return None
    size = matches[0].get("size", matches[0].get("size_bytes"))
    if size is None:
        raise SyncError(f"Attachment metadata has no size for same-name file: {attachment['name']}")
    try:
        size_int = int(size)
    except (TypeError, ValueError) as exc:
        raise SyncError(f"Attachment metadata size is invalid: {attachment['name']}") from exc
    if size_int != attachment["size"]:
        raise SyncError(f"Same-name attachment has a different size; refusing replacement: {attachment['name']}")
    token = matches[0].get("file_token", matches[0].get("token"))
    if not isinstance(token, str) or not token:
        raise SyncError(f"Attachment readback has no reusable file token: {attachment['name']}")
    return matches[0]


def resolve_resume_pending(state: dict[str, Any], records: dict[str, dict[str, dict[str, Any]]], plan: dict[str, Any], schemas: dict[str, dict[str, dict[str, Any]]], api: Lark, project: dict[str, Any], source_row: dict[str, Any], source_script_hash: str) -> None:
    pending = state.get("pending")
    if not pending:
        return
    kind = pending.get("kind")
    logical = pending.get("table")
    key = pending.get("key")
    if kind in {"material_upsert", "segment_upsert"}:
        item = plan["materials" if logical == "materials" else "segments"].get(key)
        row = records.get(logical, {}).get(key)
        if item is None or row is None:
            raise SyncError("Pending record write cannot be reconciled by the approved business key")
        verify_child_owner(logical, row, plan)
        expected = item["fields"]
        if logical == "segments":
            expected = dict(expected)
            expected["关联原画面"] = pending.get("expected_shot_links", [])
        for name, value in expected.items():
            if not field_matches(row["fields"].get(name), value, schemas[logical][name]):
                raise SyncError("Pending record write is not fully reflected live; stop for manual reconciliation")
        state.setdefault("records", {}).setdefault(logical, {})[key] = {"record_id": row["record_id"], "verified": True}
        state["pending"] = None
    elif kind == "attachment_upload":
        if logical not in {"materials", "segments"} or key not in records.get(logical, {}):
            raise SyncError("Pending attachment upload cannot be reconciled by a live business key")
        row = records[logical][key]
        attach = pending.get("attachment")
        if not isinstance(attach, dict):
            raise SyncError("Pending attachment record is incomplete")
        fresh = api.get(project["tables"][logical]["id"], row["record_id"], [attach["field"]], schemas[logical])
        items = attachment_cell(fresh["fields"].get(attach["field"]), attach["field"])
        match = matching_attachment(items, attach)
        if not match:
            raise SyncError("Pending attachment is not visible live; stop and reconcile before retry")
        # The fast path no longer pre-reads each attachment. Reconciled live cells
        # must update this run's cache, especially when prefetch raced visibility.
        row["fields"][attach["field"]] = fresh["fields"].get(attach["field"])
        state.setdefault("attachments", {}).setdefault(logical, {}).setdefault(key, {})[attach["field"]] = {
            "name": attach["name"], "size": attach["size"], "sha256": attach["sha256"],
            "file_token": match.get("file_token", match.get("token")), "verified": True,
        }
        state["pending"] = None
    elif kind == "main_update":
        expected = pending.get("expected_fields")
        if not isinstance(expected, dict):
            raise SyncError("Pending main update cannot be reconciled")
        fresh = api.get(project["tables"]["videos"]["id"], plan["plan"]["source_record_id"], list(expected) + [SOURCE_SCRIPT_FIELD], schemas["videos"])
        if sha256_bytes(cell_text(fresh["fields"].get(SOURCE_SCRIPT_FIELD)).encode("utf-8")) != source_script_hash:
            raise SyncError("Source script changed while reconciling pending main update")
        if not all(field_matches(fresh["fields"].get(name), value, schemas["videos"][name]) for name, value in expected.items()):
            raise SyncError("Pending main update is not fully reflected live; stop for manual reconciliation")
        state["pending"] = None
    else:
        raise SyncError("Unknown pending operation in state file; manual review required")


def save_state(path: Path, state: dict[str, Any], dry_run: bool) -> None:
    if not dry_run:
        state["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        atomic_json(path, state)


def safe_checkpoint(stop_file: Path | None, state_path: Path, state: dict[str, Any]) -> None:
    """Never terminate between a write and its readback/state commit."""
    if not stop_file or not stop_file.exists() or state.get("pending"):
        return
    save_state(state_path, state, False)
    raise StoppedSafely({
        "status": "stopped_safe", "stop_file": str(stop_file),
        "video_id": state.get("video_id"), "state_file": str(state_path),
        "pending": None, "writes_in_flight": False,
        "materials_verified": len(state.get("records", {}).get("materials", {})),
        "segments_verified": len(state.get("records", {}).get("segments", {})),
        "attachments_verified": sum(len(v) for t in state.get("attachments", {}).values() for v in t.values()),
        "main_fields_verified": bool(state.get("main_fields_verified")),
    })


def plan_report(plan: dict[str, Any], records: dict[str, dict[str, dict[str, Any]]], attachments: int) -> dict[str, Any]:
    return {
        "status": "dry_run_preflight_passed",
        "video_id": plan["video_id"],
        "revision": plan["revision"],
        "materials": {"total": len(plan["materials"]), "create": sum(k not in records["materials"] for k in plan["materials"]),
                      "update": sum(k in records["materials"] for k in plan["materials"])},
        "segments": {"total": len(plan["segments"]), "create": sum(k not in records["segments"] for k in plan["segments"]),
                     "update": sum(k in records["segments"] for k in plan["segments"])},
        "attachments_checked": attachments,
        "writes_performed": 0,
        "source_script_printed_or_written": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path, help="reviewed UTF-8 JSON plan")
    parser.add_argument("--project", required=True, type=Path, help="project root with explicit config")
    parser.add_argument("--workspace", type=Path, default=Path.cwd(), help="CLI cwd; relative attachments are rooted here (default: current directory)")
    parser.add_argument("--state", required=True, type=Path, help="atomic per-video progress JSON")
    parser.add_argument("--stop-file", type=Path, help="exit safely after current write/readback commits when this path exists; relative to workspace")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", dest="apply", action="store_false", help="live read-only preflight; default")
    mode.add_argument("--apply", dest="apply", action="store_true", help="apply only when the exact plan SHA is approved")
    parser.set_defaults(apply=False)
    parser.add_argument("--approved-plan-sha256", help="required with --apply; SHA-256 of the exact plan file bytes")
    args = parser.parse_args()

    try:
        project_root = args.project.resolve()
        workspace = args.workspace.resolve()
        stop_file = ((workspace / args.stop_file).resolve() if args.stop_file and not args.stop_file.is_absolute()
                     else args.stop_file.resolve() if args.stop_file else None)
        if not project_root.is_dir() or not workspace.is_dir():
            raise SyncError("--project and --workspace must resolve to existing directories")
        plan_path = args.plan.resolve()
        if not plan_path.is_file():
            raise SyncError("Plan file does not exist")
        raw = plan_path.read_bytes()
        digest = plan_digest(raw)
        if args.apply:
            if not args.approved_plan_sha256 or args.approved_plan_sha256.lower() != digest:
                raise SyncError("--apply requires --approved-plan-sha256 matching the exact plan bytes")
        elif args.approved_plan_sha256:
            raise SyncError("--approved-plan-sha256 is accepted only with --apply")

        project = validate_project(read_json(project_root / "config" / "project.json"), project_root)
        state_path = args.state if args.state.is_absolute() else (workspace / args.state)
        state_path = state_path.resolve()
        if state_path.exists() and not state_path.is_file():
            raise SyncError("--state must point to a file path")
        if stop_file and stop_file == state_path:
            raise SyncError("--stop-file must differ from --state")
        if args.apply:
            acquire_apply_lock(state_path)
        base_token = require_text(project["base_id"], "project.base_id")
        network_cfg = project.get("network", {})
        api = Lark(base_token, project["identity"], workspace, bool(network_cfg.get("ignore_proxy_env")))
        schemas = schema_for_live(api, project)
        parsed = parse_plan(raw, workspace, schemas, project, project_root)
        source_row, source_script_hash = verify_source_and_product(api, project, schemas, parsed)

        main_fields = parsed["main_fields"]
        if main_fields.get(SOURCE_SCRIPT_FIELD) is not None:
            raise SyncError("Plan must never write 视频脚本")
        records: dict[str, dict[str, dict[str, Any]]] = {"materials": {}, "segments": {}}
        for logical in ("materials", "segments"):
            requested_keys = set(parsed[logical])
            if any(parsed["video_id"] not in key for key in requested_keys):
                raise SyncError(f"{logical} business keys must contain video_id for complete scoped preflight")
            table = project["tables"][logical]["id"]
            key_field = "素材ID" if logical == "materials" else "脚本段键"
            names = list(project["tables"][logical]["fields"].keys())
            names = list(dict.fromkeys(names + (["素材附件", "关键帧"] if logical == "materials" else ["段落视频"])))
            prefetched = api.list_video_business_rows(table, key_field, parsed["video_id"], names, schemas[logical])
            records[logical] = index_prefetched_rows(logical, prefetched, requested_keys)
            for key, row in records[logical].items():
                if row.get("fields", {}).get(key_field) != key:
                    raise SyncError(f"Video-scoped query returned a non-matching record for {logical}/{key}")
                verify_child_owner(logical, row, parsed)

        for logical, key_map in (("materials", parsed["materials"]), ("segments", parsed["segments"])):
            for key, item in key_map.items():
                current = records[logical].get(key)
                if not current:
                    continue
                for attachment in item["attachments"]:
                    live_items = attachment_cell(current["fields"].get(attachment["field"]), attachment["field"])
                    matching_attachment(live_items, attachment)

        # Validate existing source/material/segment links before producing any write plan.
        material_record_ids = {key: row["record_id"] for key, row in records["materials"].items()}
        if set(material_record_ids) == set(parsed["materials"]):
            live_material_ids = set(material_record_ids.values())
            for key, current in records["segments"].items():
                existing_ids = id_list(current["fields"].get("关联原画面"), f"{key}/关联原画面")
                if set(existing_ids) - live_material_ids:
                    raise SyncError(f"Existing segment has material links not represented in this source plan: {key}")

        for item in parsed["materials"].values():
            for attachment in item["attachments"]:
                if sha256_file(attachment["path"]) != attachment["sha256"]:
                    raise SyncError(f"Attachment changed after initial validation: {attachment['name']}")
        for item in parsed["segments"].values():
            for attachment in item["attachments"]:
                if sha256_file(attachment["path"]) != attachment["sha256"]:
                    raise SyncError(f"Attachment changed after initial validation: {attachment['name']}")

        binding = {"plan_sha256": digest, "base_id": project["base_id"], "video_id": parsed["video_id"],
                   "source_record_id": parsed["plan"]["source_record_id"], "product_record_id": parsed["plan"]["product_record_id"],
                   "source_sha256": parsed["source_sha256"], "revision": parsed["revision"]}
        if state_path.exists():
            state = read_json(state_path)
            if any(state.get(k) != v for k, v in binding.items()):
                raise SyncError("Existing state file is bound to a different plan/source/project")
            if state.get("schema_version") != 1:
                raise SyncError("Unsupported state file version")
        else:
            state = {"schema_version": 1, **binding, "records": {"materials": {}, "segments": {}},
                     "attachments": {"materials": {}, "segments": {}}, "pending": None, "main_fields_verified": False}

        if not args.apply:
            report = plan_report(parsed, records, sum(len(v["attachments"]) for v in parsed["materials"].values()) + sum(len(v["attachments"]) for v in parsed["segments"].values()))
            report["plan_sha256"] = digest
            report["identity"] = project["identity"]
            report["target_tables"] = {k: project["tables"][k]["id"] for k in ("videos", "materials", "segments", "products")}
            report["state_file_exists"] = state_path.exists()
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0

        if state.get("pending"):
            resolve_resume_pending(state, records, parsed, schemas, api, project, source_row, source_script_hash)
            save_state(state_path, state, False)
        safe_checkpoint(stop_file, state_path, state)

        # Materials are created/updated and their attachment fields verified first.
        for key, item in parsed["materials"].items():
            current = records["materials"].get(key)
            expected = item["fields"]
            patch = {name: value for name, value in expected.items()
                     if not current or not field_matches(current["fields"].get(name), value, schemas["materials"][name])}
            if patch:
                state["pending"] = {"kind": "material_upsert", "table": "materials", "key": key}
                save_state(state_path, state, False)
                result = api.upsert(project["tables"]["materials"]["id"], current["record_id"] if current else None,
                                    patch if current else expected, apply=True)
                current = verified_upsert_row(result, "materials", key, expected, current, parsed, schemas["materials"])
                if current is None:
                    current = exact_row(api, "materials", key, project, schemas)
                if not current:
                    raise SyncError("Material upsert returned but the exact business key is not readable")
                verify_child_owner("materials", current, parsed)
                for name, value in expected.items():
                    if not field_matches(current["fields"].get(name), value, schemas["materials"][name]):
                        raise SyncError(f"Material field readback mismatch: {key}/{name}")
                records["materials"][key] = current
                material_record_ids[key] = current["record_id"]
                state.setdefault("records", {}).setdefault("materials", {})[key] = {"record_id": current["record_id"], "verified": True}
                state["pending"] = None
                save_state(state_path, state, False)
            else:
                state.setdefault("records", {}).setdefault("materials", {})[key] = {"record_id": current["record_id"], "verified": True}
                save_state(state_path, state, False)
            row_id = current["record_id"] if current else state["records"]["materials"][key]["record_id"]
            safe_checkpoint(stop_file, state_path, state)
            for attachment in item["attachments"]:
                items = attachment_cell(current["fields"].get(attachment["field"]), attachment["field"])
                match = matching_attachment(items, attachment)
                if not match:
                    if sha256_file(attachment["path"]) != attachment["sha256"]:
                        raise SyncError(f"Attachment changed before upload: {attachment['name']}")
                    state["pending"] = {"kind": "attachment_upload", "table": "materials", "key": key,
                                        "attachment": {k: attachment[k] for k in ("field", "name", "size", "sha256")}}
                    save_state(state_path, state, False)
                    api.upload(project["tables"]["materials"]["id"], row_id,
                               schemas["materials"][attachment["field"]]["id"], attachment["path"],
                               attachment["cli_path"], apply=True)
                    fresh = api.get(project["tables"]["materials"]["id"], row_id, [attachment["field"]], schemas["materials"])
                    items = attachment_cell(fresh["fields"].get(attachment["field"]), attachment["field"])
                    match = matching_attachment(items, attachment)
                    if not match:
                        raise SyncError(f"Uploaded attachment is missing on readback: {attachment['name']}")
                    current["fields"][attachment["field"]] = fresh["fields"].get(attachment["field"])
                state.setdefault("attachments", {}).setdefault("materials", {}).setdefault(key, {})[attachment["field"]] = {
                    "name": attachment["name"], "size": attachment["size"], "sha256": attachment["sha256"],
                    "file_token": match.get("file_token", match.get("token")), "verified": True,
                }
                state["pending"] = None
                save_state(state_path, state, False)
                safe_checkpoint(stop_file, state_path, state)

        # Segments get their reviewed source-shot links from verified material record IDs.
        live_material_ids = set(material_record_ids.values())
        for existing_key, existing_segment in records["segments"].items():
            prior_links = id_list(existing_segment["fields"].get("关联原画面"), f"{existing_key}/关联原画面")
            if set(prior_links) - live_material_ids:
                raise SyncError(f"Existing segment has material links not represented by this source plan: {existing_key}")
        for key, item in parsed["segments"].items():
            shot_ids = [material_record_ids[shot] for shot in item["source_shot_keys"]]
            current = records["segments"].get(key)
            expected = dict(item["fields"])
            expected["关联原画面"] = [{"id": rid} for rid in shot_ids]
            if current:
                old_links = id_list(current["fields"].get("关联原画面"), f"{key}/关联原画面")
                full_links = list(dict.fromkeys(old_links + shot_ids))
                expected["关联原画面"] = [{"id": rid} for rid in full_links]
            patch = {name: value for name, value in expected.items()
                     if not current or not field_matches(current["fields"].get(name), value, schemas["segments"][name])}
            if patch:
                pending_expected_links = expected["关联原画面"] if "关联原画面" in expected else []
                state["pending"] = {"kind": "segment_upsert", "table": "segments", "key": key,
                                    "expected_shot_links": pending_expected_links}
                save_state(state_path, state, False)
                result = api.upsert(project["tables"]["segments"]["id"], current["record_id"] if current else None,
                                    patch if current else expected, apply=True)
                current = verified_upsert_row(result, "segments", key, expected, current, parsed, schemas["segments"])
                if current is None:
                    current = exact_row(api, "segments", key, project, schemas)
                if not current:
                    raise SyncError("Segment upsert returned but the exact business key is not readable")
                verify_child_owner("segments", current, parsed)
                for name, value in expected.items():
                    if not field_matches(current["fields"].get(name), value, schemas["segments"][name]):
                        raise SyncError(f"Segment field readback mismatch: {key}/{name}")
                records["segments"][key] = current
                state.setdefault("records", {}).setdefault("segments", {})[key] = {"record_id": current["record_id"], "verified": True}
                state["pending"] = None
                save_state(state_path, state, False)
            else:
                state.setdefault("records", {}).setdefault("segments", {})[key] = {"record_id": current["record_id"], "verified": True}
                save_state(state_path, state, False)
            row_id = current["record_id"] if current else state["records"]["segments"][key]["record_id"]
            safe_checkpoint(stop_file, state_path, state)
            for attachment in item["attachments"]:
                items = attachment_cell(current["fields"].get(attachment["field"]), attachment["field"])
                match = matching_attachment(items, attachment)
                if not match:
                    if sha256_file(attachment["path"]) != attachment["sha256"]:
                        raise SyncError(f"Attachment changed before upload: {attachment['name']}")
                    state["pending"] = {"kind": "attachment_upload", "table": "segments", "key": key,
                                        "attachment": {k: attachment[k] for k in ("field", "name", "size", "sha256")}}
                    save_state(state_path, state, False)
                    api.upload(project["tables"]["segments"]["id"], row_id,
                               schemas["segments"][attachment["field"]]["id"], attachment["path"],
                               attachment["cli_path"], apply=True)
                    fresh = api.get(project["tables"]["segments"]["id"], row_id, [attachment["field"]], schemas["segments"])
                    items = attachment_cell(fresh["fields"].get(attachment["field"]), attachment["field"])
                    match = matching_attachment(items, attachment)
                    if not match:
                        raise SyncError(f"Uploaded segment video is missing on readback: {attachment['name']}")
                    current["fields"][attachment["field"]] = fresh["fields"].get(attachment["field"])
                state.setdefault("attachments", {}).setdefault("segments", {}).setdefault(key, {})[attachment["field"]] = {
                    "name": attachment["name"], "size": attachment["size"], "sha256": attachment["sha256"],
                    "file_token": match.get("file_token", match.get("token")), "verified": True,
                }
                state["pending"] = None
                save_state(state_path, state, False)
                safe_checkpoint(stop_file, state_path, state)

        # Main record receives the approved minimal patch last; preserve all existing child links.
        fresh_source = api.get(project["tables"]["videos"]["id"], parsed["plan"]["source_record_id"],
                               list(dict.fromkeys(list(parsed["main_fields"]) + ["脚本段", "画面素材", SOURCE_SCRIPT_FIELD])), schemas["videos"])
        if sha256_bytes(cell_text(fresh_source["fields"].get(SOURCE_SCRIPT_FIELD)).encode("utf-8")) != source_script_hash:
            raise SyncError("Source script changed during child writes; refusing the main patch")
        desired_main = dict(parsed["main_fields"])
        segment_ids = [state["records"]["segments"][key]["record_id"] for key in parsed["segments"]]
        material_ids = [state["records"]["materials"][key]["record_id"] for key in parsed["materials"]]
        for field_name, new_ids in (("脚本段", segment_ids), ("画面素材", material_ids)):
            old_ids = id_list(fresh_source["fields"].get(field_name), f"main/{field_name}")
            plan_ids = id_list(desired_main[field_name], f"main_fields/{field_name}") if field_name in desired_main else []
            merged = list(dict.fromkeys(old_ids + plan_ids + new_ids))
            desired_main[field_name] = [{"id": rid} for rid in merged]
        desired_main = {name: value for name, value in desired_main.items()
                        if not field_matches(fresh_source["fields"].get(name), value, schemas["videos"][name])}
        if desired_main:
            full_expected = {name: (value if name not in {"脚本段", "画面素材"} else
                                    [{"id": rid} for rid in list(dict.fromkeys(
                                        id_list(fresh_source["fields"].get(name), f"main/{name}") +
                                        id_list(value, f"main/{name}")))])
                             for name, value in desired_main.items()}
            safe_checkpoint(stop_file, state_path, state)
            state["pending"] = {"kind": "main_update", "table": "videos",
                                "expected_fields": full_expected}
            save_state(state_path, state, False)
            api.upsert(project["tables"]["videos"]["id"], parsed["plan"]["source_record_id"], desired_main, apply=True)
            after = api.get(project["tables"]["videos"]["id"], parsed["plan"]["source_record_id"],
                            list(dict.fromkeys(list(desired_main) + [SOURCE_SCRIPT_FIELD])), schemas["videos"])
            if sha256_bytes(cell_text(after["fields"].get(SOURCE_SCRIPT_FIELD)).encode("utf-8")) != source_script_hash:
                raise SyncError("Source script changed during main patch")
            for name, value in full_expected.items():
                if not field_matches(after["fields"].get(name), value, schemas["videos"][name]):
                    raise SyncError(f"Main field readback mismatch: {name}")
            state["pending"] = None
            state["main_fields_verified"] = True
            state["main_record_id"] = parsed["plan"]["source_record_id"]
            state["main_fields_written"] = sorted(full_expected)
            save_state(state_path, state, False)
        else:
            state["main_fields_verified"] = True
            state["main_record_id"] = parsed["plan"]["source_record_id"]
            save_state(state_path, state, False)

        print(json.dumps({"status": "technical_remote_qc_passed", "video_id": parsed["video_id"],
                          "revision": parsed["revision"], "plan_sha256": digest,
                          "materials_verified": len(parsed["materials"]), "segments_verified": len(parsed["segments"]),
                          "attachments_metadata_verified": sum(len(v) for t in state.get("attachments", {}).values() for v in t.values()),
                          "attachment_remote_bytes_sha256_verified": False,
                          "main_fields_verified": True, "source_script_unchanged": True,
                          "identity": project["identity"], "manual_audio_review_claimed": False,
                          "ui_playback_claimed": False}, ensure_ascii=False, indent=2))
        return 0
    except StoppedSafely as exc:
        print(json.dumps(exc.report, ensure_ascii=False, indent=2))
        return 0
    except SyncError as exc:
        print(json.dumps({"status": "STOP", "reason": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    except (OSError, KeyError, TypeError, ValueError, subprocess.SubprocessError) as exc:
        print(json.dumps({"status": "STOP", "reason": f"Local/runtime failure: {type(exc).__name__}"}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
