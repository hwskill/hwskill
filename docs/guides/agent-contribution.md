# Agent 贡献指引

目标仓库是 https://github.com/hwskill/hwskill 。最终产物是以该仓库默认分支为目标的 Pull Request。统一表单只要求技能来源；未填写的客观元数据由 Agent 从来源补齐。补充推荐内容时同时准备推荐文案，信息不全时保留推荐意图并请用户确认，不自动降级为仅收录。署名和个人使用体验不能编造。

1. 在修改文件前完成能力预检：必须能联网读取公开来源、贡献资料和 GitHub；必须能读写本地工作区、编辑代码并执行 Git 提交、推送和建 PR；必须能通过 subagent 或命令执行创建临时 clone 或隔离 worktree 并运行验证。subagent 本身不是强制条件，但命令执行和隔离验证能力不可缺少。关键能力缺失时立即停止并说明阻塞。
2. 优先使用 GitHub CLI：运行 `gh --version` 和 `gh auth status --hostname github.com`。缺少 gh 时识别操作系统并按照 https://cli.github.com/ 的官方说明引导安装，系统安装或提权需用户许可；未登录时引导 `gh auth login --hostname github.com --web`，由用户完成认证，不索取聊天中的令牌。登录后重新检查身份，按需执行 `gh auth setup-git`。检查目标默认分支、写权限、推送凭据和分支保护；没有直接写权限时创建或复用 Fork。登录成功不代表推送权限已验证。授权未完成时可保留进度、先准备本地变更和 PR 正文，配置完成后继续推送与建 PR。使用已有 checkout 时不得覆盖用户无关修改。
3. 从公开 `data/catalog.json` 或仓库 `entries/` 检查重复 ID，并读取当前 `schemas/` 和 `templates/`。
4. hosted 表示把贡献者拥有且获准再分发的完整技能目录放到 `skills-src/<layer>/<namespace>/<name>/`；external 表示原技能留在外部公开来源，本仓只记录来源和目录定位。external 可填写 branch、tag 或 commit，也可省略 ref 并跟随默认分支。不要执行未经审阅的不可信上游脚本。
5. 查找 LICENSE 或公开授权条款，记录证据 URL，并确认允许翻译和再分发。许可证缺失、含糊或不允许时，在写入技能文件前停止并说明阻塞，不能自行推定允许。记录来源链接、查询日期、兼容性、依赖与限制；入口依赖放入 `install.included_skills`。
6. 读取完整技能原文和相关材料，在 `translations/<namespace>/<name>.md` 生成完整中文译文。保留代码、命令、链接和技术含义，不添加原文没有的能力声明。
7. 推荐源文件是 `recommendations/<id>.md`。Frontmatter 使用 `schemas/recommendation-source.schema.json` 和 `templates/recommendations/recommendation.md`；正文使用 Markdown。构建会生成带 `body`、`body_format: markdown` 的 `schemas/recommendation.schema.json` v1 机器文档。
8. Markdown 可使用标题、段落、列表、表格、引用、代码和链接。不要依赖原始 HTML；危险协议、带用户名或密码的 URL 不会发布。推荐正文应分开说明来源事实、作者判断、适用场景、成本和组合边界，不维护技能验证状态。
9. 逐项检查推荐的 `skills[].id`。未收录技能会把推荐扩展成复合贡献；在同一个 PR 中补齐技能元数据、中文翻译、许可证核验和验证报告。需要时请用户补充来源或用途。全部引用有效时才可设为 `ready`，否则保持 `draft` 并记录缺失信息。
10. 内容完成后，在临时 clone 或隔离 worktree 中从目标最新默认分支开始应用本次变更，运行 `PYTHONPATH=src python3 -m hwskill.directory validate --repo-root . --json` 和 `PYTHONPATH=src python3 -m hwskill.directory build --repo-root . --out /tmp/hwskill-directory --json`。按 issue 的 `file`、`field`、`code`、`suggested_action` 修复，且不要提交临时输出。
11. 站点变更或推荐文章还要在 `site/` 运行 `node --test tests/*.test.mjs`、`npm run build` 和 `npm run index`，确认推荐中心、关联技能链接和 Pagefind 检查通过。
12. 在安全且环境允许时安装技能并执行一个代表性使用任务。检查 `git status`、暂存文件和 diff，创建独立贡献提交。新增或修改技能材料时，根据真实结果填写 PR 正文的九项技能验证报告；无法安全执行或环境不具备的项目写入“未验证项”，不能写成通过。
13. 网站完整提示词授权本次贡献的建分支、提交、推送和建 PR，但不授权合并。创建 PR 后等待必要 CI；失败时在同一分支修复并重新检查。最后返回实际 PR URL、目标分支、CI 状态、验证结果和未验证边界，不能把本地提交或 PR 文案描述成已创建 PR。

报告中的“获取的上游 commit”只说明本次 PR 实际检查的输入：来源指定 ref 时检查该 ref，未指定时记录当时默认分支 HEAD。不要把它写回 Entry、Catalog、网站或 feed。网站安装提示词要求 Agent 检查声明来源、可选 ref、完整材料和权限；不得把“已准备补丁”描述成“已创建 PR”。
