import assert from "node:assert/strict";
import test from "node:test";

import {
  DEFAULT_RECOMMENDATION_REQUIREMENTS,
  buildRecommendationContributionPrompt,
  buildSkillContributionPrompt,
  prepareContributionCopy,
  validateRecommendationContribution,
  validateSkillContribution,
  prepareUnifiedContribution,
} from "../src/lib/contribution-form.mjs";

const urls = {
  repository: "https://github.com/hwskill/hwskill",
  agentGuide: "https://hwskill.github.io/contribute/agent.md",
  contributionRules: "https://hwskill.github.io/contribute/CONTRIBUTING.md",
  catalog: "https://hwskill.github.io/data/catalog.json",
  entrySchema: "https://hwskill.github.io/schemas/entry.schema.json",
  recommendationSchema: "https://hwskill.github.io/schemas/recommendation-source.schema.json",
  externalTemplate: "https://hwskill.github.io/templates/entries/external.yaml",
  hostedTemplate: "https://hwskill.github.io/templates/entries/hosted.yaml",
  recommendationTemplate: "https://hwskill.github.io/templates/recommendations/recommendation.md",
};

test("unified contribution accepts a source alone and delegates metadata discovery", () => {
  const result = prepareUnifiedContribution(urls, { source: "https://github.com/example/skill" });
  assert.equal(result.ok, true);
  assert.equal(result.kind, "skill");
  assert.match(result.prompt, /gh auth status/);
  assert.match(result.prompt, /安装/);
  assert.match(result.prompt, /从来源/);
});

test("recommendation intent is preserved with partial fields rather than silently downgraded", () => {
  const result = prepareUnifiedContribution(urls, { source: "https://github.com/example/skill", reason: "适合评审" });
  assert.equal(result.ok, true);
  assert.equal(result.kind, "recommendation");
  assert.match(result.prompt, /适合评审/);
  assert.match(result.prompt, /请用户补充/);
});

test("default writing instructions do not opt into recommendations but edits do", () => {
  const input = { source: "example/skill", requirements: DEFAULT_RECOMMENDATION_REQUIREMENTS };
  assert.equal(prepareUnifiedContribution(urls, input).kind, "skill");
  assert.equal(prepareUnifiedContribution(urls, { ...input, requirements: "重点介绍入门体验" }).kind, "recommendation");
});

test("missing sources block copy and hosted recommendation retains source and extra instructions", () => {
  assert.equal(prepareUnifiedContribution(urls, { source: "  " }).ok, false);
  const result = prepareUnifiedContribution(urls, { source: "/skills/review", sourceKind: "hosted", reason: "保留证据", notes: "仅验证 Linux" });
  assert.match(result.prompt, /按 hosted 处理/);
  assert.match(result.prompt, /仅验证 Linux/);
});

test("skill contribution rejects missing source and purpose before copying", () => {
  assert.deepEqual(validateSkillContribution({ sourceKind: "external", source: "", purpose: "" }), {
    source: "请填写公开仓库或网页地址。",
    purpose: "请说明这个技能要解决的实际任务。",
  });
});

test("skill prompt assigns capability, licensing, translation, clean verification, and PR work to the agent", () => {
  const prompt = buildSkillContributionPrompt(urls, {
    sourceKind: "external",
    source: "https://github.com/example/skill",
    purpose: "审阅数据库迁移",
    notes: "兼容 Codex",
  });

  for (const expected of [
    "https://github.com/example/skill",
    "审阅数据库迁移",
    "联网能力",
    "本地工作区读写",
    "subagent 或命令执行能力",
    "Fork",
    "许可证",
    "translations/",
    "干净",
    "九项",
    "等待必要的 CI",
  ]) {
    assert.match(prompt, new RegExp(expected));
  }
  assert.doesNotMatch(prompt, /<(?:粘贴|说明|填写)/);
});

test("recommendation contribution validates user fields and supplies editable default writing requirements", () => {
  assert.equal(
    DEFAULT_RECOMMENDATION_REQUIREMENTS,
    "说明适用场景、推荐理由、技能组合关系、使用成本、已知限制和不适用场景；区分来源事实与作者判断，不虚构实测效果。",
  );
  assert.deepEqual(
    validateRecommendationContribution({ topic: "", skills: "", reason: "", author: "", requirements: "" }),
    {
      topic: "请填写推荐主题。",
      skills: "请填写至少一个技能名称、ID 或来源。",
      reason: "请说明推荐理由和适用场景。",
      author: "请填写公开作者或团队名称。",
      requirements: "请填写文章写作要求。",
    },
  );
});

test("recommendation prompt warns that missing skills expand into skill contribution work", () => {
  const prompt = buildRecommendationContributionPrompt(urls, {
    topic: "可靠的代码评审",
    skills: "example/review-skill",
    reason: "保留审阅证据",
    author: "Example Team",
    evidence: "",
    requirements: DEFAULT_RECOMMENDATION_REQUIREMENTS,
  });

  for (const expected of [
    "可靠的代码评审",
    "example/review-skill",
    "缺失技能",
    "技能元数据",
    "中文翻译",
    "许可证核验",
    "验证报告",
    "开始修改文件之前",
    "等待必要的 CI",
  ]) {
    assert.match(prompt, new RegExp(expected));
  }
  assert.doesNotMatch(prompt, /<(?:粘贴|说明|填写)/);
});

test("copy preparation refuses incomplete forms and returns a prompt only for valid input", () => {
  assert.deepEqual(
    prepareContributionCopy("skill", urls, { sourceKind: "hosted", source: "", purpose: "" }),
    {
      ok: false,
      errors: {
        source: "请填写 Agent 能访问的本地技能目录。",
        purpose: "请说明这个技能要解决的实际任务。",
      },
    },
  );

  const ready = prepareContributionCopy("recommendation", urls, {
    topic: "可靠评审",
    skills: "example/review",
    reason: "保留证据",
    author: "Example Team",
    evidence: "",
    requirements: DEFAULT_RECOMMENDATION_REQUIREMENTS,
  });
  assert.equal(ready.ok, true);
  assert.deepEqual(ready.errors, {});
  assert.match(ready.prompt, /可靠评审/);
});
