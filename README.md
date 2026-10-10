# huozhiji · Video Breakdown to Feishu

把带货视频拆成 **完整脚本框架 + 连续语义段 + 逐画面素材**，保留原文、时间线、
短语标色、复用公式和证据，再按 **产品 → 平台 → 视频画廊** 归档到飞书。

这是可安装的 Agent Skill 与一组校验、渲染、切片和同步工具。原片理解和内容复核
由执行者完成；它不是自动保证语义正确的模型，也没有内置 AI 视频生成服务。
新增 `video-link-extractor`，负责链接提取和原片准备，再衔接拆解。

## 你会得到什么

- **原声转写与校对**：完整口播原文、带时间的转写记录和明确校对状态，与画面字幕分开保存；
  ASR 自动结果始终为草稿，实际听校后才按复核范围标为已校对。
- **脚本分析**：视频原标题 H1，公式、图例与脚本段 H2；句内不同功能分别标色；
  原片公式与可替换变量公式分开，情绪分析保留画面/时间依据。
- **千川深拆**：购买问题、卖点与展示材料、声画关系、行动承接及可复用条件；
  情绪按实际变化记录，原作者主张与证据分开。规则和官方学习来源见 [千川深拆](skill/analyze-folder-videos-to-feishu/references/qianchuan-deep-breakdown.md)，只分析与整理。
- **两种视频片段**：按脚本意思形成连续段，同时保留逐画面编号、切片与关键帧。
- **素材资产库**：关联源视频和脚本段，标注产品、平台、场景、用途；
  剪辑复用与 AI 重制分别判断。
- **持续归档**：每个产品 × 平台复用一个分析文档，每条视频为一个章节，
  配合产品平台导航和视频附件画廊。
- **统一执行标准**：确定性排版、来源哈希、上下文复核、上传日志和实际保存结果校验。
  较小模型也要交同一套结构化产物，但不能据此承诺不同模型判断力相同。
- **链接提取**：B站、抖音、小红书、快手的浏览器适配器，以及需要显式配置的视频号适配器；
  保留真实标题/文案、平台 ID、分享链接和解析后链接。完整媒体解码通过后才交给拆解。

默认正文省略 `不可直接照搬`、`整片受众假设（非测量）`、`复刻素材清单`；
内部证据与必要校验数据仍保留。原始完整转写与标色字幕摘录分开保存。

## 先跑本地示例

需要 Python 3.10+；媒体演示另外需要 PATH 中可用的 FFmpeg 和 FFprobe。
以下命令在仓库根目录运行，macOS/Linux 可将 `python` 换成 `python3`。

```sh
python scripts/project.py init --project local-projects/example
python scripts/project.py doctor --project local-projects/example --feature media
python scripts/project.py demo --output outputs/demo
```

打开 `outputs/demo/analysis.html` 查看脚本页面，`outputs/demo/semantic/index.html` 查看连续段视频。
示例会生成 4 秒 RED/BLUE 色卡原片、2 个画面片段、2 个脚本段片段、缩略图、
CSV/JSON、`chapter.xml` 和 `verification.json`。字幕直接绘制在合成画面上，音轨为测试音。
它验证程序连接、排版、导出与重复运行校验，**不代表真实带货脚本的语义审查**。

初始化和演示均要求新目录或空目录，避免覆盖既有工作。重复体验请选择新的输出目录。

## 安装 Skill

```sh
python scripts/project.py install-skill --dest "<你的skills目录>/analyze-folder-videos-to-feishu"
python scripts/project.py install-skill --skill video-link-extractor --dest "<你的skills目录>/video-link-extractor"
```

目标是准确的 Skill 目录，例如所用 Agent 的 `~/.codex/skills/analyze-folder-videos-to-feishu`。
安装器会逐文件校验，并拒绝覆盖已存在的安装。它只安装 Skill，不安装媒体运行时、
模型服务或飞书登录；已有安装应先比较，再按实际升级范围处理。

安装后可向执行者说明：

> 用 analyze-folder-videos-to-feishu 拆解我提供的本地视频。
> 项目目录是……，产品是……，平台是……。先完整看原片，转写并核对原声和校对状态，
> 再分析整片脚本框架、原片公式和可复用变量公式，按完整语义段做短语标色与情绪分析。
> 保留逐画面时间线与编号。完成内容复核后，导出语义段和逐画面两类片段。

