import { defineConfig } from "astro/config";
import react from "@astrojs/react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  site: "https://hwskill.github.io",
  base: process.env.SITE_BASE ?? "/",
  output: "static",
  integrations: [react()],
  vite: { plugins: [tailwindcss()] },
  trailingSlash: "always",
  build: { format: "directory" },
});
