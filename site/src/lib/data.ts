import { readFileSync } from "node:fs";
import { resolve } from "node:path";

export type SkillSource =
  | { kind: "hosted"; path: string }
  | {
      kind: "external";
      publicity?: "public" | "private" | "unknown";
      locator:
        | { type: "git"; repository: string; path: string; ref?: string; file_url?: string }
        | { type: "web"; url: string; version_note?: string };
    };

export type CatalogItem = {
  entry: {
    schema_version: 2;
    id: string;
    name: string;
    summary: string;
    layer: "l1" | "l2" | "l3" | "l4" | "l5";
    purposes: string[];
    examples: Array<{ prompt: string; expected_outcome: string }>;
    source: SkillSource;
    install: { method: "directory" | "upstream" | "unknown"; default_scope: "project"; instructions_url?: string | null; included_skills?: string[] };
    compatibility: { agents: string[]; systems: string[]; requirements: string[] };
    license: { status: "known" | "unknown"; identifier?: string; url?: string };
    lifecycle: "active" | "deprecated" | "withdrawn";
    lifecycle_reason?: string | null;
    replacement_id?: string | null;
    owner?: string | null;
    keywords?: string[];
    limitations?: string[];
  };
  entry_digest: string;
  lifecycle: string;
  translation: {
    body: string;
    body_format: "markdown";
    translated_at: string;
    source_url: string;
  };
};

export type Recommendation = {
  id: string;
  skills: Array<{ id: string; experience_version?: string }>;
  title: string;
  summary?: string;
  body: string;
  body_format?: "markdown";
  author: string;
  topics?: string[];
  evidence?: Array<{ url: string; description?: string }>;
  status: "draft" | "ready" | "withdrawn";
  withdrawal_reason?: string;
};

const directory = resolve(process.env.HWSKILL_DIRECTORY ?? resolve(process.cwd(), ".generated/directory"));
const read = <T>(name: string): T => JSON.parse(readFileSync(resolve(directory, name), "utf8")) as T;

export const catalog = read<{ schema_version: 2; source_commit: string | null; entries: CatalogItem[] }>("catalog.json");
export const recommendationData = read<{ schema_version: 1; recommendations: Recommendation[] }>("recommendations.json");
export const curation = read<{ topics?: Array<{ slug: string; title: string }>; synonyms?: Record<string, string[]> }>("curation.json");
export const entries = catalog.entries;
export const recommendations = recommendationData.recommendations.filter((item) => item.status === "ready");

export function sourceRepository(source: SkillSource): string | null {
  if (source.kind === "hosted") return "https://github.com/hwskill/hwskill";
  if (source.locator.type === "git") return source.locator.repository.replace(/\.git$/, "");
  return source.locator.url;
}

export function topicAliases(slug: string): Set<string> {
  const topic = curation.topics?.find((item) => item.slug === slug);
  if (!topic) return new Set();
  const aliases = new Set([slug, topic.title].map((value) => value.toLowerCase()));
  let changed = true;
  while (changed) {
    changed = false;
    for (const [key, values] of Object.entries(curation.synonyms ?? {})) {
      const group = [key, ...values].map((value) => value.toLowerCase());
      if (!group.some((alias) => aliases.has(alias))) continue;
      for (const alias of group) {
        if (!aliases.has(alias)) changed = true;
        aliases.add(alias);
      }
    }
  }
  return aliases;
}

export function topicEntries(slug: string): CatalogItem[] {
  const aliases = topicAliases(slug);
  return entries.filter(({ entry }) => {
    const fields = [...entry.purposes, ...(entry.keywords ?? [])].map((value) => value.toLowerCase());
    return [...aliases].some((alias) => /[\u3400-\u9fff]/u.test(alias)
      ? fields.some((field) => field === alias)
      : fields.some((field) => field.includes(alias)));
  });
}

export function recommendationSummary(recommendation: Recommendation): string {
  return recommendation.summary ?? recommendation.body;
}
