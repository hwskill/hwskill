# 跨项目技能共享平台实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在保留旧 Git 历史与可回退入口的前提下，把仓库换代为可贡献、可发现、可安装、可验证、可发布并可由高区 Python 任务可靠增量读取的技能共享平台。

**Architecture:** `entries/`、`recommendations/` 与 JSON Schema 是人工事实源，Python `hwskill.directory` 负责确定性校验和规范化发布产物，Astro 与 Pagefind 只消费这些产物。发布器用不可变 release 和追加日志保证发布一致性；`hwskill.sharing` 用固定 feed 快照与本区 SQLite 管理可重复准备、显式确认的交接批次，不包含 bot 发送层。

**Tech Stack:** Python >=3.10、PyYAML、jsonschema Draft 2020-12、SQLite、Astro 7、TypeScript、Pagefind extended 1.5.x、Python unittest、Node 22.12+。

**Spec:** `docs/superpowers/specs/2026-09-11-cross-project-skill-sharing-design.md`、`docs/superpowers/specs/2026-09-11-skill-sharing-technical-design.md`、`docs/superpowers/specs/2026-09-11-delivery-approach.md`

## 全局约束

- 全部用户、贡献、部署、开发、运维、迁移与验收文档使用中文；复杂状态机和安全边界在代码中用中文注释解释原因与不变量。
- 首批正式收录 5–10 个技能，优先通用计算、智能计算、性能优化、大数据、C++/Python 与 PPT 等开发周边；保留候选与暂缓理由，不以数量凑层级。
- external 条目只保存定位、解析版本、摘要和验证证据，不备份正文；hosted 条目才读取仓库中的完整技能目录。
- 静态站、Schema、catalog、安装说明和 feed 读取默认不要求 AccessToken；仅非公开 Git 来源按需使用只读认证，认证头不跨域重定向转发。
- `install` 不接受任意 shell 字符串作为可信执行任务；未知安装方式只提供指引，不生成猜测命令。
- 发布完成前不产生可消费事件；发布重试复用候选、release ID、事件 ID 与首次发布时间，回滚不回滚事件历史。
- `prepare_updates` 只准备 Python/JSON 批次；`acknowledge` 表示调用方已接收而非已发群。禁止实现 sender、Webhook、签名、群卡、回执或 bot mock。
- 干净 Luna / medium 验收使用隔离配置根与临时项目，不修改真实 HOME，不继承开发技能/Profile/Hook/MCP；高阶模型结果不能替代 Luna 证据。
- 不推送、不创建 PR、不合入、不正式部署、不发群；阶段性只做本地提交。旧版固定标签为 `hwskill-legacy-v0.1.0`，新版分支为 `codex/cross-project-skill-sharing`。
- Context7 当前不可用；库行为只引用官方 Astro、Pagefind、jsonschema、Python 文档，并在验收记录注明检索日期与锁定版本。

---

### Task 0: 固定基线、计划、进度与设施边界

**Files:**
- Create: `docs/superpowers/specs/2026-09-11-cross-project-skill-sharing-design.md`
- Create: `docs/superpowers/specs/2026-09-11-skill-sharing-technical-design.md`
- Create: `docs/superpowers/specs/2026-09-11-delivery-approach.md`
- Create: `docs/validation/implementation-status.md`
- Create: `docs/superpowers/plans/2026-09-11-cross-project-skill-sharing.md`

**Interfaces:**
- Consumes: 旧基线 commit `45825a5` 与原目录三份未跟踪设计文档。
- Produces: 固定旧标签、实施分支、可恢复任务表、设施未知项与基线测试证据。

- [ ] **Step 1: 验证三份规格复制无损**

Run: `sha256sum /home/linkeo/code/hwskills/docs/superpowers/specs/2026-09-11-*.md docs/superpowers/specs/2026-09-11-*.md`

Expected: 每个同名文件哈希一致。

- [ ] **Step 2: 建立版本锚点并记录真实状态**

