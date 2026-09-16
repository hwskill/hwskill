# HWSkill 本地验收报告

日期：2026-09-16

## 结论边界

Task 0–10 的实现与 fixture 已形成目录、站点、安装验证、发布恢复、增量交接和迁移闭环。本文只记录本地可重复证据；远端静态部署、真实组织权限、真实群发送均未运行。Luna 观察器在缺受信宿主/凭据时产生 5/5 `not_run`，不是 Agent 行为通过。

统一本地入口：

```sh
scripts/validation/verify_release.sh \
  --repo-root . \
  --report /tmp/hwskill-release-verification.jsonl
```

报告逐步记录隔离 source snapshot、测试输入、`python-tests`、`directory-validate`、`directory-build`、`site-build`、`site-index`、`local-http-read`、`publishing-recovery`、`sharing-prepare-ack`。每步都带日志 SHA-256 和最多 8 KiB 的 base64 日志尾；失败时总结果为 `fail`，环境前提不满足时为 `blocked`。报告固定标记远端部署、Luna 和群发送为 `not_run`。

2026-09-16 实际执行结果：

- `/tmp/hwskill-release-verification-20260916-v3.jsonl`，SHA-256 `0ed957b3e4361e4851c74e91d61c00f3d3da17e1047813520f7c1d3f0302a645`。Python 275 项、目录 validate/build、publishing recovery 和 sharing prepare/ack 通过；站点 build/index/本地 HTTP 因缺受支持的 Linux Node `>=22.19.0 <23` 而为 `blocked`，总结果 `blocked`。
- `/tmp/hwskill-scale-verification-20260916.jsonl`，SHA-256 `5842ed5866598b4301e6cdbdac9a8cbaa85f75fa6f970e68aaa01c309ef60306`。1,000/10,000 两步均在生成数据前因同一 Node 前提被阻断，未产生可报告的规模数据。

## REQ-01 至 REQ-10

| 需求 | 本地证据 | 当前判断 |
| --- | --- | --- |
| REQ-01 低成本收录、推荐、修正 | `entries/`、`recommendations/`、模板、Schema、贡献指南及 Task 8 五任务观察器 | 本地契约 pass；真实 Luna 为 not_run |
| REQ-02 双来源与完整安装定位 | directory/verification 测试覆盖 hosted、external-git、external-web、requested/ref/SHA/digest 绑定 | 本地 pass |
| REQ-03 查找与偶然发现 | Astro 路由、Pagefind 索引、真实查询集和 recommendation/topic 页面 | 本地 pass；远端站 not_run |
| REQ-04 一句话安装与首次使用 | 三宿主识别、隔离 target、no-follow 安装、schema-valid 阶段报告 | fixture pass；真实员工首次使用 not_run |
| REQ-05 推荐独立发布 | ready/withdrawn tombstone、编辑与禁止恢复、跨 release 事件测试 | 本地 pass |
| REQ-06 合入、发布、通知可追溯 | source commit、release/event/batch ID 与不可变 record 交叉绑定 | 本地 pass；PR 与群发送 not_run |
| REQ-07 跨区只读获取、筛选与增量输出 | 同源 URL/redirect、预算、snapshot digest、prepare/ack、SQLite 交接 | 本地 HTTP/fixture pass；真实跨区网络 not_run |
| REQ-08 维护者减负与异常聚合 | 批量目录检查、发布故障恢复、重试分类、结构化 blocked/fail | 本地 pass；外部告警发送器不在范围 |
| REQ-09 badge 不增加采集系统 | 当前实现不采集或刷新远端 star，不让 badge 可用性阻断核心目录 | 契约 pass；第三方 badge 服务 not_run |
| REQ-10 旧系统有序迁移 | 固定旧 tag、只读 migration preview、异常清单、旧 runtime 引用清理 | 本地 pass；用户真实配置迁移 not_run |

## 规模验证

设计入口：

```sh
scripts/validation/verify_release.sh \
  --repo-root . \
  --report /tmp/hwskill-scale-verification.jsonl \
  --scale
```

该模式只在 `mktemp` 副本中生成 1,000 和 10,000 个 external 构造条目，执行 directory validate/build、Astro build 和 Pagefind index，并记录本机耗时、静态产物大小与索引大小。本次已执行但因 Node 前提阻断，因此不填写产物数值，也不写 `pass`。

## 未验证与交付门槛

- 远端域名、TLS、对象存储/CDN、发布权限和回滚权限：`not_run`。
- Luna 真实五任务：环境阻塞，指标保持 null/0 语义，不得转写为 pass。
- bot、Webhook、群卡、签名和群消息发送：本期不实现，`not_run`。
- 生产规模、跨区延迟、可用性与容量：没有本机探针之外的证据，不作承诺。

Task 11 应在最终提交后重新生成 JSONL 报告，引用独立代码审阅结果和最终工作树状态。当前结论是“本地功能证据部分通过，环境与外部设施项 blocked/not_run”，不是上线通过。
