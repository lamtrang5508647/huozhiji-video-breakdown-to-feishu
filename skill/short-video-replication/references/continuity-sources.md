# 连续剪辑与生成的研究依据

查阅日期：2026-11-03。下列是公开教材、剪辑工具厂商及模型官方文档；技能中的接镜表和时间预算是结合这些资料作出的制作建议。工具页面会变，运行时仍查当前接口。公开文档不能证明LibTV内部实现、账号权限或某次生成效果。

| 来源 | 支持的具体做法 | 边界 |
| --- | --- | --- |
| [Oklahoma State University — Editing](https://open.library.okstate.edu/introfilmtv/part/editing/) | 动作匹配、视线匹配、正反打和省略剪辑帮助组织连续叙事；镜头根据观众所需信息连接 | 电影剪辑原理，不证明AI已生成正确动作 |
| [Frame.io — The 180 Degree Rule and When to Break It](https://blog.frame.io/2016/08/01/180-degree-rule-and-when-to-break-it/) | 轴线、人物位置和视线服务空间理解；越轴需有清楚设计 | 不把角度数字机械当验收分数，最终看空间是否可理解 |
| [Adobe — Perform J cuts and L cuts](https://helpx.adobe.com/premiere/desktop/edit-projects/trim-clips/perform-j-cuts-and-l-cuts.html) | 下镜声先入/上镜声后出，让声音与画面剪点错开 | Adobe界面操作不等于CodexCut API；本技能按独立音轨实现 |
| [Runway — Image to Video Prompting Guide](https://help.runwayml.com/hc/en-us/articles/48324313115155-Image-to-Video-Prompting-Guide) | 图像规定构图，文本描述动作；实际末帧可继续作首图，编辑时处理边界和共享帧 | 一张静帧不提供完整运动历史，仍须验收速度和身份连续性 |
| [Runway — Gen-4 Video Prompting Guide](https://help.runwayml.com/hc/en-us/articles/39789879462419-Gen-4-Video-Prompting-Guide) | 使用清楚的动作、运镜、主体指代；避免把过多互相冲突的动作和场景塞进一次生成 | 该模型写法不自动推广为所有视频模型参数或限制 |
| [Google Cloud — Generate videos using first and last video frames](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/video/generate-videos-from-first-and-last-frames) / [Extend videos](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/video/extend-videos) | 首尾帧生成与视频延长是不同输入能力 | 入口、模型、媒体规格和限制需现场查证，不能替代用户指定的全能参考 |
| [Google Flow — Edit videos & build scenes](https://support.google.com/labs/answer/16935718?hl=en) | 已生成素材选帧、排序、裁切、场景组织与预览是一条独立编辑流程 | 不能据此推断其他画布平台已经完成同样操作 |

本机执行依据：已安装 `super-editor` 的 workflow、audience-first-production、tool-routing 与方法索引；`codexcut-video-editor` 的 project-schema、extensions 及实际CLI帮助。它们不随本技能打包，不把本机绝对路径、私有项目素材或任务ID复制进可分发源码。
