import TurndownService from "turndown";
import { gfm } from "turndown-plugin-gfm";

const DEFAULT_ORIGIN = "https://movement-engineering-20261111.hfu7565ytru.chatgpt.site";
const OMIT_TAGS = new Set([
  "NAV",
  "BUTTON",
  "SCRIPT",
  "STYLE",
  "SVG",
  "NOSCRIPT",
  "DIALOG",
  "FORM",
  "INPUT",
  "SELECT",
  "TEXTAREA",
]);
const CHROME_CLASSES = new Set([
  "site-header",
  "site-footer",
  "skip-link",
  "article-toc",
  "article-tools",
  "page-tools",
  "search-dialog",
  "search-results",
  "mobile-nav",
  "desktop-nav",
  "lexicon-toolbar",
  "related",
  "lab-console",
]);

function excluded(node) {
  return (
    OMIT_TAGS.has(node.nodeName.toUpperCase()) ||
    node.hasAttribute("data-md-exclude") ||
    node.hasAttribute("hidden") ||
    (node.getAttribute("class") || "").split(/\s+/).some((name) => CHROME_CLASSES.has(name)) ||
    ["navigation", "search", "banner", "contentinfo"].includes(node.getAttribute("role"))
  );
}

function codeText(node) {
  if (node.nodeType === 3) return node.data;
  if (node.nodeType !== 1 || excluded(node)) return "";
  return [...node.childNodes].map(codeText).join("");
}

/** Map a canonical page URL to its downloadable Markdown path. */
export function markdownPath(url) {
  const pathname = new URL(url, DEFAULT_ORIGIN).pathname;
  if (pathname === "/" || pathname === "/index.html") return "/index.md";
  if (pathname.endsWith(".md")) return pathname;
  return pathname.replace(/\/$/, "").replace(/\.html?$/, "") + ".md";
}

function absoluteURL(value, canonical) {
  try {
    const url = new URL(value, canonical);
    return ["http:", "https:", "mailto:", "tel:"].includes(url.protocol) ? url.href : null;
  } catch {
    return null;
  }
}

function destination(url) {
  // Spaces and brackets otherwise change the boundaries of a Markdown link.
  return url.replace(/[()<>\s]/g, (character) =>
    encodeURIComponent(character).replaceAll("(", "%28").replaceAll(")", "%29"),
  );
}

