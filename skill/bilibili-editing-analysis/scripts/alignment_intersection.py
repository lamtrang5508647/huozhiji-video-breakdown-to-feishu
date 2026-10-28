"""Source-preserving script-segment/shot intersections, without inferred speech timing.

This module checks the supplied metadata only. It does not inspect source media,
authenticate review decisions, or establish that text matches a source timeline.
"""
from __future__ import annotations

import copy
import csv
import html
import json
import re
from pathlib import Path


SCHEMA_VERSION = "script-shot-alignment/1"
SCOPE = ("Input source structure and declared intersections only; no media, subtitle "
         "accuracy, sentence audio alignment, or human listening verification.")
EMPTY_SUBTITLE = "未提供字幕摘录；不代表无口播"
MISSING_GUIDANCE = "未提供对应镜头组指导"


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _text(value, label):
    _require(isinstance(value, str) and bool(value.strip()), f"{label} must be nonempty text")
    return value


def _range(start, end, label):
    _require(type(start) is int and type(end) is int and 0 <= start < end,
             f"{label} must be a nonnegative integer start and a greater integer end")


def _ids(value, label):
    _require(isinstance(value, list) and all(isinstance(v, str) and v for v in value),
             f"{label} must be a list of shot IDs")
    _require(len(set(value)) == len(value), f"{label} repeats a shot ID")
    return value


def _shot_label(shot_id):
    match = re.fullmatch(r"(?:shot-|画面)(\d+)", shot_id)
    return f"画面{int(match.group(1)):03d}" if match else shot_id


def segment_alignment(segment) -> list[dict]:
    """One row per actual shot intersection; preserve all original excerpt items."""
    _require(isinstance(segment, dict), "segment must be an object")
    segment_id = _text(segment.get("id"), "segment.id")
    segment_name = _text(segment.get("name"), "segment.name")
    function = _text(segment.get("script_function"), "segment.script_function")
    start, end = segment.get("start_ms"), segment.get("end_ms")
    _range(start, end, "segment interval")
    spans = segment.get("source_shot_spans")
    _require(isinstance(spans, list) and bool(spans), "segment requires source_shot_spans")
    by_id = {}
    cursor = start
    for span in spans:
        _require(isinstance(span, dict), "source_shot_span must be an object")
        shot_id = _text(span.get("shot_id"), "span.shot_id")
        _require(shot_id not in by_id, "source_shot_spans repeats a shot ID")
        shot_start, shot_end = span.get("source_shot_start_ms"), span.get("source_shot_end_ms")
        _range(shot_start, shot_end, "source shot interval")
        left, right = span.get("segment_intersection_start_ms"), span.get("segment_intersection_end_ms")
        _range(left, right, "shot-segment intersection")
        _require(left == max(start, shot_start) and right == min(end, shot_end),
                 "shot-segment intersection must equal the exact source intersection")
        _require(left == cursor, "source_shot_spans are unordered, overlapping, or leave a gap")
        cursor = right
        by_id[shot_id] = span
    _require(cursor == end, "source_shot_spans do not cover the entire segment")
    if "source_shot_ids" in segment:
        _require(_ids(segment["source_shot_ids"], "segment.source_shot_ids") == list(by_id),
                 "segment.source_shot_ids differs from ordered source_shot_spans")

    excerpt = segment.get("source_excerpt", {})
    _require(isinstance(excerpt, dict), "source_excerpt must be an object")
    _require(excerpt.get("type") in (None, "subtitle_excerpts_only"),
             "source_excerpt must contain subtitle excerpts, not an inferred transcript")
    items = excerpt.get("items", [])
    _require(isinstance(items, list), "source_excerpt.items must be a list")
    grouped = {shot_id: [] for shot_id in by_id}
    last_order = -1
    positions = {sid: i for i, sid in enumerate(by_id)}
    for item_index, item in enumerate(items):
        _require(isinstance(item, dict), "source_excerpt item must be an object")
        shot_id = item.get("shot_id")
        _require(isinstance(shot_id, str) and shot_id in by_id,
                 "source_excerpt references a foreign or non-intersecting shot")
        _require(positions[shot_id] >= last_order, "source_excerpt items are out of source shot order")
        last_order = positions[shot_id]
        _require(isinstance(item.get("text"), str), "source_excerpt.text must be a string")
        span = by_id[shot_id]
        for key, span_key in (("intersection_start_ms", "segment_intersection_start_ms"),
                              ("intersection_end_ms", "segment_intersection_end_ms")):
            if key in item:
                _require(type(item[key]) is int and item[key] == span[span_key],
                         f"source_excerpt.{key} differs from the source shot intersection")
        if "context_only" in item:
            _require(type(item["context_only"]) is bool, "excerpt.context_only must be boolean")
        grouped[shot_id].append({**copy.deepcopy(item), "item_index": item_index})

    boundary = segment.get("boundary_context", [])
    _require(isinstance(boundary, list), "boundary_context must be a list")
    context_ids = set()
    for context in boundary:
        _require(isinstance(context, dict), "boundary_context entry must be an object")
        context_ids.update(_ids(context.get("source_shot_ids", []), "boundary_context.source_shot_ids"))
        context_spans = context.get("source_shot_spans", [])
        _require(isinstance(context_spans, list), "boundary_context.source_shot_spans must be a list")
        for span in context_spans:
            _require(isinstance(span, dict), "boundary context span must be an object")
            context_ids.add(_text(span.get("shot_id"), "boundary_context.shot_id"))
    _require(context_ids <= set(by_id), "boundary_context references a non-intersecting shot")

    formula = segment.get("reusable_formula", {})
    _require(isinstance(formula, dict), "reusable_formula must be an object")
    groups = segment.get("shot_groups", [])
    _require(isinstance(groups, list), "shot_groups must be a list")
    for group in groups:
        _require(isinstance(group, dict), "shot_group must be an object")
        _ids(group.get("source_shot_ids", []), "shot_group.source_shot_ids")

    rows = []
    for shot_id, span in by_id.items():
        left, right = span["segment_intersection_start_ms"], span["segment_intersection_end_ms"]
        excerpts = grouped[shot_id]
        guidance = [copy.deepcopy(g) for g in groups if shot_id in g.get("source_shot_ids", [])]
        source_text = "\n".join(item["text"] for item in excerpts)
        rows.append({
            "segment_id": segment_id, "segment_name": segment_name, "script_function": function,
            "shot_id": shot_id, "shot_label": _shot_label(shot_id),
            "source_start_ms": left, "source_end_ms": right,
            "segment_offset_start_ms": left - start, "segment_offset_end_ms": right - start,
            "shot_offset_start_ms": left - span["source_shot_start_ms"],
            "shot_offset_end_ms": right - span["source_shot_start_ms"],
            "source_shot_start_ms": span["source_shot_start_ms"], "source_shot_end_ms": span["source_shot_end_ms"],
            "excerpt_items": excerpts, "source_text": source_text,
            "source_text_status": "subtitle_excerpts_only" if source_text else "no_subtitle_provided",
            "context_only": shot_id in context_ids or bool(excerpts) and all(i.get("context_only") is True for i in excerpts),
            "timing_granularity": "shot_segment_intersection", "sentence_audio_alignment": "not_verified",
            "formula_scope": "segment", "reusable_formula": copy.deepcopy(formula),
            "shot_guidance_scope": "shot_group", "shot_guidance": guidance,
            "shot_guidance_status": "provided" if guidance else "not_provided",
        })
    return rows


