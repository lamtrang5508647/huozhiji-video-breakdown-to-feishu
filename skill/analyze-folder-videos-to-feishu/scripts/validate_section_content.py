"""Reject incomplete section authoring packets; never certify source interpretation."""
import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def check(content, timeline, timeline_sha256):
    errors = []

    def require(ok, message):
        if not ok:
            errors.append(message)

    require(content.get("timeline_sha256") == timeline_sha256,
            "Section content must bind the exact current timeline SHA-256")
    sections = content.get("sections", [])
    require(isinstance(sections, list) and bool(sections), "Missing semantic sections")
    if not isinstance(sections, list):
        sections = []
    for field in ("visible_brand", "strategy", "audience_hypothesis", "replication_outline"):
        require(isinstance(content.get(field), str) and bool(content[field].strip()),
                f"{field}: expected readable text, not an array/object")
    shots = {s["id"]: s for s in timeline["segments"]}
    previous = 0
    normalized_arcs = []
    for ordinal, section in enumerate(sections, 1):
        label = f"section {ordinal}"
        for field in ("name", "role", "structure", "question", "audience", "formula",
                      "slots", "conditions", "shoot"):
            require(isinstance(section.get(field), str) and bool(section[field].strip()),
                    f"{label} {field}: expected specific readable text")
        start, end = section.get("start_ms"), section.get("end_ms")
        valid_range = (isinstance(start, int) and isinstance(end, int)
                       and 0 <= start < end)
        require(valid_range, f"{label}: invalid interval")
        if not valid_range:
            normalized_arcs.append([])
            continue
        require(start == previous, f"{label}: gap/overlap in semantic coverage")
        previous = end
        expected_shots = [s["id"] for s in timeline["segments"]
                          if s["start_ms"] < end and s["end_ms"] > start]
        require(section.get("source_shot_ids") == expected_shots,
                f"{label}: source_shot_ids must equal current intersecting shots in order")
        arc = section.get("arc", [])
        require(isinstance(arc, list) and len(arc) == 4,
                f"{label}: arc needs exactly four stages; never truncate with zip")
        normalized = []
        last_time = -1
        for stage, event in enumerate(arc if isinstance(arc, list) else [], 1):
            event_label = f"{label} arc {stage}"
            if not isinstance(event, dict):
                require(False, f"{event_label}: expected time_ms, evidence_shot_id, expression")
                continue
            ms, sid = event.get("time_ms"), event.get("evidence_shot_id")
            require(isinstance(event.get("expression"), str) and bool(event["expression"].strip()),
                    f"{event_label}: missing observable source expression")
            valid_time = isinstance(ms, int) and start <= ms < end
            require(valid_time, f"{event_label}: evidence time outside semantic section")
            if isinstance(ms, int):
                require(ms >= last_time, f"{event_label}: events out of order")
                last_time = ms
            shot = shots.get(sid)
            require(bool(shot) and valid_time and shot["start_ms"] <= ms < shot["end_ms"],
                    f"{event_label}: evidence shot/time mismatch after timeline revision")
            if valid_time and isinstance(event.get("expression"), str):
                normalized.append([ms / 1000, event["expression"]])
        normalized_arcs.append(normalized)
    require(previous == round(timeline["duration_sec"] * 1000),
            "Semantic sections must cover the source duration")
    # Repeated useful conditions are allowed. Entirely identical shooting plans,
    # formulas or fill-in sentences across different sections require revision.
    for field in ("shoot", "slots", "formula"):
        counts = Counter(s.get(field).strip() for s in sections
                         if isinstance(s.get(field), str))
        require(not any(value and count > 1 for value, count in counts.items()),
                f"Repeated {field}: write section-specific actions/formulas/sentence slots")
    boundary = content.get("boundary_review", {})
    require(not content.get("machine_word_alignment_verified") or
            (isinstance(boundary, dict) and boundary.get("method") == "local_ASR_word_timings"
             and bool(boundary.get("alignment_receipt"))),
            "Machine word alignment needs explicit method and a source-bound receipt")
    return {"status": "FAIL" if errors else "PASS", "errors": errors,
            "sections": len(sections), "normalized_arcs": normalized_arcs,
            "publication_ready": False, "content_review_required": True,
            "scope": "Authoring schema, source references and repeated boilerplate only; "
                     "source meaning, imagery, sound and effectiveness still require review"}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--content", required=True, type=Path)
    p.add_argument("--timeline", required=True, type=Path)
    p.add_argument("--output", type=Path)
    a = p.parse_args()
    result = check(read(a.content), read(a.timeline), hashlib.sha256(a.timeline.read_bytes()).hexdigest())
    if a.output:
        a.output.parent.mkdir(parents=True, exist_ok=True)
        a.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
