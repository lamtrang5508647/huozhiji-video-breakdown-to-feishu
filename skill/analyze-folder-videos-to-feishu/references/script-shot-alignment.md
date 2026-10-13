# 脚本与镜头对齐：定位、仿写与镜头处理

用于完整脚本拆解后，把连续语义段、相交镜头、源字幕和复用建议放在一起查看。先完成 [脚本段与来源复核](script-segment-framework.md)，再从当前 `script_manifest.json` 派生对齐表；不另写一份重复脚本作为对齐输入。

**一行对应“一个脚本段 × 一个实际相交镜头”。** 这是语义段与镜头的时间交集，不是逐句、逐字口播对齐。字幕摘录不等于完整口播，原声对齐状态保持 `not_verified`；已有听校记录仍按原范围保留，不能由对齐表推定新增范围已听校。

## 行内容与来源

| 内容 | 从现有 manifest 读取或计算 |
| --- | --- |
| 脚本段与镜头身份 | `segments[].id`、段名、`source_shot_ids`、`source_shot_spans`；同时保留源视频身份、源 SHA-256 和时间线绑定 |
| 源片交集 | `segment_intersection_start_ms` 至 `segment_intersection_end_ms`，整数毫秒半开区间 `[start, end)` |
| 段内偏移 | 交集起止分别减去当前脚本段 `start_ms`，供连续段播放器定位 |
| 镜头内偏移 | 交集起止分别减去 `source_shot_start_ms`，供判断该镜头应取用的部分 |
| 源字幕摘录 | 当前段 `source_excerpt.items` 中对应镜头的原文；保留摘录身份、原有顺序及 `context_only` 边界上下文标记 |
| 段级公式与仿写槽 | 当前段 `reusable_formula` 的结构、填空句、变量、条件与所需证据；它们属于整段建议，不伪装成该镜头独有的口播 |
| 镜头组源观察 | 当前段 `shot_groups` 中明确引用该镜头的 `source_observation`，保留组身份；不按镜头号相近推断关联 |
| 镜头组拍摄与剪辑建议 | 对应组的 `replication_shooting_plan`：主体/动作、景别/视角、句式槽、信息顺序、剪辑衔接；明确为仿写或新拍建议 |

一个镜头跨两个语义段时，各生成一行，分别使用真实交集与相对偏移，不能在两行中都标为整镜头全长。多个镜头组明确引用同一镜头时保留各自内容，不把组级建议改写为已经发生的镜头动作。仅属于边界上下文的相交镜头，不继承邻段整组的分析或拍法。

没有字幕摘录或匹配的镜头组建议时，明确显示缺失，不把邻镜头文字搬过来。不按时长平均分配字幕字符，不从画面切点推造句子起止，不把段级填空句拆成未经复核的逐镜头口播。

## 执行与本地交付

1. 核对当前 manifest 的源视频、源哈希、时间线、语义段和镜头交集。修改来源或分析后，重新复核并生成绑定当前 manifest 哈希的回执。
2. 在已有段级公式及镜头组拍法中补足真实分析。只修改其规范输入，不手工编辑导出的对齐 JSON/CSV 来掩盖来源缺口。
3. 运行导出预检，再按已有授权生成本地产物。以下命令在仓库根目录执行，输出使用新的独立目录：

```text
python skill/analyze-folder-videos-to-feishu/scripts/export_script_v3.py --manifest <job-dir>/script_manifest.json --review-receipt <job-dir>/review-receipt.json --output <new-output-directory>
python skill/analyze-folder-videos-to-feishu/scripts/export_script_v3.py --manifest <job-dir>/script_manifest.json --review-receipt <job-dir>/review-receipt.json --output <new-output-directory> --render
```

新版 `index.html` 默认包含对齐表及播放器定位入口。点击某行时，用该行段内偏移定位所属连续脚本段；源片绝对时刻和镜头内偏移仍分别显示，不能混作播放器时间。

彩色脚本页传入同一份 manifest，复用已审阅的短语颜色与辨读备注：

```text
python skill/analyze-folder-videos-to-feishu/scripts/render_script_analysis.py --input <job-dir>/analysis.json --manifest <job-dir>/script_manifest.json --output <job-dir>/analysis.html
```

原来的连续阅读保持不变，每段下展开“脚本与镜头对齐”即可并排查看。此处的定位使用 analysis 中已有且正确的段视频 `clip_url`；不推断素材下载地址。未传 `--manifest` 时保持旧页面输出。

导出同时产生 `script-shot-alignment.json` 和 `script-shot-alignment.csv`，与脚本段视频、索引、源 manifest 保持对应。这两个文件纳入完整产物哈希验收；重复导出必须检查其内容完整性，不能仅因 MP4 存在就认定对齐交付完成。修改后使用新输出目录重建，保留旧产物与回执。

## Docx 与 Base 兼容

后续新建完整拆解时，作者在 `sections-content.json` 顶层显式填写：

```json
{
  "script_shot_alignment": true
}
```

此处为新增字段示意，不能覆盖文件中的其他内容。Docx 仅在 `content.script_shot_alignment` 为布尔值 `true` 时启用对齐展示；旧数据没有该标记时继续原有样式，已保存章节不会自动更新。本地导出的对齐表默认交付不依赖这个 Docx 标记。

继续使用现有 [固定章节格式](golden-layout-and-delivery.md)：视频原标题 H1、公式/图例/语义段 H2、连续原文及短语颜色均保留。源字幕原文、源观察和仿写建议分别呈现；对齐展示不替代原来的连续标色阅读。修订已有章节仍走当前输入复核、原位更新和实际保存结果校验，保留章节与段落导航。

现有 Base 不因本功能新增字段、表或逐视频文档；继续复用源视频、脚本段、素材关联和现有入口。生成本地对齐表不会直接写入远端，也不代表素材已进入画廊。

## 验收边界

检查每段实际相交镜头是否完整且无重复、交集与两种偏移是否一致、字幕是否逐字保留、组级建议是否来自明确关联、两个对齐文件是否纳入哈希验收。仿写时另外核对目标产品事实和复用条件，不能直接沿用源片的功效、人物经历或证明材料。

结构检查和本地播放器定位不证明逐句原声对齐、语义分析正确、真实听校完成或飞书实际保存成功。分别报告这些状态；没有真实飞书写入和回读时，远端交付仍为未验证。
