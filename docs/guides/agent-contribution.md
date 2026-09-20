# Agent 贡献指引

目标仓库是 https://github.com/hwskill/hwskill 。最终产物是该仓库默认分支上的 Pull Request。技能贡献和推荐文章都从网站静态提示词开始；提示词链接本指引、Schema、模板和当前目录。

1. 取得目标仓库并读取当前默认分支。使用已有 checkout 或 Fork，不覆盖用户无关的本地修改。
2. 从公开 `data/catalog.json` 或仓库 `entries/` 检查重复 ID，并读取当前 `schemas/` 和 `templates/`。
3. hosted 技能放到 `skills-src/<layer>/<namespace>/<name>/`；external 只填写公开来源、目录路径和 immutable full SHA，不复制正文或执行上游脚本。
4. 记录来源链接、查询日期、许可证、兼容性、依赖与限制。入口依赖放入 `install.included_skills`。不能确认的值写 `unknown`；安装或行为未运行必须明确写入限制，不能伪造 `pass`。
5. 推荐源文件是 `recommendations/<id>.md`。Frontmatter 使用 `schemas/recommendation-source.schema.json` 和 `templates/recommendations/recommendation.md`；正文使用 Markdown。构建会生成带 `body`、`body_format: markdown` 的 `schemas/recommendation.schema.json` v1 机器文档。
6. Markdown 可使用标题、段落、列表、表格、引用、代码和链接。不要依赖原始 HTML；危险协议、带用户名或密码的 URL 不会发布。正文应分开说明来源事实、作者判断、适用场景、成本和验证边界。
7. 逐项检查推荐的 `skills[].id`。未收录技能必须在同一个 PR 中新增 Entry；全部引用有效时才可设为 `ready`，否则保持 `draft` 并记录缺失信息。
8. 运行 `PYTHONPATH=src python3 -m hwskill.directory validate --repo-root . --json` 和 `PYTHONPATH=src python3 -m hwskill.directory build --repo-root . --out /tmp/hwskill-directory --json`。按 issue 的 `file`、`field`、`code`、`suggested_action` 修复，且不要提交临时输出。
9. 站点变更或推荐文章还要在 `site/` 运行 `node --test tests/*.test.mjs`、`npm run build` 和 `npm run index`，确认推荐中心、关联技能链接和 Pagefind 检查通过。
10. 检查 `git status`、暂存文件和 diff，创建独立贡献提交。PR 正文列出来源、固定版本、验证结果和未验证边界。
11. 网站完整提示词包含本次贡献的建分支、提交、推送和建 PR 授权，但不授权合并。确认 GitHub 凭据和目标远程后执行到 PR 创建完成，最后返回 PR URL；没有权限时准备本地提交与 PR 正文并明确阻塞项。

未验证条目可以继续生成安装提示词；提示词必须要求 Agent 先核对来源、版本、完整材料和权限。不得把警告变成静默安装，也不得把“已准备补丁”描述成“已创建 PR”。
