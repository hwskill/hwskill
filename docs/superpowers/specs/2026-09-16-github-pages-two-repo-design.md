# GitHub Pages 双仓部署设计

## 目标与边界

`hwskill/hwskill` 是项目唯一源码主仓；`hwskill/hwskill.github.io` 只保存发布工作流和说明。首次发布采用手动输入源码 commit SHA，不创建跨仓令牌，不推送、不修改 GitHub Pages 设置，直到维护者另行确认。静态站上线不代表文件系统不可变 release/feed 服务上线。

## 主仓

将当前 `codex/cross-project-skill-sharing` 历史作为 GitHub 主仓首发候选，不移动现存的 GitCode `main`。先把本地搜索修复和卡片改动连同测试整理为独立提交，再加入根站点 `site=https://hwskill.github.io` 的配置、锁定工具链与 CI。PR 与默认分支均运行 Python 目录/站点测试、Astro 构建、Pagefind 索引和 33 个搜索样例；PR 不获得 Pages 写权限。

## 站点仓

仓库的默认分支保存 `workflow_dispatch(source_sha)` 工作流。工作流只接受完整 40 位 SHA，并验证它是 `hwskill/hwskill` 受保护 `main` 的祖先；检出该 SHA，在独立构建任务里安装 Python/Node 依赖，重跑站点构建与索引检查，并上传完整 `site/dist/`。独立部署任务使用 `github-pages` 环境以及 `pages: write`、`id-token: write`，把本次 SHA 与部署 URL 留在工作流记录中。部署失败不发布不完整产物。

## 首次上线与后续

两仓本地提交和验证完成后，维护者分别审阅并授权推送；先推源码主仓，确认 GitHub 默认分支与 CI，再推站点仓并在 Pages 设置中选择 GitHub Actions。首次手动运行站点工作流，核对首页、技能页、搜索/筛选、Pagefind 和 JSON。后续自动触发另行设计受限 GitHub App，不在首次上线范围内。回滚通过手动重新部署曾在主仓 `main` 中的历史 SHA 完成。
