# 从视频拆解到创作复盘：使用指南

这次把课程中可操作的方法做成了 Skill 指令、规则卡和本地工具。你提供素材与本品资料，Agent 负责理解、撰写和复核，工具负责引用校验、切片、存档和报表计算。没有内置付费生成服务，也不会自动创建广告计划。

## 最省事的用法：直接说你要做什么

安装 `analyze-folder-videos-to-feishu` 后，把下面一段复制给执行者，替换路径和产品信息。开始时只需 **素材位置、产品是什么、目前确定的产品资料**。没有投放数据也可以先拆解和创作。

> 使用 $analyze-folder-videos-to-feishu，按已吸收的课程方法做完整内容工作流。
> 原片在【本地文件夹或视频链接】，产品是【名称】，本品资料在【文件路径】。
> 项目数据保存到【独立项目目录】。
> 先完整分析参考片的文案逻辑、画面次序、需求场景、主张和展示材料，保留原文、来源和时间。
> 再结合本品已知事实，给我选题卡、脚本卡、拍摄卡和测试卡；比较不同展示方式，说明为什么选、哪里缺资料。
> 本品信息不足就留待补，不借用参考片功效或用户经历。先做本地可查看结果。

常用后续指令：

| 你要做的事 | 可以直接说 |
| --- | --- |
| 只拆原片 | “只拆解这条视频，逐段对齐原句与画面，不扩展创作任务。” |
| 多出选题 | “用已核实事实和真实问题，按需求时刻、使用场景、表达视角出候选，附选择理由。” |
| 改展示方法 | “这个卖点换几种可拍的表现机制，写人、场景、动作、机位和验收条件；材料不足不用凑数。” |
| 做下一版本 | “基于这个工作台新建修订，保留父版本，只改【一个变量】；明确固定条件和未测试状态。” |
| 处理一批视频 | “继续这个文件夹，先看批量状态，已完成的复用，失败项定位原因后重试；缺分析或听校的列出来。” |
| 用报表复盘 | “把这份后台 CSV 对应到这些平台创意 ID 和本地版本，核对指标口径，给我观察、解释假设与下一轮动作。” |

## 你会收到哪些文件

| 结果 | 用途 |
| --- | --- |
| 原片报告、语义段与逐画面切片 | 看原文和画面如何配合；两类切片保留准确来源 |
| `creative-workflow.json` | 可编辑的事实、主张、证据、选题、表达模块、四卡和变体库 |
| 创作 `report.html` / `cards.csv` | 给编导、摄影、剪辑或自己查看执行；JSON 保留完整引用 |
| `state/content-batch.json` | 查看每条素材做到哪一步，下一步缺什么 |
| 复盘 `index.html` / `report.json` / `results.csv` | 查看创意版本的指标、样本状态和下一轮检查 |

初始化只建立草稿。完整四卡经填充后才可要求 `--require-ready`；这仍表示可交人工复核，不表示已拍摄、已听校或已验证效果。只请求某张卡时，完成对应内容即可，整个工作台保持草稿状态。

真实提问需原文、来源定位、采集时间和本地存档；没有出处的疑问记录为待核实。`search` 是本地逐字关键词检索，空格分词后全部匹配，适合找已存选题和模块，不是语义搜索。

## 先看一个完整演示

在仓库根目录运行，需要 Python 3.10+ 和 FFmpeg/FFprobe：

```powershell
python examples/content-workflow/build.py --output outputs/content-workflow-demo
```

打开输出目录的 `index.html`。演示实际生成并解码 4 秒 RED/BLUE 色卡，跑两类切片、四卡、同一个创意注册表的报表导入，检查续跑不重做与重复导入不累计。所有商品、台词和投放数字都是原创合成案例。

## 手动操作：创作工作台

以下在仓库根目录运行；路径含空格时加引号。独立 Skill 安装没有根目录 `project.py`，对应改为安装目录下的 `scripts/creative_workflow.py`、`performance_feedback.py`、`workflow_batch.py`。

```powershell
python scripts/project.py creative init --output local-projects/my-product/creative-v1 --product-id my-product --product-name "我的产品"
```

有已经完成的原片分析，可追加 `--source-manifest "原片的script_manifest.json"`，重复此参数绑定多条来源。**先建立本品事实卡**，每条事实附资料与限制；参考片作者的说法仍属于参考片。请 Agent 按模板补充四卡、主张—材料关系、需要补的证据与候选展示方案。

```powershell
python scripts/project.py creative validate --bundle local-projects/my-product/creative-v1/creative-workflow.json
python scripts/project.py creative render --bundle local-projects/my-product/creative-v1/creative-workflow.json --output local-projects/my-product/creative-report-v1
python scripts/project.py creative search --root local-projects/my-product --query "场景 需求"
python scripts/project.py creative init --from-bundle local-projects/my-product/creative-v1/creative-workflow.json --output local-projects/my-product/creative-v2
```

输出目录要求新建或为空，避免覆盖旧结果。渲染后打开 `creative-report-v1/report.html`。下一修订保留前版哈希关系；文件修订号不等于新创意。脚本或拍法变化时新增 `variant_id` 并记录 `parent_variant_id`，避免把不同素材的报表算到同一版本。脚本多段放入同一版本的脚本卡 `blocks`。只有一个可行展示方案时，拍摄项补 `selection_reason` 说明实际限制即可。

