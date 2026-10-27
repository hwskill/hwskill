export const THEME_STORAGE_KEY = "hwskill-theme";

export function normalizeThemePreference(value) {
  return value === "light" || value === "dark" ? value : "system";
}

export function resolveTheme(preference, systemIsDark) {
  const normalized = normalizeThemePreference(preference);
  return normalized === "system" ? (systemIsDark ? "dark" : "light") : normalized;
}

export function readThemePreference(storage) {
  try { return normalizeThemePreference(storage?.getItem(THEME_STORAGE_KEY)); }
  catch { return "system"; }
}

export function saveThemePreference(storage, preference) {
  const normalized = normalizeThemePreference(preference);
  try {
    if (normalized === "system") storage?.removeItem(THEME_STORAGE_KEY);
    else storage?.setItem(THEME_STORAGE_KEY, normalized);
  } catch { /* Theme selection still works for this document without storage. */ }
  return normalized;
}
