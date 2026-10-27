import test from "node:test";
import assert from "node:assert/strict";

const load = () => import("../src/lib/theme-preference.mjs");

test("system is the default and explicit theme overrides either OS preference", async () => {
  const { normalizeThemePreference, resolveTheme } = await load();
  for (const input of [null, undefined, "", "unknown"]) assert.equal(normalizeThemePreference(input), "system");
  assert.equal(resolveTheme("system", true), "dark");
  assert.equal(resolveTheme("system", false), "light");
  assert.equal(resolveTheme("light", true), "light");
  assert.equal(resolveTheme("dark", false), "dark");
});

test("storage errors preserve the current selection without crashing", async () => {
  const { readThemePreference, saveThemePreference } = await load();
  const blocked = { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); }, removeItem() { throw new Error("blocked"); } };
  assert.equal(readThemePreference(blocked), "system");
  assert.equal(saveThemePreference(blocked, "dark"), "dark");
  assert.equal(saveThemePreference(blocked, "system"), "system");
  const map = new Map();
  const storage = { getItem: key => map.get(key), setItem: (key, value) => map.set(key, value), removeItem: key => map.delete(key) };
  saveThemePreference(storage, "dark"); assert.equal(readThemePreference(storage), "dark");
  saveThemePreference(storage, "system"); assert.equal(readThemePreference(storage), "system");
  assert.equal(map.size, 0, "system resets the stored override");
});
