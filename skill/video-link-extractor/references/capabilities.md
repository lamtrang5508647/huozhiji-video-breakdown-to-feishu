# 能力和运行条件

| 来源 | 解析路径 | 必要条件 | 验证边界 |
| --- | --- | --- | --- |
| B站 | 浏览器页面和目标视频详情 | Node.js 20+、Playwright、Chrome/Edge/Chromium | 原平台限制可能阻塞；多 P 必须保持 p 值 |
| 抖音 | 当前目标 aweme 详情 | 同上；需要会话时显式提供 | 推荐流不能冒充目标；验证码不自动处理 |
| 小红书 | 目标笔记视频流 | 同上；分享链接保留原校验参数 | 图文笔记不能作为视频；无目标流即停止 |
| 快手 | 目标 photo 详情 | 同上；需要会话时显式提供 | 必须核对真实 photo ID |
| 视频号 | 明确允许的腾讯元宝浏览器流程，或配置的详情后端 | 腾讯流程需正常登录与本次解析授权；后端需独立来源绑定 | 导出标识与数字 ID 分开；默认不外发；不自动安装代理/证书 |
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

用户明确允许将本次视频号分享链接提交给腾讯元宝后，可运行：
此浏览器路径目前只接受单条 `https://weixin.qq.com/sph/...` 短链，不含额外查询参数或片段。

```sh
python scripts/project.py acquire --url "<视频号链接>" --output outputs/channels-source --browser "<浏览器路径>" --wechat-browser --allow-tencent-parse --manual-login --timeout 1200 --download
```

`--manual-login` 使用本次独立浏览器会话的正常登录入口，并启用可见浏览器窗口，
供用户直接完成扫码。二维码图像只截取登录控件区域。不会自动读取其他浏览器会话或导出登录态。需在二维码有效期内扫码；
过期应停止本次运行，再用新输出目录开始正常登录。该流程只提交当前指定链接，
不读取元宝历史对话，也不自动进入其他作品。缺少授权时在浏览器和网络请求前停止。

首次成功不代表登录可永久复用，也不保证所有视频号内容可下载。平台只返回导出标识时，
使用明确类型和可逆本地键交给拆解，不宣称取得原始数字视频 ID。
`--timeout` 给腾讯提供方一个总预算，各阶段使用剩余时间；扫码等待最多 15 分钟。
Node 为正常关闭浏览器预留时间，父进程超时还会清理本次启动的进程树。

程序合同测试与合成视频解码是技术验证，不等于各平台真实登录下载测试。
真实链接被平台风控/权限阻塞时保留状态和原因，不增加隐蔽绕过或代理轮换。
