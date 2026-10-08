# Base-Native Script Preview

Use this reference when the user wants the colored analysis readable in the existing source-video Base, without creating a separate Docx for each video.

## Delivery Shape

Keep the existing video record and existing segment records as the canonical records. Render one colored PNG per main video script and one PNG per complete script-segment record when segment-level records exist. A segment image may contain all of that segment's subtitle-excerpt lines in reviewed order; it must not imply complete spoken-word transcription. The current renderer rejects a preview taller than 14,000 CSS pixels; it does not paginate. Stop and report an over-height record for layout review rather than adding extra preview attachments.

Map values to the agreed fields (create a missing dedicated field only after live schema/type validation):

| Record | Field | Content |
|---|---|---|
| Main video/script record | 彩色脚本预览 | Generated PNG attachment(s) for the whole script reading view |
| Main video/script record | 标色原文 | Exact subtitle-excerpt strings joined by newline; excerpts only, no inline annotations |
| Main video/script record | 原片脚本公式 | Existing approved source formula; read-only during preview sync and included in expected readback |
| Main video/script record | 可复用变量公式 | Adaptation formula only; keep evidence/material requirements in an existing field or the annotation JSON |
| Main video/script record | 预览指纹 | Deterministic content/render fingerprint for the current preview set |
| Script-segment record | 彩色脚本预览 | Generated PNG attachment(s) for that segment |
| Script-segment record | 标色原文 | Exact subtitle-excerpt strings for that segment joined by newline, with no inline annotations |
| Script-segment record | 本段脚本公式 | Existing approved segment formula; read-only during preview sync and included in expected readback |
| Script-segment record | 预览指纹 | Deterministic fingerprint for the current segment preview |

Confirm the live field names and types before writing. If an agreed dedicated field is missing, create only that field on the existing table after validating its intended type, then reread the schema before proceeding. Do not create a table, view, or Docx. Stop on an ambiguous field mapping or type conflict. Keep the existing video gallery cover, product/platform links, routing fields, original-video attachment, and original source-script field unchanged.

## What Users Can Read And Copy

The PNG is a fixed visual snapshot. It carries phrase colors, the legend, source ordering, and page layout into the Base attachment preview. Text in the image is not an editable cell value and must not be described as copyable or searchable. The adjacent `标色原文`, `原片脚本公式`, `本段脚本公式`, and `可复用变量公式` text fields provide the copyable content. `标色原文` contains only exact subtitle strings joined by newline. Phrase-to-color annotations stay in the reviewed JSON and PNG. Preserve punctuation and wording; do not merge distinct source lines into reconstructed dialogue.

The normal analysis workflow must complete and review `原片脚本公式` and each `本段脚本公式` before preview rendering. Preview synchronization is read-only for these formula fields: include their exact approved strings in `expected_text_fields` for readback comparison, but do not modify them. Only `标色原文` and main-record `可复用变量公式` are copyable-text updates from the preview renderer. Keep the detailed `source_formula` and `reusable_formula` structures in the reviewed annotation JSON; do not overwrite the approved analysis manifest during preview generation.

For `标色原文`, preserve exact visible subtitle characters and source references. Mark the content as `subtitle_excerpts` unless the complete oral script has separate transcription and human listening evidence. Machine ASR, subtitle timing, and segment-level timing do not establish line-by-line audio review. Do not overwrite an existing `原片脚本文案` or equivalent source transcript field with annotated excerpts, summaries, reconstructed dialogue, or adaptation copy.

Use the agreed semantic family IDs and colors in the PNG renderer; do not claim that Base text-cell APIs persist inline phrase colors. If a PDF is requested, it may be included as an additional attachment, but preview and text-selection/copy behavior must be tested on that actual PDF before those capabilities are claimed. Keep structured text fields regardless.

## Preview Fingerprint And Idempotency

Calculate a stable semantic/render fingerprint over all inputs that can change the rendered result. It is separate from the PNG file hash:

- source video/record identity and source-media SHA-256 or the verified source revision;
- approved manifest/reviewed-content revision and ordered segment/source-unit IDs;
- exact subtitle text, phrase runs, source formula, segment formula, and reusable formula;
- taxonomy/family palette, renderer schema version, and renderer version.

Store the fingerprint in `预览指纹`. Generated filenames use `scriptv4-preview--<PNG-sha256>.png`; do not treat the filename's PNG hash as the semantic/render fingerprint. Do not use record IDs or titles as filesystem paths without safe normalization.

For an existing record, read the fingerprint, copyable text/formulas, and dedicated preview attachment field. If the fingerprint and all expected generated filenames match, download the attachment(s) and verify their bytes/hash before skipping. A matching text fingerprint alone does not prove that the corresponding preview still exists or is intact.

When the fingerprint changes:

1. Render one PNG per main record and one PNG per segment record. Validate exact source-unit text coverage, colors, layout, PNG signature, and file hashes. The current capture rejects previews taller than 14,000 CSS pixels; it does not paginate. If a record exceeds this limit, stop and report it for layout review rather than splitting it into extra attachments.
2. Append new files only to the dedicated `彩色脚本预览` field. Do not write them into the source-media field.
3. Read the record back, identify the newly returned attachment tokens, download those exact attachments, and compare their bytes/hash with the local files.
4. Update the copyable excerpt/formula fields and fingerprint in the same record's table batch, then read them back and compare exact text/fingerprint values.
5. Only after the new preview and text fields pass readback, remove superseded generated preview tokens from that same dedicated field. Confirm each old token is owned by the renderer using the generated filename and previous preview manifest. Do not remove any source/video/user attachment, and do not remove an attachment whose ownership is uncertain.

