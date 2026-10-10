# 版本与正式迭代

仓库从 `v1.0.0` 开始统一记录正式发布版本，首次正式迭代编号为 `001`，日期为 `2026-10-10`。此前历史未统一版本化；保留原有提交和验证记录，不补造历史发布次数。

## 版本源

根目录 [VERSION.json](../VERSION.json) 是唯一版本源，初始内容为：

```json
{
  "schema_version": 1,
  "version": "1.0.0",
  "iteration": 1,
  "released_on": "2026-10-10"
}
```

`schema_version` 描述元数据结构；`version` 是不带 `v` 的三段版本号；`iteration` 是累计正式迭代次数；`released_on` 是该版本记录的日期，格式为 `YYYY-MM-DD`。本地写入这些字段不代表 GitHub 标签或 Release 已发布。

以下安装副本必须与根文件一致，不分别管理版本：

- `skill/analyze-folder-videos-to-feishu/VERSION.json`
- `skill/video-link-extractor/VERSION.json`

修改根版本时使用版本工具同步两份副本；不要手工只改一个 Skill。用户已安装的旧副本不会因仓库版本变化自动升级，应核对实际安装目录中的 `VERSION.json` 和文件校验结果。

## 查看与校验

在仓库根目录执行：

```sh
python scripts/project.py version
python scripts/project.py --version
python scripts/versioning.py check
python scripts/versioning.py check --tag v1.0.0
```

`check` 用于检查本地版本源、两个 Skill 副本、README 和更新日志的一致性。`--tag` 用于核对拟使用的发布标签与版本是否一致，标签形式为 `v<version>`；它不替代远端提交、标签和安装内容的回读。

更新日志位于 [CHANGELOG.md](../CHANGELOG.md)。文件首行为 `# 更新日志`，每个正式版本使用以下标题格式，迭代号至少补齐三位：

```text
## v1.0.0 · 第 001 次正式版本迭代 · 2026-10-10
```

后续迭代增加新的版本条目，保留已有条目和真实验证边界。不能仅递增版本号而省略实际变更说明。

## 准备下一次迭代

先把本次实际改动、兼容性影响与验证边界写入 UTF-8 文本文件，再运行：

```sh
python scripts/versioning.py bump --part patch --date 2026-10-11 --notes-file release-notes.md
python scripts/versioning.py check
```

日期与说明文件由实际迭代决定，示例日期不代表计划发布日。说明文件使用正文、列表或 H3 小节，不包含 H1/H2 版本标题。`bump` 只修改本地版本记录、Skill 副本、README 和更新日志，不提交 Git、不推送、不创建标签或 GitHub Release。

选择版本增量时按[语义化版本规范](https://semver.org/lang/zh-CN/)的兼容性原则判断：

| 参数 | 适用变更 | 从 `1.0.0` 升级的示例 |
| --- | --- | --- |
| `--part patch` | 保持既有输入与调用方式兼容的缺陷修复 | `1.0.1` |
| `--part minor` | 保持兼容的新能力或可选输入 | `1.1.0` |
| `--part major` | 必须调整既有输入、调用方式或交付契约的不兼容变更 | `2.0.0` |

每次正式迭代的 `iteration` 加一，不随主版本或次版本归零。导出器等组件内部引擎号用于产物兼容与缓存失效，不能代替仓库发布版本。旧产物不应通过改写哈希或回执冒充新版本生成。

## 交付检查

版本准备后，先审阅变更与更新日志，运行 `python scripts/run_checks.py`；修改视频或帧导出时运行 `python scripts/run_checks.py --media`。确认两个 Skill 的新安装副本包含一致版本元数据，按安装器结果核对文件，再检查拟使用的标签。

将实际通过、跳过和未验证范围记入 [验证记录](verification.md)。本地检查、合成媒体和安装验收分别说明其覆盖范围；不将它们改称真实来源已听校、飞书交付成功、AI 生成相似或广告效果已验证。

提交、推送、标签和 Release 按当前任务的授权执行。完成后核对远端提交与对应版本文件，再报告具体版本和发布状态；仅有本地版本文件时报告本地准备完成。

版本流程不扩大 [仓库访问与使用](repository-access.md) 的权限或 [来源与许可状态](../PROVENANCE.md) 的授权范围。仓库保持公开，原仓库写入、合并和管理权限仍限所有者 `lamtrang5508647` 及其明确授权自动化；不新增整体开源许可，不把业务配置、认证、真实媒体和运行状态纳入发布。
