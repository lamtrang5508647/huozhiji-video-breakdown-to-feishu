# Consistent execution and review

Use this contract when delegating breakdown work or continuing a project with mixed models. It makes artifacts comparable; it does not make models equally capable. Record the actual available model when known. Never identify a model from an agent label alone; use the actual available model metadata. User authorization to continue ingestion carries forward; these are execution checks, not another permission request.

## Keep the three units distinct

1. A **script segment** contains a complete question, claim, response, or narrative development. Boundaries follow meaning and adjacent speech. Several real cuts may occur inside one segment. Do not split a sentence just to match the picture change.
2. A **shot group** describes how several pictures cooperate to express that segment. It can contain a montage, a before/after pair, or demonstration plus explanation.
3. A **numbered shot material** follows the actual visual source/scene/angle transition. Changing from a head close-up to a chat screenshot, hospital exterior to microscope footage, or day 10 to day 12 is a new shot. Preserve the semantic segment link so users can retrieve either the full argument or one picture.

“The breakdown is too fragmented” changes the default browsing/semantic layer; it is not permission to merge several actual shots into one numbered shot. Conversely, one uninterrupted talking-head shot does not need splitting at every subtitle, gesture, number animation, or minor camera movement. A newly dominant inserted asset needs its own cut; a decorative overlay on the same continuing scene may remain in that shot. State the distinction from the actual frames.

## Give the executor a source-bound packet

Supply the exact video ID/title, local source SHA and duration, destination profile and live schema, existing source/product IDs, the single product-platform Docx registry, and the output directory. Include complete chronological overview pages and candidate before/after frames. Inspect those images with native vision. The file's existence or an OCR result is not evidence that it was visually reviewed.

Read the whole video before assigning its framework. Resolve every candidate cut from before/after evidence. Record the decision in `boundary-review.json`; inspect denser frames or playback where uncertain. Do not convert every detector event to a cut, or decide merges merely to reach a desired shot count. Add missed cuts with evidence. The ledger certifies traceability and coverage, not the truth of the visual judgment.

After choosing boundaries, inspect a frame from **inside each final interval**, plus its start/end where necessary. Write the description and readable subtitle for that interval. Do not copy the previous/next transition description into it; do not reuse an overview timestamp as if it were the final interval midpoint. If candidate before/after frames show the same picture, inspect the detector cluster neighborhood before approving a cut at its representative timestamp. Use true source frame timestamps for close boundary decisions. An empty excerpt requires an observed reason such as no visible subtitle, occlusion, or unreadable resolution; silently blanking all excerpts is unfinished analysis.

When local PyAV is already installed, `scripts/extract_interval_evidence.py --timeline <reviewed.json> --output-dir <new-empty-directory>` deterministically extracts start/middle/end and adjacent-cut frames with actual decoded PTS and file hashes. It does not inspect those images or approve the timeline. Executors must actually view the resulting frames. A requested seek timestamp is not the measured frame timestamp. When correcting a cut, replace its evidence pair with frames around the corrected cut; do not retain a detector pair from a different second. For gradual transitions choose and state a consistent rule (for example first appearance of the new dominant material), record the transition window, and do not create a new shot for a decorative overlay or a continuing collage pan.

Ledger shape for `validate_execution_quality.py`:

```json
{
  "schema_version": 1,
  "video_id": "source-video-id",
  "source_sha256": "actual-source-sha256",
  "timeline_sha256": "actual-reviewed-timeline-sha256",
  "candidates_path": "candidates/candidates.json",
  "candidate_sha256": "actual-candidate-file-sha256",
  "decisions": [{
    "index": 1,
    "candidate_sec": 1.2,
    "decision": "cut",
    "reviewed_cut_ms": 1200,
    "reason": "Presenter ends; full-screen chat screenshot begins.",
    "before": "frames/before.jpg",
    "after": "frames/after.jpg"
  }],
  "added_cuts": []
}
```

Indexes cover all detector clusters in order. `continuous` requires `continuity: "same_source_scene"` and a concrete observed reason; `uncertain` blocks publication until resolved. Each added cut has `time_ms`, `before`, `after`, and `reason`. Evidence paths resolve relative to the ledger, unless absolute. Keep meaningful shot groups in a separate field/file; do not relabel them as shots.

## Content judgment before formatting

9.1 updates require the [context calibration](context-calibration.md) and
source-bound [context review record](context-review.md). Review each unit with
neighbors and compare source, reusable and section formulas; retain an explicit
reason for each decision, including unchanged annotations. Opening position
does not turn identity, results or a reaction into a pain/hook label. Do not
confuse a planned action with an already observed experience. The supervising
receipt must bind `context-review.json`; unresolved decisions block publication.

