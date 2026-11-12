import React, { useEffect, useRef, useState } from "react";
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
}: Pick<WorkbenchProps, "api" | "changeLayout" | "layout" | "notify"> & {
  active: boolean;
  plugins: WorkbenchProps["state"]["plugins"];
  recommendInWorkspace: (id: string) => Promise<Recommendations>;
}) {
  const [inventory, setInventory] = useState(initialInventory);
  const [inventoryError, setInventoryError] = useState("");
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<keyof typeof sorts>("relevance");
  const [shown, setShown] = useState<View[]>(["installed", "recommended"]);
  const [expanded, setExpanded] = useState<View[]>([]);
  const [revision, refresh] = useState(0);
  const [recommendations, setRecommendations] = useState<Entry[]>([]);
  const [workspaceIds, setWorkspaceIds] = useState<string[]>([]);
  const [recommendationError, setRecommendationError] = useState("");
  const [loadingRecommendations, setLoadingRecommendations] = useState(false);
  const [search, setSearch] = useState<SearchResult>();
  const [searchError, setSearchError] = useState("");
  const [paging, setPaging] = useState(false);
  const [selected, setSelected] = useState<{ entry: Entry; version: string }>();
  const [detail, setDetail] = useState<Detail>();
  const [detailError, setDetailError] = useState("");
  const [pending, setPending] = useState(false);
  const [updates, setUpdates] = useState<Record<string, Detail>>({});
  const [context, setContext] = useState<{
    x: number;
    y: number;
    anchor: HTMLElement;
    view?: View;
    entry?: Entry;
  }>();
  const operation = useRef(false);
  const lastAutoCheck = useRef(0);
  const searchSequence = useRef(0);
  const searchInput = useRef<HTMLInputElement>(null);
  const refreshAll = () => refresh((value) => value + 1);
  const installed = new Map(inventory.installed.map((entry) => [key(entry), entry]));
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
    active && (filteredView === "recommended" || shown.includes("recommended"));

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
  }, [api, active, revision]);

  const searchPage = (value: string, offset: number) =>
    api<SearchResult>(
      `${endpoint}/search?${new URLSearchParams({ q: value === "@marketplace" ? "" : value, sort, offset: String(offset) })}`,
    );
  const getDetail = (entry: Entry, version = "latest") =>
    api<Detail>(`${endpoint}/detail?${new URLSearchParams({ id: entry.id, version })}`);

  useEffect(() => {
    if (!showRecommendations) return;
    let current = true;
    setLoadingRecommendations(true);
    setRecommendationError("");
    void api<Recommendations>(`${endpoint}/recommendations`)
      .then(async (result) => {
        const requests = await Promise.allSettled(
          result.ids.map((id) => searchPage(`@id:${id}`, 0)),
        );
        if (!current) return;
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
    setSearch(undefined);
    setSearchError("");
    setPaging(false);
    if (!active || !marketplace) return;
    if (term.toLowerCase() === "@id:") {
      setSearch({ items: [], total: 0 });
      return;
    }
    const timer = window.setTimeout(() => {
      void searchPage(term, 0).then(
        (result) => {
          if (sequence === searchSequence.current) setSearch(result);
        },
        (error) => {
          if (sequence === searchSequence.current) setSearchError(message(error));
        },
      );
    }, 240);
    return () => {
      window.clearTimeout(timer);
      searchSequence.current += 1;
    };
  }, [api, active, marketplace, term, sort, revision]);

  useEffect(() => {
    setDetail(undefined);
    setDetailError("");
    if (!selected || selected.entry.builtin) return;
    let current = true;
    void getDetail(selected.entry, selected.version).then(
      (result) => {
        if (current) setDetail(result);
      },
      (error) => {
        if (current) setDetailError(message(error));
      },
    );
    return () => {
      current = false;
    };
  }, [api, selected, revision]);

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

  function setSearchText(value: string) {
    setQuery(value);
    setSelected(undefined);
  }

  function toggleView(view: View) {
    setShown((before) =>
      before.includes(view) ? before.filter((item) => item !== view) : [...before, view],
    );
  }

  function openEntry(entry: Entry, version = "latest") {
    setSelected({ entry, version });
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
  ) {
    if ("key" in event && event.key !== "ContextMenu" && !(event.shiftKey && event.key === "F10"))
      return;
    event.preventDefault();
    event.stopPropagation();
    const anchor = event.currentTarget;
    const rect = anchor.getBoundingClientRect();
    const [x, y] = "key" in event ? [rect.left, rect.bottom] : [event.clientX, event.clientY];
    setContext({ x, y, anchor, view, entry });
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
    return [
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
              run: () => openEntry(entry, local?.version ?? entry.version),
            },
            {
              label: t("下载 VSIX"),
              run: entry.vsix
                ? () => window.open(entry.vsix, "_blank", "noopener,noreferrer")
                : undefined,
            },
            {
              label: t("下载特定版本的 VSIX…"),
              run: () => openEntry(entry, local?.version ?? entry.version),
            },
          ]
        : []),
      { label: t("复制扩展 ID"), separator: !entry.builtin, run: () => copy(entry.id) },
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
    ];
  }

  function renderDetail(entry: Entry) {
    if (!selected || key(selected.entry) !== key(entry)) return null;
    if (entry.builtin)
      return <p className="extension-message">{t("随宿主加载的 Tex Studio 专业插件")}</p>;
    const local = installed.get(key(entry));
    return (
      <div className="extension-details">
        <p className="extension-id">{entry.id}</p>
        {entry.description && <p>{entry.description}</p>}
        {detailError ? (
          <p className="core-error" role="alert">
            {detailError}
          </p>
        ) : detail ? (
          <>
            <p role="status">{t(compatibility[detail.compatibility])}</p>
            <label className="extension-version">
              {t("版本")}
              <select
                aria-label={t("扩展版本")}
                value={selected.version}
                onChange={(event) => openEntry(entry, event.target.value)}
              >
                <option value="latest">{t("最新版本")}</option>
                {detail.hasPreRelease && <option value="pre-release">{t("预发布版本")}</option>}
                {[...new Set([detail.version, ...detail.versions])].map((version) => (
                  <option key={version} value={version}>
                    {version}
                  </option>
                ))}
              </select>
            </label>
            <div className="extension-detail-actions">
              {detail.compatibility === "snippets" && detail.version !== local?.version && (
                <button
                  className="extension-primary"
                  disabled={pending}
                  onClick={() => void perform(() => install(detail, detail.version))}
                >
                  {t(local ? "安装此版本" : "安装")}
                </button>
              )}
              {detail.vsix && (
                <a href={detail.vsix} rel="noreferrer" target="_blank">
                  {t("下载 VSIX")}
                </a>
              )}
              <a href={itemUrl(entry)} rel="noreferrer" target="_blank">
                {t("在 Open VSX 中查看")}
              </a>
            </div>
          </>
        ) : (
          <p role="status">{t("正在读取扩展详情…")}</p>
        )}
      </div>
    );
  }

  function renderEntries(entries: Entry[], recommended = false) {
    return (
      <ul className="extension-list">
        {entries.map((entry) => {
          const local = installed.get(key(entry));
          const update = updates[key(entry)];
          const open = selected && key(selected.entry) === key(entry);
          return (
            <li key={key(entry)}>
              <div className="extension-row" data-disabled={local?.enabled === false || undefined}>
                <button
                  type="button"
                  className="extension-entry"
                  aria-label={entry.name}
                  aria-expanded={!!open}
                  onClick={() =>
                    open ? setSelected(undefined) : openEntry(entry, local?.version ?? "latest")
                  }
                  onContextMenu={(event) => openContext(event, undefined, entry)}
                  onKeyDown={(event) => openContext(event, undefined, entry)}
                >
                  {recommended && (
                    <span className="extension-recommendation-mark" title={t("推荐")}>
                      <Icon name="star" size={11} />
                    </span>
                  )}
                  <ExtensionIcon key={entry.icon ?? entry.id} entry={entry} />
                  <span className="extension-summary">
                    <span className="extension-title" title={entry.name}>
                      {entry.name}
                    </span>
                    <span className="extension-description" title={entry.description}>
                      {entry.description}
                    </span>
                    <span className="extension-publisher">
                      {entry.verified && <Icon name="tick" size={12} />}
                      {entry.publisher}
                      {(local || entry.builtin) && (
                        <span className="extension-release">{local?.version ?? entry.version}</span>
                      )}
                    </span>
                  </span>
                </button>
                {!entry.builtin && (
                  <div className="extension-row-tools">
                    {!!entry.downloads && !local && (
                      <span className="extension-stats" title={t("下载次数")}>
                        <Icon name="download" size={12} />
                        {new Intl.NumberFormat(undefined, { notation: "compact" }).format(
                          entry.downloads,
                        )}
                        {entry.rating !== undefined && entry.rating !== null && (
                          <>
                            <Icon name="star" size={12} />
                            {entry.rating}
                          </>
                        )}
                      </span>
                    )}
                    <div className="extension-row-buttons">
                      {(!local || update) && (
                        <button
                          className="extension-primary"
                          disabled={pending}
                          onClick={() =>
                            void perform(async () => {
                              const candidate = update ?? (await getDetail(entry, entry.version));
                              if (candidate.compatibility === "snippets")
                                await install(candidate, candidate.version);
                              else openEntry(entry, candidate.version);
                            })
                          }
                        >
                          {t(local ? "更新" : "安装")}
                        </button>
                      )}
                      <NavigatorMenu
                        label={t("管理 {name}", { name: entry.name })}
                        triggerIcon={local ? "settings" : "more"}
                        items={entryActions(entry)}
                      />
                    </div>
                  </div>
                )}
              </div>
              {open && renderDetail(entry)}
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

  return {
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
    page: (
      <NavigatorPage
        className="extension-activity"
        aria-label={t("扩展")}
        hidden={!active}
        onContextMenu={(event) => {
          if (
            event.target === event.currentTarget ||
            (event.target as HTMLElement).classList.contains("extension-results")
          )
            openContext(event);
        }}
      >
        <div className="extension-search">
          <input
            ref={searchInput}
            aria-label={t("在 Open VSX 中搜索扩展")}
            placeholder={t("在 Open VSX 中搜索扩展")}
            value={query}
            onChange={(event) => setSearchText(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Escape" && query) {
                event.stopPropagation();
                setSearchText("");
              }
            }}
          />
          <NavigatorButton
            icon="clear-search"
            label={t("清除扩展搜索")}
            disabled={!query}
            onClick={() => {
              setSearchText("");
              searchInput.current?.focus();
            }}
          />
          <NavigatorMenu
            label={t("筛选扩展")}
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
        </div>
        <div className="extension-results">
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
              {!search && !searchError && (
                <p className="extension-message" role="status">
                  {t("正在搜索 Open VSX…")}
                </p>
              )}
              {search?.items.length
                ? renderEntries([
                    ...new Map(search.items.map((entry) => [key(entry), entry])).values(),
                  ])
                : search && <p className="extension-message">{t("Open VSX 中没有匹配的扩展。")}</p>}
              {search && search.items.length < Math.min(search.total, 5000) && (
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
            items={
              context.entry
                ? entryActions(context.entry)
                : context.view
                  ? [
                      {
                        label: t("隐藏“{view}”", { view: t(views[context.view]) }),
                        run: () => {
                          toggleView(context.view!);
                          if (filteredView) setSearchText("");
                        },
                      },
                      ...viewItems.map((item, i) =>
                        i === 0 ? { ...item, separator: true } : item,
                      ),
                    ]
                  : [...viewItems, ...layoutItems]
            }
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
    ),
  };
}

export { recommendationPath };
