import { createReadStream, readFileSync, statSync } from "node:fs";
import { createServer } from "node:http";
import { extname, resolve } from "node:path";
import { pathToFileURL } from "node:url";

const root = resolve(import.meta.dirname, "..");
const dist = resolve(root, "dist");
const cases = JSON.parse(readFileSync(resolve(root, "search-cases.json"), "utf8"));
const curation = JSON.parse(readFileSync(resolve(root, ".generated/directory/curation.json"), "utf8"));
const mime = { ".js": "text/javascript", ".json": "application/json", ".wasm": "application/wasm", ".css": "text/css", ".html": "text/html" };

const server = createServer((request, response) => {
  try {
    const pathname = decodeURIComponent(new URL(request.url ?? "/", "http://localhost").pathname);
    let target = resolve(dist, `.${pathname}`);
    if (!target.startsWith(`${dist}/`) && target !== dist) throw new Error("unsafe path");
    if (statSync(target).isDirectory()) target = resolve(target, "index.html");
    response.writeHead(200, { "content-type": mime[extname(target)] ?? "application/octet-stream" });
    createReadStream(target).pipe(response);
  } catch {
    response.writeHead(404).end("not found");
  }
});

await new Promise((resolveListen) => server.listen(0, "127.0.0.1", resolveListen));
const address = server.address();
const origin = `http://127.0.0.1:${address.port}`;

function queryVariants(value) {
  const normalized = value.trim().toLowerCase();
  const cjkPairs = /^[\p{Script=Han}]{4,}$/u.test(normalized) ? normalized.match(/[\p{Script=Han}]{2}/gu)?.join(" ") ?? "" : "";
  const additions = Object.entries(curation.synonyms ?? {})
    .filter(([key, values]) => key.toLowerCase() === normalized || values.some((item) => item.toLowerCase() === normalized))
    .flatMap(([key, values]) => [key, ...values]);
  return [...new Set([value, cjkPairs, ...additions].filter(Boolean))];
}

try {
  const pagefind = await import(pathToFileURL(resolve(dist, "pagefind/pagefind.js")));
  await pagefind.options({ basePath: `${origin}/pagefind/`, ranking: { termSimilarity: 0 } });
  const failures = [];
  for (const test of cases) {
    const filters = test.filters ?? {};
    const variants = queryVariants(test.query);
    const responses = variants.length
      ? await Promise.all(variants.map((query) => pagefind.search(query, { filters })))
      : [await pagefind.search(null, { filters })];
    const resultMap = new Map();
    for (const response of responses) for (const result of response.results) if (!resultMap.has(result.id)) resultMap.set(result.id, result);
    const records = await Promise.all([...resultMap.values()].slice(0, 20).map((result) => result.data()));
    const actual = [...new Set(records.map((record) => record.meta["skill-id"]).filter(Boolean))].slice(0, 5);
    const missing = test.expected_ids.filter((id) => !actual.includes(id));
    if (missing.length || (test.expected_ids.length === 0 && actual.length)) {
      failures.push({
        query: test.query,
        query_variants: variants,
        filters,
        expected: test.expected_ids,
        actual,
        observed: records.slice(0, 5).map((record) => ({ url: record.url, title: record.meta.title, skill_id: record.meta["skill-id"] ?? null })),
      });
    }
  }
  if (failures.length) {
    console.error(JSON.stringify({ result: "fail", failures }, null, 2));
    process.exitCode = 1;
  } else {
    console.log(JSON.stringify({ result: "pass", engine: "pagefind", cases: cases.length, kinds: [...new Set(cases.map((item) => item.kind))] }));
  }
} finally {
  await new Promise((resolveClose) => server.close(resolveClose));
}
