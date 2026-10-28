# Explicitly selected legacy 27-field profile

Use only when the user selected the original 27-field schema. Pass the selected explicit config; never infer or redirect the target from a bundled private profile. Invoke scripts by absolute skill path.

## Original 27-Field Folder-Report Workflow

### 0. Optional Legacy Technical Split

Use the legacy splitter only when the user explicitly requests automatic technical cuts, without a reviewed semantic timeline:

```text
python scripts/detect_and_split.py <video> --output-dir <job-dir>/<video-name>/shots
```

Its output contains clips, keyframes, `shots.csv`, `shots.json`, and a legacy handwritten `edit-list.otio.json`; the latter is unverified as an editing project. Use `--threshold 0.20` for videos with subtle cuts and `--min-scene-sec 0.40` for short-form fast cuts. Use `--copy` only when speed matters more than frame-accurate boundaries.

Do not treat every detected cut as a semantic scene. Review the contact sheet and keep the technical shot list separate from the 27-field marketing analysis.

### 1. Prepare The Folder

Choose a new job directory outside the source folder, then run:

```text
python scripts/prepare_batch.py <source-folder> --work-dir <job-dir>
```

Add `--top-level-only` only when the user does not want subfolders. The script produces `batch_manifest.json`, one asset directory per video, opening frames, 10-second contact sheets, metadata, and a 16 kHz mono WAV when audio exists.

Opening-frame filenames encode a **requested input seek**, not an actual decoded PTS; each contact sheet's filename gives its requested window, and its `fps=2` filter may select a later source frame than an apparent half-second grid position. The manifest records `visual_sampling_provenance.actual_source_pts` as `not_recorded`. Treat these assets as visual overviews only, and never infer exact source-frame times or cut boundaries from those labels or grid positions. For precise timing, decode from the hash-verified original and record each selected frame's native PTS and time base, using the available interval evidence tools; manually inspect the resulting source frames. This additional metadata is backward-compatible with existing path fields and does not retroactively validate older manifests.

Read the manifest. Continue only with entries whose status is `prepared`. Preserve failed entries and their errors for the final report.

### 2. Build Transcript Evidence

Use visible subtitles as frame evidence. When a local offline ASR engine is installed, optionally run:

```text
python scripts/offline_asr.py <audio.wav> --engine auto --output-dir <video-asset-dir>/asr
```

Do not install or download an ASR model silently. If no reliable spoken transcript or readable subtitles exist, skip Feishu writing for that video rather than guessing.

### 3. Inspect One Video Completely

For each prepared video, in manifest order:

1. Inspect every opening frame from 0–5 seconds.
2. Inspect every contact sheet in chronological order.
3. Decode additional frames when text, products, prices, actions, or claims are unclear; record each selected frame's actual source PTS rather than assuming a requested seek produced an exact frame.
4. Reconstruct the source script from spoken transcript first, synchronized subtitles second, and visible screen copy only when no reliable speech exists.
5. Distinguish advertising claims from verified facts. Record risky claims in `不可照搬点`.
6. Detect AI/simulation watermarks and disclose them; never treat simulated visuals as real product evidence.

### 4. Create Payload And Report

Copy [assets/payload_template.json](../assets/payload_template.json) and fill all 26 writable fields in the existing key order. Do not add `案例素材` to the payload.

Create one Markdown report from [assets/report_template.md](../assets/report_template.md). Keep all 27 fields in exact table order; use the source video's basename in the report.

### 5. Validate Before Writing

Run both static and live validation:

```text
python scripts/validate_payload.py <payload.json> --config <legacy-config.json> --live
```

Then complete the human judgment checklist in [references/field-rules.md](field-rules.md). Fix every error. Do not downgrade an error to a warning merely to continue the batch.

### 6. Sync And Read Back

Run a dry run first for the first record on a new computer:

```text
python scripts/sync_to_feishu.py --payload <payload.json> --video <source-video> --report <report.md> --config <legacy-config.json>
```

After the dry run passes and the user requested ingestion, write:

```text
python scripts/sync_to_feishu.py --payload <payload.json> --video <source-video> --report <report.md> --config <legacy-config.json> --write
```

The script must:

- check login and live schema;
- reject an exact duplicate title unless explicitly overridden;
- create or update one record;
- upload the original video and report together;
- read all 27 fields back in JSON and compare them with the payload;
- generate a direct record share link;
- return nonzero if any field or attachment differs.

Use `--record-id <id>` only to resume a known partial record. Use `--allow-duplicate` only after the user confirms the duplicate is intentional.

### 7. Finish The Batch

Return a concise batch summary containing:

- source folder and total discovered videos;
- successfully written titles and direct record links;
- skipped/failed files with exact reasons;
- confirmation that each successful record contains 26 content fields plus two attachments;
- any schema drift, login, dependency, or compliance warning.

Do not claim the batch is complete while a prepared video remains unanalyzed or an uploaded record has not passed readback.

## Portability

Use only Python standard-library scripts and subprocess argument arrays; never construct shell command strings. This preserves spaces, Chinese names, and Windows paths. Follow [references/portability.md](portability.md) for macOS/Windows installation and offline ASR options.
