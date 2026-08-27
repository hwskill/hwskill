# GitCode Discussion v5 API 参考

所有请求以 `https://api.gitcode.com/api/v5` 为基础，只使用 `GET`。

| 内容 | Endpoint |
| --- | --- |
| Discussion 详情 | `/repos/{owner}/{repo}/discuss/{number}` |
| 顶层评论 | `/repos/{owner}/{repo}/discuss/{number}/comment` |
| 某顶层评论的回复 | `/repos/{owner}/{repo}/discuss/{number}/comment/{comment_id}/reply` |

## 认证

优先从环境变量 `GITCODE_TOKEN` 读取 token，并作为 `PRIVATE-TOKEN` 请求头发送。不要用 URL 查询参数传递 token。

## 分页与字段

- 评论和回复使用 `page` 与 `per_page`；`per_page` 最大为 100。
- 有响应头 `total_page` 时请求至该页；否则在返回条数小于 `per_page` 时结束。
- Discussion 详情使用 `number`、`id`、`title`、`md_content`、`author`、`created_at`、`updated_at`、`comment_total`。
- 评论和回复使用 `id`、`md_content`、`author`、`created_at`；顶层评论还使用 `reply_total`。
- `content` 是渲染 HTML，不能与 `md_content` 一起写入 Markdown。

## 快照完整性

先读详情，抓取评论和回复，再读详情。两次详情的 `id`、`number`、`updated_at` 和 `comment_total` 必须一致；顶层评论数量也必须等于最终 `comment_total`。不一致表示抓取窗口发生了变化，应重试整个归档。
