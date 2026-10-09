export const DEFAULT_RECOMMENDATION_REQUIREMENTS =
  "说明适用场景、推荐理由、技能组合关系、使用成本、已知限制和不适用场景；区分来源事实与作者判断，不虚构实测效果。";

const clean = (value) => String(value ?? "").trim();

const capabilityPreflight = `开始修改文件之前，先确认并报告以下能力：
1. 具备联网能力，可以读取公开来源、贡献资料和 GitHub；
2. 具备本地工作区读写能力，可以取得仓库、编辑代码、创建分支与提交，并推送和创建 Pull Request；
3. 具备 subagent 或命令执行能力，可以创建临时 clone 或隔离 worktree，在干净条件下运行验证。
联网或工作区读写能力缺失时立即停止并说明阻塞。subagent 不是必需，但命令执行和隔离验证能力必须具备。
GitHub 优先使用 gh：先运行 gh --version 和 gh auth status --hostname github.com。未安装时识别操作系统，按 https://cli.github.com/ 的官方安装说明引导安装；涉及系统安装或提权时取得用户许可。未登录时引导用户运行 gh auth login --hostname github.com --web，认证由用户完成，不要求在聊天中粘贴令牌。登录后再次检查身份，并按需运行 gh auth setup-git。随后检查目标仓库权限；没有直接写权限时创建或复用 Fork，检查推送所需的凭据和分支保护规则。登录成功不等于具备推送权限，不要宣称未经验证的权限已经通过。
缺少 gh 或登录授权时保留输入与当前进度；可以先准备本地变更和 PR 正文，完成配置后继续推送和创建 PR。`;

export function validateSkillContribution(input) {
  const errors = {};
  if (!clean(input?.source)) {
    errors.source = input?.sourceKind === "hosted" ? "请填写 Agent 能访问的本地技能目录。" : "请填写公开仓库或网页地址。";
  }
  if (!clean(input?.purpose)) errors.purpose = "请说明这个技能要解决的实际任务。";
  return errors;
}

export function validateRecommendationContribution(input) {
  const errors = {};
  if (!clean(input?.topic)) errors.topic = "请填写推荐主题。";
  if (!clean(input?.skills)) errors.skills = "请填写至少一个技能名称、ID 或来源。";
  if (!clean(input?.reason)) errors.reason = "请说明推荐理由和适用场景。";
  if (!clean(input?.author)) errors.author = "请填写公开作者或团队名称。";
  if (!clean(input?.requirements)) errors.requirements = "请填写文章写作要求。";
  return errors;
}

export function buildSkillContributionPrompt(urls, input) {
  const hosted = input.sourceKind === "hosted";
  const sourceLabel = hosted ? "把我拥有的技能托管到 HWSkill" : "引用公开的外部技能";
  const notes = clean(input.notes) || "无额外要求";
  return `请为 HWSkill 贡献一个技能，最终创建 Pull Request 并返回 URL。

来源类型：${sourceLabel}
技能来源：${clean(input.source)}
实际用途：${clean(input.purpose)}
补充要求：${notes}

目标仓库：${urls.repository}

${capabilityPreflight}

能力预检通过后，读取贡献指引 ${urls.agentGuide}、贡献规范 ${urls.contributionRules}、当前目录 ${urls.catalog}、Entry Schema ${urls.entrySchema}，并读取${hosted ? ` hosted 模板 ${urls.hostedTemplate}` : ` external 模板 ${urls.externalTemplate}`}。检查重复 ID、兼容性、依赖和限制。

你负责完成以下工作，不要把这些步骤转交给用户：
- 读取完整技能原文和相关材料，在 translations/<namespace>/<name>.md 生成完整中文翻译；保留代码、命令、链接和技术含义，不添加原文没有的能力声明。
- 查找 LICENSE 或来源授权条款，记录证据 URL，并确认许可证允许翻译和再分发。许可证缺失、含糊或不允许时，在修改文件前停止并说明阻塞，不能自行推定允许。
- ${hosted ? "把用户拥有且获准再分发的完整技能目录放入 skills-src/，并创建 hosted Entry。" : "只在 Entry 中记录公开来源和目录定位；可选的 branch、tag 或 commit 引用按来源事实填写，省略时跟随上游默认分支。"}
- 不执行未经审阅的不可信脚本，不编造安装或使用结果。

内容完成后，在临时 clone 或隔离 worktree 中从目标最新默认分支开始，应用本次变更并运行目录 validate、build 及所有适用测试。在安全且条件允许时，安装技能并执行一个代表性使用任务。根据真实执行结果填写 Pull Request 模板中的九项技能验证报告；无法安全执行或环境不具备的项目写入“未验证项”，不得写成通过。

审阅差异只包含本次贡献，创建独立提交并推送。本提示词授权你为本次贡献创建分支、提交、推送和 Pull Request，但不授权合并；不要停在本地提交或 PR 文案。创建 PR 后等待必要的 CI；失败时在同一分支修复并重新检查。最终返回实际 PR URL、目标分支、CI 状态、验证结果和未验证边界。`;
}

