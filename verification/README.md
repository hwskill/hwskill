# 隔离安装验证

本目录保存 `VerificationReport v1` 的持久报告。报告不是安装说明的替代品：它只证明一个固定的技能、来源身份和安装材料组合在一个明确的宿主环境中的结果。

## 固定输入与分阶段

运行前，调用方必须把 catalog 中同一条目的 `source_identity`、`entry_digest` 和生命周期，与该条目的 `install.json` 一起绑定。`install_digest` 是去掉自身字段后，对完整 install JSON 的 canonical JSON 字节计算的 SHA-256；不能只摘要其中的 `install` 小对象。external 的 `requested_ref` 保留用户请求的 tag/ref，构建时未知的 `resolved_revision` 为 null；验证阶段解析该 ref、核对 checkout HEAD，并把实际 commit SHA 写入报告，二者不可混用。

报告阶段固定为：

- `metadata`：条目、来源身份和两个 digest 已绑定。
- `acquisition`：指定版本的完整目录已在受控流程中取得。外部获取不属于安装器。
- `installation`：目录已复制到隔离的 HOME 或项目目标、宿主已识别，并记录幂等或冲突结果。
- `behavior`：只有在具备必要 Agent 凭据和代表性任务时才运行；缺少时写 `not_run` 或 `blocked`，绝不写 `pass`。

结果只使用 `pass`、`fail`、`blocked`、`not_run`。报告的 `entry_digest`、`install_digest`、完整 `source_identity`（包括实际 revision）或 host 任一不一致时，旧报告仅作历史展示，不能显示为当前安装通过。报告过期由 `expires_at` 或部署侧策略决定，也不会把历史通过改写为失败。

## 运行单项确定性检查

先由受控获取阶段把 external 来源的指定 revision 检出到本地目录，并完整审阅目录。随后使用 Task 2 的发布目录运行：

```bash
PYTHONPATH=src /usr/bin/python3 scripts/verification/run_install_check.py \
  --published-root /path/to/published \
  --skill-id upstream/example \
  --source-dir /path/to/already-acquired-checkout \
  --target-root /tmp/project/.codex/skills \
  --report-dir verification/reports \
  --host codex --host-version '<实际版本>' --runner-identity '<受控运行器>'
```

脚本只读取发布的 `catalog.json` 和对应 `install.json`，验证二者绑定后复制已取得的完整目录；不会联网、不会运行 `install.sh`、`SKILL.md` 或任意来源中的命令。目标若已有同名且内容不同的目录，检查以失败退出且不覆盖；内容完全相同则幂等通过。已撤回条目在复制前停止。

`--host` 只接受 `codex`、`claude-code`、`opencode`。脚本只从受控 `PATH` 查找固定 basename（分别为 `codex`、`claude`、`opencode`），实际运行其 `--version` 并与 `--host-version` 核对；不接受任意 executable 路径，也拒绝执行位于 source、published、target 或 report 树内的程序。目标根必须分别以 `.codex/skills`、`.claude/skills`、`.opencode/skills` 结尾，表明这是隔离项目技能目录，而非任意目录。

标准输出为单个符合 `VerificationReport v1` Schema 的可审计 JSON 文档，成功和失败都会以 no-overwrite 方式持久化到 `--report-dir`。失败按 `metadata/acquisition/installation` 的实际边界标记并以非零退出，未执行的后续阶段保持 `not_run`，不会伪造通过结论。

## 本次确定性范围

自动测试分别在临时 HOME 下安装 hosted 目录、在临时 project 下安装已准备的 external 本地 checkout。两者检查完整文件复制、宿主参数、重复安装、同名冲突、复制失败保留原目录及撤回停止。没有真实 Agent 凭据或代表性任务，因此行为阶段保持 `not_run`；没有网络获取或 Agent 执行被记为通过。
