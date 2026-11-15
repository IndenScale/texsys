import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { NavigatorMenu, type NavigatorMenuItem } from "./NavigatorMenu";
import { NavigatorButton } from "./NavigatorPanel";
import { detectedSourceLanguage } from "./workspace-model";
import { t } from "./i18n";
import {
  filterOutlineSymbols,
  outlineForDocument,
  outlineSymbolAt,
  sortOutlineSymbols,
  supportsOutline,
  type OutlineRange,
  type OutlineSort,
  type OutlineSymbol,
} from "./outline-model";
import { Icon } from "./ui";
import "./outline-view.css";

type OutlineRow = { node: OutlineSymbol; depth: number; ancestors: string[] };

export function OutlineView({
  document,
  cursor,
  onNavigate,
}: {
  document?: { name: string; value: string };
  cursor?: { line: number; column: number };
  onNavigate: (range: OutlineRange) => void;
}) {
  const [followCursor, setFollowCursor] = useState(true);
  const [filterOnType, setFilterOnType] = useState(true);
  const [sort, setSort] = useState<OutlineSort>("position");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [selectedId, setSelectedId] = useState<string>();
  const [activeId, setActiveId] = useState<string>();
  const [filterQuery, setFilterQuery] = useState("");
  const [filterOpen, setFilterOpen] = useState(false);
  const treeRef = useRef<HTMLDivElement>(null);
  const rowRefs = useRef(new Map<string, HTMLDivElement>());
  const language = document ? detectedSourceLanguage(document.name) : "plaintext";
  const docKey = document?.name;
  const symbols = useMemo(
    () => (document ? outlineForDocument(language, document.value) : []),
    [document, language],
  );
  const filtering = filterOpen && filterQuery.trim() !== "";
  const displayed = useMemo(
    () => filterOutlineSymbols(sortOutlineSymbols(symbols, sort), filterQuery),
    [symbols, sort, filterQuery],
  );
  const highlightId = followCursor && activeId ? activeId : selectedId;
  const activeChain = useMemo(() => {
    const chain = new Set<string>();
    if (!highlightId) return chain;
    const walk = (list: OutlineSymbol[], trail: string[]): boolean => {
      for (const node of list) {
        const next = [...trail, node.id];
        if (node.id === highlightId) {
          next.forEach((id) => chain.add(id));
          return true;
        }
        if (walk(node.children, next)) return true;
      }
      return false;
    };
    walk(displayed, []);
    return chain;
  }, [displayed, highlightId]);
  const expandableIds = useMemo(() => {
    const ids: string[] = [];
    const walk = (list: OutlineSymbol[]) =>
      list.forEach((node) => {
        if (node.children.length) {
          ids.push(node.id);
          walk(node.children);
        }
      });
    walk(symbols);
    return ids;
  }, [symbols]);
  const allCollapsed = expandableIds.length > 0 && expandableIds.every((id) => !expanded.has(id));
  const rows = useMemo(() => {
    const flat: OutlineRow[] = [];
    const walk = (list: OutlineSymbol[], depth: number, ancestors: string[]) => {
      for (const node of list) {
        flat.push({ node, depth, ancestors });
        if (node.children.length && (filtering || expanded.has(node.id)))
          walk(node.children, depth + 1, [...ancestors, node.id]);
      }
    };
    walk(displayed, 0, []);
    return flat;
  }, [displayed, expanded, filtering]);

  useEffect(() => {
    setSelectedId(undefined);
    setActiveId(undefined);
    setFilterQuery("");
    setFilterOpen(false);
    const all = new Set<string>();
    const walk = (list: OutlineSymbol[]) =>
      list.forEach((node) => {
        all.add(node.id);
        walk(node.children);
      });
    walk(symbols);
    setExpanded(all);
  }, [docKey]);

  useLayoutEffect(() => {
    if (!followCursor || !cursor || !document) {
      setActiveId(undefined);
      return;
    }
    const hit = outlineSymbolAt(symbols, cursor.line, cursor.column);
    setActiveId(hit?.id);
    if (!hit) return;
    setExpanded((previous) => {
      const next = new Set(previous);
      hit.ancestors.forEach((id) => next.add(id));
      return next;
    });
    requestAnimationFrame(() => rowRefs.current.get(hit.id)?.scrollIntoView({ block: "nearest" }));
  }, [cursor, symbols, followCursor, document]);

  const menuItems: NavigatorMenuItem[] = [
    {
      label: t("跟随光标"),
      checkbox: true,
      checked: followCursor,
      run: () => setFollowCursor(!followCursor),
    },
    {
      label: t("在输入时筛选"),
      checkbox: true,
      checked: filterOnType,
      run: () => {
        setFilterOnType(!filterOnType);
        if (filterOnType) {
          setFilterOpen(false);
          setFilterQuery("");
        }
      },
    },
    { label: "", separator: true },
    {
      label: t("排序依据: 位置"),
      checked: sort === "position",
      run: () => setSort("position"),
    },
    { label: t("排序依据: 名称"), checked: sort === "name", run: () => setSort("name") },
    {
      label: t("排序方式: 类别"),
      checked: sort === "category",
      run: () => setSort("category"),
    },
  ];

  const toggle = (id: string) =>
    setExpanded((previous) => {
      const next = new Set(previous);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const navigate = (node: OutlineSymbol) => {
    setSelectedId(node.id);
    onNavigate(node.range);
  };
  const focusRow = (id: string | undefined) => {
    if (!id) return;
    const row = rowRefs.current.get(id);
    row?.focus();
    row?.scrollIntoView({ block: "nearest" });
  };

  return (
    <details className="welcome-side-section welcome-outline-section">
      <summary>
        <Icon name="chevron" size={14} />
        <span>{t("大纲")}</span>
        <span
          className="outline-actions"
          onClick={(event) => {
            event.preventDefault();
            event.stopPropagation();
          }}
        >
          <NavigatorButton
            label={t(allCollapsed ? "全部展开" : "全部折叠")}
            icon={allCollapsed ? "expand" : "collapse"}
            disabled={expandableIds.length === 0}
            onClick={() => setExpanded(allCollapsed ? new Set(expandableIds) : new Set())}
          />
          <NavigatorMenu label={t("大纲更多操作")} items={menuItems} />
        </span>
      </summary>
      {!document ? (
        <p className="core-hint">{t("打开文档后可查看大纲。")}</p>
      ) : !supportsOutline(language) ? (
        <p className="core-hint">{t("此文件无法提供大纲信息。")}</p>
      ) : symbols.length === 0 ? (
        <p className="core-hint">{t("文档中没有可显示的大纲符号。")}</p>
      ) : (
        <div className="outline-body">
          {filterOpen && (
            <input
              className="outline-filter"
              autoFocus
              aria-label={t("筛选大纲")}
              placeholder={t("筛选大纲")}
              value={filterQuery}
              onChange={(event) => setFilterQuery(event.target.value)}
              onKeyDown={(event) => {
                event.stopPropagation();
                if (event.key === "Escape") {
                  event.preventDefault();
                  setFilterQuery("");
                  setFilterOpen(false);
                  treeRef.current?.focus();
                } else if (event.key === "Backspace" && !filterQuery) {
                  event.preventDefault();
                  setFilterOpen(false);
                  treeRef.current?.focus();
                }
              }}
            />
          )}
          {rows.length === 0 ? (
            <p className="core-hint">{t("没有匹配的符号。")}</p>
          ) : (
            <div
              ref={treeRef}
              className="outline-tree"
              role="tree"
              aria-label={t("大纲")}
              tabIndex={-1}
              onKeyDown={(event) => {
                if (
                  filterOnType &&
                  !filterOpen &&
                  !event.metaKey &&
                  !event.ctrlKey &&
                  !event.altKey &&
                  event.key.length === 1
                ) {
                  event.preventDefault();
                  setFilterOpen(true);
                  setFilterQuery(event.key);
                  return;
                }
                if (
                  ![
                    "ArrowDown",
                    "ArrowUp",
                    "ArrowRight",
                    "ArrowLeft",
                    "Home",
                    "End",
                    "Enter",
                    " ",
                  ].includes(event.key)
                )
                  return;
                const elements = Array.from(
                  event.currentTarget.querySelectorAll<HTMLDivElement>('[role="treeitem"]'),
                );
                const index = elements.indexOf(event.target as HTMLDivElement);
                const row = rows[index];
                if (!row) return;
                event.preventDefault();
                if (event.key === "Enter" || event.key === " ") {
                  navigate(row.node);
                  return;
                }
                if (event.key === "ArrowRight") {
                  if (row.node.children.length && !expanded.has(row.node.id)) toggle(row.node.id);
                  else if (rows[index + 1]?.depth === row.depth + 1)
                    focusRow(rows[index + 1].node.id);
                  return;
                }
                if (event.key === "ArrowLeft") {
                  if (row.node.children.length && expanded.has(row.node.id)) toggle(row.node.id);
                  else {
                    const parent = rows
                      .slice(0, index)
                      .reverse()
                      .find((item) => item.depth === row.depth - 1);
                    focusRow(parent?.node.id);
                  }
                  return;
                }
                const target =
                  event.key === "Home"
                    ? 0
                    : event.key === "End"
                      ? rows.length - 1
                      : event.key === "ArrowUp"
                        ? Math.max(0, index - 1)
                        : Math.min(rows.length - 1, index + 1);
                focusRow(rows[target]?.node.id);
              }}
            >
              {rows.map(({ node, depth, ancestors }, index) => (
                <div
                  key={node.id}
                  ref={(element) => {
                    if (element) rowRefs.current.set(node.id, element);
                    else rowRefs.current.delete(node.id);
                  }}
                  role="treeitem"
                  aria-level={depth + 1}
                  aria-selected={highlightId === node.id}
                  aria-expanded={node.children.length ? expanded.has(node.id) : undefined}
                  tabIndex={index === 0 ? 0 : -1}
                  data-outline-id={node.id}
                  className={`outline-row${highlightId === node.id ? " selected" : ""}`}
                  style={{ "--outline-depth": depth } as React.CSSProperties}
                  title={node.detail ? `${node.name} · ${node.detail}` : node.name}
                  onFocus={() => setSelectedId(node.id)}
                  onClick={() => navigate(node)}
                >
                  <span className="outline-guides" aria-hidden="true">
                    {Array.from({ length: depth }, (_, level) => (
                      <span
                        key={level}
                        className={`outline-guide${activeChain.has(ancestors[level]) ? " active" : ""}`}
                        style={{ "--guide-depth": level } as React.CSSProperties}
                      />
                    ))}
                  </span>
                  <span className="outline-twistie">
                    {node.children.length > 0 && (
                      <button
                        type="button"
                        tabIndex={-1}
                        aria-label={t(expanded.has(node.id) ? "折叠 {0}" : "展开 {0}", {
                          0: node.name,
                        })}
                        onClick={(event) => {
                          event.stopPropagation();
                          toggle(node.id);
                        }}
                      >
                        <Icon name="chevron" size={16} />
                      </button>
                    )}
                  </span>
                  <span className="outline-symbol" data-kind={node.kind} aria-hidden="true" />
                  <span className="outline-name">{node.name}</span>
                  {node.detail && <small className="outline-detail">{node.detail}</small>}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </details>
  );
}
