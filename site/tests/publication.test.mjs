import test from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const load = () => import("../src/lib/publication.mjs");

test("publication follows first catalog arrival across layer moves and markdown migration", async () => {
  const { parsePublicationHistory } = await load();
  const dates = parsePublicationHistory("\x1e2026-09-11T09:00:00+08:00\nentries/l1/team/tool.yaml\nrecommendations/workflow.yaml\n\x1e2026-09-20T09:00:00+08:00\nentries/l2/team/tool.yaml\nrecommendations/workflow.md\nentries/l2/team/new.yaml\n");
  assert.equal(dates.skills["team/tool"], "2026-09-11T09:00:00+08:00");
  assert.equal(dates.recommendations.workflow, "2026-09-11T09:00:00+08:00");
  assert.equal(dates.skills["team/new"], "2026-09-20T09:00:00+08:00");
});

test("publication sorting uses instants, puts missing/invalid dates last and breaks ties by ID", async () => {
  const { newestFirst } = await load();
  const items = [
    { id: "missing" }, { id: "older", publishedAt: "2026-10-01T09:00:00+08:00" },
    { id: "b", publishedAt: "2026-10-01T03:00:00Z" }, { id: "a", publishedAt: "2026-10-01T11:00:00+08:00" },
    { id: "invalid", publishedAt: "not-a-date" },
  ];
  assert.deepEqual(newestFirst(items).map(x => x.id), ["a", "b", "older", "invalid", "missing"]);
  assert.equal(items[0].id, "missing", "sorting does not mutate normalized data");
});

test("unknown paths and invalid commit times cannot invent publication dates", async () => {
  const { parsePublicationHistory } = await load();
  assert.deepEqual(parsePublicationHistory("\x1enot-a-date\nentries/l1/team/tool.yaml\n\x1e2026-10-01T00:00:00Z\ntranslations/team/tool.md\nrecommendations/README.md\n"), { skills: {}, recommendations: {} });
});

test("real Git history ignores later edits and archives retain the recorded fallback", async () => {
  const { publicationDates } = await load();
  const repo = mkdtempSync(join(tmpdir(), "hwskill-publication-"));
  const git = (args, date) => execFileSync("git", args, { cwd: repo, stdio: "pipe", env: { ...process.env, GIT_AUTHOR_DATE: date, GIT_COMMITTER_DATE: date } });
  try {
    git(["init", "-q"]);
    mkdirSync(join(repo, "entries/l1/team"), { recursive: true });
    writeFileSync(join(repo, "entries/l1/team/tool.yaml"), "id: team/tool\n");
    git(["add", "."]);
    const commit = ["-c", "user.name=Publication test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false", "commit", "-qm"];
    git([...commit, "first"], "2026-09-01T00:00:00Z");
    writeFileSync(join(repo, "entries/l1/team/tool.yaml"), "id: team/tool\nname: Updated\n");
    git(["add", "."]); git([...commit, "edit"], "2026-10-01T00:00:00Z");
    const recorded = publicationDates(repo);
    assert.equal(Date.parse(recorded.skills["team/tool"]), Date.parse("2026-09-01T00:00:00Z"));
    writeFileSync(join(repo, ".git/shallow"), git(["rev-parse", "HEAD"]).toString().trim() + "\n");
    assert.deepEqual(publicationDates(repo, recorded), recorded, "shallow root commits cannot redate existing content");
    rmSync(join(repo, ".git"), { recursive: true, force: true });
    assert.deepEqual(publicationDates(repo, recorded), recorded);
  } finally { rmSync(repo, { recursive: true, force: true }); }
});
