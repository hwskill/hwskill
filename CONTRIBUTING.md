# 贡献技能目录

目录输入是 `entries/`、`recommendations/` 与 hosted 的 `skills-src/`。先从 `templates/` 复制模板，按 `schemas/` 填写；JSON Schema 是唯一结构规范。

外部技能只能提交来源定位、不可变 ref、兼容性声明和限制，禁止复制其正文。不要把安装或行为验证未运行的条目写成通过。

```bash
PYTHONPATH=src python -m hwskill.directory validate --repo-root . --json
PYTHONPATH=src python -m hwskill.directory build --repo-root . --out /tmp/hwskill-directory
```

`validate` 不联网且不修改输入；退出码 0、1、3 分别表示通过、输入不合格、环境受限。贡献前从实际仓库读取默认分支，不硬编码分支名；提交前更新目标基线并重新校验。
