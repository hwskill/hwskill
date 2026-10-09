import { sitePath } from "./urls";

export const contributionRepository = "https://github.com/hwskill/hwskill";

export type ContributionUrls = {
  repository: string;
  agentGuide: string;
  contributionRules: string;
  catalog: string;
  entrySchema: string;
  recommendationSchema: string;
  externalTemplate: string;
  hostedTemplate: string;
  recommendationTemplate: string;
};

export function buildContributionUrls(site: URL): ContributionUrls {
  const absolute = (path: string) => new URL(sitePath(path), site).href;
  return {
    repository: contributionRepository,
    agentGuide: absolute("/contribute/agent.md"),
    contributionRules: absolute("/contribute/CONTRIBUTING.md"),
    catalog: absolute("/data/catalog.json"),
    entrySchema: absolute("/schemas/entry.schema.json"),
    recommendationSchema: absolute("/schemas/recommendation-source.schema.json"),
    externalTemplate: absolute("/templates/entries/external.yaml"),
    hostedTemplate: absolute("/templates/entries/hosted.yaml"),
    recommendationTemplate: absolute("/templates/recommendations/recommendation.md"),
  };
}
