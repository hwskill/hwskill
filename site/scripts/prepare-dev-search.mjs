import { cpSync, existsSync, rmSync } from "node:fs";
import { resolve } from "node:path";

const siteRoot = resolve(import.meta.dirname, "..");
const source = resolve(siteRoot, "dist/pagefind");
const destination = resolve(siteRoot, "public/pagefind");

if (!existsSync(resolve(source, "pagefind.js"))) {
  throw new Error("Pagefind index is missing; run the site build and index first.");
}

rmSync(destination, { recursive: true, force: true });
cpSync(source, destination, { recursive: true });
