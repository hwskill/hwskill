# 双仓 GitHub Pages 自动部署 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 主仓 `main` 验证成功后自动派发最新源码 SHA 到站点仓，且保留安全的手动回滚。

**Architecture:** 主仓 CI 的独立任务用仅限站点仓 Actions 写权限的 Secret 调用 `workflow_dispatch`。站点仓复用现有全量构建与 Pages 部署链，在自动模式开始构建前和部署前拒绝过期 SHA。

**Tech Stack:** GitHub Actions、Python 3.12、Bash、GitHub REST API、GitHub Pages。

**Spec:** `docs/superpowers/specs/2026-09-18-automatic-github-pages-design.md`

## Global Constraints

- 主仓的 `PAGES_DISPATCH_TOKEN` 只用于站点仓 `deploy.yml` 派发；不打印、不写文件、不传给站点构建。
- 自动运行仅接受主仓远端 `main` 的最新完整 SHA；手动运行仍允许该分支历史 SHA。
- 只有主仓 `main` 的成功 `push` CI 可派发；PR、手动 CI 和失败 CI 不派发。
- 不动当前无关的 `.superpowers/` 内容；两个本地仓库均以明确文件清单暂存。
- 先推站点仓、后推主仓，最后核对线上 catalog SHA、页面和搜索。

---

### Task 1: 站点仓自动模式与过期 SHA 防护

**Files:** `/tmp/hwskill-pages-20260916-UF2t8W/scripts/require-current-source.sh`、`tests/require-current-source.sh`、`.github/workflows/deploy.yml`、`README.md`。

**Interfaces:** `bash scripts/require-current-source.sh <remote> <sha>` 成功返回 0；SHA 无效、远端不可读或不是最新 `main` 返回非零。

- [ ] 先写测试：用临时本地 Git 仓库和 bare remote 验证最新 SHA 被接受、旧 SHA 和非 40 位 SHA 被拒绝；运行测试确认缺少脚本而失败。
- [ ] 实现守卫：校验 `^[0-9a-f]{40}$`，使用 `git ls-remote --exit-code "$remote" refs/heads/main` 取唯一 SHA 并比较；运行脚本测试确认通过。
- [ ] 给 `workflow_dispatch` 增加 `release_mode` 选择输入，默认 `manual`，选项 `manual`/`automatic`；在自动模式的构建前和部署前运行守卫，部署任务先 checkout 本仓脚本。手动模式沿用历史祖先校验。
- [ ] 用 PyYAML `BaseLoader` 验证输入、两个自动守卫位置及部署权限；运行现有 `bash tests/validate-source-sha.sh` 与新测试、`bash -n`、`git diff --check`。
- [ ] 更新 README 的自动/手动触发、回滚边界；只暂存本任务文件，审查后建立本地提交。

### Task 2: 主仓成功 CI 后派发

**Files:** `scripts/ci/dispatch_pages.py`、`tests/ci/test_dispatch_pages.py`、`.github/workflows/ci.yml`、`docs/guides/deployment-guide.md`。

**Interfaces:** `python scripts/ci/dispatch_pages.py <source_sha>`；默认检查公开主仓 `main`，最新 SHA 用 `PAGES_DISPATCH_TOKEN` 向站点仓 `deploy.yml/dispatches` POST `{"ref":"main","inputs":{"source_sha":"<sha>","release_mode":"automatic"}}`，旧 SHA 安全跳过，缺失令牌或 API 错误返回非零。脚本允许测试传入本地 Git remote 和本地 HTTP endpoint，生产工作流不覆盖默认目标。

- [ ] 先写测试：临时 bare remote + HTTP 边界替身，验证最新 SHA 的 payload、旧 SHA 不派发、缺失令牌失败、HTTP 拒绝失败；运行确认脚本缺失而失败。
- [ ] 实现最小 Python 脚本：严格 SHA 校验、`git ls-remote --exit-code`、只在最新时读取令牌并 POST；日志只记录结果/运行 URL，不输出令牌；运行新测试确认通过。
- [ ] 在主仓现有 CI 增加 `needs: validate` 的派发任务，条件为 `github.event_name == 'push' && github.ref == 'refs/heads/main'`，只把 Secret 注入派发步骤，`permissions: contents: read`。
- [ ] 更新部署指南；验证 YAML 合约与任务顺序、运行 Python 全测、Node 测试、目录校验、Astro build/index、`git diff --check`；只暂存本任务文件并建立本地提交。

### Task 3: 分阶段上线与真实回执

- [ ] 核对两仓本地提交、远端 `main`、站点仓工作流仍处于手动成功状态，以及主仓 Secret 已由维护者设置（不读取其值）。
- [ ] 用已验证的 SSH 主机密钥先推站点仓；核对远端 SHA 和手动入口可见。
- [ ] 再推主仓；核对其 CI `validate` 成功且派发任务成功，记录站点仓新 `workflow_dispatch` 运行。
- [ ] 等待站点仓构建/部署成功；检查首页、`/skills/`、`/pagefind/pagefind.js`、`/data/catalog.json`，且 catalog `source_commit` 等于本次主仓 SHA。
- [ ] 若远端失败，先读运行阶段和可获取的诊断，再修复；不能把已推送或已派发当成部署成功。
