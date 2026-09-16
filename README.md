# HWSkill 技能共享目录

本仓库维护可校验的技能条目、推荐内容、静态发现站、隔离安装验证、不可变发布与增量更新读取。`entries/`、`recommendations/` 和 JSON Schema 是人工事实源；构建与发布产物不得反向改写它们。

## 本地检查

```sh
python -m venv .venv
.venv/bin/pip install .
.venv/bin/hwskill-directory validate --repo-root . --json
.venv/bin/hwskill-directory build --repo-root . --out /tmp/hwskill-directory --json
```

主要入口：

- `hwskill-directory`：目录校验、构建和旧数据迁移预览。
- `hwskill-verify`：在隔离目标中校验并安装已审阅的完整技能目录，不执行技能正文中的命令。
- `hwskill-publish`：维护者使用的不可变发布与回滚入口。
- `hwskill-sharing`：读取发布 feed，准备或确认本地增量批次；确认只表示调用方已接收，不表示消息已经发送。

贡献格式与命令见 [CONTRIBUTING.md](CONTRIBUTING.md) 和 [Agent 贡献指南](docs/guides/agent-contribution.md)。当前验证状态见 [实施状态](docs/validation/implementation-status.md) 与 [Luna 验收记录](docs/validation/luna-evaluation.md)。

## 安装新版工具

在仓库 checkout 中运行：

```sh
./install.sh
```

脚本建立隔离虚拟环境并安装当前 Python 包，把四个新版命令链接到 `${HWSKILL_BIN_DIR:-$HOME/.local/bin}`。它不会读取、迁移或删除旧用户配置，也不会修改 shell 配置；需要时请自行把该目录加入 `PATH`。

## 固定旧版回退

旧 Profile、Hook 与 MCP 运行时只在固定 tag `hwskill-legacy-v0.1.0` 中保留，其 commit 必须为 `45825a59489e96160ad73b7074717ddedd292ef5`。新版安装器可显式建立独立的旧版 checkout：

```sh
./install.sh --legacy --install-path="$HOME/.local/share/hwskill-legacy-v0.1.0"
```

该流程先 clone，再 detached checkout 固定 tag、核对完整 commit，最后才运行该 checkout 中的旧安装器；不会跟随默认分支，也不会覆盖新版安装目录。保留旧 tag 不表示旧运行时将适配未来宿主版本。新版不会自动解释或删除已有 `.hwskills`、Hook、MCP 或已安装技能；迁移和卸载必须由用户另行明确执行。

## 当前边界

- external 条目只发布来源定位、固定版本、摘要和证据，不在本仓复制正文。
- hosted 条目保留完整目录，并在构建时生成内容摘要。
- 未知安装方式只提供来源指引，不生成猜测命令。
- 静态站与 feed 默认无需 Token；私有 feed 才按显式配置使用只读凭据。
- 本仓不包含 bot sender、Webhook、群消息发送或部署凭据。
