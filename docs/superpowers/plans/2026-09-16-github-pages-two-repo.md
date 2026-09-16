# GitHub Pages 双仓部署 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 本地准备 `hwskill/hwskill` 与 `hwskill/hwskill.github.io` 的可审阅首发提交，不推送远端。

**Architecture:** 主仓保存事实源、站点源码和只读 CI；站点仓以完整源码 SHA 检出并重新构建 `site/dist/`，由同仓 Pages 工作流部署。首次以手动触发为主，不持有跨仓写凭据。

**Tech Stack:** Python 3.12、Node.js 22.19、Astro 7、Pagefind 1.5、GitHub Actions、GitHub Pages。

**Spec:** `docs/superpowers/specs/2026-09-16-github-pages-two-repo-design.md`

## Global Constraints

- 保留 `.superpowers/` 等与本次无关的工作区内容；只暂存明确列出的文件。
- 不修改 GitCode `main`，不推送、创建 PR 或修改 GitHub 仓库设置。
- 站点部署对象必须是完整 `site/dist/`；根站点 `base` 为 `/`。
- 首次发布不得增加跨仓写入凭据；站点仓部署只接受主仓 `main` 历史里的 SHA。

---

### Task 1: 整理当前网站改动

**Files:** `site/package.json`, `site/scripts/prepare-dev-search.mjs`, `site/src/components/{SearchFilters,SkillCard}.astro`, `site/src/lib/highlight.mjs`, `site/src/styles/global.css`, `site/tests/highlight.test.mjs`, `tests/site/test_static_site.py`。

- [ ] 检查各文件差异和未跟踪内容，确认 `.superpowers/` 不在交付范围。
- [ ] 运行 `PYTHONPATH=src:. python3 -m unittest discover -s tests -t . -p 'test_*.py'` 与 `node site/tests/highlight.test.mjs`。
- [ ] 运行 `npm --prefix site run build` 和 `npm --prefix site run index`，确认 33 个搜索样例通过。
- [ ] 只暂存上述网站文件，审阅暂存差异并建立独立本地提交。

### Task 2: 主仓根站点配置与 CI

**Files:** `site/astro.config.mjs`, `site/package-lock.json`, `.github/workflows/ci.yml`, `tests/site/test_static_site.py`, `docs/guides/deployment-guide.md`。

- [ ] 先加回归检查：根站点构建的公开 `site` URL、`base` 与完整 Pagefind 索引。
- [ ] 运行新增检查，确认在未设置 `site` 时失败。
- [ ] 配置 `site: 'https://hwskill.github.io'`，保留 `SITE_BASE` 根路径默认值。
- [ ] 把锁文件的下载地址规范为公开 npm 注册表，并在隔离临时目录运行一次 `npm ci`。
- [ ] 编写只读 CI：checkout、Python 3.12、Node 22.19、依赖安装、Python 全测、Astro build、Pagefind index；不授予 Pages 写权限。
- [ ] 更新部署指南以明确双仓首次手动发布边界。
- [ ] 运行本地测试和 `git diff --check`，只暂存本任务文件并建立独立本地提交。

### Task 3: 站点仓本地首发候选

**Files:** 独立本地目录中的 `.github/workflows/deploy.yml`、`README.md`。

- [ ] 在独立临时目录初始化空 Git 仓库，配置 `hwskill/hwskill.github.io` 远端但不推送。
- [ ] 编写手动工作流：验证 40 位 SHA、检出主仓该 SHA、验证其属于 `origin/main`、安装依赖、构建与索引、上传完整 `site/dist/`，然后由最小权限部署任务发布。
- [ ] 静态核验工作流 YAML、权限、路径和 SHA 约束；若本地没有 Actions 运行环境，把远端执行明确记为待验。
- [ ] 建立站点仓首个本地提交，记录提交 SHA 与目录路径，不推送。

### Task 4: 交付审计

- [ ] 对照设计逐条核对：双仓角色、SHA 来源、权限、完整产物、首次手动触发和回滚入口。
- [ ] 运行主仓全量测试、Node 高亮测试、Astro/Pagefind 构建、`git diff --check`；核对主仓提交文件清单和站点仓提交文件清单。
- [ ] 报告两个本地提交 SHA、未执行的远端 Pages 验证，以及需维护者授权的推送/设置动作。
