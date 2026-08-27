# GitCode PR 差异与评论只读 API

## 目录

- [使用的 endpoint](#使用的-endpoint)
- [变更文件与-patch-字段](#变更文件与-patch-字段)
- [标准-patch-输出边界](#标准-patch-输出边界)
- [评论与回复字段](#评论与回复字段)
- [分页](#分页)
- [认证与密钥](#认证与密钥)
- [禁止的写操作](#禁止的写操作)

## 使用的 endpoint

API base：`https://api.gitcode.com/api/v5`

| Endpoint | 用途 |
| --- | --- |
| `GET /repos/{owner}/{repo}/pulls/{number}` | 获取 PR 元数据、base/head SHA 和状态 |
| `GET /repos/{owner}/{repo}/pulls/{number}/comments` | 获取普通评论、行级检视意见和嵌套回复 |
| `GET /repos/{owner}/{repo}/pulls/comments/{id}` | 补全单条行级意见的文件路径、行号和 diff SHA |
| `GET /repos/{owner}/{repo}/pulls/{number}/files` | 获取完整分页的 PR 变更文件和文本 hunk |

评论接口使用 `direction=asc` 保持时间顺序，`per_page` 最大为 `100`。不要设置 `comment_type`，否则会遗漏
`pr_comment` 或 `diff_comment`。

## 变更文件与 patch 字段

`GET /pulls/{number}/files` 的每个 `files[]` 项包含 `patch` 对象。独立 patch CLI 遍历全部分页，
先验证所有文件，再将下列字段规范化为 Git unified diff：

| 字段 | 含义 |
| --- | --- |
| `files[].patch.diff` | 文件文本 hunk，用于合成 `diff --git`、`---`、`+++` 等 header 之后的内容 |
| `files[].patch.old_path` / `new_path` | 变更前后路径，也用于判定 rename |
| `files[].patch.a_mode` / `b_mode` | 变更前后的 Git file mode |
| `files[].patch.new_file` | 是否新增文件 |
| `files[].patch.deleted_file` | 是否删除文件 |
| `files[].patch.renamed_file` | 是否重命名文件 |
| `files[].patch.too_large` | diff 是否因过大而不完整；`true` 时必须 fail-fast |

新增文件的旧侧缺失值可能有两种 API 形状：

| `old_path` | `a_mode` | 处理 |
| --- | --- | --- |
| `null` | `null` | 接受，表示旧侧不存在 |
| 与 `new_path` 完全相同 | `"0"` | 接受，GitCode 新增文件 sentinel，同样表示旧侧不存在 |
| 其他任意组合 | 任意值 | 拒绝，避免把矛盾路径或真实旧 mode 误判为新增文件 |

PR 446 的 live files payload 中 7 个新增文件全部使用第二种形状；同一 payload 没有删除或重命名
文件，不能据此推断删除文件存在对称 sentinel。删除文件仍要求 `new_path=null`、`b_mode=null`；
重命名仍要求不同的完整 old/new 路径和受支持的常规 mode。

`files[].patch.diff` 是 PR 整个文件的 hunk 数据，不是 `comments[].position` 附近的代码片段。
评论 position 只用于定位检视意见，不能替代 files endpoint 的 PR 差异。首版 patch 只支持文本；
二进制占位、Git binary patch 或 `too_large=true` 都必须在写出前整体失败。

## 标准 patch 输出边界

- 非空成功 patch 的适用输入是 files payload 对应的 comparison preimage，可在该内容上使用普通
  `git apply --check` 和 `git apply`。files endpoint 不保证该 preimage 等于 PR metadata endpoint
  当前返回的 `pull_request.base.sha`；已合入或目标分支已前进时，preimage 可能是更早的历史提交。
- CLI 将 PR 状态、`pull_request.base.sha`、`head.sha` 和文件数分别写为 stderr 中的 `state`、
  `api_base`、`head` 和 `files`。这些字段只保留当前 API 证据，不标识 apply base。
- patch CLI 只请求 PR metadata/files，不执行 git、fetch 或评论请求。需要实际 apply 时，调用方根据
  PR 状态和仓库历史独立定位 comparison preimage。
- 空 changed-files 成功输出零字节 patch，表示 no-op；使用 `git apply --check --allow-empty`
  和 `git apply --allow-empty` 验证，不应将普通 `git apply` 对空输入的拒绝视为生成错误。

## 评论与回复字段

普通评论和行级意见共用以下字段：

- `id`
- `discussion_id`
- `comment_type`
- `body`
- `user`
- `created_at`
- `updated_at`

评论列表中的 `diff_comment` 还可能包含：

- `diff_position.base_sha/start_sha/head_sha`
- `diff_position.old_path/new_path`
- `diff_position.old_line/new_line`
- `resolved`
- `reply[]`

`reply[]` 是 discussion 的回复列表，每项至少包含 `id/body/user/created_at/updated_at`。GitCode 在
2025-07 的 API 更新中为评论列表增加了该字段，因此完整回复不需要额外猜测 discussion GET endpoint。

评论列表可能只在 `diff_position` 返回 `start_new_line/end_new_line`，缺少文件路径。脚本对每条
`diff_comment` 调用单条评论 endpoint，把完整 `position` 合并回列表数据；列表中的 `reply[]` 继续保留。
最终把位置稳定暴露为 `position`，并把 `reply[]` 规范为 `replies[]`。
单条评论 endpoint 可能把同一行级意见的 `comment_type` 返回为 `DiffNote`；脚本将其规范为
`diff_comment`，并通过 `source_comment_type` 保留原始值，确保汇总和下游过滤稳定。

## 分页

评论和文件接口在响应头返回：

- `total_count`
- `total_page`

脚本必须一直读取到 `page == total_page`。当代理或旧 API 未返回 `total_page` 时，以当前页条目数小于
`per_page` 作为结束条件。跨页重复的非空 `id` 只保留第一次出现的数据。

## 认证与密钥

公开仓库通常可以匿名读取。私有仓库使用 query 参数 `access_token`，但脚本只从
`GITCODE_TOKEN` 环境变量读取：

```text
GITCODE_TOKEN -> access_token query parameter
```

不得提供 `--token` 参数。HTTP URL、响应正文和异常消息都必须在输出前替换 token；401 且环境变量为空时，
提示调用方设置 `GITCODE_TOKEN`。

## 禁止的写操作

本技能不调用以下 endpoint：

- `POST /repos/{owner}/{repo}/pulls/{number}/comments`
- `POST /repos/{owner}/{repo}/pulls/{number}/discussions/{discussion_id}/comments`
- `PUT /repos/{owner}/{repo}/pulls/{number}/comments/{discussion_id}`
- 评论编辑或删除 endpoint

如用户后续明确要求回复或解决检视意见，应另行确认写操作范围，不得扩展本只读脚本。
