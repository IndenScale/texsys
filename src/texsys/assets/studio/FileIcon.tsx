import React from "react";
import { Icon } from "./ui";

/** Compact file-family cues; the filename remains the accessible identity. */
export function FileIcon({ path, size = 16 }: { path: string; size?: number }) {
  const name = path.split("/").pop()?.toLowerCase() ?? "";
  const extension = name.split(".").pop();
  const family = ["tsx", "jsx"].includes(extension ?? "")
    ? "react"
    : extension === "ts"
      ? "typescript"
      : ["js", "mjs", "cjs"].includes(extension ?? "")
        ? "javascript"
        : name === ".gitignore"
          ? "git"
          : name.endsWith(".lock")
            ? "lock"
            : extension === "md"
              ? "markdown"
              : extension === "py"
                ? "python"
                : ["json", "json5"].includes(extension ?? "")
                  ? "json"
                  : ["toml", "yaml", "yml", "ini"].includes(extension ?? "")
                    ? "config"
                    : ["png", "jpg", "jpeg", "svg", "webp"].includes(extension ?? "")
                      ? "image"
                      : "file";
  const kind =
    name === "package.json" || name === "package-lock.json"
      ? "package"
      : extension === "css"
        ? "css"
        : family;
  return (
    <span
      className={`core-file-icon file-${kind}`}
      style={{ width: size, height: size }}
      aria-hidden="true"
    >
      {kind === "typescript" || kind === "javascript" ? (
        <span>{kind === "typescript" ? "TS" : "JS"}</span>
      ) : kind === "react" ? (
        <svg
          width={size}
          height={size}
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.3"
        >
          <ellipse cx="12" cy="12" rx="11" ry="4" />
          <ellipse cx="12" cy="12" rx="11" ry="4" transform="rotate(60 12 12)" />
          <ellipse cx="12" cy="12" rx="11" ry="4" transform="rotate(120 12 12)" />
          <circle cx="12" cy="12" r="1.7" fill="currentColor" />
        </svg>
      ) : kind === "package" ? (
        <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true">
          <path
            d="M12 1 22 7v10l-10 6-10-6V7Z"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
          />
          <text
            x="12"
            y="16"
            textAnchor="middle"
            fontSize="10"
            fontWeight="500"
            fill="currentColor"
          >
            JS
          </text>
        </svg>
      ) : kind === "git" ? (
        <Icon name="branch" size={size} />
      ) : kind === "markdown" ? (
        <span>M↓</span>
      ) : kind === "python" ? (
        <span>Py</span>
      ) : (
        <Icon
          name={
            kind === "json" || kind === "css"
              ? "braces"
              : kind === "config"
                ? "file-config"
                : kind === "lock"
                  ? "lock"
                  : kind === "image"
                    ? "image"
                    : "file"
          }
          size={size}
        />
      )}
    </span>
  );
}
