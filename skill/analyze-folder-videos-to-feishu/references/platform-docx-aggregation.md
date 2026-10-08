# Product-Platform Aggregated Script Docx

Use one Feishu Docx for each stable product-ID × source-platform group. The Base video and script-segment records remain the evidence and navigation source; the Docx is the shared reading view containing one chapter per analyzed source video.

## Group And Document Identity

Build the group key from stable product ID and stable platform ID, not display names alone. Use display names only for the human-readable document title:

```text
<产品>竞品｜<平台>拆解
```

Reuse the exact existing `doc_token` for that group. Persist a local mapping outside the distributable Skill, bound to the Base identity and keyed by Base/product/platform IDs, with at least:

```json
{
  "base_id": "<base-id>",
  "base_folder_block_id": "<verified-library-folder-block-id>",
  "groups": {
    "<base-id>::<product-id>::<platform-id>": {
      "doc_token": "<persistent-doc-token>",
      "base_docx_block_id": "<verified-docx-entry-block-id>",
      "doc_title": "<产品>竞品｜<平台>拆解",
      "videos": {
        "<platform-video-id-or-canonical-source-url>": {
          "source_title": "<exact-original-video-title>",
          "source_url": "<canonical-source-url>",
          "chapter_root_block_id": "<verified-h1-block-id>",
          "segment_block_ids": {"<segment-id>": "<verified-h2-block-id>"},
          "content_sha256": "<chapter-content-hash>"
        }
      }
    }
  }
}
```

These identifiers have different roles and are not interchangeable: `base_id` scopes the Base; `base_folder_block_id` identifies the library folder node in that Base; `base_docx_block_id` identifies the Docx entry block in that folder; and `doc_token` identifies the Docx content. Do not pass Base block IDs or a Docx content token to a Drive folder endpoint, and do not substitute a Drive `folder_token` for a Base folder block ID.

Use the stable source-platform video ID (for example, a BV ID) or canonical source URL as the video chapter identity. Never use the title as the identity. Keep the H1 exactly equal to the original source-video title. If titles repeat, preserve both identical H1 titles and disambiguate with a metadata line directly below each H1 containing the stable video ID, platform, and source URL. Do not append IDs to or rewrite the original title.

Resolve an existing document through the persistent group mapping first, then fetch its live metadata and content to verify that the token still belongs to the intended product/platform group. If no mapping exists, inspect live candidate documents and their embedded group metadata; adopt only one unique verified match. A matching title or folder alone is insufficient. If two candidates match or identity cannot be established, stop without editing. Create a group Docx only when the first complete, reviewed video is ready; never create an empty placeholder Docx.

## Library Naming And Migration

Use `竞品拆解资料库` as the library folder name and `<产品>竞品｜<平台>拆解` as the Docx title template (for example, `示例产品竞品｜B站拆解`). A name specifies a desired label; it does not identify an object uniquely or authorize a rename/move. Perform a specific migration only when the current task instruction authorizes it and the target is bound to a verified `base_folder_block_id`, `base_docx_block_id`, and `doc_token` as appropriate. Bind the old and new names to those verified IDs, then read metadata back. If a requested source name or target name resolves ambiguously, stop. Honor authorization already given in the active conversation, including prior turns; example names do not grant blanket permission to mutate unrelated resources. Never delete any folder, document, chapter, PNG, source file, or attachment.

Place new group documents in the verified `竞品拆解资料库` folder node. Reuse its `base_folder_block_id`. Do not create another same-name folder when a unique library folder already exists.

## Chapter Structure And Evidence

Create one H1 chapter per source video. Each H1 text is the exact original video title; its following source metadata carries the stable video ID and URL. Under it, keep a clear whole-video framework with both:

- the source formula, in the observed source order and attributed to the source;
- the separate reusable-variable formula, clearly marked as an adaptation framework.

Create one H2 per reviewed semantic script segment. Preserve the segment ID and full semantic heading. Under each H2, retain the exact source subtitle excerpt in reading order with reviewed phrase-level color runs, the segment's own formula, segment-scoped emotion labels/evidence and audience hypothesis, plus source shot IDs/time intersections or other reviewed evidence references. Keep source claims and analysis/adaptation distinct. Never reconstruct unverified dialogue, imply machine timing is human sentence listening, or present the source speaker's product, mechanism, percentage, or outcome claims as verified facts.

