import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { t } from "./i18n";
import { Icon } from "./ui";
import { NavigatorButton, NavigatorPage } from "./NavigatorPanel";
import { NavigatorMenu, type NavigatorMenuItem } from "./NavigatorMenu";
import { activityBarPositionMenuItem, sidebarLayoutMenuItems } from "./atomstudio/Layout";
import { useExtensionSnippets, type ExtensionSnippet } from "./extension-snippets";
import type { WorkbenchProps } from "./sdk";
import "./extension-activity.css";

type Entry = {
  id: string;
  name: string;
  publisher: string;
  description: string;
  version: string;
  icon?: string;
  vsix?: string;
  rating?: number;
  downloads?: number;
  verified?: boolean;
  preRelease?: boolean;
  builtin?: boolean;
};
type Detail = Entry & {
  versions: string[];
  hasPreRelease: boolean;
  compatibility:
    "snippets" | "runtime" | "dependencies" | "contributions" | "manifest" | "unavailable";
};
export type ExtensionDocument = {
  entry: Entry;
  version: string;
  intent?: "install" | "download";
};
type Installed = Entry & {
  enabled: boolean;
  globalEnabled: boolean;
  workspaceEnabled: boolean | null;
  autoUpdate: boolean;
  snippets: ExtensionSnippet[];
};
type Inventory = { installed: Installed[]; autoUpdate: boolean; ignored: string[] };
type SearchResult = { items: Entry[]; total: number };
type Recommendations = { ids: string[]; workspace: string[] };
type View = "installed" | "recommended" | "enabled" | "disabled";
type Change = { action: string; id?: string; [key: string]: unknown };

const endpoint = "/api/extensions";
const recommendationPath = ".vscode/extensions.json";
const views = {
  installed: "已安装",
  recommended: "推荐",
  enabled: "已启用",
  disabled: "已禁用",
} satisfies Record<View, string>;
const sorts = {
  relevance: "相关性",
  downloadCount: "下载次数",
  averageRating: "评分",
  timestamp: "更新时间",
};
const compatibility = {
  snippets: "可用于源代码编辑器的代码片段",
  runtime: "需要 VS Code 扩展宿主",
  dependencies: "需要其他扩展或扩展包",
  contributions: "Tex Studio 尚未支持此扩展的贡献点",
  manifest: "Open VSX 未提供此版本的扩展清单",
  unavailable: "当前版本没有可下载的 VSIX",
};
const downloadsFormat = new Intl.NumberFormat(undefined, {
  notation: "compact",
  maximumFractionDigits: 0,
});
const ratingFormat = new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 });
const toolsKey = "texsys.studio.extensions.hiddenSearchTools";
type SearchTool = "clear" | "filter";
function readHiddenTools(): SearchTool[] {
  try {
    const value = JSON.parse(localStorage.getItem(toolsKey) ?? "[]");
    return (["clear", "filter"] as const).filter(
      (tool) => Array.isArray(value) && value.includes(tool),
    );
  } catch {
    return [];
  }
}
const searchTools = { clear: "清除扩展搜索", filter: "筛选扩展" } as const;
const initialInventory: Inventory = { installed: [], autoUpdate: false, ignored: [] };
const sectionKeys = Object.keys(views) as View[];
const isView = (value: string): value is View => sectionKeys.includes(value as View);
const key = (entry: Entry) => entry.id.toLowerCase();
const message = (error: unknown) => (error instanceof Error ? error.message : String(error));
const itemUrl = (entry: Entry) => `https://open-vsx.org/extension/${entry.id.replace(".", "/")}`;
const newer = (version: string, current: string) => {
  const before = current.split(/[.+-]/).slice(0, 3).map(Number);
  const after = version.split(/[.+-]/).slice(0, 3).map(Number);
  const index = after.findIndex((part, i) => part !== before[i]);
  return index >= 0 && after[index] > before[index];
};

function ExtensionIcon({ entry }: { entry: Entry }) {
  const [failed, setFailed] = useState(false);
  return entry.icon && !failed ? (
    <img
      alt=""
      src={entry.icon}
      referrerPolicy="no-referrer"
      loading="lazy"
      onError={() => setFailed(true)}
    />
  ) : (
    <span className="extension-icon-fallback">
      <Icon name="extensions" size={20} />
    </span>
  );
}

