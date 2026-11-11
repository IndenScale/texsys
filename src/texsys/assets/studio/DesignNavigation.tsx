import React, { useId, useMemo, useState } from "react";
import { ExplorerTree } from "./ExplorerTree";
import {
  CatalogControls,
  CatalogGroups,
  groupByTag,
  matchesCatalog,
  tagFields,
  tagGroupings,
  type CatalogGroup,
  type CatalogRow,
  type CatalogTags,
} from "./CatalogBrowser";
import { t } from "./i18n";
import { Empty, Icon } from "./ui";
import type { TreeEntry } from "./workspace-model";

type Binding = { value?: number; unit: string; state: string; source: string };
type Instance = {
  name: string;
  type: string;
  definition: string | null;
  parameters: Record<string, Binding>;
};
type Definition = {
  name: string;
  file: string;
  instances: Instance[];
  ports: Record<string, { kind: string; direction: string }>;
  behavior: { reference: string; parameters: Record<string, Binding> } | null;
  connections: { from: string; to: string }[];
  source?: string;
  entry?: string;
  model?: {
    family: string;
    manufacturer: string;
    model: string;
    specifications?: Record<string, string | number | boolean>;
    reference?: string;
  };
};
type LibraryItem = {
  definition: string;
  kind: "cells" | "devices" | "modules" | "models";
  owner: string | null;
  folder: string | null;
  tags: CatalogTags;
  source?: string;
};
export type LibrarySource = {
  id: string;
  path: string;
  name: string;
  version: string;
  digest: string;
  locked: boolean;
  kind?: "component";
  cli_hint?: string;
};
type SaveLibraryReference = (
  source: LibrarySource,
  entry: string,
  previousDigest?: string,
) => Promise<void>;
export type DesignIndex = {
  root: string | null;
  definitions: Record<string, Definition>;
  library: LibraryItem[];
  diagnostics: { file: string; message: string }[];
  tag_errors: string[];
  instance_tags: Record<string, CatalogTags>;
  sources: LibrarySource[];
  requested_sources?: { folder: string; source_id: string }[];
  source_errors: { path: string; message: string }[];
  references: { package: string; version: string; digest: string; entry: string }[];
};
export const designViews = {
  model: { label: "模型", icon: "system" },
  topology: { label: "拓扑图", icon: "branch" },
  schematic: { label: "原理图", icon: "system" },
  bom: { label: "物料清单", icon: "files" },
  layout: { label: "三维布置", icon: "cube" },
};
export type DesignView = keyof typeof designViews;
export type DesignSelection = { definition: string } | { occurrence: string[] };
type OpenDesign = (selection: DesignSelection, view?: DesignView) => void;
const libraryLabels = { cells: "单元", devices: "器件", modules: "模块", models: "型号" };
const artifactViews = ["topology", "schematic", "bom", "layout"] as const;

function sourceLabel(source: LibrarySource) {
  return [source.name, source.version, source.locked ? t("已锁定") : ""]
    .filter(Boolean)
    .join(" · ");
}

export function designDocumentKey(selection: DesignSelection) {
  return "definition" in selection
    ? "\0definition:" + selection.definition
    : "\0instance:" + selection.occurrence.join(".");
}

export function resolveDesign(index: DesignIndex, selection: DesignSelection) {
  if ("definition" in selection) {
    return {
      key: selection.definition,
      definition: index.definitions[selection.definition],
      instance: undefined,
    };
  }
  let key = index.root;
  let instance: Instance | undefined;
  for (const name of selection.occurrence) {
    if (!key) return;
    instance = index.definitions[key]?.instances.find((item) => item.name === name);
    key = instance?.definition ?? null;
    if (!instance) return;
  }
  return { key, definition: key ? index.definitions[key] : undefined, instance };
}

type DesignCatalogRow = CatalogRow & {
  selection: DesignSelection;
  groupId: string;
  groupLabel: string;
  tags: CatalogTags;
  searchFields: string[];
};

