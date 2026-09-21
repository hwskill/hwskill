import { createReadStream, readFileSync, statSync } from "node:fs";
import { createServer } from "node:http";
import { extname, resolve } from "node:path";
import { pathToFileURL } from "node:url";

const root = resolve(import.meta.dirname, "..");
const dist = resolve(root, "dist");
const cases = JSON.parse(readFileSync(resolve(root, "search-cases.json"), "utf8"));
const curation = JSON.parse(readFileSync(resolve(root, ".generated/directory/curation.json"), "utf8"));
const MIN_RELATIVE_SCORE = 0.9;
const MIN_ABSOLUTE_SCORE = 0.9;
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
  if (!normalized) return [];
  const synonymEntries = Object.entries(curation.synonyms ?? {});
  const canonical = synonymEntries.find(([, values]) => values.some((item) => item.toLowerCase() === normalized))?.[0];
  if (canonical && normalized.length <= 3) return [canonical];
  const shortToken = normalized.split(/\s+/u).find((token) => token.length <= 3);
  const tokenCanonical = shortToken
    ? synonymEntries.find(([, values]) => values.some((item) => item.toLowerCase() === shortToken))?.[0]
    : undefined;
  if (tokenCanonical) return [tokenCanonical];
  return canonical ? [value, canonical] : [value];
}

try {
  const pagefind = await import(pathToFileURL(resolve(dist, "pagefind/pagefind.js")));
  await pagefind.options({ basePath: `${origin}/pagefind/`, ranking: { termSimilarity: 0 } });
  const failures = [];
  for (const test of cases) {
    const filters = test.filters ?? {};
    const variants = queryVariants(test.query);
    const responses = variants.length
      ? await Promise.all(variants.map((variant) => pagefind.search(variant, { filters })))
      : [await pagefind.search(null, { filters })];
    const resultMap = new Map();
    for (const response of responses) for (const result of response.results) {
      const prior = resultMap.get(result.id);
      if (!prior || result.score > prior.score) resultMap.set(result.id, result);
    }
    const ranked = [...resultMap.values()].sort((left, right) => right.score - left.score);
    const topScore = ranked[0]?.score ?? 0;
    const scoreFloor = Math.max(topScore * MIN_RELATIVE_SCORE, MIN_ABSOLUTE_SCORE);
    const candidates = variants.length ? ranked.filter((result) => result.score >= MIN_ABSOLUTE_SCORE).slice(0, 20) : ranked;
    const candidateRecords = await Promise.all(candidates.map(async (result) => ({ ...(await result.data()), score: result.score })));
    const qualifying = variants.length
      ? candidateRecords.filter((record) => record.score >= scoreFloor)
      : candidateRecords;
    const requiredKinds = variants.length
      ? ["skill", "recommendation"].map((kind) => candidateRecords.find((record) => record.meta.kind === kind)).filter(Boolean)
      : [];
    const rankedRecords = [...new Set([...qualifying, ...requiredKinds])].sort((left, right) => right.score - left.score);
    const records = variants.length ? rankedRecords.slice(0, 5) : rankedRecords;
    for (const required of requiredKinds) {
      if (records.includes(required)) continue;
      let replaceIndex = records.length - 1;
      while (replaceIndex >= 0 && requiredKinds.includes(records[replaceIndex])) replaceIndex--;
      if (replaceIndex >= 0) records[replaceIndex] = required;
    }
    records.sort((left, right) => right.score - left.score);
    const non_search_results = records.filter((record) => !["skill", "recommendation"].includes(record.meta.kind)
      || (record.meta.kind === "skill" && !record.meta["skill-id"]));
    const skillIds = records.filter((record) => record.meta.kind === "skill").map((record) => record.meta["skill-id"]).filter(Boolean);
    const duplicateSkillIds = skillIds.filter((id, index) => skillIds.indexOf(id) !== index);
    const actual = [...new Set(skillIds)];
    const checkSkills = test.kind !== "recommendation";
    const missing = checkSkills ? test.expected_ids.filter((id) => !actual.includes(id)) : [];
    const allowed = test.allowed_ids ?? test.expected_ids;
    const unexpected = checkSkills ? actual.filter((id) => !allowed.includes(id)) : [];
    const urls = records.filter((record) => record.meta.kind === "recommendation")
      .map((record) => new URL(record.url, origin).pathname);
    const actualUrls = [...new Set(urls)];
    const checkUrls = test.kind === "recommendation" || "expected_urls" in test || "allowed_urls" in test;
    const expectedUrls = test.expected_urls ?? [];
    const allowedUrls = test.allowed_urls ?? expectedUrls;
    const missingUrls = checkUrls ? expectedUrls.filter((url) => !actualUrls.includes(url)) : [];
    const unexpectedUrls = checkUrls ? actualUrls.filter((url) => !allowedUrls.includes(url)) : [];
    const duplicateUrls = urls.filter((url, index) => urls.indexOf(url) !== index);
    if (missing.length || unexpected.length || missingUrls.length || unexpectedUrls.length || non_search_results.length || duplicateSkillIds.length || duplicateUrls.length) {
      failures.push({
        query: test.query,
        query_variants: variants,
        filters,
        expected: test.expected_ids,
        allowed,
        actual,
        unexpected,
        expected_urls: expectedUrls,
        allowed_urls: allowedUrls,
        actual_urls: actualUrls,
        unexpected_urls: unexpectedUrls,
        non_search_results: non_search_results.map((record) => record.url),
        duplicate_skill_ids: [...new Set(duplicateSkillIds)],
        duplicate_urls: [...new Set(duplicateUrls)],
        observed: records.slice(0, 5).map((record) => ({ kind: record.meta.kind, url: record.url, title: record.meta.title, skill_id: record.meta["skill-id"] ?? null, score: record.score })),
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
