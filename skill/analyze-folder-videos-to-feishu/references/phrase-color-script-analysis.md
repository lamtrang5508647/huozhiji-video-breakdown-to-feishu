# Phrase-Level Color Annotation And Script Formula

Use this reference when the requested output keeps source wording visible, colors different functions inside a sentence, and summarizes both the source-specific formula and a separate reusable variable formula.

## Keep The Evidence Literal

- Treat subtitle text as subtitle excerpts unless the source transcript has separate human listening evidence. Never join neighboring captions into a sentence that was not verified as continuous speech.
- Preserve every displayed source character exactly. Mark OCR uncertainty beside the disputed phrase; do not silently correct a plausible typo.
- A phrase annotation is a contiguous slice of its `source_text`. Store `char_start` and `char_end` as half-open Unicode character offsets. Concatenating the ordered phrase-run text must reproduce `source_text` exactly.
- Split only at meaningful functional changes. Do not color isolated connectives, particles, or grammatical filler as separate semantic elements unless that fragment itself carries a meaningful role. One sentence may have one or several phrase runs.
- Assign function tags from the sentence and neighboring source context, not by generic keyword rules. Similar words can perform different roles in different segments. Keep tags readable in the output language.
- If adjacent source shots repeat the exact same subtitle and a compact view should show it once, merge only that display unit. Keep the complete raw order and every source shot ID/time intersection in its mapping. Never remove repetition from the evidence record.
- Use the reviewed manifest's explicit cross-segment boundary intersections to decide `boundary_context`. Do not use an arbitrary short-duration threshold to exclude a caption that falls inside the segment's actual interval. A boundary-only overlap remains context and cannot bring its neighboring source group's full script formula into the segment.
- Segment intervals and shot intersections are locators. Machine word timing can support candidate segment boundaries, but it does not establish confirmed sentence-level audio timecodes or human listening.

## Keep Function, Emotion, And Claim Status Separate

The taxonomy family is a presentation grouping. A more specific phrase function explains what the source phrase does in context. Emotion is a separate field with its own evidence and scope. If only a segment-level emotion arc was reviewed, use `scope: segment`; do not repeat it as a directly observed emotion for each sentence. A hook, product mention, or call to action is not itself evidence of an emotion.

Use stable semantic family IDs and localized display labels. A renderer can map those families to supported colors, but it must show a readable legend and preserve the semantic family if the platform changes its available palette. Avoid near-identical colors. Do not reuse a color to imply a different function.

Keep source claims attributed to the source speaker. Mentions of research, registrations, brands, ingredients, personal use, percentages, tests, effects, product identity, links, or credentials are not verified merely because a function tag or colored label was assigned. An emotional audience hypothesis is not measured audience response.

## Summarize Two Different Formulas

The source formula records the source's actual ordered moves. Use concise atomic steps with this minimum structure:

```json
{
  "label": "short, source-specific step",
  "category_family": "stable family ID",
  "category_family_label": "localized display label",
  "segment_id": "source segment ID",
  "unit_ids": ["source subtitle unit IDs"],
  "reason": "why this step belongs here in the observed source order"
}
```

Keep a step for a distinct claim or turn when merging would erase its role. Preserve repetitions as source references or repeat counts. Do not normalize the source into a fixed promotion template; describe price, scarcity, evidence, product, relationship, or action steps only when they are actually present.

The reusable formula is a separate adaptation proposal. Express it with explicit variables and attach the real material needed to fill each variable. A missing testimonial, study, comparison, product fact, promotion, link, or audience result remains a required slot or is replaced by a supported non-claim route. Do not borrow details from a separate user-provided example, another product, or a platform chart.

Store user-provided example copy in its own labeled section. Preserve and annotate its wording if requested, but never merge it into the analyzed source-video transcript, source formula, product identity, or source performance evidence.

## Practical Review Checks

- Phrase runs concatenate exactly to their source line; line sequence and source references remain traceable.
- Every source unit maps to its reviewed segment and actual shot intersections; audio-review status remains explicit.
- Emotion labels reference observable source evidence and keep their declared scope.
- The source formula preserves original order and meaningful repeats; the reusable formula is clearly marked as adaptation.
- Any chart, click curve, or performance screenshot is bound to its actual source/video before it is cited as performance evidence.


## Present A Readable Result

Default to continuous reading inside complete content segments. Show a short 8–12-step whole-video formula, with more detailed steps inside the relevant segment; do not turn every colored phrase into a separate top-level formula item. Keep functional annotations expandable. A video may need fewer steps; do not add absent functions to reach a count.

Use `scripts/render_script_analysis.py` for a standalone local HTML view with legend highlighting, exact source-text copy, source/reuse-formula copy, segment video navigation, and optional shot locators. It only renders reviewed input; it does not analyze, transcribe, or publish. Point `clip_url` at an existing verified segment clip relative to the output HTML directory; do not duplicate media for a presentation change.

```text
python -X utf8 <skill-dir>/scripts/render_script_analysis.py --input <job-dir>/analysis.json --output <job-dir>/analysis.html
```

The presentation JSON contains:

