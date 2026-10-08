# Final 27-Field Rules

## Contents

1. Source of truth
2. Exact field order
3. Content rules
4. Select-field judgment
5. Quality grading
6. Mandatory self-review

## 1. Source Of Truth

- The 27-field names, types, multiplicity, and allowed options are documented in `../assets/base_config.example.json`. Its resource and field IDs are placeholders, so it cannot target a live Base. Pass a real, explicitly selected profile with `--config <legacy-config.json>`; never use a profile from another project as the default destination.
- Fetch the live schema before every write. Static rules prevent drift; live rules prevent accidental option creation.
- The target is `短视频素材拆解库·核心版` / `核心素材拆解`.
- `案例素材` is an attachment field. Never put it in a record-upsert payload; upload the source video and Markdown report after a record exists.
- The original 82-field Base is out of scope and must remain untouched.

## 2. Exact Field Order

| # | Field | Type |
|---:|---|---|
| 1 | 案例标题 | text |
| 2 | 案例素材 | attachment |
| 3 | 覆盖人群 | multi-select |
| 4 | 可用类型 | multi-select |
| 5 | 身份 | multi-select |
| 6 | 素材开头类型 | multi-select |
| 7 | 内容形式 | multi-select |
| 8 | 文案类型 | multi-select |
| 9 | 跨类目对标 | multi-select |
| 10 | 原脚本文案 | text |
| 11 | 前三秒钩子 | text |
| 12 | 开头可复刻动作 | text |
| 13 | 开头高光画面 | text |
| 14 | 开头留人画面为什么留人 | text |
| 15 | 开头文案为什么留人 | text |
| 16 | 开头切入需求要点 | multi-select |
| 17 | 主卖点 | text |
| 18 | 主痛点 | text |
| 19 | 次卖点 | text |
| 20 | 次痛点 | text |
| 21 | 结构公式 | text |
| 22 | 可迁移结构 | text |
| 23 | 不可照搬点 | text |
| 24 | 可复刻脚本方向 | text |
| 25 | 赛道品类 | single-select |
| 26 | 素材等级 | single-select |
| 27 | 复刻价值 | single-select |

The writable JSON payload contains fields 1 and 3–27 in this exact order. The Markdown report contains all 27.

## 3. Content Rules

### 案例标题

Use `产品/主题｜核心钩子或成交结构`. Identify the actual product and the material's dominant mechanism. Do not use only a hash filename.

### 原脚本文案

1. Prefer reliable speech transcription.
2. Use synchronized visible subtitles to correct names, prices, product words, and ASR homophones.
3. If opening speech is absent or unreliable, prefix visible copy with `（开头画面字）`.
4. Preserve the source order. Remove timestamps and repeated subtitle frames, not spoken repetitions.
5. Mark uncertainty as `［听不清］`, `［字幕被遮挡］`, or an equally specific note. Never infer a convenient word.
6. Exclude background-song lyrics unless they function as the advertisement's script.
7. End with `（口播）` or `（混剪）`.
8. Do not include analysis, structure labels, rewrites, or compliance commentary.

### 前三秒钩子

Describe what actually appears or is spoken during 0.0–3.0 seconds: camera distance, action, product exposure, text, result, and sequence. Do not rewrite it into a better hook.

### Opening Fields

- `开头可复刻动作`: give executable actions in chronological order, preferably with arrows.
- `开头高光画面`: identify the single strongest first-three-second visual.
- `开头留人画面为什么留人`: explain visual mechanisms such as curiosity gap, reveal, contrast, sensory tension, density, identity, or instant result.
- `开头文案为什么留人`: explain audience, timing, desire/pain, information gap, proof, or price psychology. Do not merely repeat the copy.
- `开头切入需求要点`: tag only needs actually present in the opening, not benefits introduced later.

### Selling Points And Pain Points

- `主卖点`: the strongest product benefit plus visible/verbal proof.
- `主痛点`: the main pre-purchase problem the video spends the most effort solving.
- `次卖点`: secondary benefit, proof, specification, convenience, trust, or price.
- `次痛点`: a distinct secondary obstacle. Do not paraphrase the main pain point.
- Attribute unverified claims to the material with wording such as `素材宣称`.

### Structure Fields

- `结构公式`: summarize the full video chronologically with arrows. This field replaces the removed `视频内容结构框架` field.
- `可迁移结构`: identify a reusable mechanism and appropriate destination categories.
- `不可照搬点`: record AI/simulation, efficacy, medical/nutrition, children, absolute language, price, specification, endorsement, copyright, or platform-compliance risks.
- `可复刻脚本方向`: propose one direction grounded in the source mechanism; do not copy unsupported claims.

### Concision

Keep cells scannable. Put detailed evidence in the Markdown report, but do not make a Base cell vague merely to shorten it.

## 4. Select-Field Judgment

Read the contract's allowed options from `../assets/base_config.example.json`, then verify exact allowed options from the explicitly selected live schema/config. Apply these rules:

- Prefer fewer defensible tags over broad tagging.
- `身份` describes the video's narrative perspective, not the viewer's identity.
- `素材开头类型` covers only the opening, not later scenes.
- `内容形式` describes observed production formats; avoid selecting synonymous formats together without evidence.
- `跨类目对标` may include `结构复用` plus only categories that can genuinely use the mechanism.
- `赛道品类` uses the product's transaction category, not a broad audience interest.
- Never create a natural-language near-synonym when an allowed option is absent.

## 5. Quality Grading

### 素材等级

- `S级素材`: exceptional immediate hook, clear proof, tight structure, strong conversion, and low replication ambiguity.
- `A级素材`: solid hook and reusable structure with limited pacing, proof, or compliance weaknesses.
- `B级素材`: weak/late hook, unclear product logic, repetitive structure, poor evidence, or major execution risk.

### 复刻价值

- `高`: mechanism is clear, transferable, and can be recreated safely with available evidence.
- `中`: structure is useful but requires significant rewriting, reshooting, proof, or compliance cleanup.
- `低`: value depends mainly on unverifiable effects, celebrity/copyright assets, or a non-transferable accident.

Do not automatically map S→高, A→中, or B→低. Judge execution quality and replication value separately.

## 6. Mandatory Self-Review

Before writing each record, confirm all of the following:

- Every opening claim matches a frame from 0–3 seconds.
- Every script sentence comes from reliable speech/subtitle/screen-copy evidence.
- Unclear words are marked, not guessed.
- Main/secondary selling points and pain points are distinct.
- Visual-retention and copy-retention fields explain mechanisms.
- Structure formula covers opening, middle proof, conversion, and CTA in actual order.
- Select values exist in both static config and live schema.
- AI/simulation watermarks and risky claims appear in `不可照搬点`.
- Prices, quantities, brand history, ingredients, and efficacy claims are attributed and not silently treated as facts.
- Material grade and replication value have a written rationale in the report.
- The payload validates with zero errors.
- Readback matches all 26 content fields and contains both source video and report attachments.