这三项文字输出统一归在 [拆解 Skill 的文字交付流程](skill/analyze-folder-videos-to-feishu/references/script-segment-framework.md#文本拆解转写标色与框架输出)，
由拆解 Skill 负责，链接提取 Skill 只负责准备源视频。

要同步飞书，再给出明确的项目配置和目标。不要把另一个项目的表格或账号当默认值。

## 从视频链接开始

```sh
npm ci --ignore-scripts --prefix skill/video-link-extractor/vendor/video-batch-download
python scripts/project.py doctor --feature acquire --browser "<已安装的Chrome/Edge路径>"
python scripts/project.py acquire --url "<视频链接或完整分享文案>" --output outputs/video-probe --browser "<浏览器路径>"
python scripts/project.py acquire --url "<视频链接或完整分享文案>" --output outputs/video-source --browser "<浏览器路径>" --download
```

默认只探测元数据；`--download` 才下载并检查视频流、完整解码和文件哈希。
`acquisition.json` 记录状态；通过后 `source.json` 提供原标题、平台 ID、来源 URL、
原片路径与哈希，用于后续完整脚本和素材拆解。转写能力仍在拆解 Skill 中，转写结果需听校。

**不能保证任何链接都成功。** 登录、私密作品、验证码、链接失效与平台变更可能阻塞。
小红书图文不当作视频。视频号默认不外发链接、不安装代理/证书，需要明确后端，
或显式允许在线解析；没有真实 ID 或只提供加密媒体时返回阻塞。
其他平台可尝试用户安装的 yt-dlp 专用解析器；通用网页结果不伪装成已核实身份。
详细条件、输出与拆解交接见 [链接提取](docs/link-extraction.md)。

## 接入飞书

先阅读 [飞书初始化与字段映射](docs/feishu-setup.md)。
`skill/analyze-folder-videos-to-feishu/assets/project_profile.example.json` 只有占位资源 ID，不可直接上传。

```sh
python scripts/project.py init --project local-projects/feishu --feishu-config "<你已填写的profile.json>"
python skill/analyze-folder-videos-to-feishu/scripts/check_project.py --project local-projects/feishu --live --output outputs/feishu-preflight.json
```

新项目允许空注册表，既有文档仍需真实锚点和内容哈希。初次建表、读取实时字段、
建立产品平台入口、创建并登记首个汇总文档，由执行者通过当前飞书接口完成。
仓库内的章节发布器处理已登记的文档，不能替代这一步。

上传计划支持显式平台、来源 URL 和字段名称映射。B站保留兼容识别；其他平台需要明确
的来源 URL，不能依靠标题或短链接猜视频身份。**平台归档、适配器实现与真实下载验证分别记录。**

## 依赖与检查

| 功能 | 依赖 |
| --- | --- |
| JSON 校验、HTML/XML | Python 标准库 |
| 视频和缩略图导出 | FFmpeg、FFprobe |
| B站/抖音/小红书/快手链接提取 | Node.js 20+、Playwright、Chrome/Edge/Chromium |
| 视频号链接提取 | 显式配置的授权后端；媒体验收另需 FFmpeg/FFprobe |
| 可选其他站点提取 | 自行安装的 yt-dlp 专用站点解析器 |
| 真实 PTS 证据帧 | `python -m pip install -r requirements-media.txt` |
| 可选 PNG 预览 | Node.js 22+、Chrome/Edge/Chromium |
| 可选 ASR 草稿 | 自行安装的 Whisper / whisper.cpp 与本地模型 |
| 飞书读写 | 已认证的 lark-cli 与实际权限 |

```sh
python scripts/run_checks.py
python scripts/run_checks.py --media --output outputs/checks.json
```

第二条增加 PyAV/Pillow 实际解码测试。缺少可选运行时的测试会明确报告跳过，
不能把跳过当作已经验证。ASR 默认只用本地模型，下载命名模型需要显式
`--allow-download`；自动转写始终标为草稿。

## 目录与项目状态

```text
skill/analyze-folder-videos-to-feishu/  可安装 Skill 与底层程序
skill/video-link-extractor/            链接提取 Skill、裁剪适配器与许可说明
scripts/                              新用户入口与统一测试入口
examples/                             占位配置、原创合成样例
tests/                                可移植回归测试
docs/                                 架构、飞书接入、模型执行说明
local-projects/                        本机项目，Git 忽略
outputs/                              本机测试和演示产物，Git 忽略
```

项目配置、状态、原片、派生媒体和认证信息均不随仓库分发。
旧的 27 字段模板作为显式可选工作流保留；新项目默认走脚本与素材两层交付。
详细模块关系见 [项目架构](docs/architecture.md)，跨模型要求见
[执行与复核](docs/model-execution.md)，本次验证范围见 [验证记录](docs/verification.md)。

当前为公开共享仓库：公众可以浏览、下载，并按 [仓库访问与使用](docs/repository-access.md) 中的授权范围安装、调用；原仓库的写入、合并和管理权限仅由 `lamtrang5508647` 持有。
早期模板代码的上游许可尚未核实，未给整仓添加整体开源许可证；第三方和未知来源内容的授权范围见 [来源与许可状态](PROVENANCE.md)。
