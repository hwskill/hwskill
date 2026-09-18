# 双仓 GitHub Pages 自动部署设计

## 目标与现状

`hwskill/hwskill` 的 `main` 是源码事实源，`hwskill/hwskill.github.io` 的 GitHub Actions 工作流已能从手动输入的完整源码 SHA 构建并发布站点。本设计接续首次手动上线方案，替代其中“后续再设计自动触发”的占位决定。自动化目标是：主仓 `main` 的新提交通过全部 CI 后，自动触发同一站点工作流；PR、失败的 CI、过期的提交都不能部署。保留手动选择历史 SHA 的回滚入口。

## 触发与凭据

主仓现有 `Validate source and static site` 工作流新增一个依赖 `validate` 成功的派发任务，仅在 `push` 到 `main` 时执行。任务读取 `GITHUB_SHA`，先确认它仍是主仓远端 `main` 的最新 SHA；若不是，记录跳过，不派发较旧提交。若是，则调用站点仓 `deploy.yml` 的 `workflow_dispatch` API，传入 `source_sha` 和 `release_mode=automatic`。API 返回的运行 URL 记录在主仓任务日志中；非成功响应使任务失败。

主仓自身的 `GITHUB_TOKEN` 不能访问另一仓库。维护者创建细粒度个人访问令牌，资源所有者为 `hwskill`，仅选择 `hwskill.github.io` 仓库，授予 `Actions: Read and write`；把令牌存为主仓 Actions Secret `PAGES_DISPATCH_TOKEN`。令牌不进入 Git URL、文件或日志，不授予主仓/站点仓内容写权限，也不交给站点仓部署任务。维护者负责令牌到期前轮换；未来可迁移到仅安装在站点仓的 GitHub App。无令牌时派发任务明确失败，不能把未部署写成成功。

## 站点仓安全边界

站点工作流保留必填 `source_sha`，增加默认 `manual` 的 `release_mode` 输入。两种模式都验证 SHA 为主仓 `main` 历史中的完整提交，并重新运行 Python、Node、目录、构建与 Pagefind 检查。`automatic` 模式还须在构建前和部署前确认该 SHA 仍等于主仓远端 `main`；过期则不上传或不部署。`manual` 模式允许显式选用历史 SHA 回滚。两种模式继续共用完整 `site/dist/` 产物、`github-pages` 环境与最小 Pages 权限。

自动模式只保证不会主动发布已经过期的提交，不承诺严格原子地锁住远端分支；主仓在最后一次检查之后更新时，随后成功的 CI 会再次派发新提交，最终收敛到最新已验证版本。部署失败时线上旧版保留，失败运行可见。

## 上线顺序与验收

先在本地验证两个工作流的输入、权限、过期提交保护和错误路径。维护者先创建并保存 `PAGES_DISPATCH_TOKEN`，然后推送站点仓工作流改动，最后推送主仓触发改动。主仓这次 `main` 提交本身用作首次自动部署演练：确认主仓 CI 全绿、派发 API 成功、站点仓出现 `workflow_dispatch` 运行且全绿，再核对 `https://hwskill.github.io/data/catalog.json` 的 `source_commit` 等于该主仓 SHA，并检查首页、技能页、Pagefind 与搜索。保持手动工作流可用并演练一次历史 SHA 的校验，但不实际回滚线上站点。

本次不增加定时轮询、不让主仓直接获得 Pages 写权限，也不改变文件系统 release/feed 服务。