Use the simplified default chapter layout described in [golden-layout-and-delivery.md](golden-layout-and-delivery.md): omit the whole-video **不可直接照搬** block, the whole-video audience hypothesis (non-measured), and the replication materials checklist. Keep their source analysis fields for validation; do not emit empty replacement paragraphs. Segment-level audience-response hypotheses and the material-library link remain in the chapter. Deliver an omitted item separately only when the user explicitly requests it.

Use the current reviewed annotation JSON as the rendering input. Verify exact phrase-run reconstruction against `source_text` before writing. A `subtitle_excerpts` presentation remains subtitle excerpts unless complete oral transcription has separate human listening evidence. Do not overwrite the Base's original source-script field with colored excerpts or adaptation copy. Use native Docx formatting for colors only after verifying the actual target's readback styles; preserve family labels/legend if colors are normalized. Do not claim exact RGB output without XML readback.

## Safe Video-Chapter Upsert

Before every mutation, fetch the actual live document content/XML and the mapping file. Resolve the target H1 using the exact video identity metadata and verify the stored H1 and section IDs against the incoming reviewed analysis. If a saved block ID no longer exists, search for the exact stable video ID/source URL metadata. Continue only when exactly one chapter matches. Never match or replace a chapter by title alone.

Compute a deterministic content hash from the source identity, reviewed source/segment content, phrase runs, source and reusable formulas, segment formulas, emotion/source evidence, rendering version, and palette. If that hash matches the saved mapping, inspect the live XML and skip the chapter only when its content and styles still match. If content has changed, update only the block range/anchors for that one video chapter; do not replace the full document body, delete other videos, or recreate the Docx. Preserve the H1 title and all other chapters byte-for-byte where the document API allows.

Record the hash method alongside its value. Keep `approved_analysis_sha256` and `source_video_sha256` for the reviewed input separately from the rendered chapter's `content_sha256`. The rendered hash covers only this video's H1 through the block before the next H1, excluding the document title and other videos; canonicalize XML text, order and styles while omitting generated block-ID attributes. Never use the whole growing document's hash as one video's chapter hash. Adding another video must not invalidate an unchanged earlier chapter. On retry, verify both the reviewed-input fingerprint and this chapter's live content/styles before skipping.

After writing, fetch the full document content/XML again. Verify exactly one H1 with the target video identity, all expected H2 segment anchors, exact source characters, formulas, evidence references, and intended styles. Then update the persistent mapping with verified block IDs and content hash. On any partial/ambiguous response, read back first and retry only the missing or mismatched blocks. Never blindly append a second H1 on retry. If the document API cannot safely update one chapter without replacing unrelated document content, stop and report that limitation; do not fall back to whole-document overwrite.

## Base Navigation And Write Boundaries

Keep one verified entry point to the matching video H1 on that source video's existing Base record, and a matching H2 link on each existing script-segment record. Use actual returned/verified block links, then read the records back to confirm they point to the intended anchors. If the current field mapping cannot hold a link safely, report the exact schema limitation; do not create a new table or view. Preserve the source video gallery cover, source-media attachments, product/platform navigation, original transcript, and all unrelated fields.

Keep Base mutations table-scoped and sequential. Verify each record belongs to the target table, batch only records from that table, and retain the existing per-table rate/authorization rules. After a partial write, read back all intended rows and retry only missing links. Do not claim that an API readback proves the user's logged-in interface was visually inspected.

The group mapping is the idempotency ledger for the Docx token and per-video H1/H2 anchors. Preserve it across runs and back it up with the review manifest. Do not infer document ownership from title, folder name, cached search text, or a previous response alone.

Do not invent a platform document-size limit. Monitor actual API/platform errors and current documented limits; plan a continuation volume only when a real limit is approached or reached, keeping the same product/platform identity and an explicit volume map. Do not create a new Docx per video as a workaround.

The older direct-in-Base PNG workflow remains a separate optional delivery when a user explicitly chooses it. Its installed `scripts/render_base_previews.py` and `scripts/capture_base_previews.mjs` remain available and documented in [base-native-script-preview.md](base-native-script-preview.md). PNGs already attached to Base records are preserved; this Docx migration never deletes them or other source/user attachments.
