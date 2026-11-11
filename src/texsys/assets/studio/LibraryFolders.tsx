import React, { useState } from "react";
import type { DesignIndex, LibrarySource } from "./DesignNavigation";
import { t } from "./i18n";

const storageKey = "texsys.studio.library-folders";

/** Resolve the requested directory from the server's validated, canonical source mapping. */
export function requestedLibrarySource(
  index: Pick<DesignIndex, "sources" | "requested_sources">,
  folder: string,
): LibrarySource | undefined {
  const matches = index.requested_sources?.filter((item) => item.folder === folder) ?? [];
  if (matches.length !== 1) return undefined;
  return index.sources.find((source) => source.id === matches[0].source_id);
}

export function readLibraryFolders(): string[] {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(storageKey) ?? "[]");
    return Array.isArray(value)
      ? value.filter((path): path is string => typeof path === "string").slice(0, 16)
      : [];
  } catch {
    return [];
  }
}

export function saveLibraryFolders(paths: string[]) {
  try {
    localStorage.setItem(storageKey, JSON.stringify(paths));
  } catch {
    // The list remains available in this session.
  }
}

export function LibraryFolders({
  folders,
  sources,
  add,
  remove,
  close,
}: {
  folders: string[];
  sources: LibrarySource[];
  add: (path: string) => Promise<void>;
  remove: (path: string) => void;
  close: () => void;
}) {
  const [path, setPath] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <div className="core-dialog-backdrop">
      <form
        className="core-dialog"
        role="dialog"
        aria-modal="true"
        aria-label={t("添加库目录…")}
        onKeyDown={(event) => {
          if (event.key === "Escape" && !busy) close();
        }}
        onSubmit={async (event) => {
          event.preventDefault();
          if (!path.trim() || busy) return;
          setBusy(true);
          setError("");
          try {
            await add(path.trim());
            close();
          } catch (failure) {
            setError(String(failure));
          } finally {
            setBusy(false);
          }
        }}
      >
        <strong>{t("添加库目录…")}</strong>
        <label>
          {t("库目录路径")}
          <input
            autoFocus
            required
            value={path}
            onChange={(event) => setPath(event.target.value)}
          />
        </label>
        <p>{t("输入包含 texsys.toml 的库目录。目录在此浏览器中保存。")}</p>
        {folders.length > 0 && (
          <ul className="core-library-folders">
            {folders.map((folder) => {
              const source = sources.find((item) => item.path === folder);
              return (
                <li key={folder}>
                  <span title={folder}>
                    {source ? source.name + " · " + source.version : folder}
                  </span>
                  <button type="button" disabled={busy} onClick={() => remove(folder)}>
                    {t("移除库目录")}
                  </button>
                </li>
              );
            })}
          </ul>
        )}
        {error && (
          <p className="core-error" role="alert">
            {error}
          </p>
        )}
        <div className="core-dialog-actions">
          <button type="button" disabled={busy} onClick={close}>
            {t("取消")}
          </button>
          <button type="submit" disabled={busy || !path.trim()}>
            {t("添加")}
          </button>
        </div>
      </form>
    </div>
  );
}
