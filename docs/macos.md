# macOS 安装与调用

支持 Intel 和 Apple Silicon Mac；需要 Python 3.10+。以下命令在仓库根目录、
macOS 自带 zsh 中执行。安装依赖后，三个 Skill 可以全局调用；项目、媒体、
凭证和虚拟环境始终留在当前电脑，不随仓库分发。

## 1. 准备本机运行时

先核对 `python3 --version`、`ffmpeg -version`、`ffprobe -version` 和 `node --version`。
链接提取需 Node.js 20+，同时需要可选 PNG 预览时选择 Node.js 22+。
如已使用 Homebrew，可安装或补齐缺少的命令：

```sh
brew install python ffmpeg node
```

使用现有 Chrome、Edge 或 Chromium；Chrome 常见位置为
`/Applications/Google Chrome.app/Contents/MacOS/Google Chrome`。
用户目录安装的浏览器可能在 `~/Applications`。后续 `--browser` 始终使用实际可执行文件，
路径带空格时保留引号。无需安装 Windows 的 `.exe`、PowerShell 或字体路径。

创建可供 Agent 重用的 Python 虚拟环境；已有合适环境时可改为其真实路径：

```sh
VIDEO_VENV="$HOME/.local/share/video-breakdown/venv"
python3 -m venv "$VIDEO_VENV"
VIDEO_PYTHON="$VIDEO_VENV/bin/python"
"$VIDEO_PYTHON" -m pip install -r requirements-media.txt
"$VIDEO_PYTHON" -m pip install websocket-client
npm ci --ignore-scripts --prefix skill/video-link-extractor/vendor/video-batch-download
VIDEO_BROWSER="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
"$VIDEO_PYTHON" scripts/project.py doctor --feature media --feature evidence --feature acquire --browser "$VIDEO_BROWSER"
```

`requirements-media.txt` 包含 PyAV、Pillow、NumPy；`websocket-client` 用于本地浏览器回归。
Playwright 使用上面明确选择的现有浏览器，不需自动下载另一份浏览器。
`doctor` 检查本机依赖发现结果，不访问平台、不登录飞书，也不证明任何视频已下载。
若桌面 Agent 无法发现 Homebrew 命令，先核对它的 PATH；Apple Silicon 常见目录
为 `/opt/homebrew/bin`，Intel 常见为 `/usr/local/bin`，应以实际安装为准。

## 2. 全局安装三个 Skill

默认 Codex 安装目录如下。其他 Agent 可改为其实际 skills 目录。

```sh
VIDEO_SKILLS="$HOME/.codex/skills"
"$VIDEO_PYTHON" scripts/project.py install-skill --dest "$VIDEO_SKILLS/analyze-folder-videos-to-feishu"
"$VIDEO_PYTHON" scripts/project.py install-skill --skill video-link-extractor --dest "$VIDEO_SKILLS/video-link-extractor"
"$VIDEO_PYTHON" scripts/project.py install-skill --skill bilibili-editing-analysis --dest "$VIDEO_SKILLS/bilibili-editing-analysis"
npm ci --ignore-scripts --prefix "$VIDEO_SKILLS/video-link-extractor/vendor/video-batch-download"
```

安装器逐文件校验并拒绝覆盖已有目录。升级前先比较现有安装、另存备份或安装到新目录，
不要直接覆盖本机修改。每个安装副本的 `VERSION.json` 应与根版本一致。
仓库和全局副本各有自己的 npm 依赖目录；安装器不会把本机 `node_modules` 打包复制。

重新打开对话以加载新 Skill。告诉执行者选定的 Python **绝对路径**和 Skill 目录；
上面 `VIDEO_*` 只是当前终端的便利变量，其他终端或桌面 Agent 不会自动继承。
不要把某台机器展开后的 `/Users/<姓名>/...` 路径提交进仓库。

## 3. 本地验证与独立调用

在仓库内先跑合成示例和回归：

```sh
"$VIDEO_PYTHON" scripts/project.py init --project local-projects/mac-example
"$VIDEO_PYTHON" scripts/project.py demo --output outputs/mac-demo
CHROME_PATH="$VIDEO_BROWSER" "$VIDEO_PYTHON" scripts/run_checks.py --media --output outputs/mac-checks.json
open outputs/mac-demo/analysis.html
```

初始化、示例及工作输出均使用新的空目录。`--media` 增加真实 PTS 解码回归；
检查回执里的失败与跳过，不能把缺依赖导致的跳过当作通过。

全局安装的三个入口可脱离仓库直接核对：

```sh
"$VIDEO_PYTHON" "$VIDEO_SKILLS/analyze-folder-videos-to-feishu/scripts/check_project.py" --help
"$VIDEO_PYTHON" "$VIDEO_SKILLS/video-link-extractor/scripts/acquire.py" --help
"$VIDEO_PYTHON" "$VIDEO_SKILLS/bilibili-editing-analysis/scripts/prepare_review_windows.py" --help
```

交给 Codex 的示例指令：

> 使用 video-link-extractor 提取我给定的视频链接；使用已配置虚拟环境的 Python，
> 原片通过完整解码后，再使用 analyze-folder-videos-to-feishu 做脚本和镜头拆解。
> 如需研究剪辑手法，使用 bilibili-editing-analysis；项目目录、产品和平台以本次提供为准。

如需手动探测链接，先创建仓库外的新工作目录，再运行独立入口：

```sh
VIDEO_JOB="$HOME/Documents/video-breakdown-jobs/example"
mkdir -p "$VIDEO_JOB"
"$VIDEO_PYTHON" "$VIDEO_SKILLS/video-link-extractor/scripts/acquire.py" --url "<本次视频分享链接>" --output "$VIDEO_JOB/probe" --browser "$VIDEO_BROWSER"
```

默认只探测；用户要求下载时另选新输出目录并增加 `--download`。
登录、验证码、平台限制仍可能阻塞；不要自动导出浏览器 Cookie。
准备本地审阅窗口时，对已核对的原片运行：

```sh
"$VIDEO_PYTHON" "$VIDEO_SKILLS/bilibili-editing-analysis/scripts/prepare_review_windows.py" --input "<原片绝对路径>" --output "$VIDEO_JOB/review-windows" --window-sec 10 --fps 2
open "$VIDEO_SKILLS/bilibili-editing-analysis/assets/local-review.html"
```

源视频、转写校对、语义审核和视听判断必须实际完成。合成导出、浏览器回归及文件存在
均不证明真实视频已审阅或飞书交付成功。飞书仍按 [独立配置流程](feishu-setup.md)
使用本次授权目标；ASR 模型及登录不由安装步骤自动配置。
