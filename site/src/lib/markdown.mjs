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

const tableOpen = markdown.renderer.rules.table_open
  ?? ((tokens, index, options, _environment, renderer) => renderer.renderToken(tokens, index, options));
const tableClose = markdown.renderer.rules.table_close
  ?? ((tokens, index, options, _environment, renderer) => renderer.renderToken(tokens, index, options));

markdown.renderer.rules.table_open = (...args) => `<div class="typeset-scroll">\n${tableOpen(...args)}`;
markdown.renderer.rules.table_close = (...args) => `${tableClose(...args)}</div>\n`;

function scrubRejectedDestinations(source) {
  return source.replace(
    /(\]\(\s*<?)(?:(?:javascript|data|vbscript):[^\s)>]*|https?:\/\/[^\s)>]*@[^\s)>]*)(>?)/giu,
    "$1#$2",
  );
}

export function renderMarkdown(source, { documentTitle } = {}) {
  const env = {};
  const tokens = markdown.parse(scrubRejectedDestinations(source), env);
  if (documentTitle) {
    const title = documentTitle.trim().toLowerCase();
    const firstContent = tokens.findIndex((token) => token.type !== "fence" || token.info.trim() !== "yaml");
    const heading = tokens.findIndex((token, index) =>
      token.type === "heading_open" && token.tag === "h1" && token.level === 0
      && (index === firstContent || tokens[index + 1].content.trim().toLowerCase() === title)
    );
    if (heading !== -1) tokens.splice(heading, 3);
  }
  return markdown.renderer.render(tokens, markdown.options, env);
}