function libraryCatalog(index: DesignIndex): DesignCatalogRow[] {
  const byDefinition = new Map(index.library.map((item) => [item.definition, item]));
  const sources = new Map(index.sources.map((source) => [source.id, source]));
  return index.library.map((item) => {
    const definition = index.definitions[item.definition];
    const owners = [];
    for (let owner = item.owner; owner; owner = byDefinition.get(owner)?.owner ?? null) {
      owners.unshift(index.definitions[owner].name);
    }
    const source = item.source ? sources.get(item.source) : undefined;
    const origin = source ? sourceLabel(source) : t("项目部件库");
    const scope = [origin, ...owners].join(" / ");
    const type = t(libraryLabels[item.kind]);
    return {
      id: item.definition,
      name: definition.name,
      detail: item.source || item.owner ? scope + " · " + type : type,
      icon: item.kind === "modules" ? "module" : "cube",
      selection: { definition: item.definition },
      groupId: (item.owner ?? item.source ?? "project") + ":" + item.kind,
      groupLabel: scope + " · " + type,
      tags: item.tags,
      searchFields: [definition.file, scope, type, definition.model?.manufacturer ?? ""],
    };
  });
}

function instanceCatalog(index: DesignIndex): DesignCatalogRow[] {
  if (!index.root) return [];
  const rootName = index.definitions[index.root].name;
  const rows: DesignCatalogRow[] = [];
  const ancestors = new Set<string>();
  function visit(key: string, parent: string[]) {
    if (ancestors.has(key)) return;
    ancestors.add(key);
    const parentPath = [rootName, ...parent].join(".");
    for (const item of index.definitions[key].instances) {
      const occurrence = [...parent, item.name];
      const path = parentPath + "." + item.name;
      const definition = item.definition ? index.definitions[item.definition] : undefined;
      rows.push({
        id: path,
        name: item.name,
        detail: parentPath,
        icon: definition?.instances.length ? "system" : "cube",
        selection: { occurrence },
        groupId: parentPath,
        groupLabel: parentPath,
        tags: index.instance_tags?.[path] ?? {},
        searchFields: [path, item.type, definition?.name ?? ""],
      });
      if (item.definition) visit(item.definition, occurrence);
    }
    ancestors.delete(key);
  }
  visit(index.root, []);
  return rows;
}

function catalogResults(rows: DesignCatalogRow[], query: string, grouping: string) {
  const matching = rows.filter((row) =>
    matchesCatalog(query, row.name, ...row.searchFields, ...tagFields(row.tags)),
  );
  if (grouping.startsWith("tag:"))
    return groupByTag(matching, grouping.slice(4), (row) => row.tags);
  const groups = new Map<string, CatalogGroup<DesignCatalogRow>>();
  for (const row of matching) {
    const group = groups.get(row.groupId) ?? { id: row.groupId, label: row.groupLabel, items: [] };
    group.items.push(row);
    groups.set(row.groupId, group);
  }
  return [...groups.values()];
}

