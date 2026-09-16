# 发布与运行维护指南

## 发布材料

先完成目录和站点构建，再由维护管线提供实际 `source_commit`、catalog/snapshot 摘要和构建配置身份：

```sh
hwskill-publish \
  --store-root /srv/hwskill-publication \
  --feed-id hwskill-main \
  --public-base-url https://skills.example \
  release \
  --artifact-dir /path/to/checked/site-dist \
  --source-commit '<40位Git提交>' \
  --catalog-digest 'sha256:<64位摘要>' \
  --snapshot-digest 'sha256:<64位摘要>' \
  --build-config-identity '<已审核构建配置身份>'
```

这些参数不能凭示例文本猜测；管线必须从本次已检查材料取得并保持一致。CLI 会把候选、不可变 release、feed record 和 current 指针写入同一 store，故障后以相同请求重试恢复。

## 回滚

```sh
hwskill-publish \
  --store-root /srv/hwskill-publication \
  --feed-id hwskill-main \
  --public-base-url https://skills.example \
  rollback 'release-<64位十六进制摘要>'
```

回滚会产生新的顺序记录并把 current 指向已验证的历史 release，不会改写历史目录。store 已绑定的 feed ID、公开 base URL 和构建配置身份不能静默更换。

## 备份、恢复与巡检

备份必须覆盖整个 publication store，而不是只备份 `current` 或 `feed/head.json`。在没有平台级一致性快照时，应先停止单 writer，再复制整个目录并保留权限、目录项和原子指针关系。恢复后先运行 publishing 恢复测试和本地 feed 读取，再允许发布。

SQLite consumer 状态库同样要连同主文件及可能存在的 `-wal`/`-shm` 一致备份；最安全的做法是在无 prepare/ack 进程时使用 SQLite 在线备份能力或平台一致性快照。不要用空库替换后继续 ack；首次状态丢失必须重新显式选择 `--baseline` 或 `--replay-from`。

本地巡检：

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src /usr/bin/python3 -m unittest \
  tests.publishing.test_release tests.publishing.test_recovery -v
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src /usr/bin/python3 -m unittest \
  tests.sharing.test_feed_reader tests.sharing.test_validation \
  tests.sharing.test_store tests.sharing.test_service tests.sharing.test_cli -v
```

告警应区分 `fail`、`blocked` 和 `not_run`。网络、凭据或宿主缺失属于环境阻塞，不得改写为发布或安装通过。本仓没有告警发送器或群消息发送器；这里只定义可供外部系统读取的结果。
