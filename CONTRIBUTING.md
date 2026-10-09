# 贡献技能目录

目标仓库：https://github.com/hwskill/hwskill 。在此仓库准备分支或 Fork，并向其实际默认分支提交 Pull Request。

目录人工输入包括 `entries/`、`recommendations/` 与 hosted 的 `skills-src/`。先从 `templates/` 复制模板，再按 `schemas/` 填写。JSON Schema 是结构规范；构建产物和 `site/public/` 下的生成文件不要手工编辑。

## Agent 开始前能力检查

网站生成的完整提示词要求 Agent 在修改文件前确认：能够联网读取公开来源和 GitHub；能够读写本地工作区、编辑代码、提交、推送并创建 PR；能够通过 subagent 或命令执行建立临时 clone 或隔离 worktree 并完成干净验证。subagent 不是硬性依赖，但命令执行和隔离验证能力必须具备。联网或工作区读写能力不足时应立即停止并说明阻塞。

Agent 优先通过 `gh --version` 与 `gh auth status --hostname github.com` 检查 GitHub 环境。缺少 gh 时按官方说明引导安装；未登录时引导用户完成 `gh auth login --hostname github.com --web`。没有直接写权限时创建或复用 Fork，检查推送凭据和分支保护。授权尚未完成时保留进度，可先准备本地内容与 PR 正文，配置完成后继续提交。不要把登录成功等同于推送权限已验证。

## 贡献技能

external 技能引用公开的外部仓库或网页，原始技能继续由外部来源维护；HWSkill 只提交来源定位、兼容性声明和限制。`source.locator.ref` 可填写 branch、tag 或 commit，也可省略并跟随上游默认分支。hosted 技能则把贡献者拥有且获准再分发的完整技能目录放入 `skills-src/`。入口技能依赖其他技能时，通过 `install.included_skills` 保留完整安装集合。不要执行未经审阅的不可信上游脚本。

每个技能都要由 Agent 读取完整原文和相关材料，在 `translations/<namespace>/<name>.md` 生成完整中文译文；代码、命令、链接和技术含义必须保留，不能添加原文没有的能力声明。Agent 必须查找 LICENSE 或公开授权条款、记录证据 URL，并确认允许翻译和再分发；许可证缺失、含糊或不允许时，在修改前停止，不能自行推定允许。目录、网站和 feed 不维护安装或使用状态，相关证据只随贡献 PR 提交“技能验证报告”。

## 贡献 Markdown 推荐

推荐源文件为 `recommendations/<id>.md`，从 `templates/recommendations/recommendation.md` 开始。YAML Frontmatter 由 `schemas/recommendation-source.schema.json` 校验，至少填写：

- `schema_version`、`id`、`title`、`summary`、`author`、`status`
- `skills`，每项引用一个现有 Entry ID
- 可选的 `topics`、`evidence` 和撤回原因

结束 Frontmatter 后编写 Markdown 正文。支持标题、段落、列表、表格、引用、代码和安全链接；原始 HTML 不执行，危险协议和带凭据链接不会发布。构建会把正文规范化为 `body` 和 `body_format: markdown`，并按 `schemas/recommendation.schema.json` 生成兼容的 v1 机器文档。

`ready` 推荐引用的每个 `skills[].id` 都必须存在且有效。推荐涉及尚未收录的技能时，本次任务会扩展为复合贡献。Agent 必须在同一个 PR 中补齐对应技能的元数据、中文翻译、许可证核验和验证报告；需要时请用户补充技能来源或用途。信息没有补齐时保持 `draft` 并说明缺口。

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

`validate` 不联网且不修改输入；退出码 0、1、3 分别表示通过、输入不合格、环境受限。Agent 应在临时 clone 或隔离 worktree 中从目标最新默认分支开始应用变更并运行这些检查。提交前更新目标基线、重新校验，并检查 `git status`、暂存文件和 diff，只保留本次贡献。

网站 `/contribute/` 提供统一表单，技能推荐列表页链接到该表单。仅技能来源必填，其余客观元数据可由 Agent 核实补齐；填写推荐内容时同时准备推荐文案，缺少署名或个人使用体验时请用户确认。完整提示词授权 Agent 为该次贡献创建分支、提交、推送并向目标默认分支创建 PR，但不授权合并。PR 正文使用 `.github/PULL_REQUEST_TEMPLATE.md`；涉及技能材料时由 Agent 根据干净环境中的真实结果完整填写验证报告。创建 PR 后等待必要 CI，失败时在同一分支修复。最终返回实际 PR URL、目标分支、CI 状态、检查结果和未验证边界。