## 手动操作：一批素材与恢复

建议独立项目目录结构：

```text
my-product/
  sources/       原片
  analysis/      已复核的分析与回执
  batch/         批量任务状态和生成产物
  creative-v1/   创作工作台
  feedback/      原始报表、映射与回填状态
```

原片放在批量工作目录 `batch` 外，避免把生成片段再次当原片。也可用外部原片目录，登记只读取原文件。

```powershell
python scripts/project.py batch add --workspace local-projects/my-product/batch --folder local-projects/my-product/sources --batch first
python scripts/project.py batch run --workspace local-projects/my-product/batch --through prepare --workers 2
python scripts/project.py batch status --workspace local-projects/my-product/batch
```

准备阶段验证媒体解码并提取证据。按原片字节 SHA-256 跨批次去重，不按文件名猜是否重复。需要离线转写时提供已安装模型：

```powershell
python scripts/project.py batch run --workspace local-projects/my-product/batch --through asr --asr-model "本地模型路径"
```

没有模型或原片分析时，任务明确停在等待状态。转写结果是草稿；Agent 仍需看片、听校、完成原有 `script_manifest` 和独立回执。以下 `source-id` 使用 `status` 中的 SHA-256 或唯一前缀，回执必须绑定当前分析文件：

```powershell
python scripts/project.py batch attach --workspace local-projects/my-product/batch --source-id "来源SHA前缀" --manifest "分析文件路径" --review-receipt "复核回执路径"
python scripts/project.py batch run --workspace local-projects/my-product/batch --through export --workers 2
```

每次导出存到独立尝试目录；`semantic` 是连续语义段，`shots` 是逐画面片段。状态表给出实际路径。重复运行校验已有哈希后复用；源文件、已附分析、回执或工具依赖变化会使相关旧结果失效。修改分析后重新 `attach`，不要手改旧回执哈希。

失败项不会自动无限重试；修复原因后用 `--retry-failed`，默认最多 3 次尝试。可用 `--max-attempts` 调整。`--stop-file "停止标记路径"` 存在时不启动下一批任务，当前已启动任务会结束并记录。超时会终止工具创建的本地进程树。

## 手动操作：投放 CSV 回填

```powershell
python scripts/project.py feedback init --output local-projects/my-product/feedback
```

请 Agent 根据真实后台导出编辑 `feedback_mapping.json`：

- `columns` 对应 CSV 列名；`numeric_fields` 说明每个计数/金额的真实口径。
- `creative_map` 填平台 `platform`、账户 `account`、平台创意 ID `creative_id`、本地 `variant_id`；CSV 也需平台列，不能只按标题近似匹配。
- 指标明确分子与分母，保留目标、归因窗口、币种、时区、商品价格、承接入口和版位；可增加人群/优惠等分组条件。
- `sample_criteria` 默认空，表示未评估样本是否足够；仅按你的业务判断填写，不套课程统一百分比。

```powershell
python scripts/project.py feedback import --state local-projects/my-product/feedback/feedback_state.json --csv "后台导出.csv" --mapping local-projects/my-product/feedback/feedback_mapping.json --registry local-projects/my-product/creative-v1/creative-workflow.json
python scripts/project.py feedback report --state local-projects/my-product/feedback/feedback_state.json --output local-projects/my-product/feedback-report-v1
```

打开 `feedback-report-v1/index.html`。报告保留每个确切日期范围，按累计分子/累计分母计算，不平均百分比；零分母和未知数值保留空值。只有相同口径与上下文的结果进入同一比较组，同组仍不代表随机实验。

同一报表重复导入不增加数据。已导入观察的修正必须在原命令后加 `--operation upsert --reason "修正原因"`，旧定义与数值保留在修订记录。日期范围重叠会被拒绝，请使用互不重叠的期间，或对相同期间显式修正。

复盘需要 Agent 结合四卡解释：发生了什么、哪些因素可能有关、下一轮保留/补证/再拍/再测什么。**点击更高不自动等于成交更好**。测试卡保留当初的未测试假设；后续报告以路径和版本 ID 关联，不能改写当初记录来制造成功案例。

## 飞书及能力边界

本地使用不要求飞书登录。需要归档时先配置当前项目和真实目标，按 [飞书接入](feishu-setup.md) 和现有章节发布流程执行。`batch publish` 只串行转交已有 Base 同步器；默认 dry-run，写入仍须当前计划的精确哈希。其 `--provenance-workspace` 应是同时包含原片、计划和导出文件的公共项目父目录，例如上面的 `my-product`，不是只包含状态的 `batch` 子目录。

Base 元数据回读通过不等于文档、附件画廊和实际播放全部验收。新工作台不会擅自修改你的飞书字段。当前没有真实本品资料、素材或后台报表时，只能交方法、草稿和程序验证，不能声称已验证内容效果。

课程方法及页码见 [方法参考](../skill/analyze-folder-videos-to-feishu/references/course-content-workflow.md) 与 [规则卡](../skill/analyze-folder-videos-to-feishu/references/course-rule-cards.json)。仓库只保存原创规则归纳，不附原课程 PDF。
