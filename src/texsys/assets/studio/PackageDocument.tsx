import React, { useCallback, useEffect, useRef, useState } from "react";
import { EditorActionButton } from "./EditorActionButton";
import { t } from "./i18n";
import { Icon } from "./ui";
import type { WorkbenchProps } from "./sdk";
import {
  packageKey,
  packageMessages,
  packageSelectionAction,
  matchesInstalledPackage,
  type PackagePlan,
  type PackageRecord,
  type PackageRequest,
  type PackageState,
} from "./package-types";
import "./package-manager.css";

const stateLabels: Record<string, string> = {
  synced: "已同步",
  installed: "已同步",
  missing: "尚未同步",
  changed: "内容已变化",
  drifted: "内容已变化",
  invalid: "需要处理",
  ready: "已同步",
  "not-synced": "尚未同步",
  "asset-only": "仅模型与资产",
  unavailable: "运行依赖未就绪",
  incompatible: "运行环境不兼容",
  registry: "包仓库",
  path: "本地开发目录",
};

function stateLabel(value?: string) {
  return value ? t(stateLabels[value] ?? value) : t("未检查");
}

function Action({
  children,
  reason,
  onClick,
  primary = false,
}: {
  children: React.ReactNode;
  reason?: string;
  onClick: () => void;
  primary?: boolean;
}) {
  return (
    <button
      type="button"
      className={`package-action${primary ? " package-action--primary" : ""}`}
      disabled={!!reason}
      title={reason}
      onClick={onClick}
    >
      {children}
    </button>
  );
}

function Notes({ value, empty }: { value: unknown; empty: string }) {
  const messages = packageMessages(value);
  return messages.length ? (
    <ul className="package-notes">
      {messages.map((message, index) => (
        <li key={index}>{message}</li>
      ))}
    </ul>
  ) : (
    <p className="package-muted">{empty}</p>
  );
}

function Identity({ record }: { record?: Partial<PackageRecord> | null }) {
  if (!record) return <p className="package-muted">{t("无")}</p>;
  return (
    <dl>
      <dt>{t("版本")}</dt>
      <dd>{record.version}</dd>
      <dt>{t("包来源")}</dt>
      <dd>{record.registry || record.path || stateLabel(record.source)}</dd>
      <dt>{t("内容摘要")}</dt>
      <dd>{record.digest}</dd>
    </dl>
  );
}