export function buildRecommendationContributionPrompt(urls, input) {
  const evidence = clean(input.evidence) || "未提供；不要声称实测效果";
  return `请为 HWSkill 贡献一篇推荐文章，最终创建 Pull Request 并返回 URL。

推荐主题：${clean(input.topic)}
关联技能名称、ID 或来源：${clean(input.skills)}
推荐理由：${clean(input.reason)}
公开作者名称：${clean(input.author)}
公开证据：${evidence}
写作要求：${clean(input.requirements)}

目标仓库：${urls.repository}

${capabilityPreflight}

能力预检通过后，读取贡献指引 ${urls.agentGuide}、贡献规范 ${urls.contributionRules}、当前目录 ${urls.catalog}、Recommendation Source Schema ${urls.recommendationSchema} 和 Markdown 推荐模板 ${urls.recommendationTemplate}。逐个检查关联技能是否已收录。

如果存在缺失技能，先明确告诉用户本次推荐将扩展为复合贡献，并在同一个 Pull Request 中为每个缺失技能补齐技能元数据、中文翻译、许可证核验和九项验证报告；需要时请用户补充技能来源或用途。随后读取 Entry Schema ${urls.entrySchema} 以及 external 模板 ${urls.externalTemplate} 或 hosted 模板 ${urls.hostedTemplate}。许可证缺失、含糊或不允许翻译和再分发时停止并报告，不能自行推定允许。只有全部关联技能都存在、有效且可发布时才把推荐设为 ready，否则保持 draft 并说明缺口。

用 Markdown 编写正文，遵循用户给出的写作要求。完成后在临时 clone 或隔离 worktree 中从目标最新默认分支开始，运行目录 validate、build、站点测试、站点构建和索引检查。审阅差异只包含本次贡献，创建独立提交并推送。本提示词授权你为本次贡献创建分支、提交、推送和 Pull Request，但不授权合并；不要停在本地提交或 PR 文案。创建 PR 后等待必要的 CI；失败时在同一分支修复并重新检查。最终返回实际 PR URL、目标分支、CI 状态、检查结果和未验证边界。`;
}

export function prepareContributionCopy(kind, urls, input) {
  const errors = kind === "skill" ? validateSkillContribution(input) : validateRecommendationContribution(input);
  if (Object.keys(errors).length) return { ok: false, errors };
  const prompt = kind === "skill" ? buildSkillContributionPrompt(urls, input) : buildRecommendationContributionPrompt(urls, input);
  return { ok: true, errors, prompt };
}

export function prepareUnifiedContribution(urls, input) {
  const source = clean(input?.source);
  if (!source) return { ok: false, errors: { source: "请填写技能地址、目录 ID 或 Agent 可访问的本地路径。" } };
  const requirements = clean(input?.requirements);
  const recommendation = ["topic", "reason", "author", "evidence"].some((name) => clean(input?.[name]))
    || Boolean(requirements && requirements !== DEFAULT_RECOMMENDATION_REQUIREMENTS);
  const kind = recommendation ? "recommendation" : "skill";
  const data = {
    ...input,
    source,
    purpose: clean(input?.purpose) || "请从来源读取用途和适用范围；无法确定时请用户补充，不要编造。",
    skills: source,
    topic: clean(input?.topic) || "请根据技能和推荐理由拟定主题",
    reason: clean(input?.reason) || "请用户补充推荐理由与实际场景，保留已有信息",
    author: clean(input?.author) || "请用户补充公开署名；不要从登录账号推定",
    requirements: clean(input?.requirements) || DEFAULT_RECOMMENDATION_REQUIREMENTS,
  };
  const prompt = recommendation
    ? buildRecommendationContributionPrompt(urls, data)
    : buildSkillContributionPrompt(urls, data);
  return { ok: true, errors: {}, kind, prompt: `${prompt}\n\n技能来源方式：${input?.sourceKind === "hosted" ? "有权再分发的本地技能目录，按 hosted 处理" : "公开技能地址或已收录目录 ID"}\n实际用途：${data.purpose}\n补充要求：${clean(input?.notes) || "无额外要求"}\n先匹配当前目录，已收录技能复用已有 ID，不重复收录。表单未填写的客观元数据由你从来源核实并补齐。推荐主题、署名和个人使用体验需用户确认；信息不足时保留推荐意图并请用户补充，不要悄悄改成仅收录技能。` };
}
