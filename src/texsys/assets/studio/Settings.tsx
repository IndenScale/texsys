import React, { useId, useState, type ReactNode } from "react";
import { AiConnectionSettings } from "./AiConnectionSettings";
import { setLocale, t, useLocale, type Locale } from "./i18n";
import { titlebarGroups, type TitlebarGroup } from "./layout-preferences";
import type { WorkbenchProps } from "./sdk";
import {
  defaultEditorPreferences,
  defaultTerminalPreferences,
  fontSizeOptions,
  updateUserPreferences,
  useUserPreferences,
} from "./user-preferences";
import { Icon } from "./ui";
import "./settings.css";

const categories = [
  { id: "general", label: "常规", icon: "settings" },
  { id: "workbench", label: "工作台", icon: "grid" },
  { id: "editor", label: "编辑器", icon: "code" },
  { id: "terminal", label: "终端", icon: "terminal" },
  { id: "ai", label: "AI 连接", icon: "sparkles" },
] as const;
type Category = (typeof categories)[number]["id"];

export type TitlebarSettings = {
  hidden: readonly TitlebarGroup[];
  toggle: (group: TitlebarGroup) => void;
  reset: () => void;
};

function SettingRow({
  label,
  description,
  children,
}: {
  label: string;
  description?: string;
  children: ReactNode;
}) {
  return (
    <label className="settings-card-row">
      <span className="settings-card-description">
        <span>{label}</span>
        {description && <small>{description}</small>}
      </span>
      {children}
    </label>
  );
}

function FontSize({
  label,
  value,
  change,
}: {
  label: string;
  value: number;
  change: (value: number) => void;
}) {
  return (
    <SettingRow label={label}>
      <select value={value} onChange={(event) => change(Number(event.target.value))}>
        {fontSizeOptions.map((size) => (
          <option key={size} value={size}>
            {size} px
          </option>
        ))}
      </select>
    </SettingRow>
  );
}

export function Settings({
  api,
  titlebar,
  hidden,
}: {
  api: WorkbenchProps["api"];
  titlebar: TitlebarSettings;
  hidden?: boolean;
}) {
  const locale = useLocale();
  const headingId = useId();
  const [category, setCategory] = useState<Category>("general");
  const [aiOpened, setAiOpened] = useState(false);
  const preferences = useUserPreferences();
  const current = categories.find((item) => item.id === category)!;
  return (
    <section className="settings-page" aria-labelledby={headingId} hidden={hidden}>
      <div className="settings-layout">
        <nav className="settings-navigation" aria-label={t("设置分类")}>
          <div className="settings-navigation-title">{t("设置")}</div>
          <div className="settings-categories">
            {categories.map(({ id, label, icon }) => (
              <button
                key={id}
                type="button"
                className="settings-category"
                aria-current={category === id ? "true" : undefined}
                onClick={() => {
                  setCategory(id);
                  if (id === "ai") setAiOpened(true);
                }}
              >
                <Icon name={icon} size={16} />
                {t(label)}
              </button>
            ))}
          </div>
        </nav>
        <div className="settings-content">
          <h1 id={headingId}>{t(current.label)}</h1>
          {category === "general" && (
            <div className="settings-card">
              <SettingRow
                label={t("显示语言")}
                description={t("立即应用，并在此浏览器中记住选择。")}
              >
                <select
                  value={locale}
                  onChange={(event) => setLocale(event.target.value as Locale)}
                >
                  <option value="zh-CN" lang="zh-CN">
                    简体中文
                  </option>
                  <option value="en" lang="en">
                    English
                  </option>
                </select>
              </SettingRow>
            </div>
          )}
          {category === "workbench" && (
            <>
              <div className="settings-card">
                <h2>{t("窗口标题栏")}</h2>
                {titlebarGroups.map(({ id, label }) => (
                  <SettingRow key={id} label={t(label)}>
                    <input
                      type="checkbox"
                      checked={!titlebar.hidden.includes(id)}
                      onChange={() => titlebar.toggle(id)}
                    />
                  </SettingRow>
                ))}
              </div>
              <button
                className="settings-button settings-reset"
                type="button"
                disabled={!titlebar.hidden.length}
                onClick={titlebar.reset}
              >
                {t("恢复默认值")}
              </button>
            </>
          )}
          {category === "editor" && (
            <>
              <div className="settings-card">
                <FontSize
                  label={t("编辑器字号")}
                  value={preferences.editorFontSize}
                  change={(editorFontSize) => updateUserPreferences({ editorFontSize })}
                />
                <SettingRow label={t("自动换行")}>
                  <select
                    value={preferences.editorWordWrap}
                    onChange={(event) =>
                      updateUserPreferences({
                        editorWordWrap: event.target.value as typeof preferences.editorWordWrap,
                      })
                    }
                  >
                    <option value="bounded">{t("按宽度换行，最多 110 列")}</option>
                    <option value="on">{t("按编辑器宽度换行")}</option>
                    <option value="off">{t("关闭换行")}</option>
                  </select>
                </SettingRow>
                <SettingRow label={t("显示行号")}>
                  <input
                    type="checkbox"
                    checked={preferences.editorLineNumbers}
                    onChange={(event) =>
                      updateUserPreferences({ editorLineNumbers: event.target.checked })
                    }
                  />
                </SettingRow>
                <SettingRow label={t("显示代码缩略图")}>
                  <input
                    type="checkbox"
                    checked={preferences.editorMinimap}
                    onChange={(event) =>
                      updateUserPreferences({ editorMinimap: event.target.checked })
                    }
                  />
                </SettingRow>
              </div>
              <button
                className="settings-button settings-reset"
                type="button"
                onClick={() => updateUserPreferences(defaultEditorPreferences)}
              >
                {t("恢复默认值")}
              </button>
            </>
          )}
          {category === "terminal" && (
            <>
              <div className="settings-card">
                <FontSize
                  label={t("终端字号")}
                  value={preferences.terminalFontSize}
                  change={(terminalFontSize) => updateUserPreferences({ terminalFontSize })}
                />
              </div>
              <button
                className="settings-button settings-reset"
                type="button"
                onClick={() => updateUserPreferences(defaultTerminalPreferences)}
              >
                {t("恢复默认值")}
              </button>
            </>
          )}
          {aiOpened && (
            <div hidden={category !== "ai"}>
              <AiConnectionSettings api={api} />
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