Run: `git tag --list hwskill-legacy-v0.1.0 --format='%(objectname)' && git branch --show-current`

Expected: 标签指向 `45825a5...`，分支为 `codex/cross-project-skill-sharing`。

- [ ] **Step 3: 运行旧基线并记录环境差异**

Run: `PYTHONPATH=src python -m unittest discover -s tests -t . -v`

Expected: 记录测试总数和任何既有失败；不得把环境耦合失败改写成通过。

- [ ] **Step 4: 提交规格、计划和进度入口**

```bash
git add docs/superpowers/specs docs/superpowers/plans/2026-09-11-cross-project-skill-sharing.md docs/validation/implementation-status.md
git commit -m "docs: freeze skill sharing implementation baseline"
```

### Task 1: 建立 SkillEntry 与 Recommendation 契约

**Files:**
- Modify: `pyproject.toml`
- Create: `schemas/entry.schema.json`
- Create: `schemas/recommendation.schema.json`
- Create: `schemas/catalog.schema.json`
- Create: `schemas/verification.schema.json`
- Create: `schemas/release.schema.json`
- Create: `src/hwskill/directory/{__init__,models,yaml_io,schema,entries}.py`
- Test: `tests/directory/test_entries.py`
- Test: `tests/directory/fixtures/`

**Interfaces:**
- Consumes: SkillEntry v1、Recommendation v1 与 issue 字段契约。
- Produces: `validate_repository(repo_root: Path) -> ValidationReport`；问题字段固定为 `file, field, code, severity, message, suggested_action`。

- [ ] **Step 1: 写 loader/Schema 的失败测试**

覆盖有效 hosted/external、重复 YAML key、merge key、未知字段、错误 URL/日期、ID 与路径不一致、越界路径、重复 source identity、ready 推荐缺失引用、draft 不发布、withdrawn 缺原因。每个期望值使用手写 fixture，不调用生产规范化逻辑生成。

- [ ] **Step 2: 验证 RED**

Run: `PYTHONPATH=src python -m unittest tests.directory.test_entries -v`

Expected: 因 `hwskill.directory` 尚不存在而失败。

- [ ] **Step 3: 实现最小安全读取与校验**

使用 `yaml.SafeLoader` 的自定义 mapping constructor 拒绝重复 key/merge key；用 `Draft202012Validator(schema, format_checker=FormatChecker())` 验证；额外检查跨文件关系、hosted 安全相对路径和 external locator。外部条目绝不打开 `skills-src` 正文。在 `pyproject.toml` 增加运行时依赖 `jsonschema>=4.23,<5`；不因 Task 1 删除旧依赖，旧运行时依赖在 Task 9 一次清理。

- [ ] **Step 4: 验证 GREEN 并提交**

Run: `PYTHONPATH=src python -m unittest tests.directory.test_entries -v`

Expected: 全部通过。

Commit: `feat: add skill directory schemas and validation`

### Task 2: 贡献模板、规范化构建与首批目录

**Files:**
- Create: `templates/entries/{hosted,external}.yaml`
- Create: `templates/recommendations/recommendation.yaml`
- Create: `CONTRIBUTING.md`
- Create: `docs/guides/agent-contribution.md`
- Create: `src/hwskill/directory/{digest,hosted_content,catalog,cli,__main__}.py`
- Create: `scripts/directory/{validate,build}`
- Create: `entries/`, `recommendations/`, `curation/{topics,synonyms}.yaml`
- Create: `docs/research/initial-skill-candidates.md`
- Test: `tests/directory/test_build.py`
- Test: `tests/directory/test_cli.py`

**Interfaces:**
- Consumes: `validate_repository` 与五份 Schema。
- Produces: `build_repository(repo_root: Path, out_dir: Path) -> BuildResult`；原子生成 `catalog.json`、`recommendations.json`、`status.json`、每技能 `install.json/install.md`、公开 Schema 与 Agent 指引。

- [ ] **Step 1: 写构建与 CLI 失败测试**

