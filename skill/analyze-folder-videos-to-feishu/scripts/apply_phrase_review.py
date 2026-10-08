"""Apply explicit, source-bound phrase annotations to a frozen analysis JSON."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

try:
    from phrase_annotation import canonical_taxonomy, validate_annotations
except ImportError as exc:  # Keep failure explicit when the coordinated canonical module is absent.
    raise ImportError(
        "phrase_annotation.py is required beside apply_phrase_review.py "
        "(canonical_taxonomy and validate_annotations)"
    ) from exc


class ReviewError(ValueError):
    pass


PROFILE = "phrase-functions/v9"
TAXONOMY_IDS = {
    "hook_pain", "scene_audience", "product_info", "experience_claim",
    "evidence_trust", "offer_reason", "action_cta", "unclassified",
}


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _taxonomy_rows() -> list[dict]:
    raw = canonical_taxonomy()
    if isinstance(raw, dict):
        raw = raw.get("taxonomy", raw.get("families", raw))
    if isinstance(raw, dict):
        rows = []
        for family_id, value in raw.items():
            if isinstance(value, dict):
                rows.append({"family_id": family_id, **value})
            elif isinstance(value, (tuple, list)) and len(value) >= 2:
                rows.append({"family_id": family_id, "label": value[0], "color": value[1]})
            else:
                raise ReviewError(f"unsupported canonical taxonomy entry: {family_id}")
    elif isinstance(raw, list):
        rows = raw
    else:
        raise ReviewError("canonical_taxonomy() must return a taxonomy list or mapping")
    by_id = {row.get("family_id"): row for row in rows}
    if len(by_id) != len(rows) or set(by_id) != TAXONOMY_IDS:
        raise ReviewError("canonical taxonomy must contain exactly the eight v9 families")
    for row in rows:
        if not row.get("label") or not row.get("color"):
            raise ReviewError(f"canonical taxonomy entry lacks label/color: {row.get('family_id')}")
    return rows


def _call_canonical_validator(candidate: dict) -> None:
    """Run the coordinated canonical validator on the completed analysis."""
    result = validate_annotations(candidate)
    if result is False:
        raise ReviewError("canonical validate_annotations rejected the review")


def _review_payload(review_root: dict, video_id: str) -> dict:
    if "unit_annotations" in review_root:
        payload = review_root
    else:
        payload = review_root.get(video_id)
    if not isinstance(payload, dict):
        raise ReviewError(f"review has no video object for {video_id}")
    if payload.get("analysis_sha256") is None:
        raise ReviewError("review is missing analysis_sha256")
    return payload


def _check_spans(source_text: str, runs: list[dict], taxonomy_ids: set[str], identity: str) -> None:
    if not isinstance(source_text, str) or not source_text:
        raise ReviewError(f"empty source_text: {identity}")
    if not isinstance(runs, list) or not runs:
        raise ReviewError(f"phrase_runs missing: {identity}")
    cursor = 0
    for index, run in enumerate(runs):
        if not isinstance(run, dict):
            raise ReviewError(f"phrase run must be an object: {identity}/{index}")
        text = run.get("text")
        start, end = run.get("char_start"), run.get("char_end")
        if not isinstance(text, str) or not text:
            raise ReviewError(f"empty phrase text: {identity}/{index}")
        if type(start) is not int or type(end) is not int or start != cursor or end != start + len(text):
            raise ReviewError(f"gap/overlap/invalid character offsets: {identity}/{index}")
        if source_text[start:end] != text:
            raise ReviewError(f"phrase span differs from exact source text: {identity}/{index}")
        if run.get("category_family") not in taxonomy_ids:
            raise ReviewError(f"unknown category_family: {identity}/{index}")
        if not isinstance(run.get("function_tag"), str) or not run["function_tag"].strip():
            raise ReviewError(f"function_tag missing: {identity}/{index}")
        if not isinstance(run.get("reason"), str) or not run["reason"].strip():
            raise ReviewError(f"reason missing: {identity}/{index}")
        cursor = end
    if cursor != len(source_text) or "".join(run["text"] for run in runs) != source_text:
        raise ReviewError(f"phrase runs do not exactly reconstruct source: {identity}")


def _apply_formula_group(
    original_steps: list[dict], reviewed_steps: list[dict],
    sections: dict[str, dict], units_by_section: dict[str, set[str]],
    taxonomy_ids: set[str], group_name: str,
) -> list[dict]:
    if not isinstance(reviewed_steps, list) or not reviewed_steps:
        raise ReviewError(f"review must provide {group_name} with explicit phrase annotations")
    if len(reviewed_steps) != len(original_steps):
        raise ReviewError(f"{group_name} step count differs from original ({len(reviewed_steps)} vs {len(original_steps)})")
    reviewed_by_section: set[str] = set()
    result = []
    for index, (original, step) in enumerate(zip(original_steps, reviewed_steps)):
        if not isinstance(step, dict):
            raise ReviewError(f"{group_name}[{index}] must be an object")
        section_id = step.get("section_id")
        if section_id not in sections:
            raise ReviewError(f"{group_name}[{index}] has unknown section_id: {section_id}")
        reviewed_by_section.add(section_id)
        label = step.get("label")
        if original.get("section_id") is not None and section_id != original["section_id"]:
            raise ReviewError(f"{group_name}[{index}] section_id/order differs from original")
        if not isinstance(label, str) or not label:
            raise ReviewError(f"{group_name} label missing in {section_id}")
        if label != original.get("label"):
            raise ReviewError(f"{group_name} label differs from original formula: {section_id}")
        if step.get("category_family") is not None and step["category_family"] not in taxonomy_ids:
            raise ReviewError(f"{group_name} has invalid category_family in {section_id}")
        unit_ids = step.get("source_unit_ids")
        if not isinstance(unit_ids, list) or not unit_ids or len(unit_ids) != len(set(unit_ids)):
            raise ReviewError(f"{group_name} needs unique, non-empty source_unit_ids in {section_id}")
        if any(unit_id not in units_by_section.get(section_id, set()) for unit_id in unit_ids):
            raise ReviewError(f"{group_name} source_unit_ids cross section boundary in {section_id}")
        _check_spans(label, step.get("phrase_runs"), taxonomy_ids, f"{group_name}/{section_id}")
        merged = copy.deepcopy(original)
        merged.update(copy.deepcopy(step))
        # Navigation only: v9 colors always come from explicitly reviewed runs.
        merged['category_family'] = step.get('category_family') or step['phrase_runs'][0]['category_family']
        result.append(merged)
    missing = sorted(set(sections) - reviewed_by_section)
    if missing:
        extra = sorted({step.get("section_id") for step in reviewed_steps} - set(sections))
        raise ReviewError(f"{group_name} section coverage mismatch; missing={missing}; extra={extra}")
    return result


def _apply_section_formula_runs(analysis_sections: dict[str, dict], reviewed: Any,
                                taxonomy_ids: set[str]) -> None:
    required = {section_id for section_id, section in analysis_sections.items() if section.get("formula")}
    if not isinstance(reviewed, list):
        if required:
            raise ReviewError("review must provide explicit sections_formula_runs")
        reviewed = []
    seen = set()
    for item in reviewed:
        section_id = item.get("section_id")
        if section_id not in analysis_sections or section_id in seen:
            raise ReviewError(f"sections_formula_runs has unknown or duplicate section_id: {section_id}")
        section = analysis_sections[section_id]
        formula = section.get("formula")
        if not formula or item.get("formula") != formula:
            raise ReviewError(f"sections_formula_runs formula differs from original section formula: {section_id}")
        _check_spans(formula, item.get("formula_runs"), taxonomy_ids, f"sections_formula_runs/{section_id}")
        section["formula_runs"] = copy.deepcopy(item["formula_runs"])
        seen.add(section_id)
    if seen != required:
        raise ReviewError(f"sections_formula_runs coverage mismatch; missing={sorted(required-seen)}; extra={sorted(seen-required)}")


def apply_review(analysis_path: Path, review_path: Path) -> dict:
    analysis_path = Path(analysis_path).resolve()
    review_path = Path(review_path).resolve()
    analysis_bytes = analysis_path.read_bytes()
    actual_analysis_sha = hashlib.sha256(analysis_bytes).hexdigest()
    analysis = json.loads(analysis_bytes.decode("utf-8-sig"))
    review_bytes = review_path.read_bytes()
    review_sha = hashlib.sha256(review_bytes).hexdigest()
    review_root = json.loads(review_bytes.decode("utf-8-sig"))
    payload = _review_payload(review_root, analysis.get("video_id", ""))
    if payload["analysis_sha256"] != actual_analysis_sha:
        raise ReviewError(
            "analysis_sha256 mismatch: review is bound to a different source analysis "
            f"(expected {payload['analysis_sha256']}, actual {actual_analysis_sha})"
        )

    taxonomy = _taxonomy_rows()
    taxonomy_ids = {row["family_id"] for row in taxonomy}
    source_units = analysis.get("units")
    reviewed_units = payload.get("unit_annotations")
    if not isinstance(source_units, list) or not isinstance(reviewed_units, list):
        raise ReviewError("analysis.units and review.unit_annotations must be arrays")
    source_by_id = {}
    for unit in source_units:
        unit_id = unit.get("id")
        if not unit_id or unit_id in source_by_id:
            raise ReviewError(f"source analysis has missing or duplicate unit id: {unit_id}")
        source_by_id[unit_id] = unit
    reviewed_by_id = {}
    for item in reviewed_units:
        unit_id = item.get("id")
        if not unit_id or unit_id in reviewed_by_id:
            raise ReviewError(f"review has missing or duplicate unit id: {unit_id}")
        reviewed_by_id[unit_id] = item
    missing = sorted(set(source_by_id) - set(reviewed_by_id))
    extra = sorted(set(reviewed_by_id) - set(source_by_id))
    if missing or extra:
        raise ReviewError(f"unit coverage mismatch; missing={missing}; extra={extra}")

    candidate = copy.deepcopy(analysis)
    for unit_id, source_unit in source_by_id.items():
        item = reviewed_by_id[unit_id]
        if item.get("source_text") != source_unit.get("source_text"):
            raise ReviewError(f"source_text mismatch for exact unit id {unit_id}")
        _check_spans(source_unit["source_text"], item.get("phrase_runs"), taxonomy_ids, unit_id)
        # Preserve every original unit/source field; only its phrase_runs are replaced.
        candidate_unit = next(unit for unit in candidate["units"] if unit["id"] == unit_id)
        candidate_unit["phrase_runs"] = copy.deepcopy(item["phrase_runs"])
        if "reading_span" in source_unit:
            scope = item.get("reviewed_scope")
            if not isinstance(scope, dict):
                raise ReviewError(f"explicit reviewed_scope required for selected reading span: {unit_id}")
            span = source_unit["reading_span"]
            start, end = span.get("char_start"), span.get("char_end")
            expected_kept = source_unit["source_text"][start:end]
            source_hash = hashlib.sha256(source_unit["source_text"].encode("utf-8")).hexdigest()
            if (scope.get("approved") is not True
                    or scope.get("source_text_sha256") != source_hash
                    or scope.get("kept_text") != expected_kept
                    or not isinstance(scope.get("reason"), str)
                    or not scope["reason"].strip()):
                raise ReviewError(f"reviewed_scope is stale or incomplete for exact reading_span: {unit_id}")
            # A reviewer may update only this explicit scope receipt; source text/span remain frozen.
            candidate_unit["reviewed_scope"] = copy.deepcopy(scope)
        elif "reviewed_scope" in item:
            candidate_unit["reviewed_scope"] = copy.deepcopy(item["reviewed_scope"])

    sections = {section["id"]: section for section in candidate.get("sections", [])}
    if len(sections) != len(candidate.get("sections", [])):
        raise ReviewError("analysis has duplicate section id")
    units_by_section: dict[str, set[str]] = {section_id: set() for section_id in sections}
    for unit in candidate["units"]:
        if unit.get("section_id") not in sections:
            raise ReviewError(f"unit references unknown section: {unit.get('id')}")
        units_by_section[unit["section_id"]].add(unit["id"])

    # Missing formula reviews are intentionally a hard block. Do not synthesize them.
    absent_formula_fields = [
        name for name in ("source_formula", "reusable_formula")
        if not isinstance(payload.get(name), list) or not payload[name]
    ]
    if any(section.get("formula") for section in sections.values()) and (
        not isinstance(payload.get("sections_formula_runs"), list)
        or not payload["sections_formula_runs"]
    ):
        absent_formula_fields.append("sections_formula_runs")
    if absent_formula_fields:
        raise ReviewError(
            "v9 generation blocked: review package must add explicit "
            + " and ".join(absent_formula_fields)
            + " annotations (label, category_family, section_id, source_unit_ids, phrase_runs); "
            "sections_formula_runs must bind each original section formula with exact formula_runs"
        )
    candidate["source_formula"] = _apply_formula_group(
        candidate.get("source_formula", []), payload["source_formula"],
        sections, units_by_section, taxonomy_ids, "source_formula",
    )
    candidate["reusable_formula"] = _apply_formula_group(
        candidate.get("reusable_formula", []), payload["reusable_formula"],
        sections, units_by_section, taxonomy_ids, "reusable_formula",
    )
    _apply_section_formula_runs(sections, payload["sections_formula_runs"], taxonomy_ids)

    candidate["taxonomy"] = taxonomy
    candidate["profile"] = PROFILE
    candidate["annotation_profile"] = PROFILE
    candidate["phrase_review_metadata"] = {
        "profile": PROFILE,
        "analysis_sha256": actual_analysis_sha,
        "review_sha256": review_sha,
        "review_unit_count": len(reviewed_units),
        "reviewed_unit_ids": sorted(reviewed_by_id),
        "review_formula_step_counts": {
            "source_formula": len(payload["source_formula"]),
            "reusable_formula": len(payload["reusable_formula"]),
            "sections_formula_runs": len(payload["sections_formula_runs"]),
        },
        "semantic_status": "not_asserted",
    }
    _call_canonical_validator(candidate)
    return candidate


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--analysis", required=True, type=Path)
    parser.add_argument("--review", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        candidate = apply_review(args.analysis, args.review)
        output = args.output.resolve()
        source = args.analysis.resolve()
        review_source = args.review.resolve()
        if output in (source, review_source):
            raise ReviewError("output must not overwrite source analysis or review")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(candidate, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except Exception as exc:
        print(f"phrase review blocked: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"profile": PROFILE, "video_id": candidate["video_id"], "units": len(candidate["units"]),
                      "output": str(output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
