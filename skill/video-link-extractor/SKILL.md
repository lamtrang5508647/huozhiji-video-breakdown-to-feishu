---
name: video-link-extractor
description: 从用户给出的 B站、抖音、小红书、快手、视频号分享链接提取真实标题、平台视频 ID 与可用视频；保留来源、检查完整解码，再交给视频拆解 Skill。用于视频链接提取、原片下载、标题采集及拆解前准备；平台登录和后端条件必须实际核对。
metadata:
  compatibility: Requires Python 3.10+, network access to the requested platform and its media CDN, FFmpeg/FFprobe for media acceptance, and Node.js 20+/Playwright with an installed browser for browser adapters. Channels providers require explicit configuration.
---

先取得可追溯的原片，再进行脚本与画面拆解。支持范围由解析器、会话、后端和当前平台限制决定，不能承诺“任何链接都能下载”。

1. 输入必须是用户明确提供或明确选择的视频链接。分享文案可直接传入，一次一个链接；批量逐条执行、逐条回执。专辑、直播、图文笔记不是单条视频。
2. 使用 `scripts/acquire.py --url <链接或分享文案> --output <新目录>` 探测实际元数据。默认只取元数据，不下载媒体。查看 `acquisition.json` 中的真实状态；不得用模板补原标题、平台 ID 或品牌。
3. 用户要求下载时增加 `--download`。B站、抖音、小红书、快手使用裁剪的浏览器解析器，需要 Node.js 20+、Playwright 与明确的浏览器运行时。依赖安装方式见 [能力和运行条件](references/capabilities.md)。禁止自动导出浏览器 Cookie、自动登录、启动系统代理、安装证书或绕过验证码；缺少会话时说明阻塞。
4. 视频号默认不外发链接。仅配置本地后端 `--backend-url`，或用户明确允许第三方解析时使用 `--online-endpoint` 与 `--allow-online-parser`。缺少真实视频 ID 或返回来源不一致，必须停止。后端协议见 [视频号协议](references/wechat-backend.md)。
5. `ready` 需要原链接、解析后链接、平台 native ID、真实标题/文案，以及 FFprobe 视频流检查、视频/音轨完整解码、源文件 SHA-256 全部通过。文件头、HTTP 200、缩略图或“下载完成”不能代替这些检查。下载的是平台当前提供的画质，不保证上传者原始分辨率。
6. 输出保存在调用方目录：`provider-result.json`、`acquisition.json`，下载验证通过后另有 `source.json` 与媒体。会话、签名链接和运行文件留在本地，不提交 GitHub，不发给未授权服务。
7. 提取完成后使用 `analyze-folder-videos-to-feishu` 做完整脚本、连续语义段和逐画面时间线。通过源文件路径与哈希交接；保留输入分享链接和平台 ID。转写先标为草稿，需原声逐句校对；下载成功不代表字幕、情绪、标色或拆解已审核。

在仓库内可用 `python scripts/project.py acquire ...`。单独安装本 Skill 后，直接运行本目录下的 `scripts/acquire.py`；不依赖作者的私人路径、飞书账号或原项目状态。

若自动路由为 `generic`，仅尝试调用用户已安装的 yt-dlp 的专用站点解析器。未知网页、直接媒体文件和通用解析结果无法建立真实平台身份时返回阻塞，不伪装成已支持的站点。
