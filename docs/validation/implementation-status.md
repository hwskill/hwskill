# 技能共享平台实施状态

更新时间：2026-09-11

## 恢复入口

- 实施计划：`docs/superpowers/plans/2026-09-11-cross-project-skill-sharing.md`
- 需求规格：`docs/superpowers/specs/2026-09-11-cross-project-skill-sharing-design.md`
- 技术设计：`docs/superpowers/specs/2026-09-11-skill-sharing-technical-design.md`
- 交付方案：`docs/superpowers/specs/2026-09-11-delivery-approach.md`
- 分支：`codex/cross-project-skill-sharing`
- 旧版固定标签：`hwskill-legacy-v0.1.0`，目标 `45825a5`

## 当前状态

| Task | 状态 | 证据/下一步 |
| --- | --- | --- |
| 0 基线与计划 | 已完成 | commit `90d34a0`；三份规格 SHA-256 与原目录一致 |
| 1 目录契约 | 已完成 | commits `b3e93b4`、`31383e8`、`a133f8b`；22 项目录与 wheel 测试通过，任务复审 clean |
| 2 贡献与目录构建 | 已完成 | commits `f4b462c`、`0d603d6`、`580cf97`、`aa49e60`、`3d2505e`；38 项测试与最终复审 clean；6 个公开技能、2 篇 ready 推荐 |
| 3 静态站 | 已完成 | Astro 7.0.0 构建 16 个静态路由；Pagefind 1.5.2 Extended 仅索引 6 个技能详情与 4 类筛选；33 项真实查询通过，最终复审 clean |
| 4 安装验证 | 已完成 | 27 项验证测试与 39 项目录测试通过；dirfd 原子发布、Git object 快照、失败分阶段报告及最终独立复审 clean |
| 5 发布恢复 | 未开始 | 依赖 Task 2/3/4 |
| 6 feed 读取筛选 | 未开始 | 依赖 Task 5 release Schema |
| 7 SQLite 交接 | 未开始 | 依赖 Task 6 |
| 8 Luna 体验验收 | 未开始 | 依赖 Task 2/3/4 |
| 9 迁移清理 | 未开始 | 所有替代闭环通过后执行 |
| 10 文档与验收 | 未开始 | 随阶段持续更新，最后汇总 |
| 11 最终审阅 | 未开始 | 全分支完成后执行 |

## 已知边界与决定

- Context7 MCP 在本会话工具列表中不可用；已改查 Astro、Pagefind、jsonschema 官方文档，实施时锁定实际版本并记录日期。
- 当前 Python 环境没有 pytest；仓库测试可用 `unittest`。旧基线以 `PYTHONPATH=src python -m unittest discover -s tests -t . -v` 运行 557 项，其中 556 通过，1 项因 worktree 中可发现 `superpowers` Profile 而失败。阶段 0 已提交为 `90d34a0`。
- 蓝区 Git/CI、静态托管域名/base path、不可变发布存储、高区持久卷和可用 Luna 测试凭据尚未知；先以文件系统发布适配器、fixture、本地 HTTP 和隔离目录完成独立模块。
- 本期不实现 bot 发送器、SDK/HTTP transport、平台签名、群卡、群回执或 bot mock。
- 远端 push、PR、合入、正式部署和发群均未授权。

## Ruling 记录

- Ruling: 新版直接采用独立 `directory/publishing/sharing/verification` 包，不扩展旧 `SkillRecord` — external-only 来源与旧 Path/SKILL.md 强绑定不兼容 — 若判断错误的成本是迁移代码重写，但可避免永久双运行时。
- Ruling: 首批技能允许多个领域以候选+明确未验证状态进入目录，但首页精选优先有安装证据者 — 规格允许待验证收录 — 若来源最终不适配则撤回条目并保留调研记录。
- Ruling: Astro 锁定 7.0.0，Pagefind 锁定 1.5.2 Extended，Node 下限采用 22.19.0 — `npm ci` 的锁定依赖 `undici@8.10.2` 要求该下限，22.12 会产生 EBADENGINE — 若组织镜像不支持则保留静态 JSON/HTML 边界并记录设施阻碍，不伪称站点通过。
