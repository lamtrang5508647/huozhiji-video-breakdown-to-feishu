"""Pure source-material binding checks; never verifies a claim's truth."""
from __future__ import annotations

import json
import re


class SourceEvidenceError(ValueError):
    pass


def text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise SourceEvidenceError(f"Missing text: {label}")
    return value


def json_text(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def validate_source_evidence(segment, manifest, timeline_by_id):
    """Return checked records without modifying the input or certifying its content."""
    evidence = segment.get("source_evidence", [])
    if not isinstance(evidence, list):
        raise SourceEvidenceError("segment.source_evidence must be an array")
    required = {"source_video_id", "source_sha256", "source_shot_ids", "start_ms", "end_ms", "description"}
    allowed = required | {"support_scope", "verification_status"}
    checked = []
    for index, item in enumerate(evidence):
        label = f"segment.source_evidence[{index}]"
        if not isinstance(item, dict) or not required <= set(item) or set(item) - allowed:
            raise SourceEvidenceError(f"{label} has missing or unsupported fields")
        digest = item["source_sha256"]
        if (item["source_video_id"] != manifest["video_id"] or not isinstance(digest, str)
                or not re.fullmatch(r"[a-fA-F0-9]{64}", digest)
                or digest.lower() != manifest["source"]["sha256"].lower()):
            raise SourceEvidenceError(f"{label} belongs to a different source video or SHA-256")
        status = item.get("verification_status", "unverified")
        if not isinstance(status, str) or status not in {"unverified", "source_material_only"}:
            raise SourceEvidenceError(f"{label}.verification_status cannot assert independent verification")
        text(item["description"], label + ".description")
        if "support_scope" in item:
            text(item["support_scope"], label + ".support_scope")
        shot_ids = item["source_shot_ids"]
        if (not isinstance(shot_ids, list) or not shot_ids
                or not all(isinstance(sid, str) and sid for sid in shot_ids)
                or len(set(shot_ids)) != len(shot_ids)):
            raise SourceEvidenceError(f"{label}.source_shot_ids must be unique nonempty shot IDs")
        start, end = item["start_ms"], item["end_ms"]
        if (type(start) is not int or type(end) is not int
                or not segment["start_ms"] <= start < end <= segment["end_ms"]):
            raise SourceEvidenceError(f"{label} interval must be inside the script segment")
        spans = {span["shot_id"]: span for span in segment["source_shot_spans"]}
        intervals = []
        for sid in shot_ids:
            if sid not in spans or sid not in timeline_by_id:
                raise SourceEvidenceError(f"{label} references a shot outside this source segment")
            span, shot = spans[sid], timeline_by_id[sid]
            left = max(start, segment["start_ms"], shot["start_ms"], span["segment_intersection_start_ms"])
            right = min(end, segment["end_ms"], shot["end_ms"], span["segment_intersection_end_ms"])
            if left >= right:
                raise SourceEvidenceError(f"{label} references a shot outside the material interval")
            intervals.append((left, right, sid))
        cursor = start
        ordered_ids = []
        for left, right, sid in sorted(intervals):
            if left != cursor:
                raise SourceEvidenceError(f"{label} shot references do not continuously cover the material interval")
            cursor = right
            ordered_ids.append(sid)
        if cursor != end:
            raise SourceEvidenceError(f"{label} shot references do not cover the full material interval")
        checked.append({**item, "source_sha256": digest.lower(), "source_shot_ids": ordered_ids})
    return checked


def format_source_evidence(segment, manifest, timeline_by_id):
    evidence = validate_source_evidence(segment, manifest, timeline_by_id)
    claims = segment.get("source_claims", [])
    lines = ["原作者主张及源片展示材料记录，均未独立核实。",
             "原作者主张（未独立核实）：" + (json_text(claims) if claims else "未提供原作者主张记录"),
             "源片展示材料（输入记录，内容待独立复核）："]
    for index, item in enumerate(evidence, 1):
        # Quoting keeps authored newlines inside their attributed record.
        lines.append(f"{index}. 展示观察记录：{json_text(item['description'])}")
        lines.append(f"来源：{manifest['video_id']}｜SHA-256：{item['source_sha256']}｜"
                     f"镜头：{'、'.join(item['source_shot_ids'])}｜{item['start_ms']}–{item['end_ms']} ms")
        if "support_scope" in item:
            lines.append("提交的支持范围说明（待独立复核）：" + json_text(item["support_scope"]))
    if not evidence:
        lines.append("未提供展示材料记录；不能由原作者主张推定支持材料存在，也不据此断言原片没有材料。")
    lines.extend([
        "支持范围：仅记录所引用的源片材料；材料出现不等于商品功效、因果关系或原作者结论已获验证。",
        "核实状态：未独立核实。程序仅检查来源引用与结构；材料内容、真实性及主张支持程度仍需独立复核。",
    ])
    return "\n".join(lines)
