---
name: analyze-folder-videos-to-feishu
description: 对带货视频做原声转写与校对、整片脚本框架和完整语义段拆解，输出短语标色、可复用公式、情绪依据及逐画面片段，按产品平台归档飞书。用于视频拆解、脚本复刻和素材整理；原27字段模板需明确选择。
---

# Folder Video Breakdown To Feishu

Process videos one at a time from the user's explicit source and target. Keep analysis grounded in source evidence, validate against the live Base schema, and fail closed instead of inventing content.

## 执行入口：先固定交付再展开分析

默认每条视频交付两层：完整脚本框架/连续语义段，以及逐画面视频素材库。**文档存在不代表素材已交付；关键帧不能代替视频。**

后续新做的带货视频完整脚本拆解，默认采用 [千川深拆方法](references/qianchuan-deep-breakdown.md)，新 authoring 使用 `analysis_mode: qianchuan_deep`，无需用户再次提出千川。仅要逐画面清单时按该清单交付；已保存的旧章节不自动改写。

**默认脚本拆解包含三项文字交付，不能只交素材切片：**

| 交付 | 内容和边界 |
| --- | --- |
| 原声转写与校对 | 完整口播原文、带时间的转写记录和校对状态；原文与画面字幕分别保存。未听校只能标为草稿，听不清的词保留疑点。 |
| 短语标色脚本 | 按完整语义段连续阅读，逐短语判断功能并显示颜色图例；每段保留时间范围和画面编号，源文字符不因标色而改变。 |
| 内容脚本框架 | 整片的原片结构公式、可复用变量公式，以及每段的功能序列、情绪依据、填空句和拍法；原片分析与新产品改写分开。 |

