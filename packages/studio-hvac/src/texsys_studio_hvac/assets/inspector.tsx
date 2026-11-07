import { t } from "../../../../../src/texsys/assets/studio/i18n";
import React, { useEffect, useRef, useState } from "react";
import { Empty, Icon } from "../../../../../src/texsys/assets/studio/ui";
import { parameterTitles } from "../../../../hvac-graphics/src/texsys_hvac_graphics/assets/graphics";
import type { Binding } from "../../../../hvac-graphics/src/texsys_hvac_graphics/assets/types";
import {
  bindingLabels,
  bindingValue,
  mediumNames,
  names,
  type Selection,
  type Source,
} from "./model";

export function Inspector({
  selection,
  source,
  sourcePath,
  revision,
  save,
  busy,
  onDirtyChange,
  onSelectInstance,
  onLocate,
}: {
  selection?: Selection;
  source: Source;
  sourcePath: string;
  revision: string;
  save: (operation: Record<string, unknown>, revision: string) => Promise<void>;
  busy: boolean;
  onDirtyChange: (dirty: boolean) => void;
  onSelectInstance: (name: string) => void;
  onLocate: (view: "design" | "source") => void;
}) {
  const [tab, setTab] = useState("parameters");
  const [parameter, setParameter] = useState("");
  const [binding, setBinding] = useState<Binding>({ unit: "1", state: "assumed", source: "" });
  const [base, setBase] = useState(revision);
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState("");
  const pending = useRef(false);
  const instance =
    selection?.kind === "instance"
      ? source.components[source.root]?.instances?.[selection.id]
      : undefined;
  const type = selection?.kind === "type" ? selection.id : instance?.component;
  const definition = type ? source.components[type] : undefined;
  const params = { ...definition?.behavior?.parameters, ...instance?.parameters };
  const isType = selection?.kind === "type";
  const name = selection?.id;
  useEffect(() => {
    onDirtyChange(dirty);
  }, [dirty, onDirtyChange]);
  useEffect(() => {
    setParameter("");
    setDirty(false);
    setError("");
    setTab("parameters");
  }, [name, isType]);
  useEffect(() => {
    if (dirty || !parameter) return;
    setBinding({ ...params[parameter] });
    setBase(revision);
  }, [parameter, name, revision, dirty]);
  useEffect(() => {
    const handler = (event: BeforeUnloadEvent) => {
      if (dirty) {
        event.preventDefault();
        event.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [dirty]);
  const setField = (key: keyof Binding, value: string | number | undefined) => {
    setBinding((previous) => ({ ...previous, [key]: value }));
    setDirty(true);
    setError("");
  };
  const submit = async (reset = false) => {
    if (pending.current || isType || !name) return;
    pending.current = true;
    setError("");
    try {
      await save(
        {
          kind: reset ? "reset-parameter" : "set-parameter",
          instance: name,
          parameter,
          ...(reset
            ? {}
            : {
                binding: ["known", "assumed"].includes(binding.state)
                  ? binding
                  : { unit: binding.unit, state: binding.state, source: binding.source },
              }),
        },
        base,
      );
      setDirty(false);
      setParameter("");
    } catch (error) {
      setError(error instanceof Error ? error.message : String(error));
    } finally {
      pending.current = false;
    }
  };
  return (
    <aside className="hvac-inspector" aria-label={t("对象属性")}>
      <header className="inspector-heading">
        <strong>{isType ? t("器件类型") : t("实例属性")}</strong>
      </header>
      {!name || !definition ? (
        <Empty title={t("选择一个对象")}>{t("从系统浏览器、图面或器件库选择对象。")}</Empty>
      ) : (
        <>
          <div className="object-identity">
            <span className="equipment-avatar">
              <Icon name={type?.includes("Pipe") ? "system" : "cube"} size={24} />
            </span>
            <div>
              <h2>{isType ? (t(names[type!]) ?? type) : name}</h2>
              <p>{isType ? type : (t(names[type!]) ?? type)}</p>
            </div>
            <span className="tag neutral">{isType ? t("类型") : t("实例")}</span>
          </div>
          {isType ? (
            <p className="inspector-context">{t("默认定义 · 只读。选择下方实例可修改其参数。")}</p>
          ) : (
            <div className="inspector-shortcuts">
              <button onClick={() => onLocate("design")}>
                <Icon name="fit" size={14} />
                {t("图中定位")}
              </button>
              <button onClick={() => onLocate("source")}>
                <Icon name="code" size={14} />
                {t("查看源数据")}
              </button>
            </div>
          )}
          <div className="inspector-tabs" role="tablist" aria-label={t("对象信息")}>
            {[
              ["parameters", isType ? t("默认参数") : t("参数")],
              ["ports", t("接口")],
              ["basis", t("依据")],
            ].map(([id, title]) => (
              <button
                key={id}
                role="tab"
                aria-selected={tab === id}
                className={tab === id ? "active" : ""}
                onClick={() => setTab(id)}
              >
                {title}
              </button>
            ))}
          </div>
          <div className="inspector-scroll">
            {tab === "parameters" &&
              (parameter && !isType ? (
                <form
                  className="parameter-editor"
                  onSubmit={(event) => {
                    event.preventDefault();
                    void submit();
                  }}
                >
                  <button
                    type="button"
                    className="text-button"
                    disabled={busy}
                    onClick={() => {
                      setParameter("");
                      setDirty(false);
                      setError("");
                    }}
                  >
                    ‹ {dirty ? t("放弃修改，返回参数") : t("返回参数列表")}
                  </button>
                  <h3>{t(parameterTitles[parameter]) ?? parameter}</h3>
                  <p className="field-help">
                    {t("当前值")}
                    {bindingValue(params[parameter], parameter)} ·{" "}
                    {instance?.parameters?.[parameter] ? t("实例覆盖") : t("继承自器件")}
                  </p>
                  <fieldset disabled={busy}>
                    <label>
                      {parameter === "efficiency" && binding.unit === "1"
                        ? t("数值（0–1）")
                        : t("数值")}
                      {parameter === "check_valve" && binding.unit === "1" ? (
                        <select
                          aria-label={t("参数数值")}
                          value={binding.value ?? ""}
                          disabled={!["known", "assumed"].includes(binding.state)}
                          onChange={(event) => setField("value", Number(event.target.value))}
                        >
                          <option value="" disabled>
                            {t("请选择")}
                          </option>
                          <option value="1">{t("有")}</option>
                          <option value="0">{t("无")}</option>
                        </select>
                      ) : (
                        <input
                          autoFocus
                          aria-label={t("参数数值")}
                          type="number"
                          step="any"
                          required={["known", "assumed"].includes(binding.state)}
                          disabled={!["known", "assumed"].includes(binding.state)}
                          value={binding.value ?? ""}
                          onChange={(event) =>
                            setField(
                              "value",
                              event.target.value === "" ? undefined : Number(event.target.value),
                            )
                          }
                        />
                      )}
                    </label>
                    <div className="field-pair">
                      <label>
                        {t("单位")}
                        <input
                          aria-label={t("参数单位")}
                          required
                          value={binding.unit}
                          onChange={(event) => setField("unit", event.target.value)}
                        />
                      </label>
                      <label>
                        {t("数据状态")}
                        <select
                          aria-label={t("参数状态")}
                          value={binding.state}
                          onChange={(event) => setField("state", event.target.value)}
                        >
                          {[
                            "known",
                            "assumed",
                            "unknown",
                            "missing",
                            "not-applicable",
                            "failed",
                          ].map((state) => (
                            <option key={state} value={state}>
                              {t(bindingLabels[state])}
                            </option>
                          ))}
                        </select>
                      </label>
                    </div>
                    <label>
                      {t("依据与来源")}
                      <textarea
                        aria-label={t("参数依据")}
                        required
                        rows={4}
                        value={binding.source}
                        onChange={(event) => setField("source", event.target.value)}
                      />
                    </label>
                  </fieldset>
                  {dirty && (
                    <div className="edit-impact">
                      {t("保存为")} {name}
                      {t("的实例覆盖。已有图纸、检查和仿真结果将需要更新。")}
                    </div>
                  )}
                  {dirty && base !== revision && (
                    <p className="warning-text" role="alert">
                      {t("源文件已有新修订。当前草稿已保留；保存时会检查冲突。")}
                    </p>
                  )}
                  {error && (
                    <p className="error-text" role="alert">
                      {error}
                    </p>
                  )}
                  <div className="editor-actions">
                    <button type="submit" className="primary-button" disabled={busy || !dirty}>
                      <Icon name="save" size={15} />
                      {busy ? t("保存中…") : t("保存参数")}
                    </button>
                    {instance?.parameters?.[parameter] && (
                      <button
                        type="button"
                        className="small-button"
                        disabled={busy}
                        onClick={() => void submit(true)}
                      >
                        {t("恢复继承")}
                      </button>
                    )}
                  </div>
                </form>
              ) : (
                <>
                  <div className="inspector-section-heading">
                    <h3>{isType ? t("类型默认值") : t("工程参数")}</h3>
                    <span>
                      {Object.keys(params).length}
                      {t("项")}
                    </span>
                  </div>
                  <div className="parameter-list">
                    {Object.entries(params).map(([key, item]) => (
                      <button
                        key={key}
                        disabled={isType}
                        className="parameter-row"
                        onClick={() => {
                          setParameter(key);
                          setBinding({ ...item });
                          setBase(revision);
                          setError("");
                        }}
                        aria-label={`${t(parameterTitles[key]) ?? key} ${bindingValue(item, key)} ${t(bindingLabels[item.state])}`}
                      >
                        <span className="parameter-title">
                          {t(parameterTitles[key]) ?? key}
                          <small>
                            {instance?.parameters?.[key]
                              ? t("实例覆盖")
                              : isType
                                ? t("类型默认")
                                : t("继承")}
                          </small>
                        </span>
                        <span className="parameter-value">
                          <strong>{bindingValue(item, key)}</strong>
                          <em className={`binding-state ${item.state}`}>
                            {t(bindingLabels[item.state]) ?? item.state}
                          </em>
                        </span>
                      </button>
                    ))}
                  </div>
                  {!Object.keys(params).length && (
                    <p className="section-note">{t("此对象尚未定义行为参数。")}</p>
                  )}
                  {isType && (
                    <section className="type-instances">
                      <h3>{t("使用此类型的实例")}</h3>
                      {Object.entries(source.components[source.root]?.instances ?? {})
                        .filter(([, item]) => item.component === type)
                        .map(([id]) => (
                          <button key={id} onClick={() => onSelectInstance(id)}>
                            <Icon name="cube" size={15} />
                            <span>{id}</span>
                            <Icon name="arrow" size={14} />
                          </button>
                        ))}
                      {!Object.values(source.components[source.root]?.instances ?? {}).some(
                        (item) => item.component === type,
                      ) && <p>{t("当前系统没有此类型的实例。")}</p>}
                    </section>
                  )}
                </>
              ))}
            {tab === "ports" && (
              <>
                <div className="inspector-section-heading">
                  <h3>{t("接口与介质")}</h3>
                  <span>
                    {Object.keys(definition.ports ?? {}).length}
                    {t("个")}
                  </span>
                </div>
                <div className="port-detail-list">
                  {Object.entries(definition.ports ?? {}).map(([key, port]) => (
                    <div key={key}>
                      <span className={`medium-dot ${port.kind}`} />
                      <strong>{key}</strong>
                      <span>{t(mediumNames[port.kind]) ?? port.kind}</span>
                      <small>{port.direction === "inout" ? t("双向") : port.direction}</small>
                    </div>
                  ))}
                </div>
                <p className="section-note">{t("连接关系由系统模型维护。")}</p>
              </>
            )}
            {tab === "basis" && (
              <div className="basis-details">
                <h3>{t("模型来源")}</h3>
                <code>{sourcePath}</code>
                <p>{isType ? type : `${type} → ${name}`}</p>
                <h3>{t("行为定义")}</h3>
                <code>{definition.behavior?.reference ?? t("未绑定行为")}</code>
                <p>{definition.behavior?.source}</p>
                <h3>{t("参数依据")}</h3>
                {Object.entries(params).map(([key, item]) => (
                  <div key={key}>
                    <strong>{t(parameterTitles[key]) ?? key}</strong>
                    <p>{item.source || t("未提供")}</p>
                  </div>
                ))}
              </div>
            )}
          </div>
          <footer className="inspector-footer">
            {dirty ? (
              <button className="text-button" onClick={() => setTab("parameters")}>
                <span className="draft-dot" />
                {t("未保存参数 · 返回修改")}
              </button>
            ) : (
              <>
                <Icon name={isType ? "cube" : "check"} size={14} />
                <span>{isType ? t("类型定义只读") : t("参数已保存")}</span>
              </>
            )}
          </footer>
        </>
      )}
    </aside>
  );
}
