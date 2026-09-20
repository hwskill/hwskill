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
  "data/catalog.json",
  "schemas/entry.schema.json",
  "schemas/recommendation.schema.json",
  "templates/entries/external.yaml",
  "templates/entries/hosted.yaml",
  "templates/recommendations/recommendation.yaml",
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
for (const item of catalog.entries) {
  const html = readFileSync(resolve(dist, `skills/${item.entry.id}/index.html`), "utf8");
  if (!html.includes('lang="zh-CN"') || !html.includes('data-pagefind-filter="layer"')) {
    console.error(`详情页缺少中文语言或 Pagefind 元数据: ${item.entry.id}`);
    process.exit(1);
  }
}
console.log(JSON.stringify({ result: "pass", checked: required.length, pagefind: !preIndex }));