def build_alignment(manifest) -> dict:
    """Build source-bound metadata; SHA fields are carried, not independently checked."""
    _require(isinstance(manifest, dict), "manifest must be an object")
    video_id = _text(manifest.get("video_id"), "video_id")
    source = manifest.get("source")
    _require(isinstance(source, dict), "manifest.source must be an object")
    for field in ("sha256", "timeline_manifest_sha256"):
        _require(isinstance(source.get(field), str) and re.fullmatch(r"[a-fA-F0-9]{64}", source[field]),
                 f"source.{field} must be a SHA-256")
    segments = manifest.get("segments")
    _require(isinstance(segments, list) and bool(segments), "manifest.segments must be nonempty")
    rows, seen, shot_ranges = [], set(), {}
    previous_end = None
    for segment in segments:
        current = segment_alignment(segment)
        _require(segment["id"] not in seen, "Duplicate segment ID")
        seen.add(segment["id"])
        if previous_end is not None:
            _require(segment["start_ms"] == previous_end, "Script segments are unordered, overlapping, or have gaps")
        previous_end = segment["end_ms"]
        for row in current:
            interval = row["source_shot_start_ms"], row["source_shot_end_ms"]
            _require(row["shot_id"] not in shot_ranges or shot_ranges[row["shot_id"]] == interval,
                     "A repeated source shot has inconsistent original times")
            shot_ranges[row["shot_id"]] = interval
        rows.extend(current)
    return {
        "schema_version": SCHEMA_VERSION, "video_id": video_id,
        "source_sha256": source["sha256"], "timeline_sha256": source["timeline_manifest_sha256"],
        "revision": _text(manifest["revision"], "revision") if "revision" in manifest else "not_provided",
        "transcript_status": manifest.get("transcript_status", "not_provided"),
        "audio_review_status": manifest.get("audio_review_status", "not_provided"),
        "sentence_audio_alignment": "not_verified", "validation_scope": SCOPE,
        "rows_count": len(rows), "rows": rows,
    }


