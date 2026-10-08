# 视频链接提取与拆解交接

统一入口为 `python scripts/project.py acquire`；完整帮助可用 `acquire --help`。
平台自动识别支持 B站、抖音、小红书、快手、视频号域名与分享短链，其他域名走可选 yt-dlp。
识别平台只决定解析路径，不证明平台允许下载或当前解析有效。

一次输入一个视频链接或完整分享文案；多链接输入会拒绝。多条任务逐条运行、逐条保存回执。
原分享 URL 完整保留，尤其是小红书校验参数；解析后的 canonical URL 和真实平台 ID 独立记录。
标题取平台实际标题；没有独立标题的平台取原文案并标明 `title_kind=caption`。
不得用标题、文件名或 URL 哈希伪造平台 native ID。

## 状态与文件

| 状态 | 含义 | 可以做什么 |
| --- | --- | --- |
| `metadata_ready` | 原链接与目标视频身份、真实标题已绑定 | 核对信息，尚不能当作原片已下载 |
| `ready` | 媒体有有效视频流，视频/音轨完整解码，SHA-256 已保存 | 交给拆解 Skill 开始审片、转写与时间线 |
| `blocked` | 依赖、配置、会话、元数据、来源或媒体检查不满足 | 保留失败回执，处理明确阻塞；不写成功状态 |

输出：`provider-result.json` 为后端原始合同结果，`acquisition.json` 为统一验收，
仅 `ready` 时生成 `source.json`。媒体下载的可用画质不代表上传者的原始编码/分辨率；
`original_upload_quality_verified=false` 明示这点。
每次输出需新目录或空目录；不自动覆盖既有原片或失败记录。

## 平台条件

浏览器适配器的依赖安装与显式会话规则见
[Skill 能力表](../skill/video-link-extractor/references/capabilities.md)。
B站严格核对 BV、CID 和多 P；抖音只接受目标 aweme 详情中的媒体；
小红书只接受目标笔记流；快手核对 photo ID，不把推荐卡片下载为目标。
验证码、登录要求、私密/删除作品直接记录阻塞，不自动绕过。

视频号可显式使用 `--wechat-browser --allow-tencent-parse`，将本次链接提交腾讯元宝，
在独立浏览器会话中正常登录后访问腾讯播放详情。需要扫码时加 `--manual-login`；
具体命令见 [Skill 能力表](../skill/video-link-extractor/references/capabilities.md)。
也可显式配置满足 [视频号协议](../skill/video-link-extractor/references/wechat-backend.md)
的本地授权后端，或设置在线解析 endpoint 并传 `--allow-online-parser`。路径不能混用。
仓库没有系统代理或根证书安装器，不承诺匿名解析可用。
若后端仅返回加密媒体，本版本停止，不包含该媒体解密。

其他平台只尝试当前 yt-dlp 专用解析器；playlist、multi-video、Generic 网页/文件结果被拒绝。
支持哪些站点以已安装版本实际运行结果为准，不能把“能识别一个网址”当作能下载。

## 交给拆解和飞书

先检查 `acquisition.json` 状态为 `ready`，再读取 `source.json`。
将 `video_id`、`platform`、`title`、`url`、`source_path` 和 `source_sha256`
映射到拆解 manifest 的 `source`/项目源文件配置；这个 JSON 是交接回执，不是已审核的分析 manifest。
腾讯播放详情只返回导出标识时，`source.json` 明示 `native_id_kind=platform_export_id`、
原始 `platform_export_id` 与 `identity_stability_verified=false`。`video_id` 是以 `wxexp_`
开头的可逆 Base64URL 本地键；它不是平台原始数字 ID，不能拿它调用数字 ID 接口。
源身份检查同时核对该编码和原播放 URL 的唯一 `eid`，保留原输入链接。播放 URL 和
签名参数完整保存在本地，不能因为参数看似敏感便删除后继续声称源绑定通过。
其他后端若 canonical URL 不含真实平台 ID，飞书 profile 需配置专用
`source_identity.id_field`；远端写回及回读仍需实际验证，不能删除核心绑定检查。

按现有拆解 Skill 完成：完整原片观看 → ASR 草稿与听校 → 脚本框架 → 连续语义段 →
逐画面时间线 → 短语标色和情绪依据 → 上下文复核 → 两种片段导出 → 飞书上传与回读。
提取不创建飞书记录、不改登录身份、不替代发布前审核。

原片、回执、Cookie/会话、签名媒体 URL 均留在调用方项目或忽略输出目录，不进入源码分发。
