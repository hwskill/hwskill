# 开发指南

## 代码与事实源

- `entries/`、`recommendations/`、`curation/`：人工维护事实源。
- `skills-src/`：仅保存 hosted 条目的完整正文；external 只保存定位与版本信息。
- `src/hwskill/directory/`：Schema、交叉校验和确定性构建。
- `src/hwskill/verification/`：来源绑定、隔离安装和阶段报告。
- `src/hwskill/publishing/`：不可变 release、事件、回滚与恢复。
- `src/hwskill/sharing/`：安全 feed 读取、筛选、SQLite 状态和 prepare/ack。
- `site/`：Astro 静态站与 Pagefind 查询验收。

旧 Profile/Hook/MCP runtime 不属于新版开发面；不要重新引入旧的初始化、Profile、dump 或 MCP 命令，也不要恢复 `mcp` 依赖。

## 贡献检查

先阅读 `schemas/`、`templates/` 和 `docs/guides/agent-contribution.md`。常用检查为：

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src /usr/bin/python3 -m unittest discover -s tests -t . -v
PYTHONPATH=src /usr/bin/python3 -m hwskill.directory validate --repo-root . --json
PYTHONPATH=src /usr/bin/python3 -m hwskill.directory build \
  --repo-root . --out /tmp/hwskill-directory --json
npm --prefix site run build
npm --prefix site run index
git diff --check
```

构建输出目录必须是新的非重叠路径。不要把 `/tmp` 构建产物复制回事实源，也不要通过修改派生 JSON 修正输入。

## 测试分层

- `tests/directory`：Schema、摘要、hosted/external 边界和构建。
- `tests/verification`：安装目录安全、来源绑定、报告持久化和失败分段。
- `tests/publishing`：不可变发布、故障注入恢复、回滚和 tombstone。
- `tests/sharing`：URL/重定向/预算、快照绑定、筛选、SQLite 和 ack。
- `tests/site`：路由、机器数据和搜索查询。
- `tests/agent-experience`：隔离观察器；没有受信 Luna 环境时结果只能是 `blocked/not_run`。
- `tests/migration`：旧输入的只读 preview 与清理边界。
- `tests/validation`：发布验收编排本身。

行为修复应先增加能复现缺陷的 RED，再写最小修复并运行相关分组与全套测试。测试 fixture 的通过不能替代远端服务、真实宿主或硬件环境验证。

## CLI 发现

```sh
hwskill-directory --help
hwskill-publish --help
hwskill-sharing --help
hwskill-verify --help
```

四个入口来自同一 wheel。`python -m hwskill` 和旧聚合命令已退出，不应作为兼容入口使用。

## 站点视觉与组件

站点使用 Astro + React + Tailwind CSS，shadcn/ui 的配置在 `site/components.json`，组件源码在 `site/src/components/ui/`。当前 preset 为 `b7ClMyaTC`（Base UI、Luma、Mist、Cyan、Public Sans、Remix Icon）。原生搜索与剪贴板脚本继续使用静态渲染后的 DOM，不为静态卡片或按钮加载 React hydration。

`site/src/styles/theme.css` 保存官方主题变量，`global.css` 保存站点布局。导航右侧图标打开浅色／深色／跟随系统选项，默认跟随系统；手动选择保存在 `hwskill-theme`，选择系统会删除该覆盖。首屏脚本提前恢复有效主题，主题变量用 `light-dark()`，Tailwind dark 变体通过相同的 `data-theme` 与系统兜底规则激活。不要只修改其中一套激活规则。存储受限时当前页仍可切换，无 JavaScript 时使用系统主题。

字体通过 Fontsource 同源打包：Public Sans 用于英文，中文优先使用本地苹方／微软雅黑／Noto／思源黑体，缺失时按需加载 Noto Sans SC Variable 分片。后续调整字体、圆角或颜色应同步检查浅色、深色、手机布局和 Markdown 长文。决策依据见 `docs/superpowers/specs/2026-10-27-shadcn-preset-design.md`。

`RecommendationCard.astro` 是首页、目录、主题页、技能关联推荐及搜索结果的共享推荐卡片。技能和推荐卡片使用相同的 `content-card` 标题、摘要、标签及页脚结构。辅助相关推荐区域不参与详情页正文索引，避免改变搜索主题。

内容列表按本站首次收录／发布时间倒序；关键词相关性只用于选择搜索候选，候选的展示同样按发布时间排序。日期由 `site/src/lib/publication.mjs` 从 Git 首次添加记录提取，保持条目 ID 的连续性，兼容层级移动及推荐 YAML→Markdown 迁移。构建不会修改原始业务 Schema 或按文件修改时间猜测日期。分类导航、分组和流程步骤保持其语义顺序。

CI 已使用全历史 checkout。源码归档或浅克隆使用 `site/src/data/publication-dates.json` 快照；新内容缺少可验证日期时排在已知日期之后且不显示伪造日期。需要刷新该快照时，在内容提交进入全历史工作区后运行 `node site/scripts/update-publication-dates.mjs`，随变更提交生成的 JSON。测试命令为 `node --test site/tests/*.test.mjs`；其中真实 Git 日期集成测试需要允许 Node 在临时目录启动 Git。
