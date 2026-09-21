## 变更内容

- [ ] 新增或修改技能条目
- [ ] 新增或修改推荐文章
- [ ] 其他：

请说明这次贡献解决的问题，以及新增或修改的稳定 ID。

## 来源与版本

列出公开来源、仓库内路径、许可证、查询日期，以及来源声明的 branch、tag 或 commit（若有）。推荐文章同时列出关联技能 ID；同一 PR 新增的技能请明确标注。

## 技能验证报告

新增或修改 `entries/`、`translations/` 或 `skills-src/` 时，完整填写以下九项。获取的上游 commit 只记录本次 PR 实际检查的内容，不会写入 Entry、Catalog 或网站；来源未指定 ref 时填写当时默认分支的 HEAD。

- 验证环境：
- 获取的上游 commit：
- 安装目标：
- 安装步骤：
- 完整性检查：
- 使用任务：
- 实际结果：
- 未验证项：
- 已知限制：

## 确定性检查

```text
PYTHONPATH=src python -m hwskill.directory validate --repo-root . --json
PYTHONPATH=src python -m hwskill.directory build --repo-root . --out /tmp/hwskill-directory
```

粘贴结果摘要，并说明是否预览了生成页面。未执行的检查写入报告的“未验证项”，不要编造结果。

## 变更范围

确认没有包含凭据、外部技能正文、生成目录或无关工作区修改。
