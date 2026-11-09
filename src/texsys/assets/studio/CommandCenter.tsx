import { t } from "./i18n";
import React, { useEffect, useLayoutEffect, useRef, useState, type RefObject } from "react";
import { filterCommands, type CommandItem } from "./menus";
import { filterFiles, parseFileQuery, type FileLocation, type SearchScope } from "./search";
import type { ProjectFile } from "./sdk";
import { InlineTabStrip } from "./atomstudio/WorkspaceTabs";
import { Keycaps } from "./Keycaps";
import { Icon } from "./ui";

type Result = {
  id: string;
  label: string;
  group: string;
  icon: string;
  disabled?: boolean;
  checked?: boolean;
  keys?: string[];
  file?: ProjectFile;
};
export function CommandCenter({
  project,
  showTrigger,
  disabled,
  open,
  focusRequest,
  returnFocus,
  commands,
  onOpen,
  onClose,
  invoke,
  shortcut,
  scope,
  onScopeChange,
  files,
  recentPaths,
  filesLoading,
  filesError,
  filesTruncated,
  onFile,
}: {
  project: string;
  showTrigger: boolean;
  disabled: boolean;
  open: boolean;
  focusRequest: number;
  returnFocus: RefObject<HTMLElement | null>;
  commands: CommandItem[];
  onOpen: () => void;
  onClose: () => void;
  invoke: (id: string) => void;
  shortcut: string[];
  scope: SearchScope;
  onScopeChange: (scope: SearchScope) => void;
  files: ProjectFile[];
  recentPaths: string[];
  filesLoading: boolean;
  filesError: string;
  filesTruncated: boolean;
  onFile: (file: ProjectFile, location?: FileLocation) => void;
}) {
  const root = useRef<HTMLDivElement>(null),
    button = useRef<HTMLButtonElement>(null),
    title = useRef<HTMLSpanElement>(null),
    input = useRef<HTMLInputElement>(null),
    list = useRef<HTMLDivElement>(null);
  const wasOpen = useRef(false),
    restore = useRef(false);
  const [query, setQuery] = useState(""),
    [selected, setSelected] = useState(0);
  const resultScope = query.trimStart().startsWith(">") ? "commands" : scope;
  const fileIndex =
    resultScope === "recent" ? new Map(files.map((file) => [file.path, file])) : undefined;
  const visibleFiles = fileIndex
    ? recentPaths.flatMap((path) => {
        const file = fileIndex.get(path);
        return file ? [file] : [];
      })
    : files;
  const fileQuery = resultScope !== "commands" ? parseFileQuery(visibleFiles, query) : undefined;
  const location = fileQuery?.location;
  const matches: Result[] = [
    ...(resultScope === "all" || resultScope === "commands"
      ? filterCommands(commands, query).map((command) => ({
          ...command,
          icon: command.icon ?? "arrow",
        }))
      : []),
    ...(fileQuery
      ? filterFiles(visibleFiles, fileQuery.text)
          .slice(0, 100)
          .map((file) => ({
            id: `file:${file.path}`,
            label: file.name,
            group: t("文件"),
            icon: "file",
            file,
          }))
      : []),
  ];
  const enabled = matches
    .map((item, index) => (item.disabled ? -1 : index))
    .filter((index) => index >= 0);
  const index = matches[selected] && !matches[selected].disabled ? selected : (enabled[0] ?? -1);
  const close = (focus: boolean) => {
    restore.current = focus;
    onClose();
  };
  useLayoutEffect(() => {
    const label = title.current;
    if (!label) return;
    const measure = () =>
      root.current?.style.setProperty(
        "--command-title-width",
        `${Math.ceil(label.getBoundingClientRect().width + 28)}px`,
      );
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(label);
    return () => observer.disconnect();
  }, [project]);
  useLayoutEffect(() => {
    if (open) {
      if (scope === "recent") {
        setQuery("");
        setSelected(0);
      } else if (scope !== "commands") setQuery((current) => current.replace(/^\s*>\s*/, ""));
      input.current?.focus();
      input.current?.select();
    }
  }, [open, focusRequest]);
  useLayoutEffect(() => {
    if (!open && wasOpen.current) {
      if (restore.current) {
        (returnFocus.current?.isConnected ? returnFocus.current : button.current)?.focus();
      }
      restore.current = false;
    }
    wasOpen.current = open;
  }, [open]);
  useEffect(() => {
    if (open) return;
    const timer = setTimeout(() => {
      setQuery("");
      setSelected(0);
    }, 220);
    return () => clearTimeout(timer);
  }, [open]);
  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) close(false);
    };
    document.addEventListener("pointerdown", outside, true);
    return () => document.removeEventListener("pointerdown", outside, true);
  }, [open, onClose]);
  useEffect(() => {
    if (!open) return;
    list.current
      ?.querySelector(`[data-result-index="${index}"]`)
      ?.scrollIntoView({ block: "nearest" });
  }, [index, query, open, resultScope]);
  const activate = (position: number) => {
    const result = matches[position];
    if (result && !result.disabled) {
      close(true);
      if (result.file) onFile(result.file, location);
      else invoke(result.id);
    }
  };
  const hasControls = showTrigger || open;
  return (
    <div
      ref={root}
      className={`command-center${open ? " is-open" : ""}`}
      role={hasControls ? "group" : undefined}
      aria-label={hasControls ? t("命令中心") : undefined}
      onBlur={(event) => {
        if (open && event.relatedTarget && !root.current?.contains(event.relatedTarget as Node))
          close(false);
      }}
    >
      <button
        ref={button}
        hidden={!showTrigger}
        inert={open}
        aria-hidden={open}
        className="command-center-button"
        aria-label={t("打开搜索")}
        aria-haspopup="listbox"
        aria-controls="studio-command-list"
        aria-expanded={open}
        disabled={disabled}
        onClick={onOpen}
      >
        <span className="command-center-affordance" aria-hidden="true">
          <Icon name="search" size={16} />
          <span>{t("搜索文件或运行命令…")}</span>
          <Keycaps keys={shortcut} />
        </span>
      </button>
      <span ref={title} className="command-center-project" aria-hidden={open}>
        {project}
      </span>
      <>
        <div className="command-center-input" inert={!open} aria-hidden={!open}>
          <Icon name="search" size={16} />
          <input
            ref={input}
            role="combobox"
            aria-label={t("搜索文件或命令")}
            aria-expanded={open}
            aria-autocomplete="list"
            aria-describedby="command-center-instructions"
            aria-controls="studio-command-list"
            aria-activedescendant={index >= 0 ? `studio-result-${index}` : undefined}
            autoComplete="off"
            spellCheck={false}
            placeholder={
              scope === "recent"
                ? t("搜索最近打开的文件…")
                : scope === "files"
                  ? t("搜索文件…")
                  : scope === "commands"
                    ? t("搜索或运行命令…")
                    : t("搜索文件或运行命令…")
            }
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
              setSelected(0);
            }}
            onKeyDown={(event) => {
              if (event.nativeEvent.isComposing || event.keyCode === 229) return;
              if (event.key === "Escape") {
                event.preventDefault();
                event.stopPropagation();
                close(true);
              }
              if (["ArrowDown", "ArrowUp"].includes(event.key) && enabled.length) {
                event.preventDefault();
                const current = enabled.indexOf(index);
                setSelected(
                  enabled[
                    (current + (event.key === "ArrowDown" ? 1 : enabled.length - 1)) %
                      enabled.length
                  ],
                );
              }
              if (event.key === "Enter") {
                event.preventDefault();
                activate(index);
              }
            }}
          />
          <span className="command-center-escape">
            <Keycaps keys={["Esc"]} />
          </span>
        </div>
        <div className="command-panel" inert={!open} aria-hidden={!open}>
          <span id="command-center-instructions" className="sr-only">
            {t(
              "上下方向键选择，Enter 执行或打开文件，Esc 关闭。输入 > 查找命令，输入文件名:行号定位。",
            )}
          </span>
          <div
            ref={list}
            id="studio-command-list"
            className="command-panel-list"
            role="listbox"
            aria-label={t("搜索结果")}
          >
            {matches.map((item, position) => (
              <button
                key={item.id}
                id={`studio-result-${position}`}
                data-result-index={position}
                className="command-row"
                role="option"
                tabIndex={-1}
                aria-selected={position === index}
                aria-disabled={!!item.disabled}
                aria-checked={item.checked}
                title={item.file?.path}
                onPointerDown={(event) => event.preventDefault()}
                onClick={() => activate(position)}
              >
                <span className="command-row-icon">
                  <Icon name={item.icon} size={16} expanded={item.checked} />
                </span>
                {item.file ? (
                  <span className="command-label file-result-label">
                    <span className="file-result-name">
                      {item.file.name}
                      {location &&
                        `:${location.line}${location.column ? `:${location.column}` : ""}`}
                    </span>
                    {item.file.parent && (
                      <span className="file-result-parent">{item.file.parent}</span>
                    )}
                  </span>
                ) : (
                  <span className="command-label">{item.label}</span>
                )}
                <span className="command-row-meta">
                  <span className="command-kind">{item.group}</span>
                  <span className="command-row-shortcut">
                    <Keycaps keys={item.keys ?? []} />
                  </span>
                  <span className="command-toggle-mark">
                    {item.checked && <Icon name="tick" size={13} />}
                  </span>
                </span>
              </button>
            ))}
            {fileQuery && filesLoading && <p role="status">{t("正在读取项目文件…")}</p>}
            {fileQuery && filesError && <p role="alert">{filesError}</p>}
            {!matches.length && (!fileQuery || (!filesLoading && !filesError)) && (
              <p role="status">
                {t(resultScope === "recent" && !query ? "没有最近打开的文件" : "没有匹配的结果")}
              </p>
            )}
            {fileQuery && filesTruncated && (
              <p className="search-limit">{t("仅索引前 5000 个项目文件。")}</p>
            )}
          </div>
          <footer className="command-scopes" onPointerDown={(event) => event.preventDefault()}>
            <InlineTabStrip
              label={t("搜索范围")}
              tabs={[
                { id: "all", label: t("全部"), focused: resultScope === "all" },
                { id: "commands", label: t("命令"), focused: resultScope === "commands" },
                { id: "files", label: t("文件"), focused: resultScope === "files" },
                { id: "recent", label: t("最近"), focused: resultScope === "recent" },
              ]}
              onSelect={(id) => {
                if (id !== "commands") setQuery((current) => current.replace(/^\s*>\s*/, ""));
                setSelected(0);
                onScopeChange(id as SearchScope);
              }}
            />
          </footer>
        </div>
      </>
    </div>
  );
}