export function DesignExplorer({
  mode,
  index,
  open,
  editTags,
}: {
  mode: "library" | "instances";
  index: DesignIndex;
  open: OpenDesign;
  editTags?: () => void;
}) {
  const [expanded, setExpanded] = useState(
    () => new Set(["library", "library/types", "library/types/modules", "root"]),
  );
  const [selected, select] = useState("");
  const [query, setQuery] = useState("");
  const [grouping, setGrouping] = useState("default");
  const libraryMode = mode === "library";
  const groupings = [
    { id: "default", label: t(libraryMode ? "库来源" : "项目层级") },
    ...(libraryMode
      ? tagGroupings(
          index.library.map((item) => item.tags),
          ["discipline", "family"],
        )
      : tagGroupings(Object.values(index.instance_tags ?? {}), ["floor", "discipline", "lot"])),
  ];
  const activeGrouping = groupings.some((item) => item.id === grouping) ? grouping : "default";
  const showResults = query.trim() !== "" || activeGrouping !== "default";
  const rows = useMemo(
    () => (showResults ? (libraryMode ? libraryCatalog(index) : instanceCatalog(index)) : []),
    [showResults, libraryMode, index],
  );
  const groups = catalogResults(rows, query, activeGrouping);
  const tagErrors = index.tag_errors.filter((error) =>
    error.startsWith(libraryMode ? "studio.library_tags" : "studio.instance_tags"),
  );
  const entries: TreeEntry[] = [];
  const destinations = new Map<string, { selection: DesignSelection; view?: DesignView }>();
  const append = (
    path: string,
    name: string,
    icon: string,
    children?: () => void,
    destination?: { selection: DesignSelection; view?: DesignView },
  ) => {
    entries.push({
      path,
      name,
      icon,
      directory: !!children,
      depth: path.split("/").length - 1,
      virtual: true,
      opensDocument: !!destination,
    });
    if (destination) destinations.set(path, destination);
    if (expanded.has(path)) children?.();
  };
  function appendDesign(path: string, selection: DesignSelection) {
    append(path + "/model", t("模型"), "system", undefined, { selection });
    append(path + "/artifacts", t("交付物"), "files", () => {
      for (const view of artifactViews) {
        append(
          path + "/artifacts/" + view,
          t(designViews[view].label),
          designViews[view].icon,
          undefined,
          { selection, view },
        );
      }
    });
  }
  function appendLibrary(owner: string | null, path: string, source?: string, models = false) {
    const kinds = models ? (["models"] as const) : (["cells", "devices", "modules"] as const);
    for (const kind of kinds) {
      const entries = index.library.filter(
        (item) => item.owner === owner && item.kind === kind && item.source === source,
      );
      if (!entries.length) continue;
      append(path + "/" + kind, t(libraryLabels[kind]), "folder", () => {
        for (const item of entries) {
          const definition = index.definitions[item.definition];
          const child = path + "/" + kind + "/" + encodeURIComponent(item.definition);
          const selection = { definition: item.definition };
          if (kind === "modules") {
            const hasPrivateLibrary = index.library.some(
              (member) => member.owner === item.definition,
            );
            append(
              child,
              definition.name,
              "module",
              hasPrivateLibrary
                ? () => {
                    append(child + "/library", t("模块部件库"), "folder", () =>
                      appendLibrary(item.definition, child + "/library", source),
                    );
                  }
                : undefined,
              { selection, view: "model" },
            );
          } else {
            append(child, definition.name, "cube", undefined, { selection });
          }
        }
      });
    }
  }
  function appendSource(path: string, name: string, icon: string, source?: string) {
    const items = index.library.filter((item) => item.owner === null && item.source === source);
    append(path, name, icon, () => {
      if (items.some((item) => item.kind !== "models")) {
        append(path + "/types", t("类型"), "folder", () =>
          appendLibrary(null, path + "/types", source),
        );
      }
      if (items.some((item) => item.kind === "models")) {
        appendLibrary(null, path, source, true);
      }
    });
  }
  function appendInstance(
    path: string,
    occurrence: string[],
    name: string,
    key: string | null,
    ancestors: Set<string>,
  ) {
    const selection = { occurrence };
    const children = key ? (index.definitions[key]?.instances ?? []) : [];
    if (!key || ancestors.has(key) || !children.length) {
      append(path, name, "cube", undefined, { selection });
      return;
    }
    const next = new Set([...ancestors, key]);
    append(path, name, "system", () => {
      appendDesign(path, selection);
      for (const child of children) {
        appendInstance(
          path + "/child-" + child.name,
          [...occurrence, child.name],
          child.name,
          child.definition,
          next,
        );
      }
    });
  }

  if (mode === "library" && !showResults) {
    if (index.library.some((item) => !item.source && item.owner === null)) {
      appendSource("library", t("项目部件库"), "folder");
    }
    for (const source of index.sources) {
      const path = "library-" + source.id;
      appendSource(path, sourceLabel(source), "library", source.id);
    }
  }
  if (mode === "instances" && !showResults && index.root) {
    appendInstance("root", [], index.definitions[index.root].name, index.root, new Set());
  }
  return (
    <>
      <CatalogControls
        searchLabel={t(libraryMode ? "搜索部件库" : "搜索项目实例")}
        query={query}
        setQuery={setQuery}
        grouping={activeGrouping}
        groupings={groupings}
        setGrouping={setGrouping}
        onEditTags={editTags}
      />
      {libraryMode &&
        index.source_errors.map((error) => (
          <p className="core-hint" key={error.path} title={error.message}>
            {t("无法读取库目录")} · {error.path}
          </p>
        ))}
      {tagErrors.length > 0 && (
        <p className="core-hint" title={tagErrors.join("\n")}>
          {t("部分标签配置无效。")}
        </p>
      )}
      {index.diagnostics.length > 0 && (
        <p
          className="core-hint"
          title={index.diagnostics.map((item) => item.file + " · " + item.message).join("\n")}
        >
          {t("部分 SDL 定义无法读取或引用。")}
        </p>
      )}
      {showResults ? (
        groups.length ? (
          <CatalogGroups
            key={activeGrouping + ":" + query.trim()}
            label={t(libraryMode ? "部件库搜索结果" : "实例搜索结果")}
            groups={groups}
            open={(row) => open(row.selection, "model")}
          />
        ) : (
          <p className="core-hint">
            {t(libraryMode ? "没有匹配的部件定义。" : "没有匹配的项目实例。")}
          </p>
        )
      ) : entries.length ? (
        <ExplorerTree
          label={t(mode === "library" ? "部件库" : "项目实例")}
          entries={entries}
          expanded={expanded}
          selected={selected}
          select={select}
          toggle={(path) =>
            setExpanded((previous) => {
              const next = new Set(previous);
              if (next.has(path)) next.delete(path);
              else next.add(path);
              return next;
            })
          }
          open={(path) => {
            const item = destinations.get(path);
            if (item) open(item.selection, item.view);
          }}
        />
      ) : (
        <p className="core-hint">
          {t(mode === "library" ? "项目部件库中尚无定义。" : "尚无可读取的项目设计入口。")}
        </p>
      )}
    </>
  );
}