覆盖相同 input digest 两次生成正文一致、hosted 完整目录摘要、external 不复制正文、安装 JSON/Markdown 字段一致、草稿不泄露、unknown install 无执行提示、CLI 退出码 0/1/3 和完整 issue JSON。

- [ ] **Step 2: 验证 RED 后实现确定性构建**

Run: `PYTHONPATH=src python -m unittest tests.directory.test_build tests.directory.test_cli -v`

Expected: 缺少构建器而失败；随后实现临时目录生成、同文件系统原子替换，执行时间只放发布外壳。

- [ ] **Step 3: 调研并录入 5–10 项首批技能**

逐项记录来源 URL、调研日期、requested ref/resolved revision、路径、许可证、兼容性、限制与采纳/暂缓理由。至少包含 2 个现有 hosted 技能、至少 2 个不同来源的 external 技能，并覆盖性能或 C++、大数据或 Python、PPT/开发周边；不以 Star 代表使用人数。

- [ ] **Step 4: 校验正式目录并提交**

Run: `PYTHONPATH=src python -m hwskill.directory validate --repo-root . --json`

Expected: `result=pass`，同时明确 `installation/behavior` 未运行的条目。

Commit: `feat: build validated skill catalog and contribution flow`

### Task 3: 构建 Astro 静态发现站与 Pagefind 搜索

**Files:**
- Create: `site/package.json`, `site/package-lock.json`, `site/astro.config.mjs`, `site/tsconfig.json`, `site/pagefind.yml`
- Create: `site/src/layouts/BaseLayout.astro`
- Create: `site/src/components/{SkillCard,SearchFilters,InstallPrompt,VerificationMatrix}.astro`
- Create: `site/src/pages/index.astro`
- Create: `site/src/pages/skills/index.astro`
- Create: `site/src/pages/skills/[namespace]/[name].astro`
- Create: `site/src/pages/recommendations/[id].astro`
- Create: `site/src/pages/topics/[slug].astro`
- Create: `site/src/pages/contribute/index.astro`
- Create: `site/src/styles/global.css`
- Test: `tests/site/test_static_site.py`

**Interfaces:**
- Consumes: Task 2 规范化 JSON 与安装材料，禁止读取 YAML 或联网拉 external 正文。
- Produces: `site/dist/` 静态站、同源 `/data` `/schemas` `/contribute` 资源与 Pagefind index。

- [ ] **Step 1: 写静态站行为失败测试**

用 fixture catalog 检查首页、技能列表/详情、推荐、主题、贡献路由；草稿不生成；HTML `lang=zh-CN`；Pagefind metadata 含 layer/agent/source/verification；复制失败时文本可选；badge 缺失不影响核心链接。

- [ ] **Step 2: 验证 RED，安装锁定依赖并实现页面**

Run: `PYTHONPATH=src python -m unittest tests.site.test_static_site -v`

Expected: 因站点不存在而失败。使用 Node 22.12+、锁定 Astro 7 与 Pagefind 1.5.x；普通 CSS 和少量原生脚本，不引入完整 UI 框架。

- [ ] **Step 3: 构建并验证中文检索**

Run: `npm --prefix site ci && npm --prefix site run build && npm --prefix site run index`

Expected: Astro 与 Pagefind extended 成功；至少 30 条预定义查询覆盖中文、英文、混合词、同义词、筛选和零结果。

- [ ] **Step 4: 提交**

Commit: `feat: add static skill discovery site`

### Task 4: VerificationReport 与隔离安装验证

**Files:**
- Create: `src/hwskill/verification/{__init__,models,installer,report}.py`
- Create: `scripts/verification/run_install_check.py`
- Create: `tests/verification/`
- Create: `verification/README.md`
- Create: `verification/reports/`

**Interfaces:**
- Consumes: 安装材料、固定 source identity、entry/install digest 与临时 source checkout。
- Produces: VerificationReport v1，阶段限定 `metadata/acquisition/installation/behavior` 与 `pass/fail/blocked/not_run`。

- [ ] **Step 1: 写安装安全和适用性失败测试**

