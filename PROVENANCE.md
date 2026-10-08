# 来源与许可状态

这是既有视频拆解 Skill 的通用化整理版本。代码由用户提供的早期 Skill
以及项目迭代中新增的拆解、校验、渲染和同步工具组成。

早期 27 字段报告链的原始作者、公开仓库和许可证尚未核实。相关文件包括
`check_environment.py`、`detect_and_split.py`、`validate_payload.py`、
`sync_to_feishu.py`，以及对应的 legacy 参考说明和模板。当前保留是为了兼容
明确选择该模板的项目；它们不是默认工作流。

本仓库暂不声明 MIT、Apache 或其他整体开源许可。私有托管与许可核实是两件事；
未来公开前，应确认早期代码的来源与授权，必要时移除或独立重写相关部分，
再选择合适的许可证。本说明不补授第三方代码的使用权。

FFmpeg、PyAV、Pillow、Node.js、Playwright、浏览器、Whisper、yt-dlp 与 lark-cli 是外部依赖，
不随源码分发，各自遵循上游许可。未打包模型权重、业务原片、真实飞书配置或认证资料。

## 新增链接提取组件

| 路径 | 上游与固定版本 | 许可与修改 |
| --- | --- | --- |
| `skill/video-link-extractor/vendor/video-batch-download` | [ljb1020/video-batch-download](https://github.com/ljb1020/video-batch-download)，`be5e41cf7e95b3c3388790bcce91b8becb942ef1` | MIT；只保留所需解析、下载依赖闭包，修正目标视频身份、推荐流、标题与多 P 边界；LICENSE 和 UPSTREAM.md 随副本保留 |
| `skill/video-link-extractor/vendor/qiaomu-download` | [joeseesun/qiaomu-download](https://github.com/joeseesun/qiaomu-download)，`8e92fb0c717537902ba1bad7d06c67411ea56c11` | MIT；仅改编 URL/媒体限制等必要帮助代码，保留 LICENSE 和 UPSTREAM.md；未复制后端安装器、自动升级或代理/证书流程 |

统一入口、来源验收、完整解码、视频号协议与本地回归由本项目补充。
第三方 MIT 许可只适用于对应组件，不把早期未知来源代码自动变成 MIT。
原先使用的 `tiderzheng/bili-downloader-agent` 未找到明确 LICENSE，未将其代码复制进仓库；
本次以已核实许可的适配器实现 B站入口。也未原样复制默认外发链接且缺少 LICENSE 的候选。

`examples/synthetic` 和演示程序中的测试内容是独立构造样例，
只用于校验程序契约，不作为真实视频分析质量的证据。
