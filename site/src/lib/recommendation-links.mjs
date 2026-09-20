export function resolveRecommendationSkills(recommendation, entries) {
  if (recommendation.status !== "ready") return [];
  const byId = new Map(entries.map((item) => [item.entry.id, item]));
  const missing = recommendation.skills.map((skill) => skill.id).filter((id) => !byId.has(id));
  if (missing.length) {
    throw new Error(`Recommendation ${recommendation.id} references missing skills: ${missing.join(", ")}`);
  }
  return recommendation.skills.map((skill) => byId.get(skill.id));
}
