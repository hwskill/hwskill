# shadcn preset b7ClMyaTC 接入设计

站点保留 Astro 静态路由、规范化数据、中文译文、Pagefind 搜索和贡献表单，接入 React、Tailwind CSS 与官方 shadcn/ui 源码组件。底层为 Base UI；preset 为 b7ClMyaTC：Luma、Mist、Cyan、Public Sans、medium radius、Remix Icon、subtle menu accent、default-translucent menu color。

正式接入比仅复制主题 CSS 增加构建依赖，但后续可以继续使用官方组件维护界面。静态卡片、按钮及输入框在构建期渲染；搜索和剪贴板沿用原生脚本，不为这些组件增加 React hydration。

主题变量保留 preset 的浅色、深色 Oklch 值及基础圆角，使用 CSS light-dark() 选择配色。导航提供浅色、深色和跟随系统三种选择，默认跟随系统；首屏恢复、手动覆盖及实时系统切换规则见 `2026-10-27-ui-consistency-design.md`。

## 中文字体

英文与数字使用 Public Sans Variable。中文依次尝试 PingFang SC、Microsoft YaHei、Noto Sans CJK SC、Noto Sans SC、Source Han Sans SC；最后使用同源 Noto Sans SC Variable 和系统字体兜底。页面语言保持 zh-CN。

仅用系统字体没有下载成本，但缺少中文字库的设备会出现缺字；强制统一中文 Web Font 会让已有字体的设备承担额外流量。本地优先、Web Font 最终兜底兼顾两者。Fontsource 将字体编入站点，使用 font-display: swap 和 unicode-range 分片，不依赖浏览器访问外部字体 CDN。字体包为 OFL-1.1，并保留包内许可证。

中文正文行高 1.75，长文 1.9；标题适度字距并响应式换行，代码使用独立等宽字体。

依据：

- [Apple 系统字体列表](https://developer.apple.com/fonts/system-fonts/)
- [Microsoft YaHei 屏幕阅读说明](https://learn.microsoft.com/en-gb/typography/font-list/microsoft-yahei)
- [Noto CJK 格式与地区字形](https://github.com/notofonts/noto-cjk/blob/main/Sans/README.md)
- [Astro 官方接入](https://ui.shadcn.com/docs/installation/astro)
- [指定 preset](https://ui.shadcn.com/create?preset=b7ClMyaTC)

构建、测试、搜索及页面验证记录见 `docs/validation/2026-10-27-ui-consistency.md`。
