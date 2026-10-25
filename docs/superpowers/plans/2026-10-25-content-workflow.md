# Content workflow implementation plan

> **For agentic workers:** Use subagent-driven-development and test-driven-development. Keep independent file ownership; review the integrated result against this plan and then review code quality.

**Goal:** Absorb the two user-provided course notes into the existing video breakdown toolkit and deliver an installed, usable workflow spanning evidence-based analysis, topics/scripts/shooting/test cards, resumable batch work, and measured feedback.

**Architecture:** Preserve existing source manifests, exact text, time/hash checks, contextual review, exports, and Feishu journaling. Add local, versioned sidecars and standard-library CLIs within the installable breakdown Skill. Root project.py forwards to those CLIs. Course examples remain attributed methods, not platform rules or outcome guarantees. Runtime data, PDFs, source videos and credentials stay outside Git.

**Tech Stack:** Python 3.10+, unittest, JSON/CSV, static HTML; existing FFmpeg/FFprobe and optional offline ASR; no new model service or paid API.

**Authoritative upstream:** 08fbee956a85227faddad4943b6fb2db0ab949bf. Git transport failed; the local baseline is an archive snapshot of that exact current main, not a history-preserving clone. Remote delivery must use a branch based on the actual upstream commit, never push the snapshot root over main.

## Requirements and ownership

- [x] Creative workbench: `scripts/creative_workflow.py` under the Skill, `tests/test_creative_workflow.py`, synthetic editable bundle/example assets. Validate product facts vs source claims, source/segment/evidence bindings, decisions, visual alternatives, real-comment provenance, reusable topics/modules, script/shoot/test cards and version lineage. Render a readable HTML report and searchable JSON/CSV. Do not rewrite source manifests or call generated visuals product proof. Every creative variant includes `id`, `product_id`, `hypothesis`, `changed_variable`, `control_notes`, `parent_variant_id`, `mode` (exploration/iteration). Expose standalone `init`, `validate`, `render`, `search`, `demo` commands.
- [x] Feedback: `scripts/performance_feedback.py`, `tests/test_performance_feedback.py`, sample mappings/report data. Import CSV with explicit mapping and metric definitions; bind platform creative IDs to stable local variant IDs/product IDs, dates, goal, attribution window and context. Preserve raw file hash, reject invalid/ambiguous numbers, identifiers and duplicates, never mix incompatible cohorts or imply causation. Expose `init`, `import`, `report`, `demo`; output JSON and human-readable HTML.
- [x] Batch orchestration: `scripts/workflow_batch.py`, `tests/test_workflow_batch.py`. Persistent atomic state, per-stage receipts, cross-batch source SHA identity within a workspace, finite parallel local preparation/ASR, manual analysis/review gates, actual validators/exporters, separate explicit serial publication handoff. Retry only failed runnable stages; invalidate downstream work on changed dependencies; preserve old artifacts and journal before ambiguous remote writes. Expose `add`, `run`, `status`, `attach` with clear human next actions.
- [x] Skill method integration: `references/course-content-workflow.md`, `references/course-rule-cards.json`, Skill entrypoint and workflow references, scenario-based evaluation record. Absorb double structure, observable shooting detail, multiple demonstrations, need moments, exploration/iteration, real questions and feedback. Keep course source page provenance; do not redistribute the PDFs. Qualify fixed rates, output quotas, audience inference and performance claims; exclude purchased comments, misleading proof and review evasion.
- [x] Root integration: forward `creative`, `feedback`, `batch` through `scripts/project.py` with help passthrough, meaningful CLI tests, complete end-to-end synthetic demo and Chinese usage guide in `docs/content-workflow.md`. Templates must be included in standalone Skill installs.
- [x] Verify baseline, focused red/green tests, all regression checks, actual synthetic media and interruption/resume exercise, report layout, install file hashes and standalone commands. Review spec completeness then code quality; fix material findings.
- [x] Prepare compatible minor version using existing versioning tool, update accurate validation notes, install both Skills into unused user Skill directories, initialize an empty user's local workspace, make a runnable demo, and deliver concise natural-language prompts plus verified command examples. Prepare the reviewed source snapshot for connected GitHub feature-branch/PR delivery; do not publish user documents or runtime data. Remote branch/PR receipts are reported separately after creation.

## Validation approach

Before each module implementation write regression tests and observe the intended failure, then implement and rerun. Core cases: wrong source/hash/segment; unsupported claim; source vs target fact separation; incomplete visual plan; untested variant; unknown creative; bad denominator/NaN/duplicate row; mismatched goals and attribution; corrupt/missing outputs; changed source; failed retry and waiting-for-review; repeated sync with no duplicate side effects. Use synthetic fixtures only.

Baseline and final command: `python scripts/run_checks.py --output outputs/checks.json`. Media changes/integration: `python scripts/run_checks.py --media --output outputs/checks-media.json`. Verify CLI demos from repo and installed paths; visually inspect generated reports. Passing structural checks never establishes real semantic, listening, remote or advertising results.

## Delivery audit

Each requirement above must point to executable behavior, tests/demo output or an installed skill instruction with a successful application scenario. Missing customer videos, facts and metrics must remain explicit input requirements; the toolkit can be completed and demonstrated with synthetic fixtures without fabricating a customer campaign. User usage covers local videos, share links, batch continuation, generating shoot/script/test tasks, CSV feedback, and explicit Feishu sync.
