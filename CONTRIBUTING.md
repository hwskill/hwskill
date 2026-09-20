# 贡献技能目录

目标仓库：https://github.com/hwskill/hwskill 。在此仓库准备分支或 Fork，并向其默认分支提交 Pull Request。

目录输入是 `entries/`、`recommendations/` 与 hosted 的 `skills-src/`。先从 `templates/` 复制模板，按 `schemas/` 填写；JSON Schema 是唯一结构规范。

推荐文章引用的技能尚未收录时，在同一个 PR 中先增加对应 `entries/` 条目。所有引用均有效时推荐才可设为 `ready`；信息未补齐时保持 `draft`。

外部技能只能提交来源定位、不可变 ref、兼容性声明和限制，禁止复制其正文。入口技能依赖其他技能时，通过 `install.included_skills` 保留完整安装集合。不要把安装或行为验证未运行的条目写成通过。

```bash
PYTHONPATH=src python -m hwskill.directory validate --repo-root . --json
PYTHONPATH=src python -m hwskill.directory build --repo-root . --out /tmp/hwskill-directory
```

`validate` 不联网且不修改输入；退出码 0、1、3 分别表示通过、输入不合格、环境受限。贡献前从实际仓库读取默认分支，不硬编码分支名；提交前更新目标基线并重新校验。

检查变更范围后创建独立贡献分支和提交，按 `.github/PULL_REQUEST_TEMPLATE.md` 准备说明。获得用户授权并确认 GitHub 凭据后推送分支、向目标仓库默认分支创建 Pull Request，并返回实际 PR URL。