覆盖 hosted/external、完整文件、目标识别、重复安装、同名冲突、失败不覆盖、withdrawn 停止、报告 digest/version 不匹配只作历史展示、单宿主通过不泛化。

- [ ] **Step 2: 验证 RED 后实现目录安装器**

Run: `PYTHONPATH=src python -m unittest discover -s tests/verification -t . -v`

Expected: 缺模块而失败；实现只把已审阅目录复制到隔离目标，不执行任意条目 shell，external 获取阶段与安装阶段分离。

- [ ] **Step 3: 在临时 HOME/项目运行 hosted 与 external 各一例确定性安装**

记录实际 revision、目标、冲突/重复结果和宿主识别检查；没有 Agent 凭据时行为阶段写 `blocked/not_run`，不得写 pass。

- [ ] **Step 4: 提交**

Commit: `feat: add version-bound install verification`

### Task 5: 不可变发布、事件日志与恢复

**Files:**
- Create: `src/hwskill/publishing/{__init__,models,events,store,service,cli}.py`
- Create: `scripts/publishing/release.py`
- Test: `tests/publishing/test_release.py`
- Test: `tests/publishing/test_recovery.py`

**Interfaces:**
- Consumes: 固定 source commit、catalog digest、verification/source snapshot digest、build config identity 与先前 committed log。
- Produces: `prepared -> uploaded -> checked -> activated -> committed` 候选；不可变 `releases/<release_id>/`；追加、单调 sequence feed。

- [ ] **Step 1: 写状态机失败测试**

覆盖相同输入稳定 release/event ID、首次 publication_time 冻结、activated 后日志失败恢复、旧发布不可覆盖新入口、回滚不回滚历史、普通推荐编辑不发事件、撤回/恢复生成明确新事件。

- [ ] **Step 2: 验证 RED 后实现文件系统发布适配器**

Run: `PYTHONPATH=src python -m unittest tests.publishing.test_release tests.publishing.test_recovery -v`

Expected: 缺发布器而失败；实现单写者锁、候选持久化、不可变目录、实际 read check、原子可见指针和日志补写。

- [ ] **Step 3: 故障注入并提交**

逐阶段注入写入/切换失败，确认未 committed 内容不进入 feed、重试不重复事件。

Commit: `feat: add recoverable immutable publishing`

### Task 6: Feed 读取、校验、筛选与合并

**Files:**
- Create: `src/hwskill/sharing/{__init__,models,feed_reader,validation,filtering}.py`
- Test: `tests/sharing/test_feed_reader.py`
- Test: `tests/sharing/test_validation.py`
- Test: `tests/sharing/test_filtering.py`
- Create: `tests/sharing/fixtures/`

**Interfaces:**
- Produces: `FeedReader.read_snapshot(feed_url) -> FeedSnapshot`；`build_items(snapshot, from_sequence, filters) -> list[UpdateItem]`。
- UpdateItem 固定含稳定 ID、event IDs、change type、标题摘要、skill/recommendation refs、source version、详情/安装链接、lifecycle。

- [ ] **Step 1: 写读取和语义失败测试**

覆盖无 Token 静态读取、私有模式认证、跨域 redirect 去 Authorization、timeout/429 重试、确定性错误不重试、未知 major、sequence gap、摘要不符、重复事件、skill+首次推荐合并、多技能推荐一条、编辑不推广、撤回控制变化和过滤后空区间。

- [ ] **Step 2: 验证 RED 后实现**

Run: `PYTHONPATH=src python -m unittest tests.sharing.test_feed_reader tests.sharing.test_validation tests.sharing.test_filtering -v`

Expected: 缺 sharing 模块而失败；实现有限重试和无副作用纯筛选，不执行 feed 内容。

- [ ] **Step 3: 提交**

Commit: `feat: validate and filter published skill updates`

### Task 7: SQLite 批次、显式确认、JSON 原子输出与 CLI

