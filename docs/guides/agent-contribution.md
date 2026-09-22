# Agent 贡献指引

目标仓库是 https://github.com/hwskill/hwskill 。最终产物是该仓库默认分支上的 Pull Request。技能贡献和推荐文章都从网站静态提示词开始；提示词链接本指引、Schema、模板和当前目录。

1. 取得目标仓库并读取当前默认分支。使用已有 checkout 或 Fork，不覆盖用户无关的本地修改。
2. 从公开 `data/catalog.json` 或仓库 `entries/` 检查重复 ID，并读取当前 `schemas/` 和 `templates/`。
3. hosted 技能放到 `skills-src/<layer>/<namespace>/<name>/`；external 填写公开来源和目录路径，可选填写 branch、tag 或 commit，也可省略 ref 并跟随默认分支。不要执行不受信任的上游脚本。
4. 记录来源链接、查询日期、许可证、兼容性、依赖与限制。入口依赖放入 `install.included_skills`。不能确认的值写 `unknown`。在 `translations/<namespace>/<name>.md` 提交技能原文的中文译文，并确认许可证允许翻译和再分发。
5. 推荐源文件是 `recommendations/<id>.md`。Frontmatter 使用 `schemas/recommendation-source.schema.json` 和 `templates/recommendations/recommendation.md`；正文使用 Markdown。构建会生成带 `body`、`body_format: markdown` 的 `schemas/recommendation.schema.json` v1 机器文档。
6. Markdown 可使用标题、段落、列表、表格、引用、代码和链接。不要依赖原始 HTML；危险协议、带用户名或密码的 URL 不会发布。推荐正文应分开说明来源事实、作者判断、适用场景、成本和组合边界，不维护技能验证状态。
7. 逐项检查推荐的 `skills[].id`。未收录技能必须在同一个 PR 中新增 Entry；全部引用有效时才可设为 `ready`，否则保持 `draft` 并记录缺失信息。
8. 运行 `PYTHONPATH=src python3 -m hwskill.directory validate --repo-root . --json` 和 `PYTHONPATH=src python3 -m hwskill.directory build --repo-root . --out /tmp/hwskill-directory --json`。按 issue 的 `file`、`field`、`code`、`suggested_action` 修复，且不要提交临时输出。
9. 站点变更或推荐文章还要在 `site/` 运行 `node --test tests/*.test.mjs`、`npm run build` 和 `npm run index`，确认推荐中心、关联技能链接和 Pagefind 检查通过。
10. 检查 `git status`、暂存文件和 diff，创建独立贡献提交。新增或修改技能材料时，在 PR 正文的 `## 技能验证报告` 下完整填写：验证环境、获取的上游 commit、安装目标、安装步骤、完整性检查、使用任务、实际结果、未验证项、已知限制。
11. 网站完整提示词包含本次贡献的建分支、提交、推送和建 PR 授权，但不授权合并。确认 GitHub 凭据和目标远程后执行到 PR 创建完成，最后返回 PR URL；没有权限时准备本地提交与 PR 正文并明确阻塞项。

报告中的“获取的上游 commit”只说明本次 PR 实际检查的输入：来源指定 ref 时检查该 ref，未指定时记录当时默认分支 HEAD。不要把它写回 Entry、Catalog、网站或 feed。网站安装提示词要求 Agent 检查声明来源、可选 ref、完整材料和权限；不得把“已准备补丁”描述成“已创建 PR”。
