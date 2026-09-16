import { defineConfig } from "astro/config";

export default defineConfig({
  site: "https://hwskill.github.io",
  base: process.env.SITE_BASE ?? "/",
  output: "static",
  trailingSlash: "always",
  build: { format: "directory" },
});
