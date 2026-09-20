import assert from "node:assert/strict";
import test from "node:test";
import { renderMarkdown } from "../src/lib/markdown.mjs";

test("renders supported recommendation markdown", () => {
  const html = renderMarkdown("# 标题\n\n| A | B |\n| - | - |\n| 1 | 2 |\n\n```sh\necho ok\n```\n");
  assert.match(html, /<h1>标题<\/h1>/);
  assert.match(html, /<table>/);
  assert.match(html, /<code class="language-sh">/);
});

test("does not activate html or dangerous protocols", () => {
  const html = renderMarkdown('<script>alert(1)</script>\n\n[x](javascript:alert(1))\n\n[y](data:text/html,bad)\n\n[z](https://user:secret@example.com/private)');
  assert.doesNotMatch(html, /<script|javascript:|data:text\/html|user:secret/i);
});

test("marks external links safely", () => {
  const html = renderMarkdown("[upstream](https://github.com/obra/superpowers)");
  assert.match(html, /rel="noopener noreferrer"/);
});
