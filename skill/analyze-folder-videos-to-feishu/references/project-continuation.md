# Continuing A Packaged Video Breakdown Project

Use a supplied project root containing `config/project.json` and `state/platform-document-registry.json`. If the user says “继续拆解” and the installed skill has `agents/local-project.json`, resolve its project path and verify that the selected Base matches the user's request. An explicit new target overrides this locator; never reuse old table, document, product, or account IDs for another Base.

## Startup

1. Read the profile, registry and video index. The registry is mutable working state; ZIP copies and old work directories are historical snapshots.
   If the video has a `latest_presentation` entry, read that exact source-bound revision and its review receipt instead of regenerating colors from the older source job's analysis or family-map. Keep frozen timeline/export manifests as media evidence; presentation repair does not replace them. Missing local revision files on another machine require restoring the project artifacts or reviewing again, not trusting a stale ZIP status.
2. Run `python <skill-dir>/scripts/check_project.py --project <project-root> --live --output <receipt.json>`. This makes bounded, sequential read-only checks of the configured Base, tables, document identity/configured owners and selected-identity edit permission. It does not prove field compatibility, human UI playback, transcription quality, or successful upload. Read live fields before a real write.
3. Confirm source identity, exact source title, product record/code, and platform. Capture the local file hash and duration. Link acquisition is platform-specific: an earlier B站 download does not verify 抖音 or 视频号 support. Use supported downloads or user-supplied files; do not bypass login or access controls.
4. Create a separate job directory such as `jobs/<product-code>/<platform>/<source-id>/<revision>/`; never move or rename the source. Save manifests, review evidence, derived media and per-step receipts there.

## Analysis And Delivery

Follow `script-segment-framework.md` for whole-video framework → complete semantic segments → shot groups → single numbered shots. Use `reviewed-timeline-workflow.md` for shot evidence, timeline validation and clip exports. Use `phrase-color-script-analysis.md` and `platform-docx-aggregation.md` for colored source phrases, source formula, reusable variable formula, emotion evidence and one product/platform Docx.

The user needs both layers: complete script sections support adaptation; numbered source shots support material selection. A new visual cut alone is not a new script section. Do not force every source into a fixed number of sections or the previous sample's sales formula.

Use a capable planning/review model and a lighter execution agent when the user requests that split and those model routes are available. Give execution agents bounded tasks, exact source/target IDs and permitted writes. Only one agent owns remote writes at a time. The parent checks source grounding, timings, product attribution, formulas, receipt hashes, links and readbacks. Do not claim a model switch that was unavailable.

## Live Writes And Resuming

Use the current Feishu Base/Docx tools and their version-matched schemas. The generic skill exporters validate/render locally. For the default script/shot workflow, `build_video_base_plan.py` builds a reviewable source-bound plan and `sync_video_base.py` executes that exact approved plan with a resumable journal and sequential readbacks. These helpers do not analyze videos or authorize their own output. `verify_material_gallery.py` then verifies actual gallery membership and links. `sync_to_feishu.py` remains only for the explicitly selected legacy 27-field profile; `sync_color_previews.py` remains for optional PNG previews. Old per-video scripts in a working directory are not generic synchronizers and must not be reused against a new source.

For each authorized video, read existing rows by stable video/segment/shot keys, reconcile attachments and links, write only missing or reviewed changes, and read back. Record source hash, record IDs, attachment verification and completed steps before advancing. A timeout is not proof a write failed: read back first, then retry only missing work. Stop on duplicate matching records or ambiguous remote state.

For Docx, use the exact registry doc token and video H1/H2 anchors. Keep a before-write snapshot and revision. If the live content differs from the last verified chapter, treat it as a possible human edit; compare and preserve it rather than overwriting from the old manifest. If the API revision changes during an update, refetch and reconcile before retrying. Never overwrite unrelated chapters or whole-document content.

After fetching fresh full-detail, full-document XML with the current Docx tool, save the JSON response and compare it with `python <skill-dir>/scripts/check_docx_registry.py --registry <registry.json> --snapshot <fresh-docx.json>`. This read-only helper checks existing chapter hashes and H1/H2 anchors. A mismatch is a reconciliation stop, not permission to restore the old content. An old snapshot only proves the state at its recorded revision.

Update the registry and video index only after successful readback, using atomic local replacement. Keep at most a small recovery snapshot of the previous state; do not create new online documents or duplicate large media as backups. Do not delete original videos, source evidence or user attachments without a matching request.

When a new group document is created, add its actual owner from metadata to the project's `document_owners` map after the access check. Keep that map aligned with the registry; never copy the previous document's owner by assumption.

## Completion

Report analyzed videos, complete semantic sections and shot counts, verified exported/uploaded counts, the group document link, and the exact outstanding uncertainty. Do not label subtitle excerpts as a full corrected transcript or technical clip QC as editorial correctness. AI production task sheets and paid generation remain separate unless requested.
