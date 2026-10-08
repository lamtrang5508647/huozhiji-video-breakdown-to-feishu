# 链接提取组件审查记录

日期：2026-10-08。仅使用本地静态/行为扫描，不启用收费 LLM、VirusTotal 上传或外部扫描服务。
GitHub 候选包仅作为待审代码读取，未运行候选的安装器、自动升级、Cookie 导出或代理/证书配置。
本仓保留有明确 MIT LICENSE 的最小依赖闭包，固定版本及修改见 PROVENANCE.md 和组件 UPSTREAM.md。

## 自动扫描与人工复核分别记录

`skill-vetter` 四项扫描实际运行。其 Cisco 结果解析适配了现版本 `max_severity`，
避免旧脚本把未知 JSON 结构当作 PASS；临时清理加入绝对目录范围验证。
自动扫描存在 HIGH/CRITICAL，不能称为 SAFE 或“所有安全扫描通过”。
原始机器报告保存在忽略输出目录，不把本机路径、运行回执或媒体上传到仓库。

| 工具/规则 | 自动结果 | 逐行复核与实际边界 |
| --- | --- | --- |
| Aguara | MEDIUM：URL 路径解码、Node 进程调用 | 单次 URI 解码用于完整 ID token 匹配；进程清理只针对本次启动且仍存活的浏览器 child PID。没有传入网页命令或 shell 字符串 |
| Cisco | CRITICAL：`spawnSync` | FFmpeg/FFprobe 固定 argv，浏览器清理使用固定命令加 PID 参数数组，无 shell；执行文件来自明确运行时配置。未发现该规则所指命令注入实现 |
| Cisco | HIGH：`fs.writeFile` / 低可分析覆盖率 | 报告只写到明确新/空输出目录，独占锁及 `wx` 阻止覆盖。扫描器不完整分析部分 JS；这些源文件另外人工审阅并由 Node 回归执行，不能把覆盖限制抹掉 |
| Cisco | MEDIUM：网络原语 | 视频链接解析、目标 API 与绑定媒体下载属于功能所需；网络条件写入 Skill compatibility。视频号只有显式配置端点且在线开关明确开启时才可外发 |
| secrets-scan | PASS | 未发现硬编码凭据；并非账户授权或会话安全的保证 |
| structure-check | PASS | YAML 与目录结构正常，没有远程下载后 pipe-to-shell、eval 或递归清理流程 |

扫描中的缓存提示已通过发布过滤处理；安装副本不含字节码、node_modules、会话或认证文件。
必要读写/进程/网络能力保留，没有为了消除规则标签而改名隐藏行为。
这份人工复核不把自动原始 verdict 改成 SAFE；自动 gate 原始结论与实际审查边界均保留。

## 独立复核发现并修正的问题

- 仅凭 runtime 视频流/推荐详情会取错目标：抖音、小红书、快手及 B站绑定真实对象 ID，缺目标详情不接受任意媒体。
- B站分 P 越界会回退到第一条 CID：改为严格 p/page/CID，对直接链接不允许变页；短链记录真实解析页。
- 解码映射可能只测附图：FFprobe 排除 attached_pic 后，按真正视频流 index 解码，并核对解码前后文件哈希。
- 通用后端缺少 extractor 仍可能过关：要求专用解析器、实际 native ID、原标题及来源 URL 绑定；拒绝 Generic/playlist。
- 独立 Node 入口会覆盖回执：新/空目录、独占锁及独占写入，原片/旧回执不动。
- 视频号请求回显不能证明对象身份：只作矛盾检查，必须真实 native ID/nonce/token 与源链接相符；裸短链没有该锚点时保留未验证。

未包含：自动读取浏览器 Cookie、验证码求解、stealth、代理轮换、系统证书安装、付费生成、
视频号加密媒体解密、下载完即宣布字幕/情绪/标色已正确等行为。
