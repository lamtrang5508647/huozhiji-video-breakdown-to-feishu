#!/usr/bin/env python3
"""Validate script-v3 semantic sections against a reviewed shot manifest."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from emotion_progression import manifest_emotion
from source_evidence import validate_source_evidence
from script_shot_alignment import segment_alignment, build_alignment

class InvalidManifest(ValueError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InvalidManifest(f"cannot read JSON {path}: {exc}") from exc


def ffprobe_duration(path: Path, ffprobe: str) -> tuple[float, dict]:
    result = subprocess.run(
        [ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if result.returncode:
        raise InvalidManifest(f"ffprobe failed: {result.stderr[-3000:]}")
    data = json.loads(result.stdout)
    duration = float(data.get("format", {}).get("duration", "nan"))
    if not math.isfinite(duration) or duration <= 0:
        raise InvalidManifest("ffprobe returned an invalid source duration")
    if not any(s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic") for s in data.get("streams", [])):
        raise InvalidManifest("source has no ordinary video stream")
    return duration, data


def fail_if(condition: bool, message: str) -> None:
    if condition:
        raise InvalidManifest(message)


def validate(data: dict, manifest_path: Path, timeline_override: Path | None, ffprobe: str, root_review_path: Path | None = None, require_render_approval: bool = False) -> dict:
    fail_if(data.get("schema_version") != "script-v3.1", "schema_version must be script-v3.1")
    source = data.get("source")
    fail_if(not isinstance(source, dict), "source must be an object")
    source_path = Path(source.get("path", ""))
    if not source_path.is_absolute():
        source_path = (manifest_path.parent / source_path).resolve()
    fail_if(not source_path.is_file(), f"source file missing: {source_path}")
    expected_hash = source.get("sha256")
    fail_if(not isinstance(expected_hash, str) or len(expected_hash) != 64, "source.sha256 must be 64 hex chars")
    actual_hash = sha256(source_path)
    fail_if(actual_hash.lower() != expected_hash.lower(), "source SHA-256 mismatch")

    declared_timeline_path = source.get("timeline_manifest_path")
    fail_if(not isinstance(declared_timeline_path, str) or not declared_timeline_path.strip(), "source.timeline_manifest_path is required")
    timeline_digest = source.get("timeline_manifest_sha256")
    fail_if(not isinstance(timeline_digest, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", timeline_digest), "source.timeline_manifest_sha256 must be 64 hex chars")
    timeline_path = timeline_override or Path(declared_timeline_path)
    if not timeline_path.is_absolute():
        timeline_path = (manifest_path.parent / timeline_path).resolve()
    fail_if(not timeline_path.is_file(), f"reviewed timeline missing: {timeline_path}")
    timeline_hash = sha256(timeline_path)
    fail_if(timeline_hash.lower() != timeline_digest.lower(), "reviewed timeline SHA-256 mismatch")
    timeline = read_json(timeline_path)
    fail_if(timeline.get("video_id") != data.get("video_id"), "reviewed timeline video_id differs from script manifest")
    timeline_source = timeline.get("source", {})
    fail_if(timeline_source.get("sha256", "").lower() != actual_hash.lower(), "reviewed timeline source SHA-256 differs from actual source")
    for manifest_key, timeline_key in (("feishu_record_id", "feishu_record_id"), ("url", "url")):
        current_value, source_value = source.get(manifest_key), timeline_source.get(timeline_key)
        if current_value is not None and source_value is not None:
            fail_if(current_value != source_value, f"manifest source.{manifest_key} differs from reviewed timeline")
    source_shots = timeline.get("segments")
    fail_if(not isinstance(source_shots, list) or not source_shots, "reviewed timeline has no source shots")
    shot_by_id = {s.get("id"): s for s in source_shots}
    fail_if(len(shot_by_id) != len(source_shots) or None in shot_by_id, "source shot IDs are missing or duplicated")

    probed_duration, probe_data = ffprobe_duration(source_path, ffprobe)
    declared_duration = data.get("duration_sec")
    fail_if(not isinstance(declared_duration, (int, float)) or declared_duration <= 0, "duration_sec must be positive")
    video_stream = next(s for s in probe_data["streams"] if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic"))
    rate = video_stream.get("avg_frame_rate", "30/1")
    try:
        numerator, denominator = rate.split("/")
        fps = float(numerator) / float(denominator)
    except (ValueError, ZeroDivisionError):
        fps = 30.0
    if not math.isfinite(fps) or fps <= 0:
        fps = 30.0
    frame_tolerance = 1.0 / fps
    fail_if(abs(probed_duration - declared_duration) > max(0.15, frame_tolerance * 2), "manifest duration disagrees with ffprobe")
    duration_ms = round(declared_duration * 1000)
    segments = data.get("segments")
    fail_if(not isinstance(segments, list) or not segments, "segments must be a nonempty list")
    fail_if(len({s.get("id") for s in segments}) != len(segments), "segment IDs must be unique")
    fail_if(data.get("coverage") != "full", "export requires coverage='full'")
    fail_if(segments[0].get("start_ms") != 0, "full coverage must start at 0ms")
    fail_if(segments[-1].get("end_ms") != duration_ms, f"full coverage must end at {duration_ms}ms")

    expected_emotions = set(data.get("emotion_tag_options", []))
    expected_functions = set(data.get("script_function_options", []))
    source_refs: set[str] = set()
    quantization_delta_max_ms = 0.0
    cursor = 0
    for index, segment in enumerate(segments):
        label = f"segments[{index}]"
        segment_id = segment.get("id", "")
        fail_if(not isinstance(segment_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", segment_id), f"{label}.id must be a portable ASCII filename token")
        start, end = segment.get("start_ms"), segment.get("end_ms")
        fail_if(type(start) is not int or type(end) is not int, f"{label} start/end must be integer ms")
        fail_if(start != cursor, f"coverage gap/overlap at {cursor}..{start}ms before {label}")
        fail_if(not 0 <= start < end <= duration_ms, f"invalid range for {label}")
        for candidate in segment.get("machine_asr_boundary_candidates", []):
            snap_fps = candidate.get("snap_to_fps")
            if snap_fps:
                candidate_ms = candidate.get("candidate_ms")
                quantized_ms = round(candidate_ms * snap_fps / 1000) * 1000 / snap_fps
                quantization_delta = abs(quantized_ms - candidate_ms)
                quantization_delta_max_ms = max(quantization_delta_max_ms, quantization_delta)
                fail_if(quantization_delta > 1.0, f"{label} claims frame-snapped candidate but differs by {quantization_delta:.3f}ms")
        cursor = end

        primary_groups = segment.get("source_group_ids", [])
        fail_if(not primary_groups, f"{label} has no primary semantic source groups")
        for group in primary_groups:
            fail_if(group not in {g.get("id") for g in timeline.get("semantic_groups", [])}, f"{label} references unknown semantic group {group}")
        for shot_group in segment.get("shot_groups", []):
            fail_if(shot_group.get("source_group_id") not in primary_groups, f"{label} shot_group is not a primary semantic group")
            members = shot_group.get("source_shot_ids", [])
            fail_if(not isinstance(members, list) or not all(isinstance(sid, str) for sid in members),
                    f"{label} shot_group members must be shot IDs")
            if members:
                group = next(g for g in timeline['semantic_groups'] if g.get('id') == shot_group['source_group_id'])
                approved_members = group.get('source_shot_ids')
                fail_if(not isinstance(approved_members, list),
                        f"{label} reviewed timeline lacks shot_group membership; verify before associating guidance")
                fail_if(not set(members) <= set(approved_members) or not set(members) <= set(segment.get('source_shot_ids', [])),
                        f"{label} shot_group members differ from reviewed timeline group or segment")
        for tag in segment.get("emotion_tags", []):
            fail_if(tag not in expected_emotions, f"{label} has an emotion tag absent from manifest options: {tag}")
        fail_if(segment.get("script_function") not in expected_functions, f"{label} has an unknown script function")
        excerpt = segment.get("source_excerpt", {})
        fail_if(excerpt.get("type") != "subtitle_excerpts_only", f"{label} source_excerpt must identify its evidence type")
        spans = segment.get("source_shot_spans", [])
        fail_if(not spans, f"{label} has no source_shot_spans")
        span_ids = set()
        fail_if(len(spans) != len({span.get("shot_id") for span in spans}), f"{label} repeats source_shot_spans")
        for span in spans:
            shot_id = span.get("shot_id")
            fail_if(shot_id not in shot_by_id, f"{label} references unknown source shot {shot_id}")
            shot = shot_by_id[shot_id]
            inter_start = max(start, shot["start_ms"])
            inter_end = min(end, shot["end_ms"])
            fail_if(inter_end <= inter_start, f"{label} includes non-overlapping source shot {shot_id}")
            fail_if(span.get("source_shot_start_ms") != shot["start_ms"] or span.get("source_shot_end_ms") != shot["end_ms"], f"{label} source shot timing differs for {shot_id}")
            fail_if(span.get("segment_intersection_start_ms") != inter_start or span.get("segment_intersection_end_ms") != inter_end, f"{label} source intersection is incorrect for {shot_id}")
            span_ids.add(shot_id)
            source_refs.add(shot_id)
        expected_segment_shots = {
            shot_id for shot_id, shot in shot_by_id.items()
            if shot["start_ms"] < end and shot["end_ms"] > start
        }
        fail_if(span_ids != expected_segment_shots, f"{label} source_shot_spans must equal all source shots that actually intersect this interval")
        fail_if(set(segment.get("source_shot_ids", [])) != span_ids, f"{label} source_shot_ids differ from source_shot_spans")
        fail_if(len(segment.get("source_shot_ids", [])) != len(set(segment.get("source_shot_ids", []))), f"{label} repeats source_shot_ids")
        try:
            manifest_emotion(segment, {sid: (shot["start_ms"], shot["end_ms"])
                                       for sid, shot in shot_by_id.items()})
            validate_source_evidence(segment, data, shot_by_id)
            segment_alignment(segment)
        except ValueError as exc:
            raise InvalidManifest(f"{label}: {exc}") from exc
        for context in segment.get("boundary_context", []):
            fail_if(context.get("source_group_id") in primary_groups, f"{label} boundary context must not also be a primary source group")
            fail_if(not context.get("source_shot_spans"), f"{label} boundary context needs intersecting source spans")

    fail_if(cursor != duration_ms, "full coverage end does not match duration")
    try:
        build_alignment(data)
    except ValueError as exc:
        raise InvalidManifest(f"script-shot alignment: {exc}") from exc
    missing_shots = set(shot_by_id) - source_refs
    fail_if(bool(missing_shots), f"full clip coverage failed to reference source shots: {sorted(missing_shots)[:5]}")
    transcript_status = data.get("transcript_status", "")
    fail_if(not isinstance(transcript_status, str), "transcript_status must be a string")
    audio_status = data.get("audio_review_status", "")
    fail_if(not isinstance(audio_status, str) or not audio_status.strip(), "audio_review_status must be explicit")
    completion_terms = ("human_word_by_word_listening_complete", "human listening complete", "word-for-word verified", "完整口播已校", "逐句听校完成", "人工听校完成")
    statuses = [transcript_status.lower(), str(audio_status).lower()]
    statuses.extend(str(s.get("review_status", {}).get("audio", "")).lower() for s in segments)
    if any(term in status for status in statuses for term in completion_terms):
        evidence = data.get("audio_review_evidence")
        fail_if(not isinstance(evidence, dict) or not evidence.get("evidence") or not evidence.get("reviewer") or not evidence.get("reviewed_at") or not evidence.get("scope"), "a transcript/listening completion claim requires reviewer, reviewed_at, scope, and evidence")
    root_review_sha = None
    if root_review_path:
        root_review_path = root_review_path.resolve()
        review = read_json(root_review_path)
        fail_if(review.get("manifest_sha256", "").lower() != sha256(manifest_path).lower(), "review receipt manifest SHA does not match this manifest")
        fail_if(review.get("content_and_provenance") != "PASS", "review receipt has not approved content and provenance")
        fail_if(not isinstance(review.get("reviewer"), str) or not review["reviewer"].strip(), "review receipt requires reviewer")
        fail_if(not isinstance(review.get("approved_actions"), list), "review receipt requires approved_actions list")
        if require_render_approval:
            approved = review.get("approved_actions", [])
            fail_if("render continuous segment clips" not in approved, "review receipt does not approve rendering")
        root_review_sha = sha256(root_review_path)
    elif require_render_approval:
        raise InvalidManifest("--render requires --root-review pointing to approval bound to this manifest SHA")
    return {
        "status": "valid_draft",
        "segments": len(segments),
        "coverage_ms": duration_ms,
        "source_shots_referenced": len(source_refs),
        "source_sha256": actual_hash,
        "timeline_sha256": timeline_hash,
        "ffprobe_duration_sec": round(probed_duration, 6),
        "source_fps": round(fps, 6),
        "max_claimed_frame_quantization_delta_ms": round(quantization_delta_max_ms, 3),
        "audio_review_status": audio_status,
        "emotion_evidence_incomplete": [s["id"] for s in segments
            if s.get("emotion", {}).get("progression_status") == "insufficient_evidence"],
        "review_receipt_sha256": root_review_sha,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--timeline", type=Path)
    parser.add_argument("--ffprobe", default="ffprobe")
    parser.add_argument("--root-review", "--review-receipt", dest="root_review", type=Path, help="analyst review receipt bound to this manifest SHA-256")
    parser.add_argument("--require-render-approval", action="store_true")
    args = parser.parse_args()
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    try:
        manifest_path = args.manifest.resolve()
        result = validate(read_json(manifest_path), manifest_path, args.timeline.resolve() if args.timeline else None, args.ffprobe, args.root_review, args.require_render_approval)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, StopIteration, KeyError, TypeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
