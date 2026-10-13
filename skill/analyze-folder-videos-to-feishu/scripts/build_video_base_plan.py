#!/usr/bin/env python3
"""Build a deterministic, reviewable Base upload plan from reviewed video artifacts.

This is a local transformation only. It never calls Feishu/Base and does not
invent script analysis. All content is copied or formatted from the reviewed
script manifest and the two export indexes.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any
from source_identity import canonical_schema, source_identity
from emotion_progression import progression_label, event_label
from source_evidence import SourceEvidenceError, format_source_evidence as _format_source_evidence


class PlanError(RuntimeError):
    pass


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PlanError(f"Could not read valid UTF-8 JSON: {path}") from exc
    if not isinstance(value, dict):
        raise PlanError(f"Expected JSON object: {path}")
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def text(value: Any, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise PlanError(f"Missing text: {label}")
    return value


def safe_id(value: str, label: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,159}", value):
        raise PlanError(f"Unsafe business key for {label}")
    return value


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def format_source_evidence(segment: dict[str, Any], manifest: dict[str, Any],
                           timeline_by_id: dict[str, dict[str, Any]]) -> str:
    """Compatibility wrapper using the shared export/presentation evidence gate."""
    try:
        return _format_source_evidence(segment, manifest, timeline_by_id)
    except SourceEvidenceError as exc:
        raise PlanError(str(exc)) from exc


def format_excerpt(manifest_segment: dict[str, Any], timeline_by_id: dict[str, dict[str, Any]]) -> str:
    """Return exact subtitle strings from main source groups; ignore legacy duration flags."""
    group_ids = set(manifest_segment.get("source_group_ids", []))
    explicit_context = {
        shot_id
        for item in manifest_segment.get("boundary_context", [])
        if isinstance(item, dict)
        for shot_id in item.get("source_shot_ids", [])
    }
    spans = manifest_segment.get("source_shot_spans", [])
    span_ids = {item.get("shot_id") for item in spans if isinstance(item, dict)}
    lines: list[str] = []
    for shot_id in sorted(span_ids, key=lambda sid: timeline_by_id[sid]["ordinal"]):
        shot = timeline_by_id.get(shot_id)
        if shot is None:
            raise PlanError("Manifest span references a shot absent from timeline")
        if shot.get("group_id") not in group_ids or shot_id in explicit_context:
            continue
        excerpt = shot.get("subtitle_excerpt")
        if isinstance(excerpt, str) and excerpt:
            lines.append(f"{shot_id}｜{excerpt}")
    if not lines:
        raise PlanError("Reviewed segment has no subtitle excerpts in its declared source groups")
    return "\n".join(lines)


def emotion_text(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    chunks: list[str] = []
    status_label = progression_label(value)
    if status_label:
        chunks.append(status_label)
    for index, stage in enumerate(value.get("observable_timeline", [])):
        if not isinstance(stage, dict):
            continue
        label = event_label(stage, index)
        evidence = "、".join(stage.get("evidence_shot_ids", []))
        observation = text(stage.get("observable"), "emotion.observable")
        chunks.append(f"{label}（依据：{evidence}）{observation}")
    if value.get("audience_hypothesis"):
        chunks.append("受众假设（推测，非实测）=" + str(value["audience_hypothesis"]))
    return "\n".join(chunks)


def shot_group_text(groups: Any) -> str:
    if not isinstance(groups, list) or not groups:
        raise PlanError("Reviewed segment lacks shot_groups")
    out: list[str] = []
    for group in groups:
        if not isinstance(group, dict):
            raise PlanError("Malformed shot group")
        shoot = group.get("replication_shooting_plan")
        if not isinstance(shoot, dict):
            raise PlanError("Shot group lacks replication_shooting_plan")
        group_id = text(group.get("source_group_id"), "shot_group.source_group_id")
        source_ids = group.get("source_shot_ids")
        if not isinstance(source_ids, list) or not source_ids:
            raise PlanError("Shot group has no source shot IDs")
        out.append(
            f"{group_id}｜原片观察：{text(group.get('source_observation'), 'source_observation')}\n"
            f"新拍主体/动作：{text(shoot.get('subject_action'), 'subject_action')}\n"
            f"景别/机位：{text(shoot.get('shot_size_angle'), 'shot_size_angle')}\n"
            f"句式槽位：{text(shoot.get('example_sentence_slots'), 'example_sentence_slots')}\n"
            f"信息顺序：{text(shoot.get('information_order'), 'information_order')}\n"
            f"衔接：{text(shoot.get('edit_transition'), 'edit_transition')}"
        )
    return "\n\n".join(out)


def fields_from_schema(project_root: Path, profile: dict[str, Any], logical: str) -> dict[str, dict[str, Any]]:
    table = profile["tables"][logical]
    snapshot_path = (project_root / table["fields_snapshot"]).resolve()
    if not snapshot_path.is_relative_to(project_root.resolve()) or not snapshot_path.is_file():
        raise PlanError(f"Missing or unsafe schema snapshot for {logical}")
    snapshot = read_json(snapshot_path)
    fields = snapshot.get("data", {}).get("fields")
    if not isinstance(fields, list):
        raise PlanError(f"Invalid saved schema for {logical}")
    raw = {item["name"]: item for item in fields if isinstance(item, dict) and isinstance(item.get("name"), str)}
    try:
        return canonical_schema(raw, table.get("fields"), logical)
    except ValueError as exc:
        raise PlanError(str(exc)) from exc


def typed(fields: dict[str, Any], schema: dict[str, dict[str, Any]], name: str, value: Any) -> None:
    if name not in schema:
        raise PlanError(f"Field is absent from selected project profile: {name}")
    kind = schema[name].get("type")
    if kind == "text" and isinstance(value, str) and value:
        fields[name] = value
    elif kind == "number" and isinstance(value, (int, float)) and not isinstance(value, bool):
        fields[name] = value
    elif kind == "select":
        multi = bool(schema[name].get("multiple"))
        values = value if isinstance(value, list) else [value]
        options = {x.get("name") for x in schema[name].get("options", []) if isinstance(x, dict)}
        if (multi and (not isinstance(value, list) or not value)) or (not multi and not isinstance(value, str)):
            raise PlanError(f"Select cardinality mismatch for {name}")
        if not all(isinstance(item, str) and item in options for item in values):
            raise PlanError(f"Value is not an existing saved-schema option for {name}")
        fields[name] = value
    elif kind == "link" and isinstance(value, list) and value and all(isinstance(x, dict) and isinstance(x.get("id"), str) for x in value):
        fields[name] = value
    else:
        raise PlanError(f"Value does not match the saved field type for {name}")


def bounded_path(candidate: str, base: Path, workspace: Path, label: str) -> Path:
    raw = Path(candidate)
    resolved = raw.resolve() if raw.is_absolute() else (base / raw).resolve()
    if not resolved.is_relative_to(workspace.resolve()):
        raise PlanError(f"{label} escapes --workspace")
    if not resolved.is_file():
        raise PlanError(f"Missing {label}: {resolved.name}")
    return resolved


def make_attachment(field: str, path: Path, workspace: Path) -> dict[str, str]:
    return {"field": field, "path": str(path.relative_to(workspace.resolve())), "sha256": sha256_file(path)}


def validate_export_bundle(manifest_path: Path, manifest: dict, export_path: Path, export: dict,
                           index_path: Path, timeline_path: Path, timeline: dict,
                           workspace: Path, review_path: Path | None) -> dict:
    """Bind plan inputs to the exact reviewed inputs and completed exporter receipt."""
    status = manifest.get('review_status')
    if status is not None and status not in {'reviewed', 'content_reviewed', 'supervisor_reviewed'}:
        raise PlanError('Script manifest review_status is not an explicitly approved status')
    if review_path is None:
        raise PlanError('--review-receipt is required to bind the original content review')
    review_path = review_path.resolve()
    review = read_json(review_path)
    manifest_sha = sha256_file(manifest_path)
    if (review.get('manifest_sha256') != manifest_sha or review.get('content_and_provenance') != 'PASS'
            or not isinstance(review.get('reviewer'), str) or not review['reviewer'].strip()
            or not isinstance(review.get('approved_actions'), list)
            or 'render continuous segment clips' not in review.get('approved_actions', [])):
        raise PlanError('Content review receipt does not approve this exact manifest and export')
    receipt_path = export_path.parent / 'receipt.json'
    receipt = read_json(receipt_path)
    if (receipt.get('status') != 'complete' or receipt.get('manifest_sha256') != manifest_sha
            or receipt.get('source_sha256') != manifest['source']['sha256']
            or receipt.get('review_receipt_sha256') != sha256_file(review_path)
            or receipt.get('build_id') != export.get('export', {}).get('build_id')):
        raise PlanError('Export receipt is incomplete or belongs to different reviewed inputs')
    reports = receipt.get('report_sha256')
    required = {'index.html', 'README.md', 'segments.csv', 'export_manifest.json'}
    if ('script-v3-export/1.3' in (receipt.get('engine'), export.get('export', {}).get('engine'))):
        if receipt.get('engine') != export.get('export', {}).get('engine'):
            raise PlanError('Export engine differs from receipt')
        required |= {'script-shot-alignment.json', 'script-shot-alignment.csv'}
    if not isinstance(reports, dict) or set(reports) != required:
        raise PlanError('Export receipt lacks the complete report hash set')
    if export_path != (export_path.parent / 'export_manifest.json').resolve() or index_path != (export_path.parent / 'segments.csv').resolve():
        raise PlanError('Export manifest and CSV must be the receipt-bound exporter files')
    for name, digest in reports.items():
        path = bounded_path(name, export_path.parent, workspace, 'export report')
        if sha256_file(path) != digest:
            raise PlanError('Export report changed after verified export: ' + name)
    source = manifest['source']
    original_path = bounded_path(text(source.get('timeline_manifest_path'), 'source.timeline_manifest_path'),
                                 manifest_path.parent, workspace, 'reviewed timeline')
    original_sha = sha256_file(original_path)
    if original_sha != source.get('timeline_manifest_sha256') or original_sha != receipt.get('timeline_sha256'):
        raise PlanError('Reviewed timeline changed after manifest review/export')
    original = read_json(original_path)
    for key, value in original.items():
        if key not in {'source', 'segments'} and timeline.get(key) != value:
            raise PlanError('Exported timeline differs from reviewed timeline: ' + key)
    for key, value in original.get('source', {}).items():
        if key not in {'path', 'probe'} and timeline.get('source', {}).get(key) != value:
            raise PlanError('Exported timeline source differs from reviewed timeline: ' + key)
    original_shots = original.get('segments', [])
    exported_shots = timeline.get('segments', [])
    if len(original_shots) != len(exported_shots) or not original_shots:
        raise PlanError('Exported timeline shot coverage differs from reviewed timeline')
    for approved, rendered in zip(original_shots, exported_shots):
        if any(rendered.get(key) != value for key, value in approved.items()):
            raise PlanError('Exported shot content/range differs from the reviewed source timeline')
    source_file = bounded_path(text(source.get('path'), 'source.path'), manifest_path.parent, workspace, 'source video')
    if sha256_file(source_file) != source['sha256']:
        raise PlanError('Source video changed after review/export')
    # export_manifest is the reviewed input plus exporter metadata only.
    exported_content = json.loads(json.dumps(export))
    exported_content.pop('export', None)
    for item in exported_content.get('segments', []):
        item.pop('export', None)
    if exported_content != manifest:
        raise PlanError('Export manifest content differs from the reviewed script manifest')
    records = receipt.get('segments', [])
    ids = [item.get('id') for item in manifest.get('segments', [])]
    by_id = {item.get('segment_id'): item for item in records if isinstance(item, dict)}
    if len(by_id) != len(records) or set(by_id) != set(ids) or len(set(ids)) != len(ids):
        raise PlanError('Export receipt does not cover exactly the reviewed script segments')
    export_by_id = {item['id']: item for item in export['segments']}
    for sid, record in by_id.items():
        safe_id(sid, 'receipt.segment_id')
        clip = bounded_path(f'clips/{sid}.mp4', export_path.parent, workspace, 'script segment clip')
        thumbnail = bounded_path(f'thumbnails/{sid}.jpg', export_path.parent, workspace, 'script segment thumbnail')
        if (sha256_file(clip) != record.get('clip_sha256') or sha256_file(thumbnail) != record.get('thumbnail_sha256')
                or record.get('verification', {}).get('fully_decoded') is not True
                or record.get('verification', {}).get('sha256') != record.get('clip_sha256')
                or export_by_id[sid].get('export') != record):
            raise PlanError('Script segment media changed or lacks completed exporter verification: ' + sid)
    export_status = read_json(export_path.parent / 'export_status.json')
    if export_status.get('status') != 'complete' or export_status.get('completed_segments') != len(ids):
        raise PlanError('Script export status is not complete')
    input_paths = {}
    for key, path in {'manifest': manifest_path, 'review_receipt': review_path, 'export_manifest': export_path,
                      'segment_index': index_path, 'timeline': timeline_path}.items():
        verified = bounded_path(str(path), workspace, workspace, key)
        input_paths[key] = verified.relative_to(workspace.resolve()).as_posix()
    return {'input_paths': input_paths, 'manifest_sha256': manifest_sha, 'review_receipt_sha256': sha256_file(review_path),
            'export_receipt_sha256': sha256_file(receipt_path), 'script_index_sha256': sha256_file(index_path),
            'reviewed_timeline_sha256': original_sha, 'exported_timeline_sha256': sha256_file(timeline_path)}


def validate_timeline(manifest: dict[str, Any], timeline: dict[str, Any], workspace: Path, timeline_path: Path) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    video_id = manifest["video_id"]
    if timeline.get("video_id") != video_id:
        raise PlanError("Timeline video_id does not match script manifest")
    if timeline.get("source", {}).get("sha256", "").lower() != manifest.get("source", {}).get("sha256", "").lower():
        raise PlanError("Timeline source SHA-256 does not match script manifest")
    if timeline.get("coverage") != "full" or timeline.get("review_status") != "complete":
        raise PlanError("Timeline is not marked complete with full coverage")
    shots = timeline.get("segments")
    groups = timeline.get("semantic_groups")
    if not isinstance(shots, list) or not shots or not isinstance(groups, list) or not groups:
        raise PlanError("Timeline lacks reviewed shots or semantic groups")
    by_id: dict[str, dict[str, Any]] = {}
    for shot in shots:
        if 'ordinal' not in shot:
            shot['ordinal'] = shot.get('sequence')
        if not isinstance(shot['ordinal'], int):
            raise PlanError('Timeline lacks integer sequence')
    ordered = sorted(shots, key=lambda item: item['ordinal'])
    cursor = 0
    expected_group_shots: set[str] = set()
    for shot in ordered:
        shot_id = text(shot.get("id"), "timeline shot id")
        if shot_id in by_id:
            raise PlanError("Duplicate shot id in timeline")
        if shot.get("start_ms") != cursor or not isinstance(shot.get("end_ms"), int) or shot["end_ms"] <= cursor:
            raise PlanError("Timeline shots must be continuous and have positive ranges")
        if shot.get("reviewed") is not True:
            raise PlanError("Timeline contains an unreviewed shot")
        if shot.get("verification", {}).get("fully_decoded") is not True:
            raise PlanError("A timeline shot clip lacks full-decode QC")
        cursor = shot["end_ms"]
        by_id[shot_id] = shot
    duration_ms = round(float(manifest["duration_sec"]) * 1000)
    if abs(cursor - duration_ms) > 160:
        raise PlanError("Timeline does not cover the script manifest duration")
    for group in groups:
        if group.get('shot_ids') is not None and group.get('source_shot_ids') is not None and group['shot_ids'] != group['source_shot_ids']:
            raise PlanError('Semantic group has conflicting shot membership lists')
        shot_ids = group.get("shot_ids", group.get('source_shot_ids'))
        if shot_ids is None:
            shot_ids = [shot['id'] for shot in ordered if shot.get('group_id') == group.get('id')]
        if not isinstance(shot_ids, list):
            raise PlanError("Malformed semantic group shot_ids")
        for shot_id in shot_ids:
            if shot_id not in by_id or by_id[shot_id].get("group_id") not in (None, group.get("id")):
                raise PlanError("Semantic group and timeline shot membership disagree")
            if shot_id in expected_group_shots:
                raise PlanError("A shot occurs in multiple semantic groups")
            expected_group_shots.add(shot_id)
            by_id[shot_id]['group_id'] = group.get('id')
            by_id[shot_id]['group_name'] = group.get('name', by_id[shot_id].get('group_name'))
    if expected_group_shots != set(by_id):
        raise PlanError("Semantic groups do not cover every timeline shot")

    for shot in ordered:
        shot["_clip_path"] = str(bounded_path(shot["clip_file"], timeline_path.parent, workspace, "shot clip"))
        shot["_thumb_path"] = str(bounded_path(shot["thumbnail_file"], timeline_path.parent, workspace, "shot thumbnail"))
        if (sha256_file(Path(shot['_clip_path'])) != shot.get('verification', {}).get('sha256')
                or sha256_file(Path(shot['_thumb_path'])) != shot.get('thumbnail_sha256')):
            raise PlanError('Shot media changed after verified timeline export: ' + shot['id'])
    return ordered, by_id


def build_plan(args: argparse.Namespace) -> dict[str, Any]:
    workspace = args.workspace.resolve()
    project_root = args.project.resolve()
    project = read_json(project_root / "config" / "project.json")
    manifest_path = args.manifest.resolve()
    export_path = args.export_manifest.resolve()
    index_path = args.segment_index.resolve()
    timeline_path = args.timeline.resolve()
    manifest = read_json(manifest_path)
    export = read_json(export_path)
    timeline = read_json(timeline_path)

    video_id = safe_id(text(manifest.get("video_id"), "manifest.video_id"), "video_id")
    source = manifest.get("source")
    if not isinstance(source, dict):
        raise PlanError("Manifest has no source object")
    source_sha = text(source.get("sha256"), "manifest.source.sha256").lower()
    if not re.fullmatch(r"[0-9a-f]{64}", source_sha):
        raise PlanError("Manifest source SHA is invalid")
    if source.get("feishu_record_id") and source["feishu_record_id"] != args.source_record_id:
        raise PlanError("Explicit source record ID differs from reviewed manifest")
    if not args.source_record_id.startswith("rec") or not args.product_record_id.startswith("rec"):
        raise PlanError("Source and product record IDs must be Base record IDs")
    try:
        identity = source_identity(video_id, source, platform=getattr(args, "platform", None),
                                   id_field=project.get("source_identity", {}).get("id_field"))
    except ValueError as exc:
        raise PlanError(str(exc)) from exc
    source_url, platform = identity["url"], identity["platform"]
    if export.get("video_id") != video_id or export.get("source", {}).get("sha256", "").lower() != source_sha:
        raise PlanError("Script export belongs to a different video or source SHA")
    if export.get("export", {}).get("status") != "complete":
        raise PlanError("Script segment export is not complete")
    if not index_path.is_file():
        raise PlanError("Script segment index CSV is missing")
    if not manifest.get("coverage") == "full":
        raise PlanError("Script manifest must declare full-video coverage")
    provenance = validate_export_bundle(manifest_path, manifest, export_path, export, index_path,
                                        timeline_path, timeline, workspace, getattr(args, 'review_receipt', None))

    shots, shot_by_id = validate_timeline(manifest, timeline, workspace, timeline_path)
    corrections = manifest.get('annotation_corrections', {})
    if corrections:
        if corrections.get('timeline_sha256') != manifest['source']['timeline_manifest_sha256']:
            raise PlanError('Subtitle correction bound to another timeline')
        for correction in corrections.get('corrections', []):
            shot = shot_by_id[correction['shot_id']]
            if correction['field'] != 'subtitle_excerpt' or shot['subtitle_excerpt'] != correction['frozen_text'] or not Path(correction['evidence_path']).is_file():
                raise PlanError('Subtitle correction evidence/base mismatch')
            shot['subtitle_excerpt'] = correction['native_visible_text']
    main_schema = fields_from_schema(project_root, project, "videos")
    material_schema = fields_from_schema(project_root, project, "materials")
    segment_schema = fields_from_schema(project_root, project, "segments")

    group_ids = {group["id"] for group in timeline["semantic_groups"]}
    timeline_duration = float(timeline["duration_sec"])
    manifest_segments = manifest.get("segments")
    if not isinstance(manifest_segments, list) or not manifest_segments:
        raise PlanError("Script manifest has no segments")
    sorted_segments = sorted(manifest_segments, key=lambda item: item.get("ordinal", 0))
    if len({item.get("id") for item in sorted_segments}) != len(sorted_segments):
        raise PlanError("Duplicate script segment id")
    cursor_ms = 0
    for seg in sorted_segments:
        if seg.get("start_ms") != cursor_ms or not isinstance(seg.get("end_ms"), int) or seg["end_ms"] <= cursor_ms:
            raise PlanError("Script segments must be continuous and cover the full source")
        cursor_ms = seg["end_ms"]
        if not set(seg.get("source_group_ids", [])) <= group_ids:
            raise PlanError("Script segment references an unknown source group")
        spans = seg.get("source_shot_spans")
        if not isinstance(spans, list):
            raise PlanError("Script segment lacks source_shot_spans")
        expected_ids = {
            shot["id"] for shot in shots
            if shot["start_ms"] < seg["end_ms"] and shot["end_ms"] > seg["start_ms"]
        }
        actual_ids = {item.get("shot_id") for item in spans if isinstance(item, dict)}
        if len(actual_ids) != len(spans) or actual_ids != expected_ids:
            raise PlanError("Script segment spans do not equal all actual timeline intersections")
        for span in spans:
            shot = shot_by_id[span["shot_id"]]
            expected_start = max(shot["start_ms"], seg["start_ms"])
            expected_end = min(shot["end_ms"], seg["end_ms"])
            if span.get("segment_intersection_start_ms") != expected_start or span.get("segment_intersection_end_ms") != expected_end:
                raise PlanError("Script segment span offsets disagree with timeline")
    if abs(cursor_ms - round(float(manifest["duration_sec"]) * 1000)) > 160 or abs(timeline_duration - float(manifest["duration_sec"])) > 0.16:
        raise PlanError("Script segments, timeline and manifest durations disagree")

    with index_path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != len(sorted_segments):
        raise PlanError("Script export index row count differs from manifest segments")
    index_by_id = {row.get("segment_id"): row for row in rows}
    if len(index_by_id) != len(rows):
        raise PlanError("Duplicate segment ID in script export index")
    for seg in sorted_segments:
        row = index_by_id.get(seg["id"])
        if not row or row.get("video_id") != video_id or row.get("revision") != manifest.get("revision"):
            raise PlanError("Script export index does not match reviewed manifest segment IDs/revision")
        for field, expected in (("start_ms", str(seg["start_ms"])), ("end_ms", str(seg["end_ms"]))):
            if row.get(field) != expected:
                raise PlanError("Script export index range differs from reviewed manifest")
        row["_clip_path"] = str(bounded_path(row.get("clip_path", ""), export_path.parent, workspace, "script segment clip"))

    base_id = text(project.get("base_id"), "project.base_id")
    materials: list[dict[str, Any]] = []
    tag_path = manifest_path.parent / 'shot-tag-map.json'
    tag_map = read_json(tag_path) if tag_path.exists() else {}
    if tag_map and set(tag_map) != set(shot_by_id):
        raise PlanError('Reviewed shot-tag mapping must cover this exact timeline')
    shot_key_by_id: dict[str, str] = {}
    material_revision = str(manifest.get("revision", ""))
    for shot in shots:
        shot_id = shot["id"]
        key = safe_id(f"{video_id}:{material_revision}:shot-{shot['ordinal']:04d}", "material key")
        shot_key_by_id[shot_id] = key
        start_sec, end_sec = shot["start_ms"] / 1000, shot["end_ms"] / 1000
        fields: dict[str, Any] = {}
        for name, value in (
            ("素材ID", key), ("来源记录", args.source_record_id),
            ("原视频关联", [{"id": args.source_record_id}]),
            ("开始秒", start_sec), ("结束秒", end_sec),
            ("画面编号", shot_id), ("画面序号", shot["ordinal"]),
            ("文件名", Path(shot["_clip_path"]).name),
            ("画面描述", str(shot.get("description", "")) or "（无独立画面描述）"),
            ("对应字幕摘录", str(shot.get("subtitle_excerpt", "")) or "（无可读字幕摘录）"),
            ("内容标签", "、".join(shot.get("tags", [])) or "（未标注）"),
            ("画面标签", tag_map.get(shot_id, shot.get('tags', []))),
            ("素材类型", "video/mp4"), ("可用状态", "需复核"), ("质量评级", "待复核"),
            ("内容段编号", text(shot.get("group_id"), "timeline.group_id")),
            ("内容段名称", text(shot.get("group_name"), "timeline.group_name")),
            ("拆解版本", material_revision), ("时长秒", (shot["end_ms"] - shot["start_ms"]) / 1000),
            ("校对说明", "画面与字幕摘录已复核；字幕为原画面摘录，不代表完整口播逐句人工听校。"),
        ):
            typed(fields, material_schema, name, value)
        attachments = [
            make_attachment("素材附件", Path(shot["_clip_path"]), workspace),
            make_attachment("关键帧", Path(shot["_thumb_path"]), workspace),
        ]
        materials.append({"key": key, "fields": fields, "attachments": attachments})

    segments: list[dict[str, Any]] = []
    index_hash = {row["segment_id"]: row for row in rows}
    for seg in sorted_segments:
        seg_id = safe_id(text(seg.get("id"), "manifest.segment.id"), "script segment key")
        segment_key = safe_id(f'{video_id}:{material_revision}:segment:{seg_id}', 'segment business key')
        function = text(seg.get("script_function"), "segment.script_function")
        start_sec, end_sec = seg["start_ms"] / 1000, seg["end_ms"] / 1000
        excerpt = format_excerpt(seg, shot_by_id)
        emotion = emotion_text(seg.get("emotion"))
        source_evidence = format_source_evidence(seg, manifest, shot_by_id)
        reusable = seg.get("reusable_formula", {})
        if not isinstance(reusable, dict):
            raise PlanError("Malformed reusable_formula")
        formula = text(reusable.get("structure"), "segment.reusable_formula.structure")
        slots = reusable.get("slots", [])
        conditions = str(reusable.get("conditions", ""))
        evidence = str(reusable.get("required_evidence", ""))
        fields: dict[str, Any] = {}
        for name, value in (
            ("脚本段键", segment_key), ("原视频", [{"id": args.source_record_id}]),
            ("所属产品", [{"id": args.product_record_id}]),
            ("段落序号", seg["ordinal"]), ("段落编号", str(seg["ordinal"]).zfill(2)),
            ("段名", text(seg.get("name"), "segment.name")),
            ("开始秒", start_sec), ("结束秒", end_sec), ("时长秒", end_sec - start_sec),
            ("时间范围", f"{start_sec:.3f}–{end_sec:.3f} 秒"),
            ("来源视频标题", text(manifest.get("title"), "manifest.title")),
            ("来源平台", platform), ("脚本功能", function),
            ("源文案", excerpt), ("源片结构", text(seg.get("source_structure"), "segment.source_structure")),
            ("核心问题或主张", "原片问题/主张（未独立核实）：" + text(seg.get("core_question"), "segment.core_question")),
            ("本段作用", text(seg.get("role"), "segment.role")),
            ("原片情绪推进", emotion or "原片未提供结构化情绪证据"),
            ("原片情绪标签", seg.get("emotion_tags", []) or ["未明确"]),
            ("呈现的证据", source_evidence), ("可替换句式", text(reusable.get("example_sentence_slots"), "segment.example_sentence_slots")),
            ("本段脚本公式", formula),
            ("可复用结构", "结构：" + formula + "\n变量槽位：" + "、".join(slots) + "\n复用条件：" + conditions + "\n所需证据：" + evidence),
            ("镜头组说明", shot_group_text(seg.get("shot_groups"))),
            ("模型适配判断", json_text(seg.get("model_suitability", {}))),
            ("原音校对状态", "机器词级对齐（非人工听校）" if manifest.get('machine_boundary_evidence') else "未校对"),
            ("文案来源", "源画面字幕摘录"),
            ("片段检查状态", "候选边界待人工听校"),
            ("校对说明", "脚本段文字来自已复核字幕摘录，不代表完整口播逐句人工听校；原片主张仍须独立核验。"),
            ("拆解版本", material_revision),
        ):
            typed(fields, segment_schema, name, value)
        shot_keys = [shot_key_by_id[item["shot_id"]] for item in seg["source_shot_spans"]]
        segments.append({
            "key": segment_key,
            "source_segment_id": seg_id,
            "fields": fields,
            "source_shot_keys": shot_keys,
            "attachments": [make_attachment("段落视频", Path(index_hash[seg_id]["_clip_path"]), workspace)],
        })

    whole = manifest.get("whole_video", {})
    if not isinstance(whole, dict):
        raise PlanError("Manifest whole_video must be an object")
    main_fields: dict[str, Any] = {}
    for name, value in (
        ("所属产品", [{"id": args.product_record_id}]),
        ("标题", text(manifest.get("title"), "manifest.title")),
        ("来源平台", platform), ("来源链接", source_url),
        ("整片框架分析", text(whole.get("strategy"), "whole_video.strategy") + "\n\n" + "\n".join(f"{item.get('order')}. {item.get('function')}" for item in whole.get("framework", []))),
        ("原片脚本公式", "\n".join(f"{item.get('order')}. {item.get('function')}" for item in whole.get("framework", []))),
        ("可复用脚本骨架", text(whole.get("reusable"), "whole_video.reusable")),
        ("可复用变量公式", text(whole.get("reusable"), "whole_video.reusable")),
        ("复刻适用条件", "\n".join(str(x) for x in whole.get("replication_conditions", [])) + "\n\n边界：\n" + "\n".join(str(x) for x in whole.get("claim_boundaries", []))),
        ("拆解版本", material_revision), ("画面总数", len(shots)),
        ("片中产品/品牌", "；".join(str(source.get(k)) for k in ("visible_brand", "visible_product_text") if source.get(k))),
        ("原音校对状态", "机器词级对齐（非人工听校）" if manifest.get('machine_boundary_evidence') else "未校对"),
        ("完整拆解时间线", "字幕摘录定位（非完整口播）：\n" + "\n".join(f"{s['ordinal']}. {s['name']}｜{s['start_ms']/1000:.3f}–{s['end_ms']/1000:.3f}s｜{s['script_function']}" for s in sorted_segments)),
        ("拆解进度", "脚本内容已审核；字幕摘录/镜头边界已复核；原音逐句人工听校待完成"),
    ):
        typed(main_fields, main_schema, name, value)

    if args.doc_links:
        links = read_json(args.doc_links.resolve())
        link_path = bounded_path(str(args.doc_links.resolve()), workspace, workspace, 'document links')
        provenance['input_paths']['doc_links'] = link_path.relative_to(workspace).as_posix()
        provenance['doc_links_sha256'] = sha256_file(link_path)
        if links.get("video_id") != video_id or links.get("source_sha256", "").lower() != source_sha:
            raise PlanError("Optional document links are not bound to this exact source")
        main_url = links.get("main_url")
        if main_url:
            typed(main_fields, main_schema, "彩色脚本分析", text(main_url, "doc_links.main_url"))
        segment_urls = links.get("segments", {})
        if not isinstance(segment_urls, dict):
            raise PlanError("doc_links.segments must be an object keyed by stable segment ID")
        for seg in segments:
            url = segment_urls.get(seg["source_segment_id"])
            if url:
                typed(seg["fields"], segment_schema, "彩色脚本", text(url, "doc_links segment URL"))

    result = {
        "base_id": base_id,
        "source_identity": identity,
        "export_provenance": provenance,
        "video_id": video_id,
        "source_record_id": args.source_record_id,
        "product_record_id": args.product_record_id,
        "source_sha256": source_sha,
        "revision": material_revision,
        "main_fields": main_fields,
        "materials": materials,
        "segments": segments,
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True, help="approved script_manifest.json")
    parser.add_argument("--export-manifest", type=Path, required=True, help="export/export_manifest.json")
    parser.add_argument("--review-receipt", type=Path, required=True, help="original content review bound by exporter receipt.json")
    parser.add_argument("--segment-index", type=Path, required=True, help="export/segments.csv from export_script_v3")
    parser.add_argument("--timeline", type=Path, required=True, help="complete reviewed timeline.json from export_reviewed_timeline")
    parser.add_argument("--project", type=Path, required=True, help="project root with explicit config")
    parser.add_argument("--platform", help="explicit source platform; must agree with manifest.source.platform")
    parser.add_argument("--source-record-id", required=True, help="live parent video record ID, explicitly confirmed")
    parser.add_argument("--product-record-id", required=True, help="live product record ID, explicitly confirmed")
    parser.add_argument("--workspace", type=Path, default=Path.cwd(), help="root for generated attachment paths (default: cwd)")
    parser.add_argument("--output", type=Path, required=True, help="new local plan JSON output")
    parser.add_argument("--doc-links", type=Path, help="optional source-bound JSON of already-created Docx entry URLs")
    args = parser.parse_args()
    try:
        plan = build_plan(args)
        output = args.output.resolve()
        workspace = args.workspace.resolve()
        if not output.is_relative_to(workspace):
            raise PlanError("Plan output must remain inside --workspace")
        atomic_write_json(output, plan)
        attachments = sum(len(item["attachments"]) for item in plan["materials"] + plan["segments"])
        print(json.dumps({
            "status": "local_plan_built_review_required",
            "video_id": plan["video_id"], "revision": plan["revision"],
            "material_rows": len(plan["materials"]), "script_segments": len(plan["segments"]),
            "attachments_included": attachments,
            "all_source_shot_clip_and_keyframe_pairs_included": True,
            "source_script_write": False,
            "plan_path": str(output),
            "plan_sha256": sha256_file(output),
            "next_step": "Human review the generated plan and attachment counts; only then use sync_video_base.py --dry-run.",
        }, ensure_ascii=True, indent=2))
        return 0
    except (PlanError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
