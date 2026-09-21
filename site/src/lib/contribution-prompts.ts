import { sitePath } from "./urls";

export const contributionRepository = "https://github.com/hwskill/hwskill";

export type ContributionUrls = {
  repository: string;
  agentGuide: string;
  contributionRules: string;
  catalog: string;
  entrySchema: string;
  recommendationSchema: string;
  externalTemplate: string;
  hostedTemplate: string;
  recommendationTemplate: string;
};

export function buildContributionUrls(site: URL): ContributionUrls {
  const absolute = (path: string) => new URL(sitePath(path), site).href;
  return {
    repository: contributionRepository,
    agentGuide: absolute("/contribute/agent.md"),
    contributionRules: absolute("/contribute/CONTRIBUTING.md"),
    catalog: absolute("/data/catalog.json"),
    entrySchema: absolute("/schemas/entry.schema.json"),
    recommendationSchema: absolute("/schemas/recommendation-source.schema.json"),
    externalTemplate: absolute("/templates/entries/external.yaml"),
    hostedTemplate: absolute("/templates/entries/hosted.yaml"),
    recommendationTemplate: absolute("/templates/recommendations/recommendation.md"),
  };
}

export function buildSkillPrompt(urls: ContributionUrls): string {
  return `请为 HWSkill 贡献一个技能，最终创建 Pull Request 并返回 URL。

技能来源：<粘贴仓库、网页或本地 hosted 目录>
实际用途：<说明希望解决的任务>

目标仓库：${urls.repository}
请先读取贡献指引 ${urls.agentGuide}、贡献规范 ${urls.contributionRules}、当前目录 ${urls.catalog}、Entry Schema ${urls.entrySchema}，并根据来源类型读取 external 模板 ${urls.externalTemplate} 或 hosted 模板 ${urls.hostedTemplate}。检查重复 ID，核对公开来源、可选的 branch、tag 或 commit 引用、许可证、兼容性、依赖和限制；不要执行不受信任的上游脚本，不要编造安装或使用结果。创建条目后运行目录 validate 和 build，并按 Pull Request 模板提交安装与使用验证报告；无法执行的项目要写明边界。审阅仅包含本次贡献的差异并准备 PR 说明。本提示词授权你为这次贡献创建分支、提交、推送并向目标仓库默认分支创建 Pull Request，但不授权合并；不要在准备好 PR 文案后停下。最终返回 PR URL 和检查结果。若缺少必要来源事实、GitHub 凭据或仓库权限，明确指出阻塞项，不要声称已经创建 PR。`;
}

export function buildRecommendationPrompt(urls: ContributionUrls): string {
  return `请为 HWSkill 贡献一篇推荐文章，最终创建 Pull Request 并返回 URL。

推荐主题：<填写主题>
关联技能名称、ID 或来源：<填写一个或多个>
推荐理由：<说明适用场景和价值>
公开作者名称：<填写作者或团队>
公开证据：<可选链接；没有证据时不要声称实测效果>

目标仓库：${urls.repository}
请先读取贡献指引 ${urls.agentGuide}、贡献规范 ${urls.contributionRules}、当前目录 ${urls.catalog}、Recommendation Source Schema ${urls.recommendationSchema} 和 Markdown 推荐模板 ${urls.recommendationTemplate}。逐个检查关联技能是否已收录；未收录技能必须在同一个 Pull Request 中补齐：同时读取 Entry Schema ${urls.entrySchema} 及 external 模板 ${urls.externalTemplate} 或 hosted 模板 ${urls.hostedTemplate}，创建对应技能条目。只有全部关联技能都存在、有效且可发布时才把推荐设为 ready；否则保留 draft 并说明缺失信息。用 Markdown 编写正文，并区分来源事实和作者判断；在 Pull Request 说明中附安装与使用验证报告。完成后运行目录 validate 和 build，审阅仅包含本次贡献的差异并准备 PR 说明。本提示词授权你为这次贡献创建分支、提交、推送并向目标仓库默认分支创建 Pull Request，但不授权合并；不要在准备好 PR 文案后停下。最终返回 PR URL 和检查结果。若缺少必要事实、GitHub 凭据或仓库权限，明确指出阻塞项，不要声称已经创建 PR。`;
}
