# 技能共享平台实施状态

更新时间：2026-09-20

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
| 2 贡献与目录构建 | 已完成 | commits `f4b462c`、`0d603d6`、`580cf97`、`aa49e60`、`3d2505e`；目录契约与构建测试持续通过；44 个公开技能、4 篇 ready 推荐 |
| 3 静态站 | 已完成 | Astro 7.0.0 构建 61 个静态路由；Pagefind 1.5.2 Extended 索引 44 个技能详情与 4 类筛选；36 项查询通过 |
| 4 安装验证 | 已完成 | 27 项验证测试与 39 项目录测试通过；dirfd 原子发布、Git object 快照、失败分阶段报告及最终独立复审 clean |
| 5 发布恢复 | 已完成 | 52 项发布/恢复测试通过；不可变快照、原子 no-clobber、历史验真、显式 tombstone 与最终独立复审 clean |
| 6 feed 读取筛选 | 已完成 | 83 项 sharing 全套、57 项核心与 14 项对抗复现通过；双来源、撤回/不可用控制变化及最终独立复审 clean |
| 7 SQLite 交接 | 已完成 | 46 项交接测试、103 项 sharing 全套与竞态/损坏探针通过；原子 JSON、精确 ack、uncertain 结果及最终独立复审 clean |
| 8 Luna 体验验收 | 已完成（环境阻塞） | 17 项观察器测试与独立复审 clean；隔离 runner 生成 5/5 `not_run`，缺可用 Luna 凭据/受信宿主，未伪称通过 |
| 9 迁移清理 | 已完成 | commit `91b3ec0`；只读 preview、固定 detached 旧版、新四入口、干净 wheel/sdist 和 21 项迁移清理测试通过 |
| 10 文档与验收 | 已完成（本地边界） | commit `d57054d`；中文指南与可审计脚本已完成；隔离验收发现 275 项测试（其中 5 项递归验收测试按设计 skip），目录、站点、发布恢复、prepare/ack 全部通过；1,000/10,000 规模探针通过 |
| 11 最终审阅 | 已完成（本地审阅） | 全分支本地审阅发现并修正规模 fixture 误用语义查询集；直接全测 275/275、锁文件 `npm ci`、Astro/Pagefind 与隔离验收通过；独立 reviewer 因代理用量上限未能启动，需在后续 PR 审阅补齐 |
| 12 稳定系列与推荐中心 | 实施完成，待最终复审 | 固定提交收录 Superpowers 15 项和 Matt Pocock 25 项；推荐改用 Markdown Frontmatter；新增 `/recommendations/`、共享贡献提示词、安全渲染和精确关联技能检查 |

## 已知边界与决定

- Context7 MCP 在本会话工具列表中不可用；已改查 Astro、Pagefind、jsonschema 官方文档，实施时锁定实际版本并记录日期。
- 当前 Python 环境没有 pytest；仓库测试可用 `unittest`。旧基线以 `PYTHONPATH=src python -m unittest discover -s tests -t . -v` 运行 557 项，其中 556 通过，1 项因 worktree 中可发现 `superpowers` Profile 而失败。阶段 0 已提交为 `90d34a0`。
- 蓝区 Git/CI、静态托管域名/base path、不可变发布存储、高区持久卷和可用 Luna 测试凭据尚未知；先以文件系统发布适配器、fixture、本地 HTTP 和隔离目录完成独立模块。
- 本期不实现 bot 发送器、SDK/HTTP transport、平台签名、群卡、群回执或 bot mock。
- 远端 push、PR、合入、正式部署和发群均未授权。
- 2026-09-16 使用 Node.js v22.19.0 官方 Linux 包（官方 SHASUMS256 与本地摘要均为 `c0649af18e6a24f6fe5535a3e86b341dd49a8e71117c8b68bde973ef834f16f2`）完成隔离验收。报告为 `/tmp/hwskill-release-verification-20260916-v4.jsonl`（SHA-256 `13bc55c23640a7854cee718e7701b90d75fdcbfec0dc303beb143541e07ea1fe`）和 `/tmp/hwskill-scale-verification-20260916-v3.jsonl`（SHA-256 `b671cf52309bc7f810de90958d933f405987ce55a510f9be943b7594d197f10a`）。这些是本主机临时证据，不是远端部署证据。

- Superpowers 与 Matt Pocock 共 40 个系列条目只完成来源、目录、许可证和元数据核验；逐项 installation/behavior 均未运行。Matt Pocock 的 `in-progress`、`misc`、`deprecated` 目录明确排除。未验证状态在网站显示警告，但不封锁用户复制安装提示词。

## Ruling 记录

- Ruling: 新版直接采用独立 `directory/publishing/sharing/verification` 包，不扩展旧 `SkillRecord` — external-only 来源与旧 Path/SKILL.md 强绑定不兼容 — 若判断错误的成本是迁移代码重写，但可避免永久双运行时。
- Ruling: 首批技能允许多个领域以候选+明确未验证状态进入目录，但首页精选优先有安装证据者 — 规格允许待验证收录 — 若来源最终不适配则撤回条目并保留调研记录。
- Ruling: Astro 锁定 7.0.0，Pagefind 锁定 1.5.2 Extended，Node 下限采用 22.19.0 — `npm ci` 的锁定依赖 `undici@8.10.2` 要求该下限，22.12 会产生 EBADENGINE — 若组织镜像不支持则保留静态 JSON/HTML 边界并记录设施阻碍，不伪称站点通过。
