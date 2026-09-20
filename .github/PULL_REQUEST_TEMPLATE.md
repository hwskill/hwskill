## 变更内容

- [ ] 新增或修改技能条目
- [ ] 新增或修改推荐文章
- [ ] 其他：

请说明这次贡献解决的问题，以及新增或修改的稳定 ID。

## 来源与版本

列出公开来源、不可变提交或版本、仓库内路径、许可证及查询日期。推荐文章同时列出关联技能 ID；同一 PR 新增的技能请明确标注。

## 验证

```text
PYTHONPATH=src python -m hwskill.directory validate --repo-root . --json
PYTHONPATH=src python -m hwskill.directory build --repo-root . --out /tmp/hwskill-directory
```

粘贴结果摘要，并说明是否预览了生成页面。

## 验证边界

说明没有执行的安装、行为或联网检查。不要把 `not_run`、`unknown`、`blocked` 写成通过。

## 变更范围

确认没有包含凭据、外部技能正文、生成目录或无关工作区修改。
