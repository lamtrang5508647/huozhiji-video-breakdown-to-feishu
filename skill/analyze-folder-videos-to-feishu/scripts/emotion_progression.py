"""Shared source-bound emotion event contract; never certifies interpretation."""
from __future__ import annotations

LEGACY_STAGES = ("起点", "触发", "转折", "落点")
STATUSES = {
    "observed_changes": "观察到变化",
    "no_clear_change": "未观察到明确变化",
    "insufficient_evidence": "证据不足／未明确（待复核）",
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def progression(emotion):
    """Validate shape/cardinality without inventing stages or review approval."""
    require(isinstance(emotion, dict), "Emotion must be an object")
    explicit = "progression_status" in emotion
    status = emotion.get("progression_status") if explicit else "legacy_four_stage"
    require(not explicit or isinstance(status, str) and status in STATUSES,
            "Unknown emotion progression_status")
    events = emotion.get("observable_timeline", [])
    require(isinstance(events, list), "Emotion observable_timeline must be a list")
    require(all(isinstance(event, dict) for event in events), "Emotion event must be an object")
    note = emotion.get("review_note", "")
    require(isinstance(note, str), "Emotion review_note must be text")
    if not explicit:
        require([event.get("stage") for event in events] == list(LEGACY_STAGES),
                "Legacy emotion arc needs exactly four stages: start/trigger/turn/landing")
    elif status == "observed_changes":
        require(len(events) >= 2, "observed_changes needs at least two evidence events")
    elif status == "no_clear_change":
        require(len(events) >= 1, "no_clear_change needs at least one evidence observation")
    else:
        require(bool(note.strip()), "insufficient_evidence requires an explicit review_note")
    for event in events:
        require(isinstance(event.get("observable"), str) and bool(event["observable"].strip()),
                "Emotion evidence missing: observable source expression required")
        refs = event.get("evidence_shot_ids")
        require(isinstance(refs, list) and bool(refs)
                and all(isinstance(ref, str) and bool(ref) for ref in refs),
                "Emotion evidence missing: source shot IDs required")
        require(len(refs) == len(set(refs)), "Emotion event repeats evidence shot IDs")
        require(type(event.get("evidence_time_ms")) is int,
                "Emotion evidence_time_ms must be integer milliseconds")
        if "stage" in event:
            require(isinstance(event["stage"], str) and bool(event["stage"].strip()),
                    "Emotion event stage, when supplied, must be readable text")
    return {"status": status, "explicit": explicit, "events": events, "review_note": note}


def validate_emotion(emotion, start, end, shot_ids, shot_ranges=None):
    result = progression(emotion)
    require(type(start) is int and type(end) is int and 0 <= start < end,
            "Emotion segment interval must use integer milliseconds")
    require(isinstance(shot_ids, list) and all(isinstance(s, str) for s in shot_ids),
            "Emotion source shot mapping missing")
    allowed = set(shot_ids)
    last_time = -1
    for event in result["events"]:
        ms, refs = event["evidence_time_ms"], event["evidence_shot_ids"]
        require(start <= ms < end, "Emotion evidence outside segment")
        require(ms >= last_time, "Emotion evidence events out of order")
        if result["status"] == "observed_changes":
            require(ms > last_time, "observed_changes events need distinct increasing times")
        last_time = ms
        require(set(refs).issubset(allowed), "Emotion event lacks in-segment shot evidence")
        if shot_ranges is not None:
            require(all(ref in shot_ranges for ref in refs), "Emotion evidence shot span missing")
            ranges = [shot_ranges[ref] for ref in refs]
            require(all(type(a) is int and type(b) is int and a < b
                        and a < end and b > start for a, b in ranges),
                    "Emotion evidence shot is outside segment")
            require(any(a <= ms < b for a, b in ranges), "Emotion evidence shot/time mismatch")
        else:
            require(not result["explicit"], "Explicit emotion progression needs source shot spans")
    return result


def manifest_emotion(segment, shot_ranges=None):
    if shot_ranges is None and "source_shot_spans" in segment:
        spans = segment["source_shot_spans"]
        require(isinstance(spans, list) and all(isinstance(s, dict) for s in spans),
                "Emotion source_shot_spans must be a list")
        shot_ranges = {span.get("shot_id"): (span.get("segment_intersection_start_ms"),
                                             span.get("segment_intersection_end_ms")) for span in spans}
    return validate_emotion(segment.get("emotion"), segment.get("start_ms"), segment.get("end_ms"),
                            segment.get("source_shot_ids"), shot_ranges)


def authoring_emotion(section):
    """Convert authoring names to the manifest contract without mutating inputs."""
    arc = section.get("arc", [])
    require(isinstance(arc, list), "Authoring emotion arc must be a list")
    events = []
    explicit = "emotion_progression_status" in section
    for index, item in enumerate(arc):
        require(isinstance(item, dict), "Authoring emotion event must be an object")
        refs = item.get("evidence_shot_ids")
        if refs is None:
            refs = [item.get("evidence_shot_id")]
        elif "evidence_shot_id" in item:
            require(refs == [item["evidence_shot_id"]], "Authoring emotion shot references disagree")
        event = {"observable": item.get("expression"), "evidence_time_ms": item.get("time_ms"),
                 "evidence_shot_ids": refs}
        if "stage" in item:
            event["stage"] = item["stage"]
        elif not explicit and index < len(LEGACY_STAGES):
            event["stage"] = LEGACY_STAGES[index]
        events.append(event)
    emotion = {"observable_timeline": events, "review_note": section.get("emotion_review_note", "")}
    if explicit:
        emotion["progression_status"] = section["emotion_progression_status"]
    return emotion


def validate_authoring(section, shot_ranges, shot_ids=None):
    return validate_emotion(authoring_emotion(section), section.get("start_ms"), section.get("end_ms"),
                            section.get("source_shot_ids") if shot_ids is None else shot_ids, shot_ranges)


def match_authoring(segment, section):
    observed = manifest_emotion(segment)
    # Existing presentation packets sometimes have no authoring arc at all.
    if not observed["explicit"] and "emotion_progression_status" not in section and "arc" not in section:
        return observed
    declared_status = section.get("emotion_progression_status", "legacy_four_stage")
    require(observed["status"] == declared_status, "Emotion progression status differs from authoring")
    authored = validate_emotion(authoring_emotion(section), section.get("start_ms"), section.get("end_ms"),
                               segment.get("source_shot_ids"),
                               {s["shot_id"]: (s["segment_intersection_start_ms"], s["segment_intersection_end_ms"])
                                for s in segment["source_shot_spans"]} if "source_shot_spans" in segment else None)
    require(observed["review_note"] == authored["review_note"], "Emotion review note differs from authoring")
    def evidence(result):
        return [(e["evidence_time_ms"], e["observable"], sorted(e["evidence_shot_ids"]), e.get("stage"))
                for e in result["events"]]
    require(evidence(observed) == evidence(authored), "Emotion evidence differs from authoring")
    return observed


def progression_label(emotion):
    value = progression(emotion)
    if not value["explicit"]:
        return ""
    label = f'{STATUSES[value["status"]]}；{len(value["events"])}条观察事件'
    if value["review_note"].strip():
        label += "；复核备注：" + value["review_note"]
    return label


def event_label(event, index):
    return event.get("stage") or f"观察{index + 1}"
