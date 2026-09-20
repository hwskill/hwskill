import assert from "node:assert/strict";
import test from "node:test";
import { resolveRecommendationSkills } from "../src/lib/recommendation-links.mjs";

const entries = [{ entry: { id: "local/present", name: "Present" } }];

test("withdrawn recommendations preserve missing historical skill IDs", () => {
  assert.deepEqual(resolveRecommendationSkills({ id: "old", status: "withdrawn", skills: [{ id: "local/missing" }] }, entries), []);
});

test("ready recommendations fail when a linked skill is missing", () => {
  assert.throws(
    () => resolveRecommendationSkills({ id: "ready", status: "ready", skills: [{ id: "local/missing" }] }, entries),
    /references missing skills: local\/missing/,
  );
});