- `title`, `subtitle`, `text_kind` (`subtitle_excerpts` or `user_provided_copy`), and `disclosure`.
- `taxonomy`: `family_id`, localized `label`, and hexadecimal `color` for each family.
- `sections`: stable `id`, optional short `display_id`, `name`, `time_range`, optional `start_ms` and `clip_url`, `formula`. Optional `emotion_labels`, `emotion_evidence` (`stage`, `observable`, `evidence_shot_ids`) and `emotion_hypothesis` are segment-scoped.
- `units`: unique `id`, `section_id`, literal `source_text`, ordered `phrase_runs` (`text`, `char_start`, `char_end`, `category_family`, `function_tag`, `reason`), `source_shot_ids`, `locator_note`, optional `locate_ms` in original-video milliseconds, and `raw_item_ids`.
- `source_formula` and `reusable_formula`: short steps with `label`, `category_family`, optional `section_id` and `note`. Keep richer source evidence and raw dedup mappings in the annotation manifest.
- Optional `source_formula_note`, `reuse_note`, `material_requirements`, and `links` (`label`, `url`). `material_requirements`, `claim_boundaries`, `adaptation_checks`, `assets_checklist`, and whole-video `audience_hypothesis` stay in the source/input for validation, but the default HTML/Docx chapter does not display the replication-materials checklist, the whole-video “不可直接照搬” block, or the whole-video audience hypothesis. An explicitly requested item can be delivered separately.

Validate exact source characters before rendering. Open the generated page in a real browser to verify readable layout, video metadata, locator offsets, legend highlighting, and actual button payloads. Distinguish a simulated clipboard sink from verified OS clipboard delivery. Escape embedded JSON and construct source text with `textContent`, never raw HTML.

## Product-Platform Aggregated Docx Delivery

Use one Docx per stable product-ID × source-platform group as the default reading surface; see [platform-docx-aggregation.md](platform-docx-aggregation.md) for grouping, section upserts, anchor mapping, and retry rules. Keep the source-video and script-segment records canonical and link each to the video H1 or segment H2. Do not create one Docx per video.

Render the source text with supported native Docx styles, preserving exact subtitle strings and the reviewed phrase-color spans. Include the source formula, reusable variable formula, complete segment formulas, and segment-scoped emotion/source evidence. Preserve the source-video and script-segment records, original transcript, video attachment, gallery cover, and product/platform navigation. A subtitle excerpt is not a complete or audio-reviewed oral transcript unless its separate evidence supports that status.

Before using per-phrase color styles on a target Docx, verify the supported palette by reading back a disposable legend and checking paragraph/run styles in the document XML. Never claim exact RGB rendering without readback. Keep the semantic family IDs even if the service normalizes the chosen color. Reuse the same Docx on retries. A PDF is optional only when requested; verify its actual preview and selectable-text behavior before claiming those capabilities.

## Optional Direct-In-Base PNG Delivery

Use [base-native-script-preview.md](base-native-script-preview.md) only when the user explicitly chooses to read the colored analysis inside the original Base record. That reference retains the installed local PNG renderer/capture helpers, field mapping, content-hash idempotency, attachment verification, and partial-write recovery. This optional path does not change the product-platform Docx default. Existing Base PNGs remain valid materials and must not be removed as part of the Docx migration.

For either delivery, write one update batch per table only after confirming the target record IDs belong to that table. A successful CLI dry run checks request shape, not record ownership or server-side success. For a partial result, read back each intended record and retry only missing records in their correct table. The optional Base-PNG workflow does not create a new table, view, or Docx. Preserve existing source text, attachments, video gallery cover, product/platform routing, and original-media hashes. Do not claim the user's logged-in UI was tested from API readback alone.

No interaction, retention, click, conversion, or spend curve may be invented. Add those views only when data is available for this exact source video, with event definition, time granularity, and observation window. Editorial emotion hypotheses belong in a separate dimension from measured performance.


## Default Functional Families

New presentations use [the v9 explicit-review contract](phrase-annotation-v9.md) and its canonical_taxonomy() styles. Use these default presentation families consistently across products. A family may be absent from a particular source; do not add a promotion or CTA merely to fill the palette. Fine-grained function tags remain context-specific, and emotion stays separate.

| Family ID | Display label | Local/PNG color | Docx baseline |
|---|---|---|---|
| hook_pain | 钩子/痛点 | #D83931 | red |
| scene_audience | 场景/人群 | #245BDB | blue |
| product_info | 商品信息 | #2EA121 | green |
| experience_claim | 体验/效果主张 | #1F2329 + #FFF67A background, bold | default text, bold, light-yellow background |
| evidence_trust | 证据/信任表达 | #6425D0 | purple |
| offer_reason | 优惠/促单理由 | #DE7802 | orange |
| action_cta | 行动号召 | #1F2329, bold and underline | default text, bold and underline |
| unclassified | 结构连接/未明确 | #8F959E | gray |

The native style examples above were verified on one target; rerun palette readback if the target/runtime changes. Disease-mechanism or percentage assertions are source claims, not product ingredients or verified evidence. A research mention can serve a trust-building function while remaining unverified. A blessing can serve as a closing function without becoming a problem/pain label merely because it mentions the product topic.
