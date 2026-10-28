# 候选画面多帧审阅工作表

候选画面还未取得正式批准时，可以将已有源视频、候选时间线和当前版本的 `interval-evidence.json` 整理成逐镜工作表。先在同一项目根目录保存原视频、时间线和由 `extract_interval_evidence.py` 提取的证据；证据清单必须绑定本次时间线，不能沿用修改前的记录。

```sh
python scripts/prepare_candidate_review_sheet.py \
  --root <已有项目根目录> --timeline <候选时间线.json> \
  --evidence-index <interval-evidence.json> --output <根目录内的新目录>
```

输出 `candidate-review.md`、`candidate-review.csv`、`review-events.json`。每行有候选描述、字幕摘录及其原有状态声明、标签、区间、开始/中点/末尾实际 PTS 和对应帧链接；从第二镜起另有切点前后帧，原视频也可从 Markdown 打开。所有素材必须在所选根目录内，新目录不可覆盖既有文件。`review-events.json` 可导入 B站剪辑 Skill 的 `assets/local-review.html`，在浏览器选择同一个源文件核对哈希，再逐段播放并由审阅者填写；导出的人工作答仍是声明，不能证明真实看听或内容正确。

工具实际核验源文件 SHA-256、时间线和证据绑定，并复用 `validate_interval_evidence.py` 核验帧文件哈希、PTS 与所在区间；它不判断图片像素是否真的来自该 PTS、语义是否准确或谁看过。生成后的每行均标记待复核，不自动修改候选或变成正式交付。原候选里的“字幕已视觉复核”等状态是原始声明，不是本工具新确认的结果。三帧不能代表完整画面、动态、所有字幕和音频；对复合描述和转场继续观察局部连续帧/播放原片，无法听校则保留未听校。正式片段、飞书写回及播放验证仍按原有流程执行。