The upload command appends files. An interrupted run may leave the new preview attached while text/hash fields are still old. On retry, inspect the dedicated field and reuse a matching generated filename plus verified bytes instead of appending a duplicate. Keep an export/write manifest locally with record ID, field ID, content fingerprint, local filename/hash, and the verified attachment tokens needed to identify generated assets.

## Table-Batched Writes And Recovery

Prepare one record-update batch per table, and execute table batches sequentially. Do not mix main-table and segment-table record IDs in the same request. Before each write, confirm every record belongs to the target table. Keep the existing one-table-at-a-time rate and authorization gates.

After any partial or ambiguous response, read back all target records in that table. Mark each row as `text_verified`, `preview_verified`, or `needs_retry`; retry only missing intended writes. Preserve successful rows and their attachment tokens. Do not report completion until every expected main/segment record has matching copyable text, fingerprint, and downloaded/verified preview files.

Removal of attachment tokens is destructive/high risk in the CLI. Use the exact verified file tokens, dedicated preview field, correct record ID and table ID, and the CLI confirmation flag required by its current `--help`. If any value or ownership check is uncertain, retain the old file and report the duplicate/cleanup condition rather than risk removing source media.

## Local Rendering Helpers

After these helpers are installed with the skill, invoke them from the skill directory as:

- `scripts/render_base_previews.py`: Python standard library; validate reviewed annotation JSON, render one PNG job per bound record, and write a local `sync-manifest.json` with field updates, record/table IDs, PNG name/hash, and a separate preview fingerprint.
- `scripts/capture_base_previews.mjs`: Node.js 22+ and locally installed Chrome or Edge; invoked by the Python renderer over CDP to capture local PNGs. It does not expose a standalone supported `--help` interface.

The Python renderer accepts `--input`, `--bindings`, `--output-dir`, and optional `--browser`; inspect its Python `--help` before use. Confirm Python, Node.js 22+, and Chrome/Edge availability. These helpers render local artifacts only; they do not call Base APIs or upload files. Do not install packages, start an external page service, or claim a successful capture unless the files were actually produced and inspected. An existing standalone HTML view may remain an optional local deliverable, but it is not a replacement for attaching the verified PNG to the original Base record.

The `--bindings` JSON maps reviewed analysis segment IDs to their existing Base records. Its shape is:

```json
{
  "base_id": "<base-id>",
  "provenance": {"source_video_sha256": "<sha256>"},
  "main": {"table_id": "<main-table-id>", "record_id": "<video-record-id>"},
  "sections": {
    "<analysis-segment-id>": {"table_id": "<segment-table-id>", "record_id": "<segment-record-id>"}
  }
}
```

Bindings must have non-empty, unique table/record targets. The preview fingerprint includes the approved analysis hash, rendered HTML, renderer version, source provenance, and the selected Base/table/record identity. Keep source-media hashes in `provenance` so a source replacement invalidates the previous fingerprint.

By default the existing source-formula expectations use ` → ` between ordered steps; the segment renderer normalizes its display ` + ` separators to that convention. If the approved live formula uses a different format or includes literal plus signs, set `expected_text_fields` explicitly on that main/section binding to the exact existing field string. This is a readback expectation, not a formula rewrite.

## Verified Preview Synchronization

Use `scripts/sync_color_previews.py` after local image review. It needs the existing working bot identity in `lark-cli` and uses the manifest's Base/table/record coordinates. Pass an explicit workspace and a stable per-Base job state directory; it never derives the data workspace from the skill installation path. Preserve the state directory across updates because its ownership ledger identifies exactly which generated old preview tokens may be replaced. Keep that private job state outside the distributable skill.

Run local validation first:

```text
python scripts/sync_color_previews.py --workspace <workspace> --state-dir <job-state> --manifest <sync-manifest.json>
```

Add `--remote-dry-run` for read-only target and attachment verification. For a user-authorized ingestion, use `--apply --approved-manifest-sha256 <reviewed-manifest-sha256>`; this digest binds the reviewed payload and does not require a new user permission question. The preview helper uses `--apply` as the write flag; the legacy sync helpers use `--write`. Read the installed helper's `--help` for its current options.

After actual writes, rerun the same sync with the same approved manifest and state directory. Require zero uploads and zero text writes for an unchanged result, plus successful downloaded PNG hashes and unchanged source formula expectations. The helper handles the dedicated preview/text fields; changes to visible view columns or archival of an existing Docx are separate, narrowly scoped organization actions with their own readback. Do not claim an attachment preview was opened in the user's UI from API checks alone.

The images and structured text still consume storage as source materials grow. This workflow avoids adding an independent Docx resource for every video; it does not eliminate asset-storage growth. A separate Docx is an extra deliverable only when explicitly requested. Preserve existing Docx resources during normal preview work. Move an old Docx only when the user explicitly selects an archive action; do not delete it as incidental cleanup.
