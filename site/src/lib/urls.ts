const base = import.meta.env.BASE_URL.replace(/\/$/, "");

export function sitePath(path: string): string {
  return `${base}/${path.replace(/^\//, "")}`;
}
