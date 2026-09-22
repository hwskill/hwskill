import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { resolve } from "node:path";
import { renderMarkdown } from "../src/lib/markdown.mjs";

const root = resolve(import.meta.dirname, "..");
const read = (relative) => readFileSync(resolve(root, relative), "utf8");

test("skill detail makes the Chinese translation the primary body", () => {
  const detail = read("src/pages/skills/[namespace]/[name].astro");
  const notice = "本文为技能原文的中文译文，可能滞后于上游内容，请以原文为准。";
  assert.match(detail, /renderMarkdown\(item\.translation\.body\)/);
  assert.match(detail, /item\.translation\.source_url/);
  assert.match(detail, /item\.translation\.translated_at/);
  assert.ok(detail.indexOf(notice) < detail.indexOf("set:html={translationBody}"));
  assert.match(detail, /查看原文/);
  assert.match(detail, /data-pagefind-weight="0\.05"/);
  assert.doesNotMatch(detail, /translation-body" data-pagefind-ignore/);
});

test("skill surfaces contain no verification state UI", () => {
  const sources = [
    "src/lib/data.ts",
    "src/components/SkillCard.astro",
    "src/components/SearchFilters.astro",
    "src/components/InstallPrompt.astro",
    "src/pages/skills/[namespace]/[name].astro",
  ].map(read).join("\n");
  assert.doesNotMatch(sources, /verification|验证矩阵|安装与行为已验证|未完成验证/i);
});

test("translation markdown renders useful structures without activating unsafe markup", () => {
  const html = renderMarkdown(`# 标题

| A | B |
| - | - |
| 1 | 2 |

\`\`\`ts
const ok = true;
\`\`\`

<script>alert(1)</script>

[script](javascript:alert(1))
[data](data:text/html,bad)
[credential](https://user:secret@example.com/private)
`);
  assert.match(html, /<table>/);
  assert.match(html, /<code class="language-ts">/);
  assert.doesNotMatch(html, /<script|javascript:|data:text\/html|user:secret/i);
  assert.doesNotMatch(html, /<a[^>]+href=["'](?:javascript|data|vbscript):/i);
});
