# 贡献技能目录

目标仓库：https://github.com/hwskill/hwskill 。在此仓库准备分支或 Fork，并向其实际默认分支提交 Pull Request。

目录人工输入包括 `entries/`、`recommendations/` 与 hosted 的 `skills-src/`。先从 `templates/` 复制模板，再按 `schemas/` 填写。JSON Schema 是结构规范；构建产物和 `site/public/` 下的生成文件不要手工编辑。

## 贡献技能

external 技能提交来源定位、兼容性声明和限制；`source.locator.ref` 可填写 branch、tag 或 commit，也可省略并跟随上游默认分支。入口技能依赖其他技能时，通过 `install.included_skills` 保留完整安装集合。不要执行不受信任的上游脚本。

每个技能都要在 `translations/<namespace>/<name>.md` 提交原文中文译文；翻译和再分发必须符合来源许可证。目录、网站和 feed 不维护安装或使用状态，相关证据只随贡献 PR 提交“技能验证报告”。

## 贡献 Markdown 推荐

推荐源文件为 `recommendations/<id>.md`，从 `templates/recommendations/recommendation.md` 开始。YAML Frontmatter 由 `schemas/recommendation-source.schema.json` 校验，至少填写：

- `schema_version`、`id`、`title`、`summary`、`author`、`status`
- `skills`，每项引用一个现有 Entry ID
- 可选的 `topics`、`evidence` 和撤回原因

结束 Frontmatter 后编写 Markdown 正文。支持标题、段落、列表、表格、引用、代码和安全链接；原始 HTML 不执行，危险协议和带凭据链接不会发布。构建会把正文规范化为 `body` 和 `body_format: markdown`，并按 `schemas/recommendation.schema.json` 生成兼容的 v1 机器文档。

`ready` 推荐引用的每个 `skills[].id` 都必须存在且有效。推荐涉及尚未收录的技能时，必须在同一个 PR 中增加对应 `entries/` 条目；信息没有补齐时保持 `draft` 并说明缺口。

推荐正文说明来源事实、作者判断、适用场景、成本和组合边界，不维护技能验证状态。

## 技能验证报告

新增或修改 `entries/`、`translations/` 或 `skills-src/` 的 PR 必须在正文中保留以下标题和九个字段，且每项填写实际内容：

```markdown
## 技能验证报告

- 验证环境：
- 获取的上游 commit：
- 安装目标：
- 安装步骤：
- 完整性检查：
- 使用任务：
- 实际结果：
- 未验证项：
- 已知限制：
```

“获取的上游 commit”记录该 PR 检查时实际取得的内容。条目指定 ref 时检查该 ref；未指定时检查当时默认分支 HEAD。该 commit 不进入 Entry、Catalog、网站或 feed。无法执行的检查写入“未验证项”，不要编造结果。仅修改文档、站点或推荐文章且未修改技能材料时无需此报告。

## 校验并创建 PR

在仓库根目录运行：

```bash
PYTHONPATH=src python3 -m hwskill.directory validate --repo-root . --json
PYTHONPATH=src python3 -m hwskill.directory build --repo-root . --out /tmp/hwskill-directory --json
cd site
node --test tests/*.test.mjs
npm run build
npm run index
```

`validate` 不联网且不修改输入；退出码 0、1、3 分别表示通过、输入不合格、环境受限。提交前更新目标基线、重新校验，并检查 `git status`、暂存文件和 diff，只保留本次贡献。

网站 `/contribute/` 和 `/recommendations/` 都提供可复制的 Agent 提示词。完整提示词授权 Agent 为该次贡献创建分支、提交、推送并向目标默认分支创建 PR，但不授权合并。PR 正文使用 `.github/PULL_REQUEST_TEMPLATE.md`；涉及技能材料时完整填写验证报告。最终返回实际 PR URL 和检查结果；没有凭据或权限时明确报告阻塞项。
