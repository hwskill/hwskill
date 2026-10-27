import { execFileSync } from "node:child_process";

export function parsePublicationHistory(history) {
  const dates = { skills: {}, recommendations: {} };
  for (const commit of history.split("\x1e")) {
    const [date, ...paths] = commit.trim().split(/\r?\n/).filter(Boolean);
    if (!Number.isFinite(Date.parse(date))) continue;
    for (const path of paths) {
      const skill = path.match(/^entries\/l[1-5]\/([a-z0-9_-]+\/[a-z0-9_-]+)\.ya?ml$/);
      const recommendation = path.match(/^recommendations\/([a-z0-9_-]+)\.(?:md|ya?ml)$/);
      const group = skill ? dates.skills : recommendation ? dates.recommendations : null;
      const id = skill?.[1] ?? recommendation?.[1];
      if (group && (!group[id] || Date.parse(date) < Date.parse(group[id]))) group[id] = date;
    }
  }
  return dates;
}

export function publicationDates(repoRoot, fallback = { skills: {}, recommendations: {} }) {
  try {
    const options = { cwd: repoRoot, encoding: "utf8", stdio: ["ignore", "pipe", "ignore"], maxBuffer: 8 * 1024 * 1024 };
    if (execFileSync("git", ["rev-parse", "--is-shallow-repository"], options).trim() !== "false") return fallback;
    const history = execFileSync("git", ["-c", "core.quotepath=false", "log", "--reverse", "--diff-filter=A", "--format=%x1e%cI", "--name-only", "--", "entries/", "recommendations/"], options);
    const dates = parsePublicationHistory(history);
    return { skills: { ...fallback.skills, ...dates.skills }, recommendations: { ...fallback.recommendations, ...dates.recommendations } };
  } catch {
    // Source archives and shallow checkouts use the recorded history snapshot.
    return fallback;
  }
}

export { comparePublication, newestFirst, publicationLabel } from "./publication-order.mjs";