先按 [script-segment-framework.md 的文字交付流程](references/script-segment-framework.md#文本拆解转写标色与框架输出) 完成原声转写/来源校对，再分析框架与短语功能。已有可信转写时复用并核对其源片身份；需要机器草稿时使用 [offline_asr.py](scripts/offline_asr.py)。ASR 的 `transcript.json` 与 `transcript.txt` 成对保留，不能只交 TXT 后宣称已经校对。缺少音轨、ASR 运行时或实际听校能力时，明确受阻项，先交可核实的字幕摘录与画面分析；不得把摘录改称完整原声转写。

**新增脚本与镜头对齐交付。** 新做的完整拆解按 [script-shot-alignment.md](references/script-shot-alignment.md) 将每个脚本段与其相交画面逐行对照，给出源时间、段内/镜头内偏移、原字幕、段级仿写槽位及对应组级拍法/剪辑建议。原文、仿写和源观察分别展示；跨段镜头保留各段真实交集，空字幕不补文。`export_script_v3.py` 默认导出对齐 HTML/JSON/CSV；彩色脚本 HTML 用 `render_script_analysis.py --manifest <script-manifest.json>` 接入同一对齐表。新 authoring 的 `sections-content.json` 写 `script_shot_alignment: true` 启用 Docx 对照；旧章节无该标记时保留已有格式，不自动远端改写。此粒度是段与镜头交集，不代表逐句口播已校准。

若项目有 `config/presentation-baseline.json`，读取其中第一条样板的确切视频身份与章节锚点；不要靠列表顺序猜样板。仅复用格式，具体脚本和品牌仍来自当前原片。

先读项目配置和最新状态 → 完整看源片证据 → 转写并核对原声/字幕来源与校对状态 → 分析整片框架和完整语义段 → 上下文短语标色与内容复核 → 审逐画面时间线 → 固定程序排版 → 按授权导出两类视频并上传 → 飞书实际回读。业务产品、原片品牌、视频来源分别记录。

委派给较小模型时，先读 [execution-quality-contract.md](references/execution-quality-contract.md)，做有来源证据的小样并独立复核；通过后再展开。不能让模型自行宣布与其他模型能力相同。

**仿写交付前逐版整稿复核。** 按 [仿写交付规范](references/imitation-authoring.md) 对照原稿、完整改写、框架与镜头；按 [故事逻辑复核](references/story-logic-review.md) 实际阅读每版口播、核对人物关系、行动因果、经历归属及镜头，填写绑定当前稿件的审核记录并运行 `scripts/story_logic_review.py`。没有独立审阅者时如实记录同人第二遍复核；程序只检查记录和引用，不判断语义或事实。

**原稿连续标色阅读。** 新生产的分析在 `analysis.json` 明确设置 `layout_profile: "source-reading/v9.2"`，然后调用既有渲染、发布与回读流程；新增 H2「原稿｜标色阅读」，仅连续展示现有来源文本及其短语颜色，来源和校对状态单独说明，不把字幕摘录充作听校原声。既有输入不设置 profile 时维持原布局。渲染、发布及修复工具均可选 `--layout-profile source-reading/v9.2`，但整个流程必须一致；推荐写入 analysis 以便审批哈希和回读持续绑定。源文文本与已审阅的范围、颜色不能改写。

脚本段输入先运行 `validate_section_content.py`：绑定当前时间线哈希，逐段具体写填空句和拍法，实际情绪事件须落在所引镜头内。新数据显式区分实际变化、无明显变化和证据不足，旧四阶段数据保持兼容；不强补转折，也不截断事件。重复整段套话、旧镜头引用、状态不一致均退回；通过此检查仍需独立内容复核。

**句内标色使用 v9 合约与 9.1 上下文复核。** 先读 [phrase-annotation-v9.md](references/phrase-annotation-v9.md)、[context-calibration.md](references/context-calibration.md) 和 [context-review.md](references/context-review.md)。每条字幕、整片公式和段内公式必须明确审阅短语功能及其上下文，再交给统一渲染器；禁止用镜头编号范围、段标签、关键词或缺省颜色覆盖整句。逐句记录阅读理由、相邻语境与保留/修改决定；9.1 发布须提交绑定当前输入哈希的 `context-review.json`，不能仅写“全部已审核”。`phrase_annotation.py` 和 `context_review.py` 只检查来源、字符、标签与审阅记录，不判定含义正确。执行模型不得把结构检查 PASS 改写成“语义审核通过”；发布前监督者须裁定争议、核对原文和公式及跨视频同类表达。钩子与惊讶反应、信息与评价、出处与结果、已发生体验与未来计划分别判断；不得仅因处在开头就整段标红，也不得把灰色当难句的缺省颜色。

默认章节模板为（可在项目配置中另行约定）：视频原标题 **H1**；“原片结构公式 / 可复用变量公式 / 句内功能颜色图例” **H2**；每个完整脚本段“段01｜段名” **H2**。段内保持元信息 → 连续标色原文 → 功能序列 → 带依据的情绪推进 → 源画面范围 → 复刻公式和拍法。不能用加粗正文冒充 H2，也不能把观察注释混入字幕。

当前默认简化章节不显示三个整片/复刻板块：`不可直接照搬`、`整片受众假设（非测量）`、`复刻素材清单`。源 analysis 中相应字段保留用于校验；明确要求其中某项时，另行交付该项。段级 `受众反应假设（非测量）`、情绪依据、原文、公式、拍法、H1/H2、时间和素材库入口继续保留。

**用 `render_platform_chapter.py` 生成格式，再核验飞书实际保存的逐字样式。** 用现有产品×平台汇总文档，不复制新文档。文档顶部和视频章节提供“素材资产库·视频片段”入口；用 `verify_material_gallery.py` 确认全部上传素材实际进入正确画廊。详细说明见 [golden-layout-and-delivery.md](references/golden-layout-and-delivery.md)。内容判断仍要复核；这些程序统一格式和交付验收。

## Start a new project

Keep runtime data outside this Skill. For a local-only task, create a dedicated job directory and use reviewed manifests without a Feishu login. For a new Feishu target, use [assets/project_profile.example.json](assets/project_profile.example.json) as a schema reference only: replace all placeholder IDs from the selected live Base, retain field option metadata in separate snapshots, and choose the actual working `bot` or `user` identity. Keep credentials in the CLI authentication store. Start the document registry as `{"schema_version":1,"base_id":"<actual-base-id>","groups":{}}`; a new empty registry does not mean anything has been published. Create and register a product/platform document only when the first reviewed chapter is ready. The repository distribution additionally provides `scripts/project.py init/doctor/demo/install-skill`; that root convenience CLI is not part of a standalone Skill installation.

## Continue An Existing Project

For a packaged project or a request to continue earlier breakdowns, read [references/project-continuation.md](references/project-continuation.md). Load the project's explicit configuration and live registry before making a new document or record. A machine-local `agents/local-project.json`, when present, is only a locator for the matching project; the user's current target takes precedence. Never infer authorization to write another Base from a saved profile. The project preflight is `scripts/check_project.py --project <project-root> --live --output <receipt.json>`; the legacy `check_environment.py --config <legacy-config.json> --live` is not the default project check.

The two linked deliverables are complete script analysis/semantic segment clips and a numbered shot-material library. Document updates and Base upload are agent-operated through current Feishu tools, not an unattended end-to-end pipeline. Read [references/document-access.md](references/document-access.md) before creating or transferring a project document so the intended human can edit it.

## Choose The Workflow First

用户要结合千川信息流带货深入拆解、复刻脚本或提升素材调用时，先读 [references/qianchuan-deep-breakdown.md](references/qianchuan-deep-breakdown.md)。在原文、语义段、逐画面基础上分析购买情境、信息推进、主张与展示材料、声画关系、行动承接及有条件的复用；不要只加标签或套统一销售公式。用既有字段和 `analysis_mode: qianchuan_deep` 显示具体问题、作用、论证/声画关系和复用条件，不新增逐视频文档。情绪按实际变化记录，原作者主张不等于证据已验证。纯整理与深拆不进入 AI 任务台、生成服务或广告投放。

用户明确要求新建产品 AI 素材任务子表、倒推逐镜头提示词并保留产品参考与生成结果时，读 [references/ai-material-taskboard.md](references/ai-material-taskboard.md)。仅整理任务不提交生成；缺失本品包装/剂型参考不妨碍建表和写条件提示词，但必须保留具体待补状态。新表是明确要求的生产任务台，不自动扩展为全库复制。

用户要整理已有素材库或区分“剪辑复用”和“视频模型重制”时，读 [references/material-reuse-management.md](references/material-reuse-management.md)。逐素材核对可见内容与原片语境，两种用途分别标注，复用现有素材ID、附件和产品平台画廊；适合生成的判断不等于已经生成验证。千川选材另标用途与优先级，独立表情包/趣味转场归档，叠表情的有效实拍按主体保留；同类镜头挑代表。获授权配置归档排除后，素材验收显式传 `--archive-zone 表情包归档`，同时核对归档片段附件与关联，不能将归档误报为丢失或假报为画廊可见。

When delegating execution or reviewing inconsistent results, read [references/execution-quality-contract.md](references/execution-quality-contract.md). Keep script segments, shot groups, and actual numbered shots separate. Resolve every candidate cut in a source-bound ledger and run `scripts/validate_execution_quality.py` on corrected revisions; this structural gate does not certify the model's visual judgment. Validate real examples before claiming an improvement, and never claim an unavailable model was used.

To check whether each numbered shot's description matches its midpoint image, use [逐镜中点内容复核](references/midpoint-content-review.md): an independent reviewer actually views and records each midpoint; the optional validator checks evidence binding and the declared decisions only, not the truth of the descriptions or approval to publish.

When a timeline supplies `source_group_spans`, independently check the exact per-shot intersections with the declared semantic-group intervals using [镜头与语义段的时间交集检查](references/shot-group-intersection.md). Keep the single primary `group_id` distinct from cross-group spans; missing spans are not a pass. This optional read-only check proves only consistency of declared times, not actual footage, semantic accuracy or publication readiness, and does not change the existing export or Base ownership rules.

For consistent document formatting and discoverable assets, read [references/golden-layout-and-delivery.md](references/golden-layout-and-delivery.md). Use the deterministic chapter renderer and actual saved-XML validation; do not let an executor substitute bold paragraphs for required H2 headings. Use the generic plan/sync/gallery-verification helpers for the two deliverables. Document publication alone is not completion of the material library.

For requests to understand and adapt a complete video, start with the whole-video framework and complete semantic script segments before going down to reusable shot groups and single shots. Read [references/script-segment-framework.md](references/script-segment-framework.md). Preserve source claims and transcript evidence separately from adaptation suggestions. For direct requests for a visual shot inventory without script-segment planning, use [references/reviewed-timeline-workflow.md](references/reviewed-timeline-workflow.md).

For script-led whole-video breakdowns, include the phrase-colored reading view and source/reusable formulas by default unless the user requests a simpler output. By default, group results into one reusable Docx per stable product-ID × source-platform key; read [references/phrase-color-script-analysis.md](references/phrase-color-script-analysis.md) and [references/platform-docx-aggregation.md](references/platform-docx-aggregation.md). The source-video and script-segment records remain the canonical evidence and navigation records. The older direct-in-Base PNG workflow remains available only when explicitly selected; see [references/base-native-script-preview.md](references/base-native-script-preview.md). A Docx is a presentation layer over reviewed source evidence and does not replace the segment manifest, transcript provenance, or claim checks.

For requests for 逐画面拆解、时间线、画面编号、镜头素材标签 or “先拆解再切片”, read [references/reviewed-timeline-workflow.md](references/reviewed-timeline-workflow.md). The shot workflow remains the evidence source for numbered single shots; do not run `detect_and_split.py` first and describe automatic cuts as a completed breakdown.

The original 27-field folder-report workflow below is a separate schema profile. Use it only when the user selected that configured Base or explicitly asked for that template. All legacy scripts require an explicit `--config <legacy-config.json>`; there is no bundled default target. `assets/base_config.example.json` documents the 27-field contract with placeholder resource IDs. Supply an explicit config matching the selected live schema. Never force the legacy 27 fields onto an unrelated table.

## Hard Rules

1. Inspect locally extracted frames with the executing agent's available vision capability. Do not upload private media to another provider or invoke a paid service without task authorization. Record the actual tool/model and evidence inspected; do not infer source facts from another chat's memory.
2. Do not modify, rename, or delete source videos. Write all derived files to a separate job directory.
3. Do not assume “latest files” when the user names a folder. Enumerate that folder deterministically and report the exact file list.
4. Resolve the user's current Base URL and read its live tables and fields first. Read [references/field-rules.md](references/field-rules.md) and [assets/base_config.example.json](assets/base_config.example.json) only for the original 27-field profile; they do not override the selected target. For legacy scripts, explicitly pass the user's matching config.
5. Use verified select options. If schema/tag optimization is authorized, explicitly create the agreed option set and reread it before use. Never rely on record writes to auto-create options. Stop on unhandled schema differences.
6. Keep `原脚本文案` transcript-only. Do not insert analysis, reconstructed sales copy, timestamps, or summaries. Mark unclear words explicitly.
7. Do not mark unverified content complete. For the original profile, skip records whose required transcript, opening evidence, or fields cannot be verified. For visual timelines, label subtitle excerpts and audio-review status separately; a subtitle excerpt is not a complete or audio-corrected transcript.
8. Write sequentially. Wait at least 0.75 seconds between records. Never run Base list/write operations concurrently.
9. Reuse the existing working `lark-cli` identity/profile and verify access by reading the selected Base. Do not switch authentication or start a new login merely because an old script assumes `--as user`. Never package credentials, access tokens, or app secrets.
10. Only enable a sync script's write mode when the user explicitly requested Feishu ingestion. Legacy helpers use `--write`; the Base preview helper uses `--apply` plus the reviewed manifest SHA-256. Otherwise keep the run as a dry run. Prior authorization in the active task carries forward; do not request it again just because a helper is invoked.
11. Read visible packaging, logos, and captions when checking brands, names, quantities, or technical terms. ASR homophones and contextual guesses cannot justify replacing a brand. If audio was not actually reviewed, do not claim the script was corrected against the audio.
12. The legacy splitter's handwritten `edit-list.otio.json` has not been verified as an editor-compatible project. Do not promise OTIO, Premiere, or DaVinci import compatibility without a real parser/import test. The reviewed exporter produces validated clips, thumbnails, CSV, and JSON; it makes no editing-project compatibility claim.
13. For phrase-colored script delivery, default to one Docx per stable product-ID × source-platform group, updated in place by video identity; never create one Docx per video or rewrite the full document to update one video. Keep existing source/video and script-segment records canonical, with links to that video's H1 and segment H2 anchors. Do not create tables or views for routine script delivery; an explicitly requested AI task child table uses references/ai-material-taskboard.md. Retain the video gallery cover, product/platform navigation, source script, source media, and prior user attachments. Preserve the optional direct-in-Base PNG workflow and its installed helpers for requests that choose that delivery; never delete older PNGs during the Docx migration.

## First-Run Preflight

Resolve this skill's directory and invoke scripts by absolute path. Use `python3` on macOS or `py -3`/`python` on Windows.

For the reviewed-timeline workflow, use its reference preflight and the user's live target. The following legacy check uses the original schema profile and must not be run as a target check for another Base:

```text
python scripts/check_environment.py --config <legacy-config.json> --live
```

Check dependencies for the requested stage only; local analysis does not require Feishu login. If a required dependency or permission is missing, read [references/portability.md](references/portability.md), report the blocked stage, and continue independent local work. Do not claim a remote delivery until readback succeeds.

## Product-Platform Aggregated Script Docx

Use [references/platform-docx-aggregation.md](references/platform-docx-aggregation.md) for all default phrase-colored script delivery. Create or reuse one Docx for the stable product-ID × source-platform group, and upsert only the current source-video chapter. The document is titled `<产品>竞品｜<平台>拆解`; each video H1 remains its exact original title and is paired with a stable video ID/BV ID or source URL in metadata so duplicate titles remain distinguishable. Do not create an empty document before the first reviewed video is ready.

Preserve exact source subtitle excerpts, color annotations, source and reusable formulas, segment formulas, emotion evidence, and source references. Label excerpts and audio-review status honestly; do not overwrite source transcript fields or present source claims as verified facts. Use persistent document-token and chapter-anchor mapping, verify against live Docx XML before each update, skip unchanged chapter hashes, and update only that video chapter. Keep the main and segment Base records linked to the matching H1/H2 anchors. Do not alter source attachments, video gallery cover, or other records' chapters.

The direct-in-Base PNG flow remains an optional alternative when explicitly chosen. Its installed renderer and capture helpers remain available; preserve prior PNG previews and attachments during this migration. A Docx update must not silently delete or replace them.

## Explicit Legacy Profile Only

Only when the user selected the original 27-field schema, read [references/legacy-folder-workflow.md](references/legacy-folder-workflow.md). Preserve explicit target/config selection and do not apply that report template to the current product/platform project.

## Portability

Follow [references/portability.md](references/portability.md). Use absolute skill paths and subprocess argument arrays; verify existing dependencies rather than silently downloading models.
