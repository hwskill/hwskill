import MarkdownIt from "markdown-it";

const markdown = new MarkdownIt({
  html: false,
  linkify: false,
  typographer: false,
});

function isSafeLink(value) {
  const link = value.trim();
  if (!link || /[\u0000-\u001f\u007f]/u.test(link) || link.startsWith("//")) return false;
  if (link.startsWith("#") || link.startsWith("/") || link.startsWith("./") || link.startsWith("../")) return true;

  try {
    const parsed = new URL(link, "https://hwskill.local/");
    const hasExplicitScheme = /^[a-z][a-z0-9+.-]*:/iu.test(link);
    if (!hasExplicitScheme) return parsed.origin === "https://hwskill.local";
    if (parsed.protocol === "mailto:") return true;
    return (parsed.protocol === "http:" || parsed.protocol === "https:") && !parsed.username && !parsed.password;
  } catch {
    return false;
  }
}

markdown.validateLink = isSafeLink;

const defaultLinkOpen = markdown.renderer.rules.link_open
  ?? ((tokens, index, options, _environment, renderer) => renderer.renderToken(tokens, index, options));

markdown.renderer.rules.link_open = (tokens, index, options, environment, renderer) => {
  const href = tokens[index].attrGet("href") ?? "";
  if (/^https?:/iu.test(href)) tokens[index].attrSet("rel", "noopener noreferrer");
  return defaultLinkOpen(tokens, index, options, environment, renderer);
};

function scrubRejectedDestinations(source) {
  return source.replace(
    /(\]\(\s*<?)(?:(?:javascript|data|vbscript):[^\s)>]*|https?:\/\/[^\s)>]*@[^\s)>]*)(>?)/giu,
    "$1#$2",
  );
}

export function renderMarkdown(source) {
  return markdown.render(scrubRejectedDestinations(source));
}
