# Reviewed Timeline: Understand → Number → Export → Verify → Upload

Use this workflow when the requested deliverable is a complete video breakdown with a timeline, numbered visual shots, tags, and matching video clips. The exporter enforces a reviewed manifest; it does not understand the video or approve its own content.

## 1. Resolve Source, Target, And Existing Access

- Enumerate the specified local sources or read the specified Base's video records. Preserve source files and record IDs. For every source, capture its SHA-256 and `ffprobe` duration.
- Parse the user's current Base URL, including app and table identifiers. Read the live table/field schema using the currently working CLI profile/identity. `assets/base_config.example.json` documents only the legacy 27-field contract; the `sync_to_feishu.py` helper requires an explicitly supplied `--config` and is not target discovery or a general timeline uploader.
- Reuse working credentials. If a read fails, report the exact current authorization error and follow the applicable Feishu access workflow; do not silently switch identity, replace global CLI configuration, or retry as a different account.
- Keep derived frames, evidence, manifests, exports, and writeback receipts under a separate job directory.

## 2. Understand The Whole Video Before Exporting Clips

Inspect the full duration chronologically. Extract overview frames and denser frames around visual changes. A cut detector may suggest timestamps, but it must not export the final clips at this stage. The old `detect_and_split.py` mixes detection and export, so it is not the default for this workflow.

Use source frames before and after each candidate boundary to distinguish a new visual shot from a subtitle-only change, animation, flash, or camera movement. A person or model reviewing the actual source evidence must resolve the candidates. A sparse contact sheet alone cannot prove every cut has been found; extract additional frames or inspect continuous source playback wherever the boundary remains unclear.

Separate two levels:

- A **content group** is a continuous argument or narrative section, such as opening question, explanation, product presentation, or closing instruction.
- A **numbered visual shot** is the smallest reviewed visual unit intended as an individual reusable clip. Several shots may belong to one content group. Use `画面 001`, `画面 002`, … consistently in the timeline, exported media, and Feishu records.

For each shot record the source start/end time, visible content and action, content-group purpose, readable subtitle excerpt, tag dimensions, and source evidence. Describe observed facts separately from the inferred persuasive role.

Read brands, labels, numbers, and technical terms from the actual screen/packaging or trustworthy source audio. Do not silently turn an ASR homophone into a guessed brand. When uncertain, record the uncertainty. `subtitle_excerpt` is only the readable excerpt from that interval; it does not become a complete script. If source audio was not reviewed, set an explicit audio-review status and do not claim audio-corrected or word-for-word transcription.

## 3. Write And Review The Manifest

The CLI accepts UTF-8 JSON. Relative source paths resolve from the manifest's directory. Required structure:

```json
{
  "source": {
    "path": "original.mp4",
    "sha256": "<the source file's actual 64-character SHA-256>",
    "feishu_record_id": "<existing original-video record, when applicable>"
  },
  "video_id": "source-id",
  "revision": "v1",
  "review_status": "complete",
  "coverage": "full",
  "duration_sec": 2.0,
  "audio_review_status": "not_reviewed",
  "semantic_groups": [
    {"id": "g01", "name": "Opening question"}
  ],
  "segments": [
    {
      "id": "shot-001",
      "start_ms": 0,
      "end_ms": 2000,
      "description": "A presenter faces the camera; a question is displayed.",
      "group_id": "g01",
      "group_name": "Opening question",
      "subtitle_excerpt": "<only the text actually readable in this interval>",
      "tags": ["人物口播", "问题引入"],
      "tag_dimensions": {"画面类型": ["人物口播"], "内容作用": ["问题引入"]},
      "reviewed": true,
      "evidence": ["frames/shot-001-start.jpg", "frames/shot-001-end.jpg"]
    }
  ]
}
```

The example uses a two-second fixture; never copy its duration or review approval into a real video. `review_status: complete` and each `reviewed: true` may be set only after the actual review. Evidence references remain provenance for human/model inspection; the exporter does not authenticate their contents.

The validation gate rejects changed source hashes, duplicate shot IDs, unreviewed segments, empty descriptions/tags/evidence, invalid or noninteger millisecond boundaries, overlaps, and out-of-order intervals. `coverage: full` must span exactly `0..round(duration_sec*1000)` without gaps. `sample` permits gaps and must be described as a partial sample throughout reporting. The source's probed duration must agree within the documented frame tolerance.

Top-level, source, and per-shot extension fields are retained in `timeline.json`, including semantic groups, subtitles, and tag dimensions. CSV contains the core flat columns; use JSON for complete structured metadata.

## 4. Dry-Run, Then Export Only The Approved Intervals

Resolve the installed skill path before executing:

```text
python <skill-dir>/scripts/export_reviewed_timeline.py --manifest <reviewed.json> --output <new-export-dir>
python <skill-dir>/scripts/export_reviewed_timeline.py --manifest <reviewed.json> --output <new-export-dir> --render
```

