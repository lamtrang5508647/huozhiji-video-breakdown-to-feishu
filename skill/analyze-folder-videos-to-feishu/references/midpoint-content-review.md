# 逐镜中点内容复核

用于已经确定候选画面区间、需要检查“描述是不是写到了相邻画面”时。此复核与边界、完整画面、音轨和内容审批分开；独立审阅优先选没有写候选描述的审阅者。先从哈希确认的原片提取每镜实际 PTS 的中点帧，亲自看每张图及必要的相邻/连续帧，逐条比对当前描述；不能仅因文件存在、关键词类似或一整页抽样已生成就声称看过。对照无法证明的描述选 `uncertain`，明显不符选 `needs_revision`；仅中点可见内容确实支持当前描述时选 `confirm_midpoint`。观察内容只描述可看见的东西，原作者主张和实际事实分别保留。

保存记录为独立 JSON，保留完整 timeline 和 interval-evidence 文件 SHA-256、实际原片 SHA-256、视频 ID、作者和独立复核者身份声明。每条写 `shot_id`、与 timeline 完全相同的 `description`、该镜 `middle.sha256` 与 `middle.actual_pts_ms`、具体 `observed`、判断 `reason` 和 `decision`。所有镜头各一条，保留原始 evidence 的路径；局部复核须标明未覆盖，不能伪造整片 `confirm_midpoint`。不同镜头合法共用同一源帧时不要仅凭同一图片哈希判错。

修改描述或调整、合并镜头边界时，不能沿用旧镜头的字幕、标签或证据已核状态：保留旧值供追溯，逐字段重新对照新范围确认，尚未复核的移至待复核或清除当前已核标记。边界变化后重新按新范围提取中点并重新审核；原中点引用不能自动证明新镜头。中点描述通过不证明字幕摘录、标签、整段内容或边界正确；复核中点后仍需独立逐项确认，不能只把总 `reviewed` 改为 true。

校验器会读取原片解码帧 PTS，按现有提取器的时间选择规则核对中点帧时间。它不验证图片像素确为源片该帧，也不证明观看、语义或人物判断真实；缺少可用 PyAV 或源片时应阻断并明确未验证。

```json
{
  "schema": "midpoint-content-review/v1",
  "video_id": "example-video",
  "source_sha256": "实际原片SHA256",
  "timeline_sha256": "待审时间线文件SHA256",
  "evidence_sha256": "interval-evidence.json文件SHA256",
  "author_id": "候选描述作者",
  "reviewer_id": "独立审阅者",
  "review_mode": "independent",
  "scope": "midpoint_only",
  "shots": [{
    "shot_id": "shot-001", "description": "与待审时间线逐字一致的描述",
    "frame_sha256": "该镜中点帧文件SHA256", "actual_pts_ms": 500,
    "observed": "画面中实际看到的内容", "reason": "中点与描述的具体对应关系",
    "decision": "confirm_midpoint"
  }]
}
```

完成实际审阅后运行：

```sh
python scripts/validate_midpoint_content_review.py --timeline <候选时间线.json> --evidence-index <interval-evidence.json> --review <审阅记录.json> --output <新回执.json>
```

工具复用 `validate_interval_evidence.py` 检查原片关联、帧哈希、实际 PTS 与区间，并核对每镜、描述、身份声明和当前文件哈希；若待修、未决、漏镜、来源或内容变化，报告 `BLOCKED`。它不能鉴定审阅者身份、证明看过图或判断描述真假，结果明确为 `PASS_STRUCTURE_ONLY`，不等于完整镜头、语义、字幕、原音或发布批准。声明的独立审阅者须与作者不同；无法做到时应如实保留未完成，而不是伪装独立。需修稿时保留旧候选与差异，修订后重新提取/绑定与实际复核，不得沿用旧 hash 作为新稿审阅。

### 同一提取器的可选 JPEG 对照

如果中点证据使用 `extract_interval_evidence.py` 的 JPEG95 输出，可追加 `--strict-current-extractor`。工具以当前环境的 PyAV 解码对应帧，并用当前 Pillow/JPEG95 重新编码，要求字节 SHA-256 与证据 JPEG 一致。结果为 `matched` 仅说明**当前解码和编码规则下各镜字节一致**；缺少审核记录时为 `partial_or_unverified`，并报告已对照与应对照镜头数。静止或重复画面在不同 PTS 可以相同，不能据此证明帧唯一、审阅者实际看过或语义正确。模式会额外报告 `frame_time_uniqueness: not_assessed`。

此模式需要当前环境的 PyAV 和 Pillow/JPEG 编码；同帧 PNG、其他 JPEG 质量、元数据或跨库版本编码可能不同。若不匹配，输出 `mismatch_or_unverified` 并阻断该严格模式，不应据此断言证据伪造：可用本机提取器在新目录重新取帧、人工核对并按新证据重新审核。未指定选项时维持原有兼容行为，原模式仍不能证明证据像素来自源帧。
