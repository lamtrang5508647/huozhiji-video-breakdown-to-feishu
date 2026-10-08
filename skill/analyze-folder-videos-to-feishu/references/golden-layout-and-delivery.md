# Stable chapter format and material access

This workflow applies to the product/platform aggregated Docx presentation. It provides a consistent chapter structure across executors and models.

## Required structure

- One H1: exact source-video title. Stable video identity and source URL directly below it.
- An explicit link to the existing script-segment gallery and another labelled **素材资产库·视频片段** pointing to the existing product/platform material gallery. Do not create duplicate views or documents.
- H2: **原片结构公式**.
- H2: **可复用变量公式**.
- H2: **句内功能颜色图例**.
- H2 for each complete semantic segment: **段01｜段名**, **段02｜段名**, and so on. Put time ranges in the metadata paragraph, not the heading.

Under every segment, preserve this order: bold metadata labels for script function / source time range / source content groups; continuous phrase-colored source excerpts; source function sequence; observable emotion progression with evidence; source shot range; segment-level **受众反应假设（非测量）**; segment formula, fill-in sentence, and shot/edit instructions. Do not append a shot ID after every phrase in the reading paragraph. Exact traceability remains in the JSON and shot-range references.

The default simplified chapter omits exactly these three requested display blocks: **不可直接照搬**, **整片受众假设（非测量）**, and **复刻素材清单**. Keep their source analysis fields intact for validation and other authorized use; do not replace them with blank paragraphs. If the user explicitly asks for one of these items, provide it as a separate requested deliverable. This does not remove the segment-level audience-response hypothesis, section emotion evidence, original text, formulas, shot guidance, H1/H2 structure, time ranges, or the material-library navigation link.

Use the renderer's fixed palette. Source excerpts remain source text; an analysis label or adaptation formula must never become a subtitle. Labels and color assignments still require content judgment; a renderer cannot decide them correctly on its own. A long effect-claim passage may legitimately share a color, but never color an entire paragraph by default without checking the functions inside it.

All new/updated phrase presentations use [the v9 annotation contract](phrase-annotation-v9.md). Formula phrases are colored individually too. Experience and CTA use the platform's default foreground, with yellow background/bold or bold/underline respectively. In an earlier deployment, native readback converted explicit dark RGB to green; do not assume arbitrary RGB support or weaken the exact-character comparator to accept that drift. HTML uses its dark foreground independently; named Docx foreground colors are used only for the six supported colored families.

For an explicitly reviewed annotation stored in a source unit, use `display_kind: "observation_note"` to place it in a separate reading note. If a mixed unit contains both screen-description commentary and a literal subtitle, retain the full unit and specify `reading_span` as an exact substring of that unit. Never remove an annotation with a heuristic that could also delete actual source speech. Do not silently rewrite frozen source/export manifests to make a presentation change pass.

## Publishing and updating

Before writing, validate the current manifest, analysis, and generated chapter. Fetch the live chapter plus the full snapshot needed by the registry guard. If hashes differ from the registry, reconcile the real changes instead of overwriting them with a cached copy. For format repair, preserve the H1 and existing segment H2 block IDs so existing Base links continue to work. Keep a pending-write journal; after an uncertain write, read back before retrying.

After writing, compare actual XML against the generated layout: heading levels and order, exact source title and identity, source text and phrase colors, all segment metadata, source range, retained segment audience-response hypotheses, and both navigation links. Confirm the three omitted whole-video blocks are absent and that the block/H2 count matches the rendered layout; do not rely on old hard-coded paragraph or H2 positions. Re-read source and segment links if anchors changed. Update the registry only after successful readback. The existence of an H2 somewhere in a chapter is not sufficient: all required formula, legend, and segment headings must have the specified levels.

For a newly approved video in an existing group, use `scripts/publish_platform_chapter.py --help`. It defaults to a live read-only preflight; writing requires `--apply`, both the reviewed analysis and manifest SHA-256 values, v9 annotations, and a separate `--semantic-review` supervising receipt bound to those exact inputs. An already registered chapter must match the fixed layout to return a no-op. A mismatch requires guarded in-place repair, not another append. The helper preserves other chapters, validates the actual saved XML with the same renderer, and writes canonical H1/H2 anchor links only after readback. It does not create a new product/platform document or infer content approval.

For an existing reviewed chapter's formatting repair, use `scripts/repair_platform_chapter.py --help`. It also defaults to read-only; apply requires both approval hashes and, for v9, the separate `--semantic-review` receipt. It keeps H1 and segment H2 IDs, inserts verified replacement body blocks before removing the superseded generated text blocks, and records each verified group in a resumable receipt. It refuses unrecognized resource blocks, registry differences, changed payloads, and uncertain pending writes. Never delete a pending journal to force a retry without first comparing its saved before-state to live content. Do not change the frozen layout while repair is running.

## Material library acceptance

The complete output has both continuous semantic clips and numbered shot clips. The source-video row links to both; each material links back to the source and intersecting semantic segments. Keep the existing video attachment as the gallery cover; keyframes are supplementary.

Use `verify_material_gallery.py --help` for the read-only verification command. Pass the actual profile, exact upload plan and state, actual gallery view ID, business product code, and platform. Read the gallery's real filter and query its full paginated result. Verify every planned material appears in that result, carries the actual video attachment, and links to the right source and script segments. Show source-video and script-segment fields on cards when the existing schema provides them. Preserve existing visible fields and navigation destinations when clarifying labels.

When the user has authorized editorial archive filtering, add `--archive-zone 表情包归档`. This opt-in verifies the actual exclusion filter and separately checks every hidden archived clip's attachment, product/platform, source, semantic and parent links. Report visible and archived counts separately; hiding memes does not delete them or make them visible. Without this flag, all planned materials must still be visible.

The acceptance report must distinguish: technical remote readback; source/content review; manual audio review; remote video playback. Do not claim the last two from successful uploads or local decode tests.

## Lower-model execution

Give the executor one video packet, the approved first-chapter schema, exact JSON keys, live tags, and a bounded first sample. Review that sample before scaling. If it merges unrelated pictures, shifts descriptions to adjacent intervals, blanks subtitles, invents claims, changes title/brand, or omits formulas/assets, return it for correction. Do not relax the check to make a model pass.

Use the actual available model ID. This package standardizes formatting and delivery checks; it does not guarantee equal visual/language judgment across models. Save benchmark scope and remaining errors, and retain stronger review for disputed source evidence.
