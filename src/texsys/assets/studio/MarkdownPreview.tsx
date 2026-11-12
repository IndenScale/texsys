import React, { useId, useMemo, type ReactNode } from "react";
// Reuse Monaco's locked Marked lexer; render its tokens as React nodes, never HTML.
import { marked, type Token, type Tokens } from "marked";
// entities is already locked through markdownlint-cli2 and decodes lexer text safely.
import { decodeHTML } from "entities";
import "./markdown-preview.css";

type MarkdownLink = { href: string; kind: "external" | "anchor" | "file" };

/** Resolve document links without turning Markdown into a browser navigation surface. */
export function markdownLink(href: string, path = ""): MarkdownLink | undefined {
  const value = decodeHTML(href).trim();
  if (!value || /[\\\p{Cc}]/u.test(value) || value.startsWith("//")) return;
  if (value.startsWith("#")) return { href: value, kind: "anchor" };
  if (/^[a-z][a-z\d+.-]*:/i.test(value)) {
    if (!/^(https?:|mailto:)/i.test(value)) return;
    try {
      const url = new URL(value);
      return { href: url.href, kind: "external" };
    } catch {
      return;
    }
  }
  let relative: string;
  try {
    relative = decodeURIComponent(value.split(/[?#]/, 1)[0]);
  } catch {
    return;
  }
  if (!relative || /[\\:\p{Cc}]/u.test(relative)) return;
  const parts = relative.startsWith("/") ? [] : path.split("/").slice(0, -1);
  for (const part of relative.split("/")) {
    if (!part || part === ".") continue;
    if (part === "..") {
      if (!parts.length) return;
      parts.pop();
    } else parts.push(part);
  }
  return parts.length ? { href: parts.join("/"), kind: "file" } : undefined;
}

function headingSlug(text: string) {
  return decodeHTML(text)
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\s_-]/gu, "")
    .trim()
    .replace(/\s+/g, "-");
}

export function MarkdownPreview({
  content,
  path = "",
  onOpenFile,
  remoteBaseUrl,
}: {
  content: string;
  path?: string;
  onOpenFile?: (path: string) => void;
  remoteBaseUrl?: string;
}) {
  const tokens = useMemo(() => marked.lexer(content, { gfm: true }), [content]);
  const prefix = useId();
  const headings = new Map<string, number>();
  const resolveLink = (href: string): MarkdownLink | undefined => {
    const target = markdownLink(href, path);
    if (target?.kind !== "file" || !remoteBaseUrl) return target;
    try {
      return markdownLink(new URL(decodeHTML(href).trim(), remoteBaseUrl).href);
    } catch {
      return undefined;
    }
  };
  const link = (href: string, children: ReactNode, title?: string | null) => {
    const target = resolveLink(href);
    if (!target) return <>{children}</>;
    if (target.kind === "file")
      return onOpenFile ? (
        <a
          href="#"
          title={title ?? target.href}
          onClick={(event) => {
            event.preventDefault();
            onOpenFile(target.href);
          }}
        >
          {children}
        </a>
      ) : (
        <span title={target.href}>{children}</span>
      );
    if (target.kind === "anchor")
      return (
        <a href={"#" + prefix + "-" + target.href.slice(1)} title={title ?? undefined}>
          {children}
        </a>
      );
    return (
      <a href={target.href} title={title ?? undefined} target="_blank" rel="noreferrer noopener">
        {children}
      </a>
    );
  };
  const render = (items: Token[]): ReactNode =>
    items.map((token, index) => {
      const children = () => render("tokens" in token ? (token.tokens ?? []) : []);
      let node: ReactNode;
      switch (token.type) {
        case "space":
        case "def":
          return null;
        case "heading": {
          const heading = token as Tokens.Heading;
          const slug = headingSlug(heading.text);
          const occurrence = headings.get(slug) ?? 0;
          headings.set(slug, occurrence + 1);
          node = React.createElement(
            `h${heading.depth}`,
            {
              id: `${prefix}-${slug}${occurrence ? "-" + occurrence : ""}`,
            },
            children(),
          );
          break;
        }
        case "paragraph":
          node = <p>{children()}</p>;
          break;
        case "text":
          node = "tokens" in token && token.tokens ? children() : decodeHTML(token.text);
          break;
        case "escape":
          node = decodeHTML(token.text);
          break;
        case "html":
          // Raw HTML is text, including script tags and event attributes.
          node = remoteBaseUrl ? null : token.text;
          break;
        case "strong":
          node = <strong>{children()}</strong>;
          break;
        case "em":
          node = <em>{children()}</em>;
          break;
        case "del":
          node = <del>{children()}</del>;
          break;
        case "codespan":
          node = <code>{decodeHTML(token.text)}</code>;
          break;
        case "code":
          node = (
            <pre>
              <code>{token.text}</code>
            </pre>
          );
          break;
        case "blockquote":
          node = <blockquote>{children()}</blockquote>;
          break;
        case "br":
          node = <br />;
          break;
        case "hr":
          node = <hr />;
          break;
        case "link":
          node = link(token.href, children(), token.title);
          break;
        case "image": {
          const image = token as Tokens.Image;
          const target = resolveLink(image.href);
          node =
            target?.kind === "external" && /^https?:/i.test(target.href) ? (
              <img
                src={target.href}
                alt={decodeHTML(image.text)}
                title={image.title ?? undefined}
                loading="lazy"
                referrerPolicy="no-referrer"
              />
            ) : (
              link(image.href, decodeHTML(image.text || image.href), image.title)
            );
          break;
        }
        case "list": {
          const list = token as Tokens.List;
          const items = list.items.map((item, itemIndex) => (
            <li key={itemIndex}>
              {item.task && (
                <input type="checkbox" checked={!!item.checked} disabled aria-label={item.text} />
              )}
              {render(item.tokens)}
            </li>
          ));
          node = list.ordered ? <ol start={Number(list.start) || 1}>{items}</ol> : <ul>{items}</ul>;
          break;
        }
        case "table": {
          const table = token as Tokens.Table;
          node = (
            <table>
              <thead>
                <tr>
                  {table.header.map((cell, column) => (
                    <th key={column} style={{ textAlign: table.align[column] ?? undefined }}>
                      {render(cell.tokens)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {table.rows.map((row, rowIndex) => (
                  <tr key={rowIndex}>
                    {row.map((cell, column) => (
                      <td key={column} style={{ textAlign: table.align[column] ?? undefined }}>
                        {render(cell.tokens)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          );
          break;
        }
        default:
          node = token.raw;
      }
      return <React.Fragment key={index}>{node}</React.Fragment>;
    });
  return <article className="markdown-preview">{render(tokens)}</article>;
}
