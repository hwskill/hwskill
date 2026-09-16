# 静态目录部署指南

## 前提与边界

本仓只生成静态站和文件系统发布材料，没有执行任何远端部署，也不包含云账号、域名、TLS、CDN 或发布凭据。生产托管方案必须另行评审。

GitHub Pages 首次上线采用双仓：`hwskill/hwskill` 保存源码，`hwskill/hwskill.github.io` 保存手动发布工作流。站点仓从已合入主仓 `main` 的完整 commit SHA 重新构建，发布完整 `site/dist/` 到 `https://hwskill.github.io/`；主仓 CI 只验证，不部署。首次上线仍需维护者分别授权推送、在站点仓选择 GitHub Actions 作为 Pages 来源并完成线上验收。该静态站发布不启用下述文件系统 release/feed 服务。

运行时要求以锁文件为准：Python 3.10 以上；站点 `package.json` 要求 Node `>=22.19.0 <23`。离线验收使用已有 `site/node_modules`；若依赖未预置，应将该步骤记录为 `blocked`，不能跳过后写成通过。

## 构建

```sh
PYTHONPATH=src /usr/bin/python3 -m hwskill.directory validate --repo-root . --json
PYTHONPATH=src /usr/bin/python3 -m hwskill.directory build \
  --repo-root . --out /tmp/hwskill-directory --json
npm --prefix site run build
npm --prefix site run index
```

`npm run build` 的 prebuild 会从仓库事实源重新生成 `site/.generated/directory`；`npm run index` 在 `site/dist/pagefind` 生成索引并运行真实查询集。部署对象是完整 `site/dist/`，不要只上传 HTML 或只上传 Pagefind 目录。

子路径托管通过构建时环境变量设置：

```sh
SITE_BASE=/skills/ npm --prefix site run build
SITE_BASE=/skills/ npm --prefix site run index
```

同一 release 的 HTML、`data/*.json`、schemas 和 Pagefind 索引必须作为一个不可分割版本发布。不要在上传期间让 head 指向半成品目录。

GitHub Pages 根站点构建保持默认 `SITE_BASE=/`。生产构建的 Astro `site` 是 `https://hwskill.github.io`；发布包包含 `index.html`、`skills/`、`data/`、`schemas/` 和 `pagefind/`。部署记录必须保存源码 SHA；回滚时重新构建并发布曾合入主仓 `main` 的历史 SHA，而不是手工改写线上文件。

## 本地发布前演练

```sh
scripts/validation/verify_release.sh \
  --repo-root . \
  --report /tmp/hwskill-release-verification.jsonl
```

脚本复制当前树到 `mktemp` 隔离目录，创建包含固定旧 tag 的隔离 Git snapshot，并使用隔离 HOME 运行 Python 全测、目录构建、Astro/Pagefind、本地 HTTP 读取、发布恢复和 prepare/ack。报告已存在时脚本拒绝覆盖，每步保留日志摘要；缺受支持的 Node 会返回 `blocked`，不会写成 `pass`。最终 `summary.result` 为 `pass` 只代表这些本地步骤通过；其中 `remote_deployment`、`luna`、`group_delivery` 仍明确为 `not_run`。

规模探针需显式启用，且只在临时副本生成数据：

```sh
scripts/validation/verify_release.sh \
  --repo-root . \
  --report /tmp/hwskill-scale-verification.jsonl \
  --scale
```

输出记录 1,000/10,000 条目的本机耗时、静态产物 KiB 和搜索索引 KiB；这些数据不能外推为生产容量承诺。
