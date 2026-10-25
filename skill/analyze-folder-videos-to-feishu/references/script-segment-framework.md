# Whole-Video Framework And Script Segments

Use this mode when the user needs to understand a source video, choose a complete passage, or adapt its storytelling. Start from the reviewed source and build in this order:

**whole-video framework → complete semantic script segment → shot group → single numbered source shot.** A visual cut alone does not create a new script segment. Keep connected arguments, paired comparisons, actions, and evidence together until the spoken task is complete.

## 文本拆解：转写、标色与框架输出

此流程用于默认的完整脚本拆解；只要求画面清单的任务仍走逐画面流程。转写、标色、框架是同一条源视频的三项交付，不能用其中一项代替另两项。

1. **先准备原声文字。** 核对源视频身份、SHA-256、音轨和实际时长。已有转写先核对其来源与覆盖范围；没有可信转写且具备本地 ASR 时，运行下面的草稿命令。画面大字、字幕、口播可能不同，分别记录。ASR 时间只能作定位候选，不能直接当成逐字准确的剪辑边界。
2. **逐句校对并留下状态。** 对照原声复核漏字、同音词、断句、说话人、品牌、成分、价格、数量、百分比及否定词；画面包装与上下文作辅助证据，不能靠常识补齐。听不清的地方保留明确疑点。保留原始草稿，校订版另存，并记录源音频/源视频哈希、转写版本、复核范围、修改和未决项。JSON 中的状态要随纯文字一并交付。`asr_draft`、`human_verified: false` 不得因程序通过而升级；真实逐句听校完成后，按下文的 `audio_review_evidence` 契约记录证据，只对实际复核的范围作完成声明。
3. **按内容分析框架。** 从当前源文和原片证据总结整片实际顺序，再拆完整语义段，保留同一论点、对比、原因解释和证据链。每段注明脚本功能、源时间范围、相交画面编号，并写出具体的功能序列、可观察情绪及其依据、复刻填空句和拍法。原片结构公式忠实记录真实顺序；可复用变量公式写成可替换槽位，缺少产品事实或证据时保留待补槽位，不借用原片人物经历。
4. **再做短语标色与固定排版。** 使用 [phrase-color-script-analysis.md](phrase-color-script-analysis.md) 与 v9/9.1 上下文合约，按短语实际功能标色，保留连续原文和图例。完整转写独立保存；当前呈现器的 `text_kind` 支持 `subtitle_excerpts`、`user_provided_copy`，不可把口播草稿伪装成字幕摘录或自造枚举。字幕与口播不同处明确标注，不静默替换已经绑定来源的 `source_text`。默认文档继续使用视频原标题 H1、公式/图例/语义段 H2，按产品 × 平台复用现有文档。

在仓库根目录生成机器草稿的示例（`<job-dir>` 为独立任务目录；本地模型和 ASR 可执行程序须已配置）：

```text
python skill/analyze-folder-videos-to-feishu/scripts/offline_asr.py <source-audio.wav> --engine openai-whisper --model <local-model.pt> --output-dir <job-dir>/asr-draft
```

whisper.cpp 路径改用 `--engine whisper-cpp --model <local-model.bin> --whisper-cpp <executable>`。程序生成原始引擎 JSON、`transcript.json` 和 `transcript.txt`，规范化 JSON 明确标为 `transcript_status: asr_draft`、`human_verified: false`；它不执行逐句听校。缺少依赖、音轨或听校能力时报告受阻项，字幕分析可继续，但完整转写/原声校对仍为未验证。不要为了得到“完成”状态自动下载模型或编造口播。

交付和写回时，完整原文只放在项目已有的原脚本字段或本地转写文件，时间定位和状态保存在配套记录；不混入分析标签、公式或改写句。标色分析、两类公式及段落指导写入当前视频的分析章节，素材片段继续链接源视频与语义段。已有字段由项目映射决定，此流程不自动新建重复表格、视图或逐视频文档。

新完整拆解同时交付[脚本与镜头对齐表](script-shot-alignment.md)：每行来自已有段落与镜头的真实时间交集，显示原字幕、段级句式槽和对应组级拍法，保留一段多镜头及一镜头跨段关系。彩色 HTML 渲染时传 `--manifest`；Docx authoring 设置 `script_shot_alignment: true`。不要另造重复原文或把交集时间当逐句音频时间。

## Evidence And Boundaries

- Capture the source file SHA-256, exact duration, original source-video record (when supplied), and the existing reviewed shot manifest. Never modify the source or the old shot assets.
- Require `source.timeline_manifest_path` and its 64-hex `source.timeline_manifest_sha256`; verify that the timeline's `video_id` and source SHA match the script manifest and actual source file. A same-named shot such as `画面001` from another video is never interchangeable.
- Keep the full-video framework grounded in the source: audience positioning is a hypothesis, source claims are unverified unless separately supported, and source ordering/time is distinct from any proposed adaptation ordering or runtime.
- Keep source script text separate from analysis and rewritten lines. A readable subtitle excerpt is only an excerpt. Machine ASR or word timings can locate candidate sentence boundaries; they do not establish that the words, names, tone, or full transcript have been human-checked.
- If a segment edge crosses a numbered shot, retain every actual shot intersection as `source_shot_ids` and `source_shot_spans`. Record only the contextual overlap in `boundary_context`; do not assign the entire neighboring semantic group, source structure, or its shot-group advice to the segment.
- Validate that the segments are ordered, continuous, non-overlapping, cover the intended source range, and preserve source hash and duration. Do not shorten a sentence to force the old shot partition.

## Analysis Shape

分析时按 [课程方法的双结构记录](course-content-workflow.md#1-用两条线解释一个完整语义段) 分开文案逻辑与画面动作，再写声画关系；一段多镜头仍以真实交集对齐。新片制作请求按该参考生成独立的选题、脚本、拍摄或测试卡，记录本品事实、候选机制、执行条件和未决项，不覆盖下面的源片字段。课程建议的探索数量、成片公式和效果标签不改变完整语义段边界或默认报告显示范围。

The video-level record should state the source strategy, ordered argument/story framework, source transition logic, reusable structure, adaptation outline, required evidence, claim boundaries, and review status. Keep the source framework and proposed adaptation in separate fields.

Each script-segment record should include:

- stable ID, ordinal, name, source time range, primary semantic source groups, source-shot IDs and exact intersection spans;
- source excerpt with per-shot provenance and an explicit `subtitle_excerpts_only` type unless a transcript has separate human review evidence;
- the segment's source role, source structure, central question, and source claims;
- observable source-emotion events with their actual time and shot/subtitle evidence; new records explicitly distinguish observed changes, no clear change, and insufficient evidence. Legacy reviewed **starting expression → trigger → turn → landing** records remain compatible; do not invent four stages for a neutral passage;
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