function converter(canonical) {
  const service = new TurndownService({
    headingStyle: "atx",
    bulletListMarker: "-",
    codeBlockStyle: "fenced",
    emDelimiter: "*",
    preformattedCode: true,
  });
  service.use(gfm);
  service.addRule("singleLineHeadings", {
    filter: ["h1", "h2", "h3", "h4", "h5", "h6"],
    replacement(content, node) {
      const heading = content.trim().replace(/\s*\n\s*/g, " ");
      return `\n\n${"#".repeat(Number(node.nodeName[1]))} ${heading}\n\n`;
    },
  });
  service.addRule("absoluteLinks", {
    filter: (node) => node.nodeName === "A" && node.hasAttribute("href"),
    replacement(content, node) {
      const href = absoluteURL(node.getAttribute("href"), canonical);
      if (!href) return content;
      const title = node.getAttribute("title");
      const suffix = title
        ? ` "${title.replaceAll("\\", "\\\\").replaceAll('"', '\\"').replace(/\s+/g, " ")}"`
        : "";
      const target = `${destination(href)}${suffix}`;
      if (
        node.querySelector("h1,h2,h3,h4,h5,h6,p,div,section,article,ul,ol,table,pre,blockquote")
      ) {
        // An HTML card may wrap several blocks in one anchor. CommonMark link
        // labels cannot span those blocks, so link its heading and keep its prose.
        const blocks = content.trim();
        const heading = /(^|\n)(#{1,6}) ([^\n]+)(?=\n|$)/;
        if (heading.test(blocks)) {
          return `\n\n${blocks.replace(heading, (_match, before, level, text) => `${before}${level} [${text}](${target})`)}\n\n`;
        }
        const label = service.escape(node.getAttribute("aria-label") || title || "查看详情");
        return `\n\n${blocks}\n\n[${label}](${target})\n\n`;
      }
      return `[${content || service.escape(node.textContent || href)}](${target})`;
    },
  });
  service.addRule("absoluteImages", {
    filter: "img",
    replacement(_content, node) {
      const src = absoluteURL(node.getAttribute("src"), canonical);
      if (!src) return "";
      return `![${service.escape(node.getAttribute("alt") || "")}](${destination(src)})`;
    },
  });
  service.addRule("codeWithLanguage", {
    filter: "pre",
    replacement(_content, node) {
      const code = node.querySelector("code") || node;
      const classes = `${code.getAttribute("class") || ""} ${node.getAttribute("class") || ""}`;
      const language =
        (classes.match(/(?:^|\s)(?:language|lang)-([\w+#.-]+)/) || [])[1] ||
        (node.getAttribute("data-language") || "").replace(/[^\w+#.-]/g, "");
      const source = codeText(code).replace(/\r\n?/g, "\n").replace(/\n$/, "");
      const fence = "`".repeat(
        Math.max(3, ...[...source.matchAll(/`+/g)].map(([run]) => run.length + 1)),
      );
      return `\n\n${fence}${language}\n${source}\n${fence}\n\n`;
    },
  });
  service.addRule("readableDetails", {
    filter: "details",
    replacement: (content) => `\n\n${content.trim()}\n\n`,
  });
  service.addRule("detailsSummary", {
    filter: "summary",
    replacement: (content) => `\n\n**${content.trim()}**\n\n`,
  });
  service.addRule("tableCells", {
    filter: ["th", "td"],
    replacement(content, node) {
      const prefix = node === node.parentNode.firstElementChild ? "| " : " ";
      // GFM tables cannot contain physical newlines or unescaped column bars.
      const cell = content
        .trim()
        .replace(/(?<!\\)\|/g, "\\|")
        .replace(/\s*\n\s*/g, "<br>");
      return `${prefix}${cell} |`;
    },
  });
  service.addRule("completeTables", {
    filter: "table",
    replacement(content, node) {
      const first = node.rows[0];
      if (!first) return "";
      const hasHeader =
        first.parentNode.nodeName === "THEAD" ||
        [...first.cells].every((cell) => cell.nodeName === "TH");
      const emptyHeader = hasHeader
        ? ""
        : `|${"  |".repeat(first.cells.length)}\n|${" --- |".repeat(first.cells.length)}\n`;
      return `\n\n${emptyHeader}${content.trim().replace(/\n{2,}/g, "\n")}\n\n`;
    },
  });
  // Added last so exclusion also wins over link, table and other standard rules.
  service.addRule("excludeInterface", {
    filter: excluded,
    replacement: () => "",
  });
  return service;
}

/**
 * Export the complete editorial page. HTML is converted on the build server;
 * authored Markdown is preserved verbatim, including its own link semantics.
 */
export function pageMarkdown(page, { origin = DEFAULT_ORIGIN, body, sourceMarkdown } = {}) {
  const canonicalURL = new URL(page.path || "/", origin);
  canonicalURL.search = "";
  canonicalURL.hash = "";
  const canonical = canonicalURL.href;
  const service = converter(canonical);
  const title = `# ${service.escape(page.title || "Movement")}`;
  const metadata = [page.english, page.summary]
    .filter(Boolean)
    .map((value) => service.escape(value));
  metadata.push(`原文：[${canonical}](${destination(canonical)})`);
  const related = (page.related || []).flatMap((item) => {
    const href = absoluteURL(item.href, canonical);
    return href ? [`- [${service.escape(item.label || item.href)}](${destination(href)})`] : [];
  });
  const footer = related.length ? `\n\n## 相关阅读\n\n${related.join("\n")}` : "";

  if (sourceMarkdown !== undefined) {
    if (typeof sourceMarkdown !== "string") throw new TypeError("sourceMarkdown must be a string");
    // A supplied complete article owns its H1 and is never parsed or rewritten.
    const hasTitle = /^\uFEFF?\s*#\s+\S/.test(sourceMarkdown);
    const source = hasTitle ? sourceMarkdown : `${title}\n\n${sourceMarkdown}`;
    return `${source}\n\n---\n\n${metadata.join("\n\n")}${footer}\n`;
  }

  const content = service.turndown(body ?? page.body ?? "");
  return `${[title, ...metadata, content].filter(Boolean).join("\n\n")}${footer}\n`;
}
