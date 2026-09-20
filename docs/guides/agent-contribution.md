# Agent 贡献指引

目标仓库是 https://github.com/hwskill/hwskill 。贡献的最终产物是该仓库默认分支上的 Pull Request。技能贡献和推荐文章都从网站静态提示词开始；提示词链接本指引、Schema、模板和当前目录。

1. 取得目标仓库并读取其当前默认分支。使用已有 checkout 或 Fork；不要覆盖用户无关的本地修改。
2. 从 `schemas/` 和 `templates/` 读取当前契约，并从公开 `data/catalog.json` 或仓库 `entries/` 确认目标 ID 没有重复。
3. hosted 技能放到安全的 `skills-src/<layer>/<namespace>/<name>/`；external 只填写公开来源、目录路径和 immutable full SHA，绝不复制正文或执行上游脚本。
4. 记录来源链接、查询日期、许可证、兼容性和限制。入口技能依赖其他技能时，在 `install.included_skills` 列出完整安装集合，并在 requirements 或 limitations 说明依赖关系。不能确认的值写 `unknown`，安装/行为未运行必须明确为 `not_run` 或 unknown。
5. 撰写推荐文章时逐项检查 `skills[].id`。未收录技能必须在同一个变更中先增加 Entry；全部引用都有效时推荐才可设为 `ready`，否则保持 `draft` 并记录缺失信息。
6. 在仓库根目录运行 `python -m pip install 'setuptools>=68' wheel .` 安装 Python 项目依赖，再运行 `PYTHONPATH=src python -m hwskill.directory validate --repo-root . --json`，按 issue 的 `file`、`field`、`code`、`suggested_action` 修复。
7. 运行 `PYTHONPATH=src python -m hwskill.directory build --repo-root . --out /tmp/hwskill-directory`。构建产物是派生物，不修改人工源；不要提交临时输出。
8. 检查 `git status`、暂存文件和 diff，只保留本次贡献；创建独立贡献分支并提交。PR 正文使用 `.github/PULL_REQUEST_TEMPLATE.md`，列出来源、固定版本、验证结果和未验证边界。
9. 推送或创建 PR 前确认用户请求是否已经授权这些动作；网站提供的完整贡献提示词包含本次贡献的分支、提交、推送和建 PR 授权，但不授权合并。确认 GitHub 凭据与目标远程可用后，推送贡献分支并向目标仓库实际默认分支创建 Pull Request，最后返回 PR URL。没有权限时准备好本地提交和完整 PR 正文，明确报告阻塞项。

不得把“已准备补丁”描述成“已创建 PR”。推送、创建 PR 或向外部系统发送消息需要用户授权和真实凭据。
