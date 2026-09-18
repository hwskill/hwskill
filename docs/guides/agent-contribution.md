# Agent 贡献指引

目标仓库是 https://github.com/hwskill/hwskill 。先取得该仓库当前默认分支，再从仓库的 `templates/entries/` 或 `templates/recommendations/` 选择模板；Schema 位于 `schemas/`。提交目标为该仓库的 Pull Request。

1. 从 `schemas/` 和 `templates/` 读取当前契约，确认目标 ID 没有重复。
2. hosted 技能放到安全的 `skills-src/<layer>/<namespace>/<name>/`；external 只填写公开来源、目录路径和 immutable full SHA，绝不复制正文或执行上游脚本。
3. 记录来源链接、查询日期、许可证、兼容性和限制。不能确认的值写 `unknown`，安装/行为未运行必须明确为 `not_run` 或 unknown。
4. 运行 `python -m hwskill.directory validate --repo-root . --json`，按 issue 的 `file`、`field`、`code`、`suggested_action` 修复。
5. 需要预览时运行 build 到新的输出路径。构建产物是派生物，不修改人工源；外部 `unknown` 安装方式不会生成可执行命令。

贡献准备只产生补丁与本地检查结果。推送、创建 PR 或向外部系统发送消息需要另行授权和真实凭据。
