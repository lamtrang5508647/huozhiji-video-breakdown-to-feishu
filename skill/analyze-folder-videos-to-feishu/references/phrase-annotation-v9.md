# v9: Explicit Phrase Review And Consistent Display

Use this contract for all newly authored or repaired script presentations. v8 is
readable for migration; it must not be used to bypass v9 publication review.

## Meaning before color

Read the unit with the preceding and following source units. Describe what the
words do here with a specific Chinese `function_tag` and a context-based `reason`.
Use one contiguous run per meaningful role; one role may legitimately fill a
whole sentence. Never require a number of colors or split every particle.

| Example | Correct distinction |
|---|---|
| 我又买了一瓶 | 本人复购经历，黄色；不是向观众发出的 CTA |
| 链接我放评论区了 | 面向观众的入口，粗体下划线 |
| 希望真的有点用吧 | 期待/待观察，灰色；不是已出现效果 |
| 没有一点作用 | 无效体验，完整黄色；保留否定 |
| 好像有点变化了 | 限定变化自述，连同“好像”保留；不抽成肯定疗效 |
| 我觉得又被割了 | 疑虑/情绪，灰色；不是可核研究 |
| 去医院做了检测 | 出处/方法话术，紫色；检测真实性仍未核 |
| 显示长出两三根 | 结果主张，黄色；不能自动证明产品因果 |
| 锯棕榈可抑制DHT | 成分指向绿色＋作用主张黄色；主张未核实 |
| 评论区链接＋优惠券 | 实际入口粗下划线＋优惠橙色 |
| 祝你头发强韧 | 祝愿收束灰色；没有行动请求就不标 CTA |

这些例子是审阅校准案例，不是关键词分类规则。人物故事中的动作、
面向观众的指令和原作者效果主张必须分别判断。情绪证据、事实真伪与
功能标签是三个不同维度。价格顾虑可以橙色，但普通选择动机不自动
成为优惠；缺少实际促销时不补促销槽位。

## Frozen v9 schema

Set `annotation_profile: phrase-functions/v9`. Obtain the full legend from
`phrase_annotation.canonical_taxonomy()`; do not write custom labels/colors.
HTML and Docx use the same red/blue/green/purple/orange/gray families. Experience
uses bold light-yellow background; CTA uses bold underlined dark text. No
positive/negative sentiment meaning is assigned to these colors.

Each unit's runs cover its exact `source_text`, with Unicode offsets, family,
specific fine function and rationale. No shot/section family inheritance or
first-substring-match override is allowed. A reviewed input packet binds the
exact analysis SHA-256, unit IDs and source text; `apply_phrase_review.py` rejects
missing, extra, repeated or stale units. It never fills gaps with default colors.

Each source/reusable formula step supplies `phrase_runs` covering its own `label`
and `source_unit_ids` in that section. A compound step can remain concise for
readability, but must color each actual function separately. Every section's
`formula_runs` covers its formula and equals the corresponding content detail.
Formula references prove identity, not that the formula interpretation is right.

Preserve negation, uncertainty, conditions and quoted-speaker scope. To crop a
reading span, supply explicit `reviewed_scope` with the source-text UTF-8 hash,
exact kept text, reason and approval. The checker validates freshness only; a
supervisor must check that the excluded text does not change meaning. Observation
notes stay separate from source script in display and copy operations. Preserve
uncertain DHT/DHC/DHD source spelling with a note instead of correcting by guess.

When a later subtitle depends on an earlier conditional or quoted statement,
record that dependency explicitly in the run's `context_unit_ids`, and describe
the premise in its fine function/reason. The checker rejects stale IDs but cannot
decide the reference's meaning. In a source formula, write “条件下的停用主张”
instead of removing “如果…” and summarizing it as an unconditional guarantee.

## Independent review and delivery

1. Worker annotates every unit and formula explicitly against the frozen source.
2. Supervisor reads the proposal, checks the above confusions and formula/source
   order, corrects mistakes, and records the reviewed analysis/manifest hashes
   and complete unit IDs. An executor must not generate its own semantic PASS.
3. Run both renderers and v9 structure validation. Structural validation returns
   `semantic_verdict: not_assessed`; it cannot establish equivalent model quality.
4. Publish only this video in the existing product/platform Docx. Use the exact
   supervising review receipt with `--semantic-review` for v9 publication/repair.
   Preserve chapter/segment H1/H2 links, videos and attachments.
5. Read the actual saved Docx XML and compare every character's effective style,
   including which character is colored. Merely finding all expected colors
   somewhere in the paragraph is insufficient.

Single-color proportions and repeated rationales are signals to reread, not
automatic evidence of wrong labels. Run the real changed path after upgrading;
report layout/style checks, semantic review and source-audio/claim verification
as separate scopes. Never claim a requested model was used when it is unavailable, nor
promise that rules make every weaker model equivalent to the supervisor.

## 9.1 context review gate

For all new writes, also read [context-calibration.md](context-calibration.md)
and [context-review.md](context-review.md). The v9 schema remains compatible;
publication now requires `context-review.json` beside the reviewed analysis.
Bind its byte SHA-256 in the supervising receipt's `context_review_sha256`.
`publication_review.py` validates each unit's actual reading decision, context,
text/run hashes and all formula review records, and blocks unresolved decisions.
An older global receipt is historical evidence, not sufficient approval for
new publication. Record-integrity success still cannot certify semantic truth.