function quantities(index: DesignIndex, key: string) {
  const cache = new Map<string, Map<string, bigint>>();
  const visiting = new Set<string>();
  let incomplete = false;
  function visit(id: string) {
    const known = cache.get(id);
    if (known) return known;
    const result = new Map<string, bigint>();
    if (visiting.has(id)) {
      incomplete = true;
      return result;
    }
    visiting.add(id);
    for (const child of index.definitions[id].instances) {
      if (!child.definition) {
        incomplete = true;
      } else if (!index.definitions[child.definition].instances.length) {
        result.set(child.definition, (result.get(child.definition) ?? 0n) + 1n);
      } else {
        for (const [definition, count] of visit(child.definition)) {
          result.set(definition, (result.get(definition) ?? 0n) + count);
        }
      }
    }
    visiting.delete(id);
    cache.set(id, result);
    return result;
  }
  const rows = [...visit(key)];
  return { rows, incomplete };
}

export function DesignDocument({
  index,
  selection,
  view,
  setView,
  openFile,
  referenceEntry,
}: {
  index: DesignIndex;
  selection: DesignSelection;
  view: DesignView;
  setView: (view: DesignView) => void;
  openFile: (path: string) => void;
  referenceEntry: SaveLibraryReference;
}) {
  const tabId = useId();
  const scope = resolveDesign(index, selection);
  const definition = scope?.definition;
  if (!scope?.key || !definition) return <Empty title={t("无法解析设计对象")} />;
  const occurrence = "occurrence" in selection ? selection.occurrence : undefined;
  const source = index.sources.find((item) => item.id === definition.source);
  const libraryItem = index.library.find((item) => item.definition === scope.key);
  const reference =
    source && definition.entry && libraryItem?.owner === null ? (
      <LibraryReference
        key={scope.key + source.digest}
        entry={definition.entry}
        source={source}
        references={index.references}
        save={referenceEntry}
      />
    ) : undefined;
  const title = occurrence
    ? [index.root ? index.definitions[index.root].name : "", ...occurrence].join(" / ")
    : definition.name;
  const isModule = !occurrence && libraryItem?.kind === "modules";
  const model = definition.model;
  if (model) {
    return (
      <section className="core-design-document" aria-label={title}>
        <header className="core-design-header">
          <div className="core-design-identity">
            <Icon name="cube" size={28} />
            <div>
              <div className="core-design-context">
                {t("型号")} · {source ? sourceLabel(source) + " · " + t("只读") : t("项目部件库")}
              </div>
              <h1>{title}</h1>
            </div>
          </div>
          {reference}
        </header>
        <div className="core-design-body">
          <p>
            {t("族")} · {model.family}
          </p>
          <p>
            {t("厂家")} · {model.manufacturer}
          </p>
          {model.reference && (
            <p>
              {t("资料来源")} · {model.reference}
            </p>
          )}
          {Object.entries(model.specifications ?? {}).map(([name, value]) => (
            <p key={name}>
              {name} · {String(value)}
            </p>
          ))}
        </div>
      </section>
    );
  }
  const label = t(isModule ? "模块概览" : occurrence ? "实例概览" : "定义概览");
  const ports = Object.entries(definition.ports);
  const bindings = Object.entries({
    ...definition.behavior?.parameters,
    ...scope.instance?.parameters,
  });
  const views = Object.entries(designViews) as [DesignView, (typeof designViews)[DesignView]][];
  const quantitiesForView = view === "bom" ? quantities(index, scope.key) : undefined;
  return (
    <section className="core-design-document" aria-label={title}>
      <header className="core-design-header">
        <div className="core-design-identity">
          <Icon name={isModule ? "module" : "system"} size={28} />
          <div>
            <div className="core-design-context">
              {label}
              {source && " · " + sourceLabel(source) + " · " + t("只读")}
            </div>
            <h1 title={title}>{title}</h1>
          </div>
        </div>
        {source ? (
          reference
        ) : (
          <button
            type="button"
            className="core-design-source"
            title={definition.file}
            onClick={() => openFile(definition.file)}
          >
            <Icon name="files" size={15} /> {t("查看源码")}
          </button>
        )}
      </header>
      <div
        className="core-design-views"
        role="tablist"
        aria-label={t("设计视图")}
        onKeyDown={(event) => {
          if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
          const current = views.findIndex(([id]) => id === view);
          const target =
            event.key === "Home"
              ? 0
              : event.key === "End"
                ? views.length - 1
                : (current + (event.key === "ArrowRight" ? 1 : -1) + views.length) % views.length;
          event.preventDefault();
          setView(views[target][0]);
          event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="tab"]')[target]?.focus();
        }}
      >
        {views.map(([id, option]) => (
          <button
            key={id}
            type="button"
            role="tab"
            id={tabId + "-" + id}
            aria-controls={tabId + "-panel"}
            aria-selected={view === id}
            tabIndex={view === id ? 0 : -1}
            onClick={() => setView(id)}
          >
            <Icon name={option.icon} size={15} /> {t(id === "model" ? "概览" : option.label)}
          </button>
        ))}
      </div>
      <div
        className="core-design-body"
        id={tabId + "-panel"}
        role="tabpanel"
        aria-labelledby={tabId + "-" + view}
        tabIndex={0}
      >
        {view === "model" && (
          <>
            <dl className="core-design-summary">
              <div>
                <dt>{t("子实例")}</dt>
                <dd>{definition.instances.length}</dd>
              </div>
              <div>
                <dt>{t("端口")}</dt>
                <dd>{ports.length}</dd>
              </div>
              <div>
                <dt>{t("连接")}</dt>
                <dd>{definition.connections.length}</dd>
              </div>
            </dl>
            <h3>{t("端口")}</h3>
            {ports.length ? (
              <ul>
                {ports.map(([name, port]) => (
                  <li key={name}>
                    {name} · {port.kind} · {port.direction}
                  </li>
                ))}
              </ul>
            ) : (
              <p>{t("未声明端口。")}</p>
            )}
            <h3>{t("参数")}</h3>
            {bindings.length ? (
              <ul>
                {bindings.map(([name, binding]) => (
                  <li key={name} title={binding.source}>
                    {name} · {binding.value ?? t("待定")} {binding.unit} · {t(binding.state)}
                  </li>
                ))}
              </ul>
            ) : (
              <p>{t("未声明参数。")}</p>
            )}
          </>
        )}
        {view === "topology" && (
          <>
            <h2>{t("拓扑图")}</h2>
            <p>{t("按当前范围查看组成与端口连接。")}</p>
            <h3>{t("子实例")}</h3>
            <ul>
              {definition.instances.map((child) => (
                <li key={child.name}>
                  {child.name} · {child.type}
                </li>
              ))}
            </ul>
            <h3>{t("连接")}</h3>
            <ul>
              {definition.connections.map((connection, index) => (
                <li key={index}>
                  {connection.from} → {connection.to}
                </li>
              ))}
            </ul>
          </>
        )}
        {quantitiesForView && (
          <>
            <h2>{t("物料清单")}</h2>
            <p>{t("按叶子定义汇总的组成预览。")}</p>
            {quantitiesForView.incomplete && (
              <p role="status">{t("有未解析或循环的实例，数量不完整。")}</p>
            )}
            <table>
              <thead>
                <tr>
                  <th>{t("定义")}</th>
                  <th>{t("数量")}</th>
                </tr>
              </thead>
              <tbody>
                {quantitiesForView.rows.map(([key, count]) => (
                  <tr key={key}>
                    <td title={key}>{index.definitions[key].name}</td>
                    <td>{String(count)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
        {(view === "schematic" || view === "layout") && (
          <Empty title={t("尚无关联交付物")}>{t("当前范围没有已关联的交付物。")}</Empty>
        )}
      </div>
    </section>
  );
}

function LibraryReference({
  entry,
  source,
  references,
  save,
}: {
  entry: string;
  source: LibrarySource;
  references: DesignIndex["references"];
  save: SaveLibraryReference;
}) {
  const [busy, setBusy] = useState(false);
  const [confirmReview, setConfirmReview] = useState(false);
  const [error, setError] = useState("");
  const pinned = references.some(
    (item) => item.package === source.name && item.entry === entry && item.digest === source.digest,
  );
  const previous = references.find(
    (item) => item.package === source.name && item.digest !== source.digest,
  );
  if (!source.locked)
    return (
      <div className="core-hint">
        <p>{t("此部件包尚未按当前版本导入项目。请先在终端导入，再刷新部件库。")}</p>
        {source.cli_hint && <code>{source.cli_hint}</code>}
      </div>
    );
  return (
    <div>
      {pinned ? (
        <span className="core-design-context">{t("已引用到项目")}</span>
      ) : (
        <button
          type="button"
          className="core-design-source"
          disabled={busy}
          onClick={async () => {
            if (previous && !confirmReview) {
              setConfirmReview(true);
              return;
            }
            setBusy(true);
            setError("");
            try {
              await save(source, entry, previous?.digest);
              setConfirmReview(false);
            } catch (failure) {
              setError(String(failure));
            } finally {
              setBusy(false);
            }
          }}
        >
          {t(previous ? (confirmReview ? "确认引用版本" : "复核部件引用") : "引用到项目")}
        </button>
      )}
      {previous && confirmReview && (
        <p className="core-hint">
          {t("将部件引用更新到已导入的锁定版本，项目依赖保持不变。")} {previous.version} →{" "}
          {source.version}{" "}
          <button type="button" disabled={busy} onClick={() => setConfirmReview(false)}>
            {t("取消")}
          </button>
        </p>
      )}
      {error && (
        <p className="core-error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