- For each semantic section: identify its concrete question, persuasive role, source information order, reusable variable formula, evidence requirements, and a shootable action/shot/edit suggestion. Generic “attract attention/build trust/promote conversion” alone is insufficient.
- Emotion labels need an observed phrase, gesture, or vocal cue with a time and shot reference. Separate author expression from hypothetical audience reaction. A question is not automatically doubt, and a reaction meme is not proof of a person's actual state. Use “未明确” when warranted.
- Keep business product classification separate from packaging brand. Read visible product text. ASR, a previous video's brand, or the business folder name cannot resolve a label. Preserve uncertainty and disagreements between captions, packaging, title, and audio.
- Preserve source subtitle characters. Use the [v9 explicit phrase review](phrase-annotation-v9.md) for every unit and both formula layers. Never inherit a section/shot family as a fallback, use candidate-number ranges, or silently apply the first matching substring. Missing/stale/incomplete annotations block publication. Brand naming, claims, evidence, audience, price reasons, and CTA are not interchangeable. Color never means the claim is verified; a structure PASS never means semantic review passed.
- Preserve negation, quotation and conditions across subtitle cards and picture cuts. In the reviewed source example, “突然直接就下定义 / 它能帮助育发 / 不严谨的” is a criticism of prematurely concluding efficacy; the middle card alone cannot become an affirmative effect claim in the analysis, formula, or reusable clip label. Carry the surrounding qualification in the semantic segment and material-use conditions. Source words may remain verbatim while their analytical interpretation must reflect the complete sentence.
- ASR is a draft. Reject implausible word spans, long repetitions, hallucinated music text, and chunk-edge bunching as alignment evidence. Check semantic boundaries against credible adjacent words; if unreliable, retain candidate status. Never turn a machine status into “人工逐句听校”.
- Store free-form fine-grained descriptions separately from the live Base select tags. Use an explicit shot-to-select mapping with only current options, bound to the reviewed timeline. Never auto-create options from a slash-separated description.

## Reviewer acceptance and delivery

Before assembly run `scripts/validate_section_content.py --content <sections-content.json> --timeline <reviewed_timeline.json> --output <receipt.json>`. The authoring packet needs `timeline_sha256`; readable strings for whole-video `visible_brand`, `strategy`, `audience_hypothesis`, `replication_outline`; and section-specific `slots`, `shoot`, `formula`. Put brand uncertainty in readable text, keeping any structured evidence in a separate field. Each section lists the exact ordered intersecting `source_shot_ids` and exactly four `arc` objects: `{ "time_ms": 1234, "evidence_shot_id": "shot-001", "expression": "the observed words/action" }`. Never silently truncate extra events. Rebind all references after timeline changes. Identical shooting plans or fill-in sentences across sections are returned for revision. This gate checks known structural defects, not whether descriptions match the pixels.

An export helper must consume an existing reviewer decision bound to every input; it must never mint its own content PASS just because ffmpeg can run. In this project `content-review-approval.json` contains `status`, `reviewer`, `evidence_inspected`, explicit `approved_actions` including `render continuous segment clips`, and `input_sha256` with exact keys `reviewed_timeline.json`, `sections-content.json`, `source.json`, `subtitle-corrections.json` (null if that optional file was absent at review). The reviewer creates it only after actual source review and an export decision. Recheck the full input snapshot and unchanged review receipt after assembly. A pure assembler produces a pending manifest; content approval alone never automatically grants export approval. A visual `boundary_review` object never implies machine word alignment or human listening.

Use `scripts/validate_content_approval.py --job <job-directory>` to check this input review. The semantic exporter separately requires an explicit render action in its manifest-bound review receipt; labels inside the manifest cannot substitute for it. These are reviewer workflow checks within the user's already authorized task, not a new request for user permission.

The reviewer checks all unresolved brands/numbers/claims, all merged candidates where source/scene/angle/date may change, all semantic boundary crossings, opening and closing sequence, and a reproducible selection across the remaining duration. Findings go back to the executor for correction. Save which evidence was actually inspected. A mechanical PASS, model self-rating, or ffmpeg success cannot replace this content review.

Keep visual-cut timing and script/audio timing distinct. Speech may continue across a real picture cut. Move a **script-segment** boundary only when source meaning and credible word timing justify it; an ASR word crossing is not a reason to erase/move a visual cut. A 9-section script has 8 internal audio boundaries, regardless of how many shot groups or shots it contains.

Run the normal timeline/script validators and the ledger check before publishing the corrected revision. Test a new contract on the same problematic source: show the old error is rejected and the corrected artifact passes; state which quality dimensions remain judgment-based. Do not promise parity from one passing sample.

Export continuous semantic segments and individual numbered shots. Full-decode checks, start/end inspection, source/attachment hashes, live schema checks, minimal sequential writes, and readback remain required. If source audio extends beyond the video stream, explicitly record any terminal frame hold; do not silently discard the audio or relax duration checks.

Append original-title chapters to the existing product-platform Docx, link matching H1/H2 anchors from the existing Base, preserve original script/media and user edits, and resume by stable keys. Correct an error found after export by a new reviewed annotation revision; keep media provenance and do not freeze a known false subtitle merely to preserve an old hash.

## Fixed presentation and complete material delivery

The user-approved first chapter is the presentation baseline. Executors provide reviewed JSON; they do not choose heading levels, field order, or colors independently. Use `scripts/render_platform_chapter.py` to create the Docx chapter and its layout receipt. Read `references/golden-layout-and-delivery.md` before publishing. A bold paragraph is not H2. Read back actual XML and verify the heading tags after writing; local XML alone does not prove remote formatting.

Analysis publication and material-library delivery are separate states. A video is not delivered merely because its document exists. Build the exact upload plan with `scripts/build_video_base_plan.py`; upload with `scripts/sync_video_base.py` using the approved plan hash and resumable journal. After the uploader finishes, run `scripts/verify_material_gallery.py` against the actual product/platform gallery. It checks visible material keys, video attachment names, video cover, source links, semantic links, and parent links. A wrong filter or missing attachment must fail delivery, even if upload calls returned success. No concurrent Base readers/writers during these sequential operations.

Record at least: content review status, transcript provenance, local clip QC, document layout readback, uploaded material/semantic counts, live gallery membership, and UI playback status. Keep pending work explicit. Live metadata verification is not remote-byte hashing or browser playback. Do not label an empty or unfinished library as completed, and do not infer that a working formula is broken solely from a serialized schema placeholder: inspect real row values and actual filter results first.