def _time(ms):
    return f"{ms // 60000:02d}:{ms // 1000 % 60:02d}.{ms % 1000:03d}"


def _escaped(value):
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False)
    return html.escape(value, quote=True).replace("\n", "<br>")


def _formula_html(formula):
    labels = (("structure", "本段公式"), ("example_sentence_slots", "本段句式"),
              ("slots", "变量槽位"), ("conditions", "复用条件"))
    parts = [f"<div><b>{label}：</b>{_escaped(formula[key])}</div>"
             for key, label in labels if key in formula]
    return "".join(parts) or "未提供本段仿写参考"


def _guidance_html(groups):
    if not groups:
        return MISSING_GUIDANCE
    parts = []
    for group in groups:
        parts.append("<div><b>镜头组：</b>" + _escaped(group.get("source_group_id", "未提供编号"))
                     + "；适用画面：" + _escaped("、".join(group.get("source_shot_ids", []))) + "</div>")
        if "source_observation" in group:
            parts.append("<div><b>组级原片观察：</b>" + _escaped(group["source_observation"]) + "</div>")
        plan = group.get("replication_shooting_plan", {})
        if isinstance(plan, dict):
            for key, label in (("subject_action", "主体/动作"), ("shot_size_angle", "景别/机位"),
                               ("example_sentence_slots", "组级句式"), ("information_order", "信息顺序"),
                               ("edit_transition", "剪辑衔接")):
                if key in plan:
                    parts.append(f"<div><b>{label}：</b>{_escaped(plan[key])}</div>")
    return "".join(parts)


def render_alignment_table(rows, player_id=None) -> str:
    """HTML fragment; optional buttons use segment-local seconds, without scripts."""
    if player_id is not None:
        _text(player_id, "player_id")
    body = []
    for row in rows:
        timing = (f"源片 {_time(row['source_start_ms'])}–{_time(row['source_end_ms'])}<br>"
                  f"段内 {_time(row['segment_offset_start_ms'])}–{_time(row['segment_offset_end_ms'])}<br>"
                  f"镜头内 {_time(row['shot_offset_start_ms'])}–{_time(row['shot_offset_end_ms'])}")
        button = ""
        if player_id is not None:
            button = (f'<br><button type="button" class="alignment-play" data-player="{html.escape(player_id, quote=True)}" '
                      f'data-start="{row["segment_offset_start_ms"] / 1000:.3f}" '
                      f'data-end="{row["segment_offset_end_ms"] / 1000:.3f}">播放此交集</button>')
        context = "<div><b>边界上下文</b></div>" if row["context_only"] else ""
        subtitle = _escaped(row["source_text"]) if row["source_text"] else EMPTY_SUBTITLE
        body.append("<tr><td>" + _escaped(row["shot_label"]) + "<br>" + _escaped(row["segment_name"])
                    + "<br>" + _escaped(row["script_function"]) + context + "</td><td>" + timing + button
                    + "</td><td>" + subtitle + "</td><td>" + _formula_html(row["reusable_formula"])
                    + "</td><td>" + _guidance_html(row["shot_guidance"]) + "</td></tr>")
    return ('<div class="script-shot-alignment"><p>按脚本段与画面交集对齐；字幕摘录不代表完整口播，'
            '未验证逐句音频时间。仿写为段级参考，镜头建议为对应组级参考。</p>'
            '<table><thead><tr><th>画面 / 脚本段</th><th>时间定位</th><th>原字幕摘录</th>'
            '<th>段级仿写参考</th><th>镜头组处理建议</th></tr></thead><tbody>'
            + "".join(body) + '</tbody></table></div>')


def write_alignment_files(output: Path, manifest) -> dict:
    """Write local JSON and UTF-8-BOM CSV; preserve nested original objects in JSON columns."""
    result = build_alignment(manifest)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    json_path = output / "script-shot-alignment.json"
    csv_path = output / "script-shot-alignment.csv"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    identity_keys = ["video_id", "source_sha256", "timeline_sha256", "revision", "transcript_status", "audio_review_status"]
    row_fields = list(result["rows"][0])
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=identity_keys + row_fields)
        writer.writeheader()
        for row in result["rows"]:
            values = {key: result[key] for key in identity_keys}
            values.update({key: json.dumps(value, ensure_ascii=False, allow_nan=False)
                           if isinstance(value, (dict, list)) else value for key, value in row.items()})
            writer.writerow(values)
    return {"rows_count": result["rows_count"]}