**Files:**
- Create: `src/hwskill/sharing/{store,service,output,cli,__main__}.py`
- Create: `scripts/sharing/pull_updates.py`
- Test: `tests/sharing/test_store.py`
- Test: `tests/sharing/test_service.py`
- Test: `tests/sharing/test_cli.py`

**Interfaces:**
- Produces: `prepare_updates(reader, store, config) -> PreparedBatch` 与 `acknowledge(store, consumer_id, batch_id) -> Acknowledgement`。
- PreparedBatch 固定含 `schema_version, consumer_id, batch_id, feed_id, from_sequence, through_sequence, filter_digest, snapshot_identity, items`。

- [ ] **Step 1: 写交接状态机失败测试**

覆盖首次初始化必须显式 baseline/replay、状态库意外缺失拒绝自动跳过、prepare 重试稳定 ID、pending 重启恢复、快照变化重算并拒绝旧 ID、精确 ack、重复 ack 幂等、错误 ack 拒绝、空批次可确认、事务中断、JSON 写失败不推进 cursor。

- [ ] **Step 2: 验证 RED 后实现 SQLite 事务**

Run: `PYTHONPATH=src python -m unittest tests.sharing.test_store tests.sharing.test_service tests.sharing.test_cli -v`

Expected: 缺状态存储而失败；使用单实例锁和事务，把确认游标与 pending 分离。代码中文注释强调 ack 不等于发送成功。

- [ ] **Step 3: 验证原子 JSON 输出并提交**

输出使用同目录临时文件、flush/fsync、`os.replace`；失败保留旧文件和 DB 状态。

Commit: `feat: add durable update handoff batches`

### Task 8: 干净 Luna 员工体验验收与提示词优化

**Files:**
- Create: `tests/agent-experience/tasks/`
- Create: `tests/agent-experience/observer.py`
- Create: `scripts/validation/run_clean_luna_eval.sh`
- Create: `docs/validation/luna-evaluation.md`

**Interfaces:**
- Consumes: 公开站点/仓库贡献指引、机器索引、模板、校验 CLI 与安装材料。
- Produces: 未参与调优样例；每任务成功率、调用次数、人工介入、耗时和可取得 token；独立确定性观察结果。

- [ ] **Step 1: 写观察器失败测试并实现确定性检查**

检查实际文件范围、Schema、完整目录、宿主识别、来源 revision 和输出 JSON，不解析 Luna 自述为 pass。

- [ ] **Step 2: 建立隔离环境**

使用 `mktemp -d` 的配置根、HOME 替代目录和项目目录；禁止挂载开发技能、旧 Profile/Hook/MCP；只提供员工可获取资源和必要认证。

- [ ] **Step 3: 运行收录/推荐/修正与 hosted/external 安装**

至少保留一个未调优任务。若 Luna/凭据/网络不可用，记录 `blocked` 及已完成的隔离与确定性验证，不用 Sol/Astra 成功代替。

- [ ] **Step 4: 根据失败只优化正式公共资源并复测**

记录前后工具调用、人工介入、耗时和可取得 token；不为降低指标省略校验。

Commit: `test: add clean Luna employee workflow evaluation`

### Task 9: 迁移预览、新入口与旧运行时退出

**Files:**
- Create: `src/hwskill/directory/migration.py`
- Create: `scripts/directory/migration-preview`
- Create: `tests/migration/`
- Replace: `pyproject.toml`
- Replace: `install.sh`
- Remove: 经引用图确认后的旧 Profile/Hook/MCP/runtime、旧外部快照、旧测试与 Demo。

**Interfaces:**
- Produces: `migration_preview(repo_root, out_dir) -> MigrationPreview`，只生成候选和无法转换清单；新版 CLI 只保留 directory/sharing/publishing/verification 所需入口。

- [ ] **Step 1: 写迁移失败测试**

覆盖 manual -> hosted、upstream snapshot -> external、未知 locator/install 进入异常清单且不删除；旧 tag checkout；新版无 `setup/profile/dump/serve-mcp`；旧用户配置不自动迁移或删除。

- [ ] **Step 2: 验证 RED 后实现 preview**

