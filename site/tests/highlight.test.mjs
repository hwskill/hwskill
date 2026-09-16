import assert from "node:assert/strict";
import test from "node:test";
import { highlightSegments } from "../src/lib/highlight.mjs";

test("highlights visible English and Chinese search terms without changing their text", () => {
  assert.deepEqual(highlightSegments("GitCode PR 检视", ["PR", "检视"]), [
    { text: "GitCode ", match: false },
    { text: "PR", match: true },
    { text: " ", match: false },
    { text: "检视", match: true },
  ]);
});

test("handles punctuation literally and leaves filter-only cards unchanged", () => {
  assert.deepEqual(highlightSegments("C++ 性能", ["c++"]), [
    { text: "C++", match: true },
    { text: " 性能", match: false },
  ]);
  assert.deepEqual(highlightSegments("C++ 性能", []), [{ text: "C++ 性能", match: false }]);
});
