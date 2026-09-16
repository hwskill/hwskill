# 开发指南

## 代码与事实源

- `entries/`、`recommendations/`、`curation/`：人工维护事实源。
- `skills-src/`：仅保存 hosted 条目的完整正文；external 只保存定位与版本信息。
- `src/hwskill/directory/`：Schema、交叉校验和确定性构建。
- `src/hwskill/verification/`：来源绑定、隔离安装和阶段报告。
- `src/hwskill/publishing/`：不可变 release、事件、回滚与恢复。
- `src/hwskill/sharing/`：安全 feed 读取、筛选、SQLite 状态和 prepare/ack。
- `site/`：Astro 静态站与 Pagefind 查询验收。

旧 Profile/Hook/MCP runtime 不属于新版开发面；不要重新引入旧的初始化、Profile、dump 或 MCP 命令，也不要恢复 `mcp` 依赖。

## 贡献检查

先阅读 `schemas/`、`templates/` 和 `docs/guides/agent-contribution.md`。常用检查为：

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src /usr/bin/python3 -m unittest discover -s tests -t . -v
PYTHONPATH=src /usr/bin/python3 -m hwskill.directory validate --repo-root . --json
PYTHONPATH=src /usr/bin/python3 -m hwskill.directory build \
  --repo-root . --out /tmp/hwskill-directory --json
npm --prefix site run build
npm --prefix site run index
git diff --check
```

构建输出目录必须是新的非重叠路径。不要把 `/tmp` 构建产物复制回事实源，也不要通过修改派生 JSON 修正输入。

## 测试分层

- `tests/directory`：Schema、摘要、hosted/external 边界和构建。
- `tests/verification`：安装目录安全、来源绑定、报告持久化和失败分段。
- `tests/publishing`：不可变发布、故障注入恢复、回滚和 tombstone。
- `tests/sharing`：URL/重定向/预算、快照绑定、筛选、SQLite 和 ack。
- `tests/site`：路由、机器数据和搜索查询。
- `tests/agent-experience`：隔离观察器；没有受信 Luna 环境时结果只能是 `blocked/not_run`。
- `tests/migration`：旧输入的只读 preview 与清理边界。
- `tests/validation`：发布验收编排本身。

行为修复应先增加能复现缺陷的 RED，再写最小修复并运行相关分组与全套测试。测试 fixture 的通过不能替代远端服务、真实宿主或硬件环境验证。

## CLI 发现

```sh
hwskill-directory --help
hwskill-publish --help
hwskill-sharing --help
hwskill-verify --help
```

四个入口来自同一 wheel。`python -m hwskill` 和旧聚合命令已退出，不应作为兼容入口使用。
