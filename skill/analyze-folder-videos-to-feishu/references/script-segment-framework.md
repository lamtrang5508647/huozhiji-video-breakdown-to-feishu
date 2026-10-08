# Whole-Video Framework And Script Segments

Use this mode when the user needs to understand a source video, choose a complete passage, or adapt its storytelling. Start from the reviewed source and build in this order:

**whole-video framework → complete semantic script segment → shot group → single numbered source shot.** A visual cut alone does not create a new script segment. Keep connected arguments, paired comparisons, actions, and evidence together until the spoken task is complete.

## Evidence And Boundaries

- Capture the source file SHA-256, exact duration, original source-video record (when supplied), and the existing reviewed shot manifest. Never modify the source or the old shot assets.
- Require `source.timeline_manifest_path` and its 64-hex `source.timeline_manifest_sha256`; verify that the timeline's `video_id` and source SHA match the script manifest and actual source file. A same-named shot such as `画面001` from another video is never interchangeable.
- Keep the full-video framework grounded in the source: audience positioning is a hypothesis, source claims are unverified unless separately supported, and source ordering/time is distinct from any proposed adaptation ordering or runtime.
- Keep source script text separate from analysis and rewritten lines. A readable subtitle excerpt is only an excerpt. Machine ASR or word timings can locate candidate sentence boundaries; they do not establish that the words, names, tone, or full transcript have been human-checked.
- If a segment edge crosses a numbered shot, retain every actual shot intersection as `source_shot_ids` and `source_shot_spans`. Record only the contextual overlap in `boundary_context`; do not assign the entire neighboring semantic group, source structure, or its shot-group advice to the segment.
- Validate that the segments are ordered, continuous, non-overlapping, cover the intended source range, and preserve source hash and duration. Do not shorten a sentence to force the old shot partition.

## Analysis Shape

The video-level record should state the source strategy, ordered argument/story framework, source transition logic, reusable structure, adaptation outline, required evidence, claim boundaries, and review status. Keep the source framework and proposed adaptation in separate fields.

Each script-segment record should include:

- stable ID, ordinal, name, source time range, primary semantic source groups, source-shot IDs and exact intersection spans;
- source excerpt with per-shot provenance and an explicit `subtitle_excerpts_only` type unless a transcript has separate human review evidence;
- the segment's source role, source structure, central question, and source claims;
- the observable source-emotion arc as **starting expression → trigger → turn → landing**, with the triggering shot or subtitle evidence at every stage;
- a separate audience-response hypothesis, clearly marked as an inference rather than measured response;
- observable emotion tags and one script-function value in separate taxonomies;
- reusable structure, fill-in slots, example sentence slots, required target-product facts, and conditions for reuse;
- ordered shot groups, each separating source observation from a new filming/edit suggestion. Make the suggested shot plan concrete: subject/action, framing, sentence slot, information order, and edit transition;
- model suitability split into neutral generation candidates, shots needing real references, and claims/effects that require real evidence footage.

Do not infer feelings from a meme sticker alone. If the source does not present a clear emotion, tag it `未明确`. Do not give exact emotion scores without a supported measurement method.

## Product And Claim Separation

Record the business product classification, visible source brand/package, and intended target product in separate fields. A source brand or target-product name cannot be transferred from packaging or a filename. Keep source statements about efficacy, personal use, percentages, credentials, testing, store identity, and purchase links as attributed claims until their evidence and current status are checked. Never turn a generated visual into proof of product identity or effect.

Adaptation guidance should show a usable filming/editing route and fillable sentence pattern. When evidence is missing, leave an explicit evidence slot or change the argument to a supported product fact or general explanation; do not create a testimonial to fill the gap. This mode does not create an AI production task sheet or submit work to a paid generation service.

## Validation And Export

Use the script-v3 validator against the source shot manifest before rendering:

```text
python scripts/validate_script_v3.py --manifest <script-manifest.json>
```

The validation rejects source-hash drift, cross-video references, missing or duplicated shot intersections, gaps/overlaps, mismatched boundary spans, ungrounded taxonomy values, and frame-snapping claims that differ from the declared frame quantization. A completed transcript is allowed only with separate human-review evidence.

Export remains a local operation. The default command is a dry run; pass an analyst review receipt whose manifest SHA-256 matches the exact file before using `--render` (`--review-receipt` is the general option name; `--root-review` remains an alias):

```text
python scripts/export_script_v3.py --manifest <script-manifest.json> --review-receipt <review-receipt.json> --output <new-output-directory>
python scripts/export_script_v3.py --manifest <script-manifest.json> --review-receipt <review-receipt.json> --output <new-output-directory> --render
```

Minimum review-receipt contract:

```json
{
  "reviewer": "<analyst identity>",
  "content_and_provenance": "PASS",
  "manifest_sha256": "<SHA-256 of the exact script manifest being exported>",
  "approved_actions": ["render continuous segment clips"]
}
```

This is an internal, machine-bound record of the analyst's content/provenance review; it is not an extra user-confirmation step when the work is already authorized. For Feishu writeback, list that distinct action only when it is already within the user's authorization. Never hardcode a sample receipt, reviewer identity, video ID, or output path.

If any transcript or word-by-word listening status claims human review is complete, include `audio_review_evidence` with `reviewer`, `reviewed_at`, `scope`, and a nonempty `evidence` list. Each evidence item should identify the source audio/hash, reviewed time range or segment IDs, and listening notes. Machine ASR alone is never human-review evidence. Keep the status pending when such evidence is absent.

The exporter produces continuous H.264/AAC clips, midpoint thumbnails, a playable local `index.html`, CSV/JSON metadata, Chinese instructions, hashes, and a completion receipt. It probes stream durations and fully decodes every clip. Re-running an identical completed build verifies and reuses it; a changed or partial output is preserved and requires a new revision/output directory.

Do not upload to Feishu unless the user explicitly asks for that operation. After an authorized upload, follow the separate live-schema, minimal-write, attachment, and readback gates in [reviewed-timeline-workflow.md](reviewed-timeline-workflow.md). Do not claim full transcript verification, human listening, or playback in the desktop Base UI unless those exact checks happened.