The first command validates and prints the plan without creating outputs. The second exports H.264 video and AAC audio when the source has audio. Output names use a stable ASCII source/revision prefix and sequence number. A nonempty output directory is rejected rather than overwritten; use a new revision/output directory for a revised timeline.

Outputs:

- `clips/`: one precisely re-encoded MP4 per reviewed interval.
- `thumbnails/`: one midpoint image per clip, suitable as a browsing cover.
- `timeline.json`: preserved semantic metadata plus per-clip verification and hashes.
- `timeline.csv`: numbered shots, source timecodes, tags, evidence, and media paths.
- `export_status.json`: partial/failed/complete state and completed clip count.

Each clip must have the expected streams, container and stream durations within `max(0.15 seconds, two source frames)`, and a successful full decode. Frame timing is quantized by the source frame rate; millisecond labels do not imply arbitrary subframe imagery. The source hash is checked again at the end. Failed or interrupted output remains available for diagnosis and is not marked complete.

After technical checks, inspect representative exported starts/ends and all ambiguous semantic boundaries against the approved source evidence. A successful encoder does not prove semantic grouping or transcript correctness. Do not upload a segment as verified until its relevant checks have passed.

## 5. Organize Feishu For Reuse

Inspect the existing table before changing it. A practical design is to retain the original-video table and add one shot-material table linked to it; a separate content-group table is optional when the volume requires one.

Recommended original-video fields: title, source URL, source-video attachment, source ID/hash, duration, transcript, transcript-review status, breakdown revision, breakdown status, content overview, and timeline/report attachment.

Recommended shot fields: stable shot key, link to original record, revision, shot number, content-group ID/name, start/end milliseconds, readable timecodes, duration, observed description, subtitle excerpt, clip attachment, thumbnail attachment, tag dimensions, evidence, boundary-review status, and upload/readback status. Use a stable key such as `video_id + revision + segment.id` to resume without duplicating records.

Keep tags in a few useful independent dimensions: visual/material type, visible subject, action, narrative function, and scene/context. Product or brand tags require evidence. Define only values supported by the inspected material; avoid speculative demographic/effectiveness tags or a growing freeform pile. When schema/tag optimization is authorized, create the explicit intended fields/options and then reread the live schema before records are written.

Use a sorted grid for the exact timeline and a gallery grouped/filtered by tags for material selection. Millisecond number fields are the source of timing truth. Do not represent relative video time as arbitrary calendar dates merely to force a project-management timeline view.

### Product And Platform Routing

- Treat a product code and linked product record as identity; a business name is a label and must not merge products with different codes.
- Keep product assignment explicit on the source-video record. Zero linked products means unclassified, one is usable, and multiple links must stop for review. Do not infer the product from a clip title, packaging, or a matching name.
- Store the source URL and known platform on the source-video record. Prefer the canonical source URL field and use a legacy platform-specific URL only as a fallback. If the platform is missing, keep it pending; if multiple platform values appear, stop rather than choose one.
- Link every material row to the exact source-video record. Material-side product/platform projections may be formulas; treat formula and lookup fields as read-only and verify their computed values after reading them back.
- For multi-platform navigation, route from a product catalog entry to that product's platform directory, then to a gallery filtered by both product code and platform. Use an explicit Markdown link label in URL-style text fields, and only link to a gallery that exists and has been read back. API configuration and URL resolution confirm coordinates, not that a person can click through the interface.
- Keep stable product/platform navigation keys, but do not hard-code a particular Base, product code, tenant URL, or view ID into this workflow. Do not claim media ingestion or platform download support without testing that source and path.

## 6. Upload And Read Back

Use the available Feishu Base/Drive tools or an explicitly tested project synchronizer mapped to the current target. This skill's reviewed exporter is local only; it does not itself create tables or upload attachments. Never invoke the old fixed-profile uploader against a new schema.

Process sequentially: dry-run field mapping → minimal record write → upload clip/thumbnail with the proper Base attachment context → update the intended record → read that record back. Verify the stable key, original-video linkage, shot number, times, description, tags, and attachment names/counts. Match readback to the local verified manifest; an upload HTTP success alone is insufficient. Preserve uploaded tokens in a local receipt so retries reuse completed attachments and do not create duplicate records.

Conclude with actual completed/failed counts, direct table/record links, the breakdown coverage, transcript/audio-review limits, and any precise blockers. Only call the workflow complete once every intended segment has passed local QC and remote readback.

The legacy handwritten `edit-list.otio.json` is not a tested editing project. Do not promise OTIO/Premiere/DaVinci compatibility without real validation using the target parser/editor. This workflow delivers clips and timecoded metadata; a tested project export is a separate capability.
