export function comparePublication(left, right) {
  const time = item => Number.isFinite(Date.parse(item.publishedAt)) ? Date.parse(item.publishedAt) : -Infinity;
  const delta = time(right) - time(left);
  if (Number.isFinite(delta) && delta !== 0) return delta;
  if (time(left) !== time(right)) return time(right) > time(left) ? 1 : -1;
  return (left.id ?? left.entry?.id ?? "").localeCompare(right.id ?? right.entry?.id ?? "", "en");
}

export function newestFirst(items) {
  return [...items].sort(comparePublication);
}

export function publicationLabel(date) {
  if (!date || !Number.isFinite(Date.parse(date))) return "";
  return new Intl.DateTimeFormat("sv-SE", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date(date));
}
