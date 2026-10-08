# 能力和运行条件

| 来源 | 解析路径 | 必要条件 | 验证边界 |
| --- | --- | --- | --- |
| B站 | 浏览器页面和目标视频详情 | Node.js 20+、Playwright、Chrome/Edge/Chromium | 原平台限制可能阻塞；多 P 必须保持 p 值 |
| 抖音 | 当前目标 aweme 详情 | 同上；需要会话时显式提供 | 推荐流不能冒充目标；验证码不自动处理 |
| 小红书 | 目标笔记视频流 | 同上；分享链接保留原校验参数 | 图文笔记不能作为视频；无目标流即停止 |
| 快手 | 目标 photo 详情 | 同上；需要会话时显式提供 | 必须核对真实 photo ID |
| 视频号 | 配置的本地详情后端或明确允许的在线解析 | 后端、实际授权及真实来源 ID | 默认无第三方请求；不自动安装代理/证书 |
| 其他链接 | 可选 yt-dlp 专用站点解析器 | 用户自行安装 yt-dlp | 随当前版本支持范围；Generic 结果不标为已验证 |

浏览器依赖在 `vendor/video-batch-download` 安装：

```sh
npm ci --ignore-scripts --prefix skill/video-link-extractor/vendor/video-batch-download
python scripts/project.py doctor --feature acquire --browser "<已安装浏览器路径>"
python scripts/project.py acquire --url "<链接>" --output outputs/source-probe --browser "<浏览器路径>"
python scripts/project.py acquire --url "<链接>" --output outputs/source-download --browser "<浏览器路径>" --download
```

独立安装时从 Skill 根目录运行 `npm ci --ignore-scripts --prefix vendor/video-batch-download`，
以及 `python scripts/acquire.py ...`。本仓库不打包浏览器、FFmpeg、账号、模型或下载后的视频。
如使用 Playwright 自带浏览器，须自行明确安装；不会因运行提取命令自动下载浏览器。

使用明确的 `--storage-state <本地会话文件>` 才加载该登录会话；应将它放在仓库外。
不要向终端打印会话内容、完整签名 CDN 链接或第三方凭证。参数中的链接、回执和原文留在本机。

程序合同测试与合成视频解码是技术验证，不等于各平台真实登录下载测试。
真实链接被平台风控/权限阻塞时保留状态和原因，不增加隐蔽绕过或代理轮换。
