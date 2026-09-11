import { readFileSync } from "node:fs";
import { resolve } from "node:path";

export type StageResult = "pass" | "fail" | "blocked" | "not_run" | "unknown";
export type CatalogItem = {
  entry: {
    schema_version: 1;
    id: string;
    name: string;
    summary: string;
    layer: "l1" | "l2" | "l3" | "l4" | "l5";
    purposes: string[];
    examples: Array<{ prompt: string; expected_outcome: string }>;
    source: { kind: "hosted" | "external"; identity: string };
    install: { method: "directory" | "upstream" | "unknown"; default_scope: "project"; instructions_url?: string | null };
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
  source_identity: { kind: string; identity: string; resolved_revision?: string | null; content_digest?: string | null };
  lifecycle: string;
  install_capability: string;
  verification_summary: Record<string, { result: StageResult; report_id?: string | null; executed_at?: string | null }>;
};

export type Recommendation = {
  id: string;
  skills: Array<{ id: string; experienced_version?: string }>;
  title: string;
  body: string;
  author: string;
  topics?: string[];
  evidence?: Array<{ url: string; description?: string }>;
  status: "draft" | "ready" | "withdrawn";
  withdrawal_reason?: string;
};

const directory = resolve(process.env.HWSKILL_DIRECTORY ?? resolve(process.cwd(), ".generated/directory"));
const read = <T>(name: string): T => JSON.parse(readFileSync(resolve(directory, name), "utf8")) as T;

export const catalog = read<{ schema_version: 1; source_commit: string | null; entries: CatalogItem[] }>("catalog.json");
export const recommendationData = read<{ schema_version: 1; recommendations: Recommendation[] }>("recommendations.json");
export const curation = read<{ topics?: Array<{ slug: string; title: string }>; synonyms?: Record<string, string[]> }>("curation.json");
export const entries = catalog.entries;
export const recommendations = recommendationData.recommendations.filter((item) => item.status === "ready");

export function sourceRepository(identity: string): string | null {
  if (identity.startsWith("git:")) return identity.slice(4).split("\0", 1)[0].replace(/\.git$/, "");
  if (identity.startsWith("web:")) return identity.slice(4);
  return null;
}

export function verificationLabel(item: CatalogItem): string {
  const installation = item.verification_summary.installation?.result ?? "not_run";
  const behavior = item.verification_summary.behavior?.result ?? "not_run";
  return installation === "pass" && behavior === "pass" ? "已验证" : "未完成安装与行为验证";
}

export function verificationState(item: CatalogItem): StageResult {
  const results = [item.verification_summary.installation?.result, item.verification_summary.behavior?.result];
  if (results.every((result) => result === "pass")) return "pass";
  for (const state of ["fail", "blocked", "unknown"] as const) if (results.includes(state)) return state;
  return "not_run";
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
    const terms = [...entry.purposes, ...(entry.keywords ?? [])].join(" ").toLowerCase();
    return [...aliases].some((alias) => terms.includes(alias));
  });
}