Run: `PYTHONPATH=src python -m unittest discover -s tests/migration -t . -v`

Expected: 缺 preview 而失败，随后只生成候选，不更改源树。

- [ ] **Step 3: 证明替代闭环后清理旧簇**

先验证所有保留技能均有 hosted/external 条目、来源和安装定位；再删除旧 Profile、Hook、MCP、source snapshot/runtime 及仅为它们服务的依赖和测试。用 `rg` 查悬空引用，不保留永久双运行时。

- [ ] **Step 4: 验证真正固定的旧版回退入口**

旧版说明必须 checkout `hwskill-legacy-v0.1.0` 后运行该版本安装器；测试断言内部 clone/checkout 不再跟随 main。不得移动旧标签修补。

- [ ] **Step 5: 提交**

Commit: `refactor: switch repository to skill sharing platform`

### Task 10: 中文文档、部署演练与需求验收

**Files:**
- Replace: `README.md`
- Create: `docs/guides/{user-guide,deployment-guide,development-guide,operations-guide,migration-guide}.md`
- Create: `docs/validation/acceptance-report.md`
- Create: `scripts/validation/verify_release.sh`
- Modify: `docs/validation/implementation-status.md`

**Interfaces:**
- Consumes: 所有已实现命令、站点产物、发布日志、Luna/确定性证据。
- Produces: REQ-01..REQ-10 可追踪证据表、真实设施未知项和恢复入口。

- [ ] **Step 1: 按实际代码写中文指南**

分别覆盖员工浏览/安装/贡献、开发结构和测试、静态部署与 base URL、发布/SQLite 备份恢复、旧标签安装与回退；禁止写未执行的上线、真实群或 Agent 通过结论。

- [ ] **Step 2: 在干净临时目录按文档演练**

Run: `scripts/validation/verify_release.sh`

Expected: Python 全量测试、目录 validate/build、Astro build、Pagefind index、本地静态 HTTP 读取、发布故障恢复和 sharing prepare/ack 均返回可审计结果。

- [ ] **Step 3: 进行 1,000/10,000 构造数据规模检查**

记录构建时间、产物大小、搜索索引大小与运行环境；只报告本机数据，不外推生产容量。

- [ ] **Step 4: 逐项复核需求并提交**

在 `acceptance-report.md` 对 REQ-01..REQ-10 链接命令、产物和未验证项；更新进度文档的 commit、检查、阻碍和下一步。

Commit: `docs: complete skill sharing delivery guide and evidence`

### Task 11: 全分支审阅与完成前验证

**Files:**
- Modify: 仅审阅发现所需的测试、实现和文档。
- Modify: `docs/validation/acceptance-report.md`
- Modify: `docs/validation/implementation-status.md`

**Interfaces:**
- Consumes: `hwskill-legacy-v0.1.0..HEAD` 全部差异。
- Produces: 规格符合性审阅、代码质量审阅、最终验证命令与无远端副作用声明。

- [ ] **Step 1: 让高能力审阅者按需求、状态机和迁移边界检查全分支**

重点检查 external 正文泄漏、Token 边界、发布/ack 幂等、撤回重算、草稿泄漏、验证泛化、旧 runtime 悬挂引用和 bot 范围膨胀。

- [ ] **Step 2: 对发现执行一次定向修复与复审**

每个行为修复先写会失败的回归测试；无证据的建议不直接采纳。

- [ ] **Step 3: 运行新鲜完整验证**

```bash
PYTHONPATH=src python -m unittest discover -s tests -t . -v
python -m hwskill.directory validate --repo-root . --json
npm --prefix site ci
npm --prefix site run build
npm --prefix site run index
scripts/validation/verify_release.sh
git status --short
```

Expected: 所有适用本地检查通过；设施、Luna 或真实外部网络未验证项明确列为 blocked/not_run，不伪称上线。

- [ ] **Step 4: 更新最终证据并形成本地可审阅提交**

不得 push、创建 PR、合入、正式部署或发群。
