# 干净 Luna 员工体验验收

更新时间：2026-09-11

## 当前结论

本轮状态为 `blocked`，不是通过。当前会话没有获准使用的独立 Luna 执行工具与最小 API 凭据，因此没有启动模型、联网、创建任务或用 Sol/Astra 代跑。2026-09-11T12:17:18Z 运行无执行模式后，脚本生成了通过 `evaluation.schema.json` 校验的记录：5 个任务均为 `not_run`，成功率和 token 用量均为 `null`。该记录只证明任务定义、公开输入和隔离设施的确定性预检完成，不证明 Luna 员工流程已经验收。

运行证据：

```text
scripts/validation/run_clean_luna_eval.sh --output /tmp/hwskills-task8-luna-blocked.json
exit: 2
status: blocked
tasks: 5 not_run, 0 pass, 0 fail
sha256: f02360605288a2b6531ab2ac0142a091c69c840863b1d7dc26168c1f30256c7d
```

输出文件位于临时目录，不作为长期验收凭据提交；以后取得 Luna 能力时应把真实运行产物保存到获准的证据存储，并重新记录摘要。

## 固定任务

| 任务 | 工作流 | 调优状态 | 当前状态 | 确定性观察重点 |
| --- | --- | --- | --- | --- |
| `hosted-install-holdout` | hosted 安装 | `holdout`，未参与提示词调优 | `not_run` | 完整目录、Codex 识别、验证报告 |
| `external-install` | external 安装 | `optimized` | `not_run` | Git origin、requested ref、实际 HEAD、完整目录 |
| `skill-contribution` | 技能收录 | `optimized` | `not_run` | 文件范围、Entry Schema、hosted 来源路径、全仓校验 |
| `recommendation-contribution` | 推荐 | `optimized` | `not_run` | 文件范围、Recommendation Schema、技能引用、全仓校验 |
| `information-correction` | 信息修正 | `optimized` | `not_run` | 只修改目标条目、目标文案、全仓校验 |

`hosted-install-holdout` 保留短而未调优的员工任务描述。它不能因其他四项提示词变化而改写，避免全部样例都被针对性优化后失去回归意义。

## 隔离边界

脚本每次运行都用 `mktemp -d` 创建临时 HOME、`CODEX_HOME`、XDG 配置根、项目根和运行时临时目录，退出时只删除本次生成且名称匹配的根。项目由观察器从以下公开仓库表面复制：贡献指引、Schema、模板、人工目录源、hosted 源、Task 2 生成目录，以及目录/安装验证器所需的最小 Python 代码。复制器拒绝符号链接和特殊文件。

真实执行必须显式增加 `--execute` 并只接受当前进程的 `CODEX_API_KEY`。脚本启动后立即把凭据移入不导出的 shell 变量并固定 `/usr/bin:/bin`，任何准备或版本探测子进程都拿不到凭据；只有通过固定路径白名单、root 所有权和不可组/全局写检查的 Codex 可执行文件会收到它。Shell 先打开 Host 文件描述符，信任检查、版本探测和真正执行都绑定该 FD；记录阶段再次核对同一 FD 的 device/inode/version。生产脚本没有环境变量或参数形式的 fake-host 入口；测试只直接调用观察器的 test-only 探针 API。当前桌面安装位于用户可写目录，不满足该信任边界，因此真实模式会诚实产出 blocked 记录。

脚本不读取已有 Codex 登录，不复制开发者 HOME、旧技能、Profile、Hook、MCP 配置、当前会话或任务判定 oracle；项目中的 `.evaluation/task.json` 只有 prompt 与公开输入清单。Host 固定为 `gpt-5.6-luna`、`medium`、临时会话、忽略用户配置和规则、无人工批准策略，并将 Agent 工具环境的继承策略固定为 `none`，避免把 API 凭据继续传给模型启动的命令。脚本未启用 Web Search；外部 Git 获取若受网络策略阻止，应作为该任务失败或阻塞记录，不能改用本机已有私有 checkout。

## 独立观察口径

`tests/agent-experience/observer.py` 不读取 Agent 的成功声明。每个 pass 必须由以下实际证据共同得到：

1. Agent 退出后先以 dirfd 与 `O_NOFOLLOW` 把结果冻结到观察器控制的快照；基线前后文件摘要仅落在任务允许范围，符号链接、FIFO、socket 或其他特殊节点直接失败。
2. 必需文件存在；安装目录与已审阅目录逐文件 SHA-256 完全一致。
3. JSON 由 Draft 2020-12 Schema 校验；贡献 YAML 由仓库严格 loader 与全目录 validator 校验。
4. 观察器重新探测真实 Codex 可执行文件版本，并核对外层记录的 Host、Luna 模型、medium 档位与隔离参数。
5. external 安装用固定 `/usr/bin/git`、临时 HOME/XDG 配置、禁用 system config、fsmonitor 与 hooks，重新读取 origin、requested ref、`HEAD`、工作树洁净度和报告中的 resolved revision。
6. JSON/YAML 关键业务值用独立 JSON Pointer 断言，防止“格式合法但没有完成指定修改”。

观察结果固定记录工具调用次数、人工介入、耗时和可取得的 token 总量。Codex JSONL 没有提供某项用量时写 `null`，不估算；未运行任务只有 `attempts` 明确为 `0`，其余指标保持 `null`。

## 后续真实复测

取得专用 Luna API 凭据和获准网络边界后运行：

```sh
CODEX_API_KEY=... scripts/validation/run_clean_luna_eval.sh \
  --execute --output /approved/evidence/luna-evaluation.json
PYTHONPATH=src /usr/bin/python3 tests/agent-experience/observer.py \
  validate-result --input /approved/evidence/luna-evaluation.json
```

只有 5 项任务的独立观察均为 `pass` 时，才能计算真实成功率并更新本页。若失败源于公开指引，再只修改员工能取得的正式资源并比较前后指标；holdout 任务定义保持不变。当前 REQ-01、REQ-02、REQ-04 的 Luna 证据仍为 blocked，不能据此声称真实安装、首次使用或代贡献已经完成。
