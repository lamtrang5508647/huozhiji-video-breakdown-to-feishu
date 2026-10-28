# 语义段切口预检

既有视觉分组不一定刚好落在口播句间：切口可能打断一个词或把同一句拆开。使用实际原片、带 `semantic_groups` 的候选时间线，以及来源相同且带 `segments[].start_sec/end_sec/words[]` 的机器转写，先生成只读风险报告：

```sh
python scripts/check_semantic_boundaries.py --source <原片> --timeline <候选时间线.json> --asr <机器转写.json> --output <新的风险报告.json>
```

工具计算原片、时间线、转写 SHA-256；核对视频 ID、双方声明的源文件哈希、声明的时长和分段连续性，并逐切口记录：`machine_word_overlap`（机器词时间跨边界）、`machine_segment_overlap`（机器句时间跨边界）、`no_machine_overlap_detected`（未检出机器重叠）或 `not_determinable_invalid_asr_times`（没有检出重叠且存在缺失、异常或不可信的时间）。所有状态均要求实际听校。格式与哈希通过不证明转写文本、识别时间、源时长或语义真实。

用切口前后的连续声音与画面实际试听，判断是否截词、断句或需保留上下文；必要时调整边界并重新复核、重新计算来源与产物绑定。机器时间可能偏移、漏词，**没有重叠不代表安全**；异常时间不应被当作无问题。无法听校时保留候选状态，不自动将现有分段标为可交付。报告仅做风险提示，不修改源片、候选时间线、转写或飞书。
