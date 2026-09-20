# 贡献技能目录

目标仓库：https://github.com/hwskill/hwskill 。在此仓库准备分支或 Fork，并向其实际默认分支提交 Pull Request。

目录人工输入包括 `entries/`、`recommendations/` 与 hosted 的 `skills-src/`。先从 `templates/` 复制模板，再按 `schemas/` 填写。JSON Schema 是结构规范；构建产物和 `site/public/` 下的生成文件不要手工编辑。

## 贡献技能

external 技能只能提交来源定位、不可变 full SHA、兼容性声明和限制，禁止复制正文或执行上游脚本。入口技能依赖其他技能时，通过 `install.included_skills` 保留完整安装集合。来源和元数据核验不能写成安装或行为验证通过。

未完成安装或行为验证的技能仍可收录，并可在网站生成安装提示词。条目必须明确提示 `not_run`、依赖与风险，让用户知情后决定是否继续，不能把未验证状态写成安装成功。

## 贡献 Markdown 推荐

推荐源文件为 `recommendations/<id>.md`，从 `templates/recommendations/recommendation.md` 开始。YAML Frontmatter 由 `schemas/recommendation-source.schema.json` 校验，至少填写：

- `schema_version`、`id`、`title`、`summary`、`author`、`status`
- `skills`，每项引用一个现有 Entry ID
- 可选的 `topics`、`evidence` 和撤回原因

结束 Frontmatter 后编写 Markdown 正文。支持标题、段落、列表、表格、引用、代码和安全链接；原始 HTML 不执行，危险协议和带凭据链接不会发布。构建会把正文规范化为 `body` 和 `body_format: markdown`，并按 `schemas/recommendation.schema.json` 生成兼容的 v1 机器文档。

`ready` 推荐引用的每个 `skills[].id` 都必须存在且有效。推荐涉及尚未收录的技能时，必须在同一个 PR 中增加对应 `entries/` 条目；信息没有补齐时保持 `draft` 并说明缺口。

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

网站 `/contribute/` 和 `/recommendations/` 都提供可复制的 Agent 提示词。完整提示词授权 Agent 为该次贡献创建分支、提交、推送并向目标默认分支创建 PR，但不授权合并。PR 正文使用 `.github/PULL_REQUEST_TEMPLATE.md`，最终返回实际 PR URL 和验证结果；没有凭据或权限时明确报告阻塞项。
