import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const dist = resolve(root, "dist");
const generated = resolve(root, ".generated/directory");
const preIndex = process.argv.includes("--pre-index");
const catalog = JSON.parse(readFileSync(resolve(generated, "catalog.json"), "utf8"));
const recommendations = JSON.parse(readFileSync(resolve(generated, "recommendations.json"), "utf8")).recommendations;
const curation = JSON.parse(readFileSync(resolve(generated, "curation.json"), "utf8"));
const required = [
  "index.html",
  "skills/index.html",
  "contribute/index.html",
  "recommendations/index.html",
  "data/catalog.json",
  "schemas/entry.schema.json",
  "schemas/recommendation.schema.json",
  "schemas/recommendation-source.schema.json",
  "templates/entries/external.yaml",
  "templates/entries/hosted.yaml",
  "templates/recommendations/recommendation.md",
  "contribute/agent.md",
  ...catalog.entries.map((item) => `skills/${item.entry.id}/index.html`),
  ...recommendations.filter((item) => item.status === "ready").map((item) => `recommendations/${item.id}/index.html`),
  ...(curation.topics ?? []).map((topic) => `topics/${topic.slug}/index.html`),
];
if (!preIndex) required.push("pagefind/pagefind.js");
const missing = required.filter((relative) => !existsSync(resolve(dist, relative)));
if (missing.length) {
  console.error(JSON.stringify({ result: "fail", missing }, null, 2));
  process.exit(1);
}
const recommendationIndex = readFileSync(resolve(dist, "recommendations/index.html"), "utf8");
for (const expected of [
  "Superpowers：把 Agent 研发变成可审查流程",
  "Matt Pocock：可组合的工程与沟通工具箱",
  "/schemas/recommendation-source.schema.json",
  "/templates/recommendations/recommendation.md",
  "未收录技能必须在同一个 Pull Request 中补齐",
]) {
  if (!recommendationIndex.includes(expected)) {
    console.error(`技能推荐页缺少必要内容: ${expected}`);
    process.exit(1);
  }
}
for (const recommendation of recommendations.filter((item) => item.status === "ready")) {
  const html = readFileSync(resolve(dist, `recommendations/${recommendation.id}/index.html`), "utf8");
  if (!html.includes('data-pagefind-meta="kind"') || !html.includes('>recommendation</span>')) {
    console.error(`推荐页缺少 Pagefind 类型元数据: ${recommendation.id}`);
    process.exit(1);
  }
  const links = [...html.matchAll(/href="([^"]*\/skills\/[^"#?]+)"/g)].map((match) => {
    const pathname = new URL(match[1], "https://hwskill.local").pathname;
    return pathname.match(/\/skills\/(.+?)\/?$/)?.[1] ?? "";
  }).filter(Boolean);
  const expected = recommendation.skills.map((skill) => skill.id).sort();
  const actual = [...new Set(links)].sort();
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    console.error(`推荐页关联技能链接不完整或包含未知技能: ${recommendation.id}`);
    process.exit(1);
  }
}
for (const item of catalog.entries) {
  const html = readFileSync(resolve(dist, `skills/${item.entry.id}/index.html`), "utf8");
  if (!html.includes('lang="zh-CN"') || !html.includes('data-pagefind-filter="layer"')) {
    console.error(`详情页缺少中文语言或 Pagefind 元数据: ${item.entry.id}`);
    process.exit(1);
  }
  for (const expected of [
    "本文为技能原文的中文译文，可能滞后于上游内容，请以原文为准。",
    "查看原文",
    item.translation.source_url,
    item.translation.translated_at,
  ]) {
    if (!html.includes(expected)) {
      console.error(`详情页缺少译文信息: ${item.entry.id}: ${expected}`);
      process.exit(1);
    }
  }
  if (/验证矩阵|安装与行为已验证|未完成验证/u.test(html)) {
    console.error(`详情页仍包含已移除的核验状态: ${item.entry.id}`);
    process.exit(1);
  }
}
console.log(JSON.stringify({ result: "pass", checked: required.length, pagefind: !preIndex }));
