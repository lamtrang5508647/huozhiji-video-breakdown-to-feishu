# 剪辑师交接：脚本段、画面与编辑事件

已有按源片审核的 `script_manifest.json`、画面导出 `timeline.json`、原片、源时间线和编辑观察时，可在本机生成交接表。交接工具内含本仓库 `analyze-folder-videos-to-feishu/scripts/script_shot_alignment.py` 的交集实现快照，不依赖其他已安装 Skill 的版本。原片及业务记录留在本机；本功能不修改飞书。

1. 以一个现有项目目录作为 `--root`。画面导出 `timeline.json`、对应 `clip_file` 均须在其下；新 Markdown/CSV 将生成在该目录顶层，文件名使用未占用的字母、数字、下划线或短横线。链接只写根目录内相对路径；换电脑需保持相对目录结构。
2. `--manifest` 传入该片脚本清单，`--source` 传入原片文件，`--timeline` 传入脚本清单引用的原始时间线。原片和时间线文件的 SHA-256 必须分别与脚本清单中的来源值相符；工具还会读取该时间线，核对视频身份、原片哈希，以及每个引用画面 ID 和完整起止区间，不能只靠文件哈希证明区间正确。`--export-timeline` 传入画面导出时的 timeline.json，它的画面 ID、区间、来源身份和每个片段哈希会核对。导出片段的实际画面与映射是否一致仍需人工核验。
3. 用实际检查的材料建立单独 `events.json`，只记录有依据的事件；音频没听过写 `not_reviewed`，采样帧观察写 `observed_sampled`，逐帧观察写 `observed_frames`。事件可跨段，会关联每个相交画面；没有事件的行显示待审阅。事件描述、复刻方案和证据由审阅者提供，不是模型自动识别结果。

```json
{
  "schema": "editing-events/v1",
  "video_id": "example-video",
  "source_sha256": "64位小写源文件哈希",
  "events": [{
    "id": "event-01", "start_ms": 1000, "end_ms": 1500,
    "visual_status": "observed_frames", "audio_status": "not_reviewed",
    "observation": "描述实际看到的变化", "evidence": "具体帧与查看范围",
    "recreation": "可执行的分层剪辑方法，未知参数写待调整"
  }]
}
```

```sh
python skill/bilibili-editing-analysis/scripts/build_editing_handoff.py \
  --root <项目目录> --manifest <脚本清单> --source <原视频> --timeline <原始时间线> \
  --export-timeline <画面导出目录/timeline.json> --events <事件.json> --name <新文件名前缀>
```

没有画面导出 timeline 时，可改用 `--assets` 指定 `editing-assets/v1`：`video_id`、`source_sha256` 与脚本来源一致，`assets` 内每项明确 `shot_id`、根目录内相对 `path`、片段 `sha256`、`source_start_ms` 与 `source_end_ms`。允许空数组；缺失画面明确显示“素材缺失”。不接受路径穿越、未知 ID、重复 ID、错哈希或错区间。不要凭文件顺序猜片段。

输出 Markdown 按段展开画面、源交集、字幕摘录、参考视频链接、实际观察和复刻步骤，CSV 每行对应一个“脚本段 × 相交画面”；事件根据时间重叠附加。交集时间并非逐句音频时间，视频片段路径指向完整画面素材，并非剪掉交集以外的部分。程序检查来源和输入结构，不检查内容观点、切点、音效或审核声明是否正确；另行核对素材是否可用及授权。

CSV 保留原有列，并在末尾增加“逐事件源时段与本行交集”：每个事件一行，带事件 ID、事件完整源区间、它在当前脚本段与画面交集内的区间和时长，以及该事件的观察与复刻步骤。文本内换行会以 `\n` 显示，避免误认成不同事件；原有观察、复刻和 `events` 列保持原样。Markdown 同样显示事件完整源区间和当前行相交区间。时间统一为**原视频源时间**，只是按声明区间计算，既不是新配音时间，也不证明逐句语音对齐。事件跨行时，不要把完整事件时长在每行重复计入；只接触边界而没有时长交集的不算相交。
