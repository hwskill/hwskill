import { readFileSync, writeFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import { publicationDates } from "../src/lib/publication.mjs";

const root = resolve(import.meta.dirname, "../..");
if (execFileSync("git", ["rev-parse", "--is-shallow-repository"], { cwd: root, encoding: "utf8" }).trim() !== "false") {
  throw new Error("Publication snapshots require a checkout with full Git history.");
}
const path = resolve(import.meta.dirname, "../src/data/publication-dates.json");
const fallback = JSON.parse(readFileSync(path, "utf8"));
writeFileSync(path, `${JSON.stringify(publicationDates(root, fallback), null, 2)}\n`);
console.log("Updated first-publication dates from Git history; no file modification times used.");