export function PackageDocument({
  api,
  refresh,
  revision,
  hasUnsaved,
  openFile,
}: {
  api: WorkbenchProps["api"];
  refresh: WorkbenchProps["refresh"];
  revision: string;
  hasUnsaved: boolean;
  openFile: (path: string) => void;
}) {
  const [data, setData] = useState<PackageState>();
  const [tab, setTab] = useState<"installed" | "browse">("installed");
  const [query, setQuery] = useState("");
  const [registry, setRegistry] = useState("");
  const [catalog, setCatalog] = useState<PackageRecord[]>([]);
  const [catalogRegistry, setCatalogRegistry] = useState("");
  const [selected, setSelected] = useState<PackageRecord>();
  const [detail, setDetail] = useState<PackageRecord>();
  const [version, setVersion] = useState("");
  const [plan, setPlan] = useState<PackagePlan>();
  const [request, setRequest] = useState<PackageRequest>();
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [loadedRevision, setLoadedRevision] = useState("");
  const [testResult, setTestResult] = useState<{
    status: string;
    elapsed_s?: number;
    stdout?: string;
    stderr?: string;
    engineering_acceptance?: string;
  }>();
  const generation = useRef(0);
  const selectionGeneration = useRef(0);
  const load = useCallback(async () => {
    const current = ++generation.current;
    setLoadedRevision("");
    const result = await api<PackageState>("/api/packages");
    if (current !== generation.current) return;
    setData(result);
    setLoadedRevision(revision);
    return result;
  }, [api, revision]);
  useEffect(() => {
    let active = true;
    setPlan(undefined);
    setRequest(undefined);
    setSelected(undefined);
    setDetail(undefined);
    setTestResult(undefined);
    selectionGeneration.current++;
    void load().catch((failure: unknown) => {
      if (active) setError(String(failure));
    });
    return () => {
      active = false;
      generation.current++;
    };
  }, [load]);

  const mutationReason = busy
    ? t("正在执行包操作")
    : hasUnsaved
      ? t("请先保存工作区修改，再更改依赖。")
      : loadedRevision !== revision
        ? t("项目已变化，正在刷新包状态。")
        : undefined;
  const selectedInstalled = data?.packages.find((item) => item.name === selected?.name);
  const canUpdate = packageSelectionAction(selectedInstalled) === "update";
  const matchingInstalled = matchesInstalledPackage(selected, selectedInstalled);
  const runtime = selectedInstalled?.runtime_status as
    { status?: string; message?: string; requirements?: unknown } | undefined;
  const runtimeLabel =
    runtime?.status === "ready"
      ? t("运行依赖已就绪")
      : runtime?.status === "missing"
        ? t("运行依赖未就绪")
        : stateLabel(runtime?.status);
  const rows = (tab === "installed" ? (data?.packages ?? []) : catalog).filter((item) =>
    `${item.name} ${item.version} ${item.description ?? ""}`
      .toLocaleLowerCase()
      .includes(query.trim().toLocaleLowerCase()),
  );

  async function perform(name: string, action: () => Promise<void>) {
    setBusy(name);
    setError("");
    setMessage("");
    try {
      await action();
    } catch (failure) {
      setError(String(failure));
    } finally {
      setBusy("");
    }
  }
  function clearPlan() {
    setPlan(undefined);
    setRequest(undefined);
  }
  function chooseTab(next: "installed" | "browse") {
    setTab(next);
    setSelected(undefined);
    setDetail(undefined);
    setTestResult(undefined);
    clearPlan();
    selectionGeneration.current++;
  }
  async function choose(item: PackageRecord) {
    const current = ++selectionGeneration.current;
    setSelected(item);
    setDetail(item);
    setVersion(item.version);
    setTestResult(undefined);
    clearPlan();
    setError("");
    if (item.source === "registry" || item.registry || tab === "browse") {
      try {
        const result = await api<PackageRecord>("/api/packages", {
          action: "details",
          registry: item.registry || catalogRegistry,
          name: item.name,
          version: item.version,
        });
        if (current === selectionGeneration.current)
          setDetail({ ...item, ...result, runtime_status: item.runtime_status });
      } catch (failure) {
        if (current === selectionGeneration.current) setError(String(failure));
      }
    }
  }
  async function browse() {
    clearPlan();
    setSelected(undefined);
    setDetail(undefined);
    selectionGeneration.current++;
    await perform("browse", async () => {
      const result = await api<{ registry: string; packages: PackageRecord[] }>("/api/packages", {
        action: "browse",
        registry: registry.trim(),
      });
      setCatalog(
        result.packages.map((item) => ({ ...item, source: "registry", registry: result.registry })),
      );
      setCatalogRegistry(result.registry);
      setRegistry(result.registry);
    });
  }
  async function preview(action: PackageRequest["action"]) {
    if (!selected || !data || mutationReason) return;
    clearPlan();
    await perform("plan", async () => {
      const change: PackageRequest = { action, name: selected.name, revision: data.revision };
      if (action !== "remove") {
        change.version = version.trim();
        change.registry = selected.registry || catalogRegistry;
      }
      const result = await api<PackagePlan>("/api/packages", { action: "plan", request: change });
      setPlan(result);
      setRequest(change);
    });
  }
  async function apply() {
    if (!plan || !request || mutationReason) return;
    await perform("apply", async () => {
      await api("/api/packages", { action: "apply", request, plan: plan.plan });
      clearPlan();
      setSelected(undefined);
      setDetail(undefined);
      selectionGeneration.current++;
      await refresh();
      await load();
      setMessage(t("依赖变更已应用；运行工程检查以复核受影响的结论。"));
    });
  }
  async function synchronize() {
    if (mutationReason) return;
    clearPlan();
    await perform("sync", async () => {
      await api("/api/packages", { action: "sync", revision: data?.revision });
      await refresh();
      await load();
      setSelected(undefined);
      setDetail(undefined);
      setMessage(t("已按锁文件同步。版本未自动升级。"));
    });
  }
  async function runTests() {
    if (!matchingInstalled || mutationReason || !selectedInstalled?.tests?.length) return;
    await perform("test", async () => {
      setTestResult(undefined);
      const result = await api<NonNullable<typeof testResult>>("/api/packages", {
        action: "test",
        name: selectedInstalled.name,
      });
      setTestResult(result);
    });
  }

  return (
    <section className="package-document" aria-label={t("包管理")}>
      <header className="package-header">
        <div>
          <h1>{t("包管理")}</h1>
          <p>{t("管理工程能力的来源、版本与项目依赖。")}</p>
        </div>
        <div className="package-actions">
          <EditorActionButton
            icon="files"
            label={t("打开项目清单")}
            onClick={() => openFile("texsys.toml")}
          />
          <EditorActionButton
            icon="refresh"
            label={busy ? t("正在执行包操作") : t("刷新包状态")}
            disabled={!!busy}
            onClick={() =>
              void perform("refresh", async () => {
                clearPlan();
                await load();
              })
            }
          />
          <Action
            reason={mutationReason || (!data ? t("正在读取包状态") : undefined)}
            onClick={() => void synchronize()}
          >
            {busy === "sync" ? t("同步中…") : t("按锁同步")}
          </Action>
        </div>
      </header>
      {error && (
        <div className="package-error" role="alert">
          {error}
        </div>
      )}
      {message && (
        <p className="package-message" role="status">
          {message}
        </p>
      )}
      {!!data?.diagnostics.length && (
        <div className="package-error">
          <Notes value={data.diagnostics} empty="" />
        </div>
      )}
      <div className="package-layout">
        <aside className="package-browser" aria-label={t("工程包列表")}>
          <div className="package-tabs">
            <button
              type="button"
              aria-pressed={tab === "installed"}
              disabled={!!busy}
              title={busy ? t("正在执行包操作") : undefined}
              onClick={() => chooseTab("installed")}
            >
              {t("项目依赖")}
            </button>
            <button
              type="button"
              aria-pressed={tab === "browse"}
              disabled={!!busy}
              title={busy ? t("正在执行包操作") : undefined}
              onClick={() => chooseTab("browse")}
            >
              {t("浏览仓库")}
            </button>
          </div>
          {tab === "browse" && (
            <form
              className="package-registry"
              onSubmit={(event) => {
                event.preventDefault();
                void browse();
              }}
            >
              <label>
                {t("包仓库目录")}
                <input
                  value={registry}
                  onChange={(event) => {
                    setRegistry(event.target.value);
                    clearPlan();
                  }}
                  placeholder={t("本机或共享目录路径")}
                  disabled={!!busy}
                />
              </label>
              <Action
                reason={
                  busy
                    ? t("正在执行包操作")
                    : !registry.trim()
                      ? t("请输入包仓库目录。")
                      : undefined
                }
                onClick={() => void browse()}
              >
                {t("读取仓库")}
              </Action>
            </form>
          )}
          <label className="package-search">
            <span>{t("搜索工程包")}</span>
            <input type="search" value={query} onChange={(event) => setQuery(event.target.value)} />
          </label>
          <ul className="package-list">
            {rows.map((item) => (
              <li key={packageKey(item)}>
                <button
                  type="button"
                  className="package-row"
                  disabled={!!busy}
                  title={busy ? t("正在执行包操作") : undefined}
                  aria-pressed={selected ? packageKey(selected) === packageKey(item) : false}
                  onClick={() => void choose(item)}
                >
                  <span className="package-row-name">
                    <span>{item.name}</span>
                    <small>{item.version}</small>
                  </span>
                  <span className="package-row-status">
                    <small>
                      {tab === "installed"
                        ? t(item.direct ? "直接依赖" : "间接依赖")
                        : stateLabel(item.source)}
                    </small>
                    <small>{tab === "installed" ? stateLabel(item.state) : t("可用版本")}</small>
                  </span>
                </button>
              </li>
            ))}
          </ul>
          {!rows.length && (
            <p className="package-empty">
              {!data && tab === "installed"
                ? t("正在读取包状态")
                : query.trim()
                  ? t("没有匹配的工程包。")
                  : tab === "installed"
                    ? t("项目尚未声明依赖。浏览包仓库，选择一个能力包加入项目。")
                    : catalogRegistry
                      ? t("此仓库没有可用的包版本。")
                      : t("输入包仓库目录，查看可用的能力包和版本。")}
            </p>
          )}
        </aside>
        <div className="package-detail">
          {detail && selected ? (
            <>
              <h2>{detail.name}</h2>
              <p className="package-muted">{detail.description || t("此包未提供说明。")}</p>
              <Identity record={detail} />
              {selectedInstalled && (
                <dl>
                  <dt>{t("项目锁定版本")}</dt>
                  <dd>{selectedInstalled.version}</dd>
                  <dt>{t("同步状态")}</dt>
                  <dd>{stateLabel(selectedInstalled.state)}</dd>
                </dl>
              )}
              <h3>{t("提供的能力")}</h3>
              <Notes
                value={detail.capabilities}
                empty={t("此包未声明能力清单。可查看包说明与公开部件。")}
              />
              <h3>{t("适用范围")}</h3>
              <Notes value={detail.scope} empty={t("此包未声明适用范围。使用前需核对模型条件。")} />
              {packageMessages(detail.limitations).length > 0 && (
                <Notes value={detail.limitations} empty="" />
              )}
              <h3>{t("包依赖")}</h3>
              <Notes value={detail.dependencies} empty={t("没有声明其他工程包依赖。")} />
              <h3>{t("所选版本运行声明")}</h3>
              <Notes value={detail.runtime} empty={t("此版本未声明 Python 运行能力。")} />
              {runtime && (
                <>
                  <h3>{t("当前项目运行状态")}</h3>
                  <p className="package-muted">{runtimeLabel}</p>
                  {runtime.message && <p className="package-muted">{runtime.message}</p>}
                  <Notes value={runtime.requirements} empty="" />
                </>
              )}
              <h3>{t("包验证")}</h3>
              <Notes value={detail.tests} empty={t("此包未声明可运行的 Unit Test。")} />
              <Action
                reason={
                  mutationReason ||
                  (!matchingInstalled
                    ? t("先将所选版本加入项目并同步。")
                    : !selectedInstalled?.tests?.length
                      ? t("此包未声明可运行的 Unit Test。")
                      : undefined)
                }
                onClick={() => void runTests()}
              >
                {busy === "test" ? t("测试运行中…") : t("运行包测试")}
              </Action>
              {testResult && (
                <div className="package-test-result" role="status">
                  <p>
                    {t("包测试结果")}：
                    {testResult.status === "pass"
                      ? t("通过")
                      : testResult.status === "fail"
                        ? t("未通过")
                        : testResult.status}
                    {testResult.elapsed_s != null && ` · ${testResult.elapsed_s.toFixed(2)} s`}
                  </p>
                  <p className="package-muted">{t("此结果验证包内能力，不构成项目工程接受。")}</p>
                  <details>
                    <summary>{t("查看测试输出")}</summary>
                    <pre>
                      {[testResult.stdout, testResult.stderr]
                        .filter(Boolean)
                        .join("\n")
                        .slice(0, 12000)}
                    </pre>
                  </details>
                </div>
              )}
              {(selected.source === "registry" || selected.registry || tab === "browse") && (
                <>
                  <h3>{t("更改项目依赖")}</h3>
                  <label className="package-version">
                    {t("目标版本")}
                    <input
                      value={version}
                      disabled={!!busy}
                      onChange={(event) => {
                        setVersion(event.target.value);
                        clearPlan();
                      }}
                    />
                  </label>
                  <div className="package-actions">
                    <Action
                      primary
                      reason={
                        mutationReason || (!version.trim() ? t("请输入精确版本。") : undefined)
                      }
                      onClick={() => void preview(canUpdate ? "update" : "add")}
                    >
                      {t(canUpdate ? "预览版本更新" : "预览加入项目")}
                    </Action>
                    {selectedInstalled?.direct && (
                      <Action reason={mutationReason} onClick={() => void preview("remove")}>
                        {t("预览移除依赖")}
                      </Action>
                    )}
                  </div>
                </>
              )}
              {tab === "installed" && selected.source === "path" && (
                <>
                  <p className="package-muted">
                    {t("本地开发包由项目清单声明。浏览部件内容请使用部件库。")}
                  </p>
                  {selectedInstalled?.direct && (
                    <div className="package-actions">
                      <Action reason={mutationReason} onClick={() => void preview("remove")}>
                        {t("预览移除依赖")}
                      </Action>
                    </div>
                  )}
                </>
              )}
            </>
          ) : (
            <div className="package-empty">
              <Icon name="module" size={28} />
              <p>{t("选择工程包，查看能力、来源与版本。")}</p>
              <p>{t("包管理负责依赖安装；部件库负责选择和引用包内的模型。")}</p>
            </div>
          )}
          {plan && request && (
            <section className="package-plan" aria-label={t("依赖变更预览")}>
              <h3>{t("依赖变更预览")}</h3>
              {!plan.changes.length && <p className="package-muted">{t("依赖内容没有变化。")}</p>}
              {plan.changes.map((change) => (
                <div className="package-change" key={change.name}>
                  <h4>{change.name}</h4>
                  <div className="package-change-columns">
                    <div>
                      <p>{t("变更前")}</p>
                      <Identity record={change.before} />
                    </div>
                    <div>
                      <p>{t("变更后")}</p>
                      <Identity record={change.after} />
                    </div>
                  </div>
                </div>
              ))}
              <p className="package-muted">
                {t("应用后将更新清单与锁文件，并同步所选内容。既有工程结论需按新依赖复核。")}
              </p>
              <div className="package-actions">
                <Action primary reason={mutationReason} onClick={() => void apply()}>
                  {t("应用依赖变更")}
                </Action>
                <Action reason={busy ? t("正在执行包操作") : undefined} onClick={clearPlan}>
                  {t("取消预览")}
                </Action>
              </div>
            </section>
          )}
        </div>
      </div>
    </section>
  );
}
