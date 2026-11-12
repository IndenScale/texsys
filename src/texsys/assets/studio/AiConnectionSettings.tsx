import React, { useEffect, useId, useState, type FormEvent } from "react";
import { Select } from "./atomstudio/primitives";
import type { StudioAiMode, StudioAiSettings } from "./ai-settings";
import { t } from "./i18n";
import type { WorkbenchProps } from "./sdk";

type Connection = Pick<StudioAiSettings, "mode" | "baseUrl" | "model">;
type Notice = { kind: "saved" | "tested" } | { kind: "error"; message: string };
const blank: Connection = { mode: "disabled", baseUrl: "", model: "" };

function errorMessage(error: unknown) {
  return error instanceof Error && error.message ? error.message : t("连接操作失败，请重试。");
}

export function AiConnectionSettings({ api }: { api: WorkbenchProps["api"] }) {
  const credentialHintId = useId();
  const [saved, setSaved] = useState<StudioAiSettings>();
  const [draft, setDraft] = useState<Connection>(blank);
  const [key, setKey] = useState("");
  const [removeKey, setRemoveKey] = useState(false);
  const [busy, setBusy] = useState<"saving" | "testing">();
  const [notice, setNotice] = useState<Notice>();
  const [loadAttempt, setLoadAttempt] = useState(0);

  useEffect(() => {
    let current = true;
    void api<StudioAiSettings>("/api/ai/settings")
      .then((config) => {
        if (!current) return;
        setSaved(config);
        setDraft(config);
      })
      .catch((error: unknown) => {
        if (current) setNotice({ kind: "error", message: errorMessage(error) });
      });
    return () => {
      current = false;
    };
  }, [api, loadAttempt]);

  const enabled = draft.mode !== "disabled";
  const baseUrl = enabled ? draft.baseUrl.trim().replace(/\/+$/, "") : "";
  const model = enabled ? draft.model.trim() : "";
  const sameDestination = draft.mode === saved?.mode && baseUrl === saved.baseUrl;
  const hasStoredKey = sameDestination && saved.hasApiKey;
  const dirty = !sameDestination || model !== saved.model || Boolean(key.trim()) || removeKey;

  function update(patch: Partial<Connection>) {
    setDraft((previous) => ({ ...previous, ...patch }));
    setNotice(undefined);
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!dirty || busy) return;
    setBusy("saving");
    setNotice(undefined);
    try {
      const config = await api<StudioAiSettings>("/api/ai/settings", {
        mode: draft.mode,
        baseUrl,
        model,
        ...(enabled && key.trim() ? { apiKey: key.trim() } : {}),
        ...(enabled && removeKey ? { apiKey: "" } : {}),
      });
      setSaved(config);
      setDraft(config);
      setKey("");
      setRemoveKey(false);
      setNotice({ kind: "saved" });
      window.dispatchEvent(new Event("texsys:ai-settings-changed"));
    } catch (error) {
      setNotice({ kind: "error", message: errorMessage(error) });
    } finally {
      setBusy(undefined);
    }
  }

  async function testConnection() {
    if (dirty || busy || !enabled) return;
    setBusy("testing");
    setNotice(undefined);
    try {
      await api<{ ok: true }>("/api/ai/test", {});
      setNotice({ kind: "tested" });
    } catch (error) {
      setNotice({ kind: "error", message: errorMessage(error) });
    } finally {
      setBusy(undefined);
    }
  }

  if (!saved) {
    return (
      <div className="settings-card settings-connection-state">
        {notice?.kind === "error" ? (
          <>
            <p role="alert">{notice.message}</p>
            <button
              type="button"
              className="settings-button"
              onClick={() => {
                setNotice(undefined);
                setLoadAttempt((attempt) => attempt + 1);
              }}
            >
              {t("重试")}
            </button>
          </>
        ) : (
          <p role="status">{t("正在读取 AI 连接…")}</p>
        )}
      </div>
    );
  }

  return (
    <form onSubmit={(event) => void save(event)}>
      <fieldset className="settings-card settings-connection" disabled={Boolean(busy)}>
        <label className="settings-field">
          <span>{t("连接方式")}</span>
          <Select
            width="wide"
            value={draft.mode}
            onChange={(event) => {
              const mode = event.target.value as StudioAiMode;
              update(mode === saved.mode ? saved : { ...blank, mode });
              setKey("");
              setRemoveKey(false);
            }}
          >
            <option value="disabled">{t("禁用")}</option>
            <option value="byok">{t("BYOK · 自带模型密钥")}</option>
            <option value="byoa">{t("BYOA · 自带 Agent")}</option>
          </Select>
        </label>
        {enabled ? (
          <>
            <label className="settings-field">
              <span>{t(draft.mode === "byoa" ? "Agent 网关地址" : "模型服务地址")}</span>
              <input
                type="url"
                value={draft.baseUrl}
                autoComplete="off"
                placeholder={
                  draft.mode === "byoa" ? "http://127.0.0.1:8080/v1" : "https://api.example.com/v1"
                }
                onChange={(event) => update({ baseUrl: event.target.value })}
                spellCheck={false}
                required
              />
              <small>{t("填写兼容 Chat Completions 的服务根地址。")}</small>
            </label>
            <label className="settings-field">
              <span>{t(draft.mode === "byoa" ? "Agent 路由名称" : "模型名称")}</span>
              <input
                type="text"
                value={draft.model}
                autoComplete="off"
                onChange={(event) => update({ model: event.target.value })}
                spellCheck={false}
                required
              />
            </label>
            <div className="settings-field">
              <label>
                <span>{t(draft.mode === "byoa" ? "访问令牌（可选）" : "API 密钥")}</span>
                <input
                  type="password"
                  value={key}
                  autoComplete="new-password"
                  aria-describedby={credentialHintId}
                  onChange={(event) => {
                    setKey(event.target.value);
                    setRemoveKey(false);
                    setNotice(undefined);
                  }}
                />
              </label>
              <small id={credentialHintId}>
                {t(
                  hasStoredKey && removeKey
                    ? "保存后移除已存凭据。"
                    : hasStoredKey
                      ? "凭据已保存在本机。留空可保留现有凭据。"
                      : draft.mode === "byok"
                        ? "连接测试和生成提交说明需要 API 密钥。密钥保存在本机。"
                        : "凭据仅由本机服务保存。",
                )}
              </small>
              {hasStoredKey && (
                <button
                  className="settings-button settings-credential-action"
                  type="button"
                  onClick={() => {
                    setRemoveKey(!removeKey);
                    setKey("");
                    setNotice(undefined);
                  }}
                >
                  {t(removeKey ? "保留凭据" : "移除已存凭据")}
                </button>
              )}
            </div>
          </>
        ) : (
          saved.mode !== "disabled" && <p>{t("保存关闭后会清除已保存的连接和凭据。")}</p>
        )}
        <p>{t("生成提交说明时会把暂存差异发送给已配置的连接。测试连接只发送测试内容。")}</p>
      </fieldset>
      <div className="settings-actions">
        <button type="submit" className="core-primary" disabled={Boolean(busy) || !dirty}>
          {t(busy === "saving" ? "正在保存…" : "保存设置")}
        </button>
        <button
          type="button"
          className="settings-button"
          disabled={Boolean(busy) || !enabled || dirty || (draft.mode === "byok" && !hasStoredKey)}
          onClick={() => void testConnection()}
        >
          {t(busy === "testing" ? "正在测试…" : "测试连接")}
        </button>
        {notice ? (
          <span className="settings-status" role={notice.kind === "error" ? "alert" : "status"}>
            {notice.kind === "error"
              ? notice.message
              : t(notice.kind === "tested" ? "连接测试通过。" : "已保存设置。")}
          </span>
        ) : (
          enabled && dirty && <span className="settings-status">{t("保存后可测试连接。")}</span>
        )}
      </div>
    </form>
  );
}
