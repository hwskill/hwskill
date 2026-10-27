(() => {
  let preference = "system";
  try {
    const saved = localStorage.getItem("hwskill-theme");
    if (saved === "light" || saved === "dark") preference = saved;
  } catch { /* Default to the system if storage is unavailable. */ }
  const root = document.documentElement;
  root.dataset.themePreference = preference;
  root.dataset.theme = preference === "system"
    ? (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light")
    : preference;
})();