export function useExtensionActivity({
  active,
  api,
  changeLayout,
  layout,
  notify,
  plugins,
  recommendInWorkspace,
  openDocument,
  activeDocument,
}: Pick<WorkbenchProps, "api" | "changeLayout" | "layout" | "notify"> & {
  active: boolean;
  plugins: WorkbenchProps["state"]["plugins"];
  recommendInWorkspace: (id: string) => Promise<Recommendations>;
  openDocument: (document: ExtensionDocument) => void;
  activeDocument?: ExtensionDocument;
}) {
  const [inventory, setInventory] = useState(initialInventory);
  const [inventoryError, setInventoryError] = useState("");
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<keyof typeof sorts>("relevance");
  const [shown, setShown] = useState<View[]>(["installed", "recommended"]);
  const [expanded, setExpanded] = useState<View[]>(["installed", "recommended"]);
  const [revision, refresh] = useState(0);
  const [recommendations, setRecommendations] = useState<Entry[]>([]);
  const [workspaceIds, setWorkspaceIds] = useState<string[]>([]);
  const [recommendationError, setRecommendationError] = useState("");
  const [loadingRecommendations, setLoadingRecommendations] = useState(false);
  const [search, setSearch] = useState<SearchResult>();
  const [searchError, setSearchError] = useState("");
  const [completedSearch, setCompletedSearch] = useState("");
  const [paging, setPaging] = useState(false);
  const [hiddenTools, setHiddenTools] = useState(readHiddenTools);
  const searchCache = useRef(new Map<string, { result: SearchResult; expires: number }>());
  const detailCache = useRef(new Map<string, { result: Promise<Detail>; expires: number }>());
  const recommendationRevision = useRef(-1);
  const [pending, setPending] = useState(false);
  const [updates, setUpdates] = useState<Record<string, Detail>>({});
  const [context, setContext] = useState<{
    x: number;
    y: number;
    anchor: HTMLElement;
    view?: View;
    entry?: Entry;
    target?: SearchTool | "input" | "search";
  }>();
  const operation = useRef(false);
  const lastAutoCheck = useRef(0);
  const searchSequence = useRef(0);
  const searchInput = useRef<HTMLInputElement>(null);
  const searchDraftTimer = useRef<number | undefined>(undefined);
  const refreshAll = () => {
    searchCache.current.clear();
    detailCache.current.clear();
    refresh((value) => value + 1);
  };
  const installed = useMemo(
    () => new Map(inventory.installed.map((entry) => [key(entry), entry])),
    [inventory.installed],
  );
  const builtins = plugins.map((plugin): Entry => ({
    id: plugin.id,
    name: plugin.name,
    publisher: "Tex Studio",
    version: plugin.version,
    description: t(plugin.discipline),
    builtin: true,
  }));
  const installedEntries = [...builtins, ...inventory.installed];
  const term = query.trim();
  const filter = /^@(installed|recommended|enabled|disabled)(?:\s+(.*))?$/i.exec(term);
  const filterView = filter?.[1].toLowerCase();
  const filteredView = filterView && isView(filterView) ? filterView : undefined;
  const marketplace = Boolean(term && !filteredView);
  const showRecommendations =
    active &&
    !marketplace &&
    (filteredView === "recommended" ||
      (!filteredView && shown.includes("recommended") && expanded.includes("recommended")));

  useExtensionSnippets(inventory.installed);

  useEffect(() => {
    let current = true;
    api<Inventory>(endpoint).then(
      (result) => {
        if (!current || operation.current) return;
        setInventory(result);
        setInventoryError("");
      },
      (error) => {
        if (current) setInventoryError(message(error));
      },
    );
    return () => {
      current = false;
    };
  }, [api, revision]);

  async function searchPage(value: string, offset: number, signal?: AbortSignal) {
    const params = new URLSearchParams({
      q: value === "@marketplace" ? "" : value,
      sort,
      offset: String(offset),
    });
    const id = params.toString();
    const cached = searchCache.current.get(id);
    if (cached && cached.expires > Date.now()) return cached.result;
    const result = await api<SearchResult>(`${endpoint}/search?${id}`, undefined, signal);
    if (searchCache.current.size >= 40)
      searchCache.current.delete(searchCache.current.keys().next().value!);
    searchCache.current.set(id, { result, expires: Date.now() + 60_000 });
    return result;
  }

  const getDetail = useCallback(
    (entry: Entry, version = "latest") => {
      const id = new URLSearchParams({ id: entry.id, version }).toString();
      const cached = detailCache.current.get(id);
      if (cached && cached.expires > Date.now()) return cached.result;
      const result = api<Detail>(`${endpoint}/detail?${id}`);
      if (detailCache.current.size >= 40)
        detailCache.current.delete(detailCache.current.keys().next().value!);
      detailCache.current.set(id, { result, expires: Date.now() + 60_000 });
      void result.catch(() => {
        if (detailCache.current.get(id)?.result === result) detailCache.current.delete(id);
      });
      return result;
    },
    [api],
  );

  useEffect(() => {
    if (!showRecommendations || recommendationRevision.current === revision) return;
    let current = true;
    setLoadingRecommendations(true);
    setRecommendationError("");
    void api<Recommendations>(`${endpoint}/recommendations`)
      .then(async (result) => {
        const requests = await Promise.allSettled(
          result.ids.map((id) => searchPage(`@id:${id}`, 0)),
        );
        if (!current) return;
        recommendationRevision.current = revision;
        setWorkspaceIds(result.workspace.map((id) => id.toLowerCase()));
        setRecommendations(
          requests.flatMap((request) =>
            request.status === "fulfilled" ? request.value.items : [],
          ),
        );
        if (requests.some((request) => request.status === "rejected")) {
          setRecommendationError(t("部分推荐未能从 Open VSX 加载。"));
        }
      })
      .catch((error) => {
        if (current) setRecommendationError(message(error));
      })
      .finally(() => {
        if (current) setLoadingRecommendations(false);
      });
    return () => {
      current = false;
    };
  }, [api, showRecommendations, revision]);

  useEffect(() => {
    const sequence = ++searchSequence.current;
    const searchKey = `${sort}:${term}`;
    setSearchError("");
    setPaging(false);
    if (!active || !marketplace) return;
    if (term.toLowerCase() === "@id:") {
      setSearch({ items: [], total: 0 });
      setCompletedSearch(searchKey);
      return;
    }
    const controller = new AbortController();
    void searchPage(term, 0, controller.signal).then(
      (result) => {
        if (sequence === searchSequence.current) {
          setSearch(result);
          setCompletedSearch(searchKey);
        }
      },
      (error) => {
        if (sequence === searchSequence.current) setSearchError(message(error));
      },
    );
    return () => {
      controller.abort();
      searchSequence.current += 1;
    };
  }, [api, active, marketplace, term, sort, revision]);

  function loadMore() {
    if (!search || paging) return;
    const sequence = searchSequence.current;
    setPaging(true);
    void searchPage(term, search.items.length)
      .then(
        (result) => {
          if (sequence !== searchSequence.current) return;
          setSearch({ ...result, items: [...search.items, ...result.items] });
          setSearchError("");
        },
        (error) => {
          if (sequence === searchSequence.current) setSearchError(message(error));
        },
      )
      .finally(() => {
        if (sequence === searchSequence.current) setPaging(false);
      });
  }

  async function perform(task: () => Promise<void>) {
    if (operation.current) return;
    operation.current = true;
    setPending(true);
    try {
      await task();
    } catch (error) {
      notify(message(error), true);
    } finally {
      operation.current = false;
      setPending(false);
    }
  }

  async function change(request: Change) {
    const result = await api<Inventory>(endpoint, request);
    setInventory(result);
    setInventoryError("");
    return result;
  }

  async function install(entry: Entry, version: string) {
    await change({
      action: "install",
      id: entry.id,
      version,
      previousVersion: installed.get(key(entry))?.version ?? null,
    });
    setUpdates((before) => {
      const next = { ...before };
      delete next[key(entry)];
      return next;
    });
    notify(t("已安装 {name}。", { name: entry.name }));
  }

  function checkUpdates(automatic = false) {
    return perform(async () => {
      const results = await Promise.allSettled(
        inventory.installed.map(async (entry) => {
          const latest = await getDetail(entry, entry.preRelease ? "pre-release" : "latest");
          if (latest.compatibility === "snippets" && newer(latest.version, entry.version))
            return latest;
        }),
      );
      const available = results.flatMap((result) =>
        result.status === "fulfilled" && result.value ? [result.value] : [],
      );
      setUpdates(Object.fromEntries(available.map((entry) => [key(entry), entry])));
      if (automatic) {
        for (const entry of available) {
          if (installed.get(key(entry))?.autoUpdate) await install(entry, entry.version);
        }
      }
      if (results.some((result) => result.status === "rejected")) {
        notify(t("部分扩展未能检查更新。"), true);
      } else if (!automatic) {
        notify(
          available.length
            ? t("发现 {count} 个扩展更新。", { count: available.length })
            : t("扩展已是最新版本。"),
        );
      }
    });
  }

  useEffect(() => {
    if (!active || !inventory.autoUpdate || !inventory.installed.length || pending) return;
    if (Date.now() - lastAutoCheck.current < 30 * 60_000) return;
    lastAutoCheck.current = Date.now();
    void checkUpdates(true);
  }, [active, inventory, pending]);

  useEffect(() => {
    if (!active) setContext(undefined);
  }, [active]);

  useEffect(() => () => window.clearTimeout(searchDraftTimer.current), []);

  function setSearchText(value: string) {
    window.clearTimeout(searchDraftTimer.current);
    const input = searchInput.current;
    if (input && input.value !== value) input.value = value;
    setQuery(value);
  }

  function toggleView(view: View) {
    setShown((before) =>
      before.includes(view) ? before.filter((item) => item !== view) : [...before, view],
    );
  }

  function openEntry(entry: Entry, version = "latest", intent?: ExtensionDocument["intent"]) {
    openDocument({ entry, version, intent });
  }

  function copy(value: string) {
    void perform(async () => {
      await navigator.clipboard.writeText(value);
    });
  }

  function openContext(
    event: React.MouseEvent<HTMLElement> | React.KeyboardEvent<HTMLElement>,
    view?: View,
    entry?: Entry,
    target?: SearchTool | "input" | "search",
  ) {
    if ("key" in event && event.key !== "ContextMenu" && !(event.shiftKey && event.key === "F10"))
      return;
    event.preventDefault();
    event.stopPropagation();
    const anchor =
      event.currentTarget.querySelector<HTMLElement>(":scope > .extension-entry") ??
      event.currentTarget;
    const rect = anchor.getBoundingClientRect();
    const [x, y] = "key" in event ? [rect.left, rect.bottom] : [event.clientX, event.clientY];
    setContext({ x, y, anchor, view, entry, target });
  }

  const layoutItems = [
    { ...activityBarPositionMenuItem(layout, changeLayout), separator: true },
    ...sidebarLayoutMenuItems(layout, changeLayout),
  ];
  const viewItems = sectionKeys.map((view) => ({
    label: t(views[view]),
    checked: shown.includes(view),
    checkbox: true,
    run: () => toggleView(view),
  }));
  const updateEntries = Object.values(updates).filter((entry) => installed.has(key(entry)));
  const titleItems: NavigatorMenuItem[] = [
    { label: t("视图"), submenu: viewItems },
    {
      label: t("检查扩展更新"),
      run: inventory.installed.length && !pending ? () => void checkUpdates() : undefined,
    },
    {
      label: t("更新所有扩展"),
      run:
        updateEntries.length && !pending
          ? () =>
              void perform(async () => {
                for (const entry of updateEntries) await install(entry, entry.version);
              })
          : undefined,
    },
    {
      label: t(inventory.autoUpdate ? "为扩展禁用自动更新" : "为扩展启用自动更新"),
      run: pending
        ? undefined
        : () =>
            void perform(async () => {
              lastAutoCheck.current = 0;
              await change({ action: "auto-update-all", enabled: !inventory.autoUpdate });
            }),
    },
    {
      label: t("启用所有扩展"),
      separator: true,
      run:
        !pending && inventory.installed.some((entry) => !entry.enabled || !entry.globalEnabled)
          ? () =>
              void perform(async () => {
                await change({ action: "enable-all" });
              })
          : undefined,
    },
    {
      label: t("禁用所有已安装的扩展"),
      run:
        !pending && inventory.installed.some((entry) => entry.enabled || entry.globalEnabled)
          ? () =>
              void perform(async () => {
                await change({ action: "disable-all" });
              })
          : undefined,
    },
    { label: t("显示已启用的扩展"), run: () => setSearchText("@enabled") },
  ];

  function entryActions(entry: Entry): NavigatorMenuItem[] {
    const local = installed.get(key(entry));
    const recommended = recommendations.some((item) => key(item) === key(entry));
    const enable = (enabled: boolean, scope: string) => () =>
      void perform(async () => {
        await change({ action: "enable", id: entry.id, scope, enabled });
      });
    const actions: NavigatorMenuItem[] = [
      ...(local
        ? [
            {
              label: t(local.globalEnabled ? "禁用" : "启用"),
              run: enable(!local.globalEnabled, "global"),
            },
            {
              label: t(local.enabled ? "禁用（工作区）" : "启用（工作区）"),
              run: enable(!local.enabled, "workspace"),
            },
            {
              label: t("自动更新"),
              checked: local.autoUpdate,
              checkbox: true,
              separator: true,
              run: () =>
                void perform(async () => {
                  await change({ action: "auto-update", id: entry.id, enabled: !local.autoUpdate });
                }),
            },
            {
              label: t("卸载"),
              run: () =>
                void perform(async () => {
                  await change({ action: "uninstall", id: entry.id });
                  notify(t("已卸载 {name}。", { name: entry.name }));
                }),
            },
          ]
        : []),
      ...(!entry.builtin
        ? [
            {
              label: t("安装特定版本…"),
              separator: !!local,
              run: () => openEntry(entry, local?.version ?? entry.version, "install"),
            },
          ]
        : []),
      {
        label: t("复制"),
        separator: !entry.builtin,
        run: () => copy(`${entry.name} (${entry.id})`),
      },
      { label: t("复制扩展 ID"), run: () => copy(entry.id) },
      ...(!entry.builtin
        ? [
            { label: t("复制链接"), run: () => copy(itemUrl(entry)) },
            ...(recommended
              ? [
                  {
                    label: t("忽略建议"),
                    separator: true,
                    run: () =>
                      void perform(async () => {
                        await change({ action: "ignore", id: entry.id, enabled: true });
                      }),
                  },
                ]
              : []),
            {
              label: t("添加到工作区建议"),
              separator: !recommended,
              run: workspaceIds.includes(key(entry))
                ? undefined
                : () =>
                    void perform(async () => {
                      const result = await recommendInWorkspace(entry.id);
                      setWorkspaceIds(result.workspace.map((id) => id.toLowerCase()));
                      refreshAll();
                    }),
            },
          ]
        : []),
      ...(!entry.builtin
        ? [
            {
              label: t("下载 VSIX"),
              separator: true,
              run: entry.vsix
                ? () => window.open(entry.vsix, "_blank", "noopener,noreferrer")
                : undefined,
            },
            {
              label: t("下载特定版本的 VSIX…"),
              run: () => openEntry(entry, local?.version ?? entry.version, "download"),
            },
          ]
        : []),
    ];
    return actions.flatMap(({ separator, ...item }) =>
      separator ? [{ label: "", separator: true }, item] : [item],
    );
  }

  function renderEntries(entries: Entry[], recommended = false) {
    return (
      <ul className="extension-list">
        {entries.map((entry) => {
          const local = installed.get(key(entry));
          const update = updates[key(entry)];
          const selected = activeDocument && key(activeDocument.entry) === key(entry);
          return (
            <li
              key={key(entry)}
              className="extension-row"
              data-selected={selected || undefined}
              data-context={context?.entry === entry || undefined}
              data-disabled={local?.enabled === false || undefined}
              onContextMenu={(event) => openContext(event, undefined, entry)}
              onKeyDown={(event) => openContext(event, undefined, entry)}
            >
              <button
                type="button"
                className="extension-entry"
                aria-label={entry.name}
                aria-current={selected || undefined}
                onClick={() => openEntry(entry, local?.version ?? entry.version)}
              >
                {recommended && (
                  <span className="extension-recommendation-mark" title={t("推荐")}>
                    <Icon name="star" size={11} />
                  </span>
                )}
                <ExtensionIcon key={entry.icon ?? entry.id} entry={entry} />
                <span className="extension-summary">
                  <span className="extension-title-line">
                    <span className="extension-title" title={entry.name}>
                      {entry.name}
                    </span>
                    {!!entry.downloads && !local && (
                      <span className="extension-stats" title={t("下载次数")}>
                        <Icon name="download" size={12} />
                        {downloadsFormat.format(entry.downloads)}
                        {entry.rating != null && (
                          <span className="extension-rating" title={t("评分")}>
                            <Icon name="star" size={12} />
                            {ratingFormat.format(entry.rating)}
                          </span>
                        )}
                      </span>
                    )}
                  </span>
                  <span className="extension-description" title={entry.description}>
                    {entry.description}
                  </span>
                  <span className="extension-publisher">
                    {entry.verified && <Icon name="tick" size={12} />}
                    <span title={entry.publisher}>{entry.publisher}</span>
                    {(local || entry.builtin) && (
                      <span className="extension-release">{local?.version ?? entry.version}</span>
                    )}
                  </span>
                </span>
              </button>
              {!entry.builtin && (
                <div className="extension-row-actions">
                  {(!local || update) && (
                    <button
                      type="button"
                      className="extension-primary"
                      disabled={pending}
                      onClick={() => {
                        if (update) void perform(() => install(update, update.version));
                        else {
                          openEntry(entry, entry.version);
                          void perform(async () => {
                            const candidate = await getDetail(entry, entry.version);
                            if (candidate.compatibility === "snippets")
                              await install(candidate, candidate.version);
                          });
                        }
                      }}
                    >
                      {t(local ? "更新" : "安装")}
                    </button>
                  )}
                  {local && (
                    <NavigatorButton
                      label={t("管理 {name}", { name: entry.name })}
                      icon="settings"
                      aria-haspopup="menu"
                      aria-expanded={context?.entry === entry}
                      onClick={(event) => {
                        const anchor = event.currentTarget;
                        const { left, bottom } = anchor.getBoundingClientRect();
                        setContext({ x: left, y: bottom, anchor, entry });
                      }}
                    />
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ul>
    );
  }

  const entries: Record<View, Entry[]> = {
    installed: installedEntries,
    enabled: [...builtins, ...inventory.installed.filter((entry) => entry.enabled)],
    disabled: inventory.installed.filter((entry) => !entry.enabled),
    recommended: recommendations.filter(
      (entry) => !installed.has(key(entry)) && !inventory.ignored.includes(key(entry)),
    ),
  };
  const visibleViews = filteredView
    ? [filteredView]
    : sectionKeys.filter((view) => shown.includes(view));

  function toggleSearchTool(tool: SearchTool) {
    const next = hiddenTools.includes(tool)
      ? hiddenTools.filter((item) => item !== tool)
      : [...hiddenTools, tool];
    setHiddenTools(next);
    try {
      localStorage.setItem(toolsKey, JSON.stringify(next));
    } catch {
      /* Optional preference storage. */
    }
  }

  const searchToolItems: NavigatorMenuItem[] = (["clear", "filter"] as const).map((tool) => ({
    label: t(searchTools[tool]),
    checked: !hiddenTools.includes(tool),
    checkbox: true,
    run: () => toggleSearchTool(tool),
  }));
  const searchPending = completedSearch !== `${sort}:${term}` && !searchError;
  const clearSearch = () => {
    setSearchText("");
    searchInput.current?.focus();
  };
  function editSearch(command: "undo" | "redo" | "cut" | "copy" | "paste" | "selectAll") {
    const input = searchInput.current;
    if (!input) return;
    input.focus({ preventScroll: true });
    if (command === "selectAll") input.select();
    else if (command === "paste")
      void perform(async () => {
        const text = await navigator.clipboard.readText();
        if (document.activeElement !== input) return;
        document.execCommand("insertText", false, text.replace(/[\r\n]+/g, " "));
      });
    else document.execCommand(command);
  }
  function searchEditItems(): NavigatorMenuItem[] {
    const input = searchInput.current;
    const hasSelection = input?.selectionStart !== input?.selectionEnd;
    const mac = /Mac|iPhone|iPad/.test(navigator.platform);
    const modifier = mac ? "⌘" : "Ctrl";
    return [
      { label: t("撤销"), shortcut: [modifier, "Z"], run: () => editSearch("undo") },
      {
        label: t("重做"),
        shortcut: mac ? ["⇧", modifier, "Z"] : [modifier, "Y"],
        run: () => editSearch("redo"),
      },
      {
        label: t("剪切"),
        separator: true,
        shortcut: [modifier, "X"],
        run: hasSelection ? () => editSearch("cut") : undefined,
      },
      {
        label: t("复制"),
        shortcut: [modifier, "C"],
        run: hasSelection ? () => editSearch("copy") : undefined,
      },
      { label: t("粘贴"), shortcut: [modifier, "V"], run: () => editSearch("paste") },
      {
        label: t("选择全部"),
        separator: true,
        shortcut: [modifier, "A"],
        run: () => editSearch("selectAll"),
      },
    ];
  }
  function contextItems(): NavigatorMenuItem[] {
    if (!context) return [];
    if (context.entry) return entryActions(context.entry);
    if (context.target === "input") return searchEditItems();
    if (context.target === "search") return searchToolItems;
    if (context.target) {
      const tool = context.target;
      return [
        {
          label: t("隐藏“{view}”", { view: t(searchTools[tool]) }),
          run: () => toggleSearchTool(tool),
        },
        ...searchToolItems.map((item, index) => (index ? item : { ...item, separator: true })),
      ];
    }
    if (!context.view) return [...viewItems, ...layoutItems];
    const view = context.view;
    return [
      {
        label: t("隐藏“{view}”", { view: t(views[view]) }),
        run: () => {
          toggleView(view);
          if (filteredView) setSearchText("");
        },
      },
      ...viewItems.map((item, index) => (index ? item : { ...item, separator: true })),
    ];
  }

  return {
    renderDocument: (request: ExtensionDocument, changeVersion: (version: string) => void) => (
      <ExtensionDetails
        request={request}
        load={getDetail}
        changeVersion={changeVersion}
        installed={installed.get(key(request.entry))}
        pending={pending}
        revision={revision}
        manage={entryActions(request.entry)}
        install={(entry) => void perform(() => install(entry, entry.version))}
      />
    ),
    actions: (
      <>
        <NavigatorButton
          label={t("刷新扩展")}
          icon="refresh"
          onClick={refreshAll}
          disabled={pending}
        />
        <NavigatorMenu label={t("扩展更多操作")} items={titleItems} />
      </>
    ),
    headingProps: {
      tabIndex: -1,
      onContextMenu: (event: React.MouseEvent<HTMLElement>) => openContext(event),
      onKeyDown: (event: React.KeyboardEvent<HTMLElement>) => openContext(event),
    },
    page: active ? (
      <NavigatorPage
        className="extension-activity"
        aria-label={t("扩展")}
        onContextMenu={(event) => openContext(event)}
      >
        <div
          className="extension-search"
          onContextMenu={(event) => openContext(event, undefined, undefined, "search")}
        >
          <input
            ref={searchInput}
            aria-label={t("在 Open VSX 中搜索扩展")}
            placeholder={t("在 Open VSX 中搜索扩展")}
            defaultValue={query}
            onContextMenu={(event) => openContext(event, undefined, undefined, "input")}
            onChange={(event) => {
              const value = event.currentTarget.value;
              window.clearTimeout(searchDraftTimer.current);
              searchDraftTimer.current = window.setTimeout(() => setQuery(value), 200);
            }}
            onKeyDown={(event) => {
              openContext(event, undefined, undefined, "input");
              if (event.key === "Escape" && event.currentTarget.value) {
                event.stopPropagation();
                setSearchText("");
              }
            }}
          />
          {!hiddenTools.includes("clear") && (
            <span
              className="extension-search-tool"
              onContextMenu={(event) => openContext(event, undefined, undefined, "clear")}
              onKeyDown={(event) => openContext(event, undefined, undefined, "clear")}
            >
              <NavigatorButton
                className="extension-clear-search"
                icon="clear-search"
                label={t(searchTools.clear)}
                onClick={clearSearch}
              />
            </span>
          )}
          {!hiddenTools.includes("filter") && (
            <span
              className="extension-search-tool"
              onContextMenu={(event) => openContext(event, undefined, undefined, "filter")}
              onKeyDown={(event) => openContext(event, undefined, undefined, "filter")}
            >
              <NavigatorMenu
                label={t(searchTools.filter)}
                triggerIcon="filter"
                items={[
                  { label: "Open VSX", run: () => setSearchText("@marketplace") },
                  ...sectionKeys.map((view) => ({
                    label: t(views[view]),
                    run: () => setSearchText(`@${view}`),
                  })),
                  {
                    label: t("按扩展 ID 搜索"),
                    separator: true,
                    run: () => {
                      setSearchText("@id:");
                      searchInput.current?.focus();
                    },
                  },
                  {
                    label: t("排序方式"),
                    submenu: (Object.keys(sorts) as (keyof typeof sorts)[]).map((order) => ({
                      label: t(sorts[order]),
                      checked: sort === order,
                      run: () => {
                        setSort(order);
                        if (!marketplace) setSearchText("@marketplace");
                      },
                    })),
                  },
                  ...(inventory.ignored.length
                    ? [
                        {
                          label: t("恢复已忽略的建议"),
                          run: () =>
                            void perform(async () => {
                              for (const id of inventory.ignored)
                                await change({ action: "ignore", id, enabled: false });
                            }),
                        },
                      ]
                    : []),
                ]}
              />
            </span>
          )}
        </div>
        <div className="extension-results" aria-busy={marketplace && searchPending}>
          {marketplace && searchPending && (
            <span className="extension-progress" aria-hidden="true" />
          )}
          {inventoryError && (
            <p className="core-error" role="alert">
              {inventoryError}
            </p>
          )}
          {marketplace ? (
            <>
              {searchError && (
                <p className="core-error" role="alert">
                  {searchError}
                </p>
              )}
              {!search?.items.length && searchPending && (
                <p className="extension-message" role="status">
                  {t("正在搜索 Open VSX…")}
                </p>
              )}
              {search?.items.length
                ? renderEntries([
                    ...new Map(search.items.map((entry) => [key(entry), entry])).values(),
                  ])
                : search &&
                  !searchPending &&
                  !searchError && (
                    <p className="extension-message">{t("Open VSX 中没有匹配的扩展。")}</p>
                  )}
              {search && !searchPending && search.items.length < Math.min(search.total, 5000) && (
                <button
                  type="button"
                  className="extension-more"
                  disabled={paging}
                  onClick={loadMore}
                >
                  {t(paging ? "读取中" : "加载更多")}
                </button>
              )}
            </>
          ) : (
            visibleViews.map((view) => {
              const text = filter?.[2]?.trim().toLowerCase();
              const matching = entries[view].filter(
                (entry) =>
                  !text ||
                  `${entry.id} ${entry.name} ${entry.description}`.toLowerCase().includes(text),
              );
              const open = !!filteredView || expanded.includes(view);
              return (
                <section className="extension-section" key={view}>
                  <button
                    type="button"
                    className="extension-section-toggle"
                    aria-expanded={open}
                    onClick={() =>
                      filteredView
                        ? setSearchText("")
                        : setExpanded((before) =>
                            open ? before.filter((item) => item !== view) : [...before, view],
                          )
                    }
                    onContextMenu={(event) => openContext(event, view)}
                    onKeyDown={(event) => openContext(event, view)}
                  >
                    <Icon name={open ? "chevron-down" : "chevron-right"} size={14} />
                    <strong>{t(views[view])}</strong>
                    <span className="extension-count">{matching.length}</span>
                  </button>
                  {open && (
                    <>
                      {view === "recommended" && recommendationError && (
                        <p className="core-error" role="alert">
                          {recommendationError}
                        </p>
                      )}
                      {matching.length ? (
                        renderEntries(matching, view === "recommended")
                      ) : (
                        <p className="extension-message">
                          {t(
                            view === "recommended" && loadingRecommendations
                              ? "正在读取推荐扩展…"
                              : "此视图中没有扩展。",
                          )}
                        </p>
                      )}
                    </>
                  )}
                </section>
              );
            })
          )}
        </div>
        {context && (
          <NavigatorMenu
            label={t("扩展操作")}
            point={context}
            items={contextItems()}
            onDismiss={() => {
              setContext(undefined);
              if (document.activeElement === document.body) {
                (context.anchor.isConnected ? context.anchor : searchInput.current)?.focus({
                  preventScroll: true,
                });
              }
            }}
          />
        )}
      </NavigatorPage>
    ) : null,
  };
}

function ExtensionDetails({
  request,
  load,
  changeVersion,
  installed,
  pending,
  revision,
  manage,
  install,
}: {
  request: ExtensionDocument;
  load: (entry: Entry, version: string) => Promise<Detail>;
  changeVersion: (version: string) => void;
  installed?: Installed;
  pending: boolean;
  revision: number;
  manage: NavigatorMenuItem[];
  install: (entry: Detail) => void;
}) {
  const { entry, version, intent } = request;
  const [detail, setDetail] = useState<Detail>();
  const [settled, setSettled] = useState<{
    id: string;
    version: string;
    revision: number;
    error?: string;
  }>();
  const versionField = useRef<HTMLSelectElement>(null);
  const sameExtension = detail && key(detail) === key(entry) ? detail : undefined;
  const current =
    settled?.id === key(entry) && settled.version === version && settled.revision === revision;
  const currentDetail = current && !settled.error ? sameExtension : undefined;

  useEffect(() => {
    if (entry.builtin) return;
    let current = true;
    const selection = { id: key(entry), version, revision };
    void load(entry, version).then(
      (result) => {
        if (!current) return;
        setDetail(result);
        setSettled(selection);
      },
      (error) => {
        if (current) setSettled({ ...selection, error: message(error) });
      },
    );
    return () => {
      current = false;
    };
  }, [entry, version, revision, load]);

  useEffect(() => {
    if (intent) versionField.current?.focus({ preventScroll: true });
  }, [intent, entry.id]);

  const directDownload = version === entry.version ? entry.vsix : undefined;
  const download = currentDetail?.vsix ?? directDownload;
  return (
    <section className="extension-details" aria-label={entry.name}>
      <header className="extension-detail-heading">
        <ExtensionIcon key={entry.icon ?? entry.id} entry={entry} />
        <div>
          <h1>{entry.name}</h1>
          <p className="extension-detail-meta">
            <span>{entry.publisher}</span>
            <span>{entry.id}</span>
            <span>
              {t("版本")} {installed?.version ?? entry.version}
            </span>
          </p>
          {entry.description && <p>{entry.description}</p>}
          <div className="extension-detail-actions">
            {!entry.builtin && (
              <>
                <label className="extension-version">
                  {t("扩展版本")}
                  <select
                    ref={versionField}
                    aria-label={t("扩展版本")}
                    value={version}
                    onChange={(event) => changeVersion(event.target.value)}
                  >
                    <option value="latest">{t("最新版本")}</option>
                    {sameExtension?.hasPreRelease && (
                      <option value="pre-release">{t("预发布版本")}</option>
                    )}
                    {[
                      ...new Set([
                        entry.version,
                        ...(sameExtension?.versions ?? []),
                        ...(sameExtension ? [sameExtension.version] : []),
                      ]),
                    ].map((option) => (
                      <option value={option} key={option}>
                        {option}
                      </option>
                    ))}
                  </select>
                </label>
                {currentDetail?.compatibility === "snippets" &&
                  currentDetail.version !== installed?.version && (
                    <button
                      className="extension-primary"
                      type="button"
                      disabled={pending}
                      onClick={() => install(currentDetail)}
                    >
                      {t(installed ? "安装此版本" : "安装")}
                    </button>
                  )}
                {download && (
                  <a href={download} rel="noreferrer" target="_blank">
                    {t("下载 VSIX")}
                  </a>
                )}
                <a href={itemUrl(entry)} rel="noreferrer" target="_blank">
                  {t("在 Open VSX 中查看")}
                </a>
              </>
            )}
            <NavigatorMenu
              label={t("管理 {name}", { name: entry.name })}
              items={manage}
              triggerIcon="settings"
            />
          </div>
        </div>
      </header>
      {entry.builtin ? (
        <p>{t("随宿主加载的 Tex Studio 专业插件")}</p>
      ) : current && settled.error ? (
        <p className="core-error" role="alert">
          {settled.error}
        </p>
      ) : (
        <p role="status" className="extension-detail-status">
          {t(currentDetail ? compatibility[currentDetail.compatibility] : "正在检查扩展兼容性…")}
        </p>
      )}
    </section>
  );
}

export { recommendationPath };
