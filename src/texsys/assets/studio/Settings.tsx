import React, { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { AiConnectionSettings } from "./AiConnectionSettings";
import { defaultLocale, setLocale, t, translate, useLocale } from "./i18n";
import { titlebarGroups, type TitlebarGroup } from "./layout-preferences";
import type { WorkbenchProps } from "./sdk";
import {
  defaultEditorPreferences,
  defaultTerminalPreferences,
  defaultUserPreferences,
  fontSizeOptions,
  updateUserPreferences,
  useUserPreferences,
  type UserPreferences,
} from "./user-preferences";
import { Icon } from "./ui";
import "./settings.css";

const categories = {
  common: { label: "常用设置", icon: "check" },
  general: { label: "常规", icon: "settings" },
  workbench: { label: "工作台", icon: "grid" },
  files: { label: "文件", icon: "file" },
  editor: { label: "编辑器", icon: "code" },
  terminal: { label: "终端", icon: "terminal" },
  ai: { label: "AI 连接", icon: "sparkles" },
} as const;
type Category = keyof typeof categories;

export type TitlebarSettings = {
  hidden: readonly TitlebarGroup[];
  toggle: (group: TitlebarGroup) => void;
  reset: () => void;
};

type SettingInfo = {
  category: Exclude<Category, "common" | "ai">;
  label: string;
  description: string;
  keywords: string;
  common?: boolean;
};

type Control = (props: { id: string; "aria-describedby": string }) => ReactNode;
type Setting = SettingInfo & {
  id: string;
  modified: boolean;
  reset: () => void;
  control: Control;
};

const checkbox =
  (value: boolean, change: (value: boolean) => void): Control =>
  (props) => (
    <input
      {...props}
      type="checkbox"
      checked={value}
      onChange={(event) => change(event.target.checked)}
    />
  );

function choices<T extends string | number>(options: readonly { value: T; label: string }[]) {
  return (value: T, change: (value: T) => void): Control =>
    (props) => (
      <select
        {...props}
        value={value}
        onChange={(event) => {
          const option = options.find(({ value }) => String(value) === event.target.value);
          if (option) change(option.value);
        }}
      >
        {options.map(({ value, label }) => (
          <option key={value} value={value}>
            {t(label)}
          </option>
        ))}
      </select>
    );
}

const fontSize = choices(fontSizeOptions.map((value) => ({ value, label: `${value} px` })));
const wordWrap = choices([
  { value: "bounded", label: "按宽度换行，最多 110 列" },
  { value: "on", label: "按编辑器宽度换行" },
  { value: "off", label: "关闭换行" },
] as const);
const displayLanguage = choices([
  { value: "zh-CN", label: "简体中文" },
  { value: "en", label: "English" },
] as const);

const titlebarDescriptions: Record<TitlebarGroup, string> = {
  commandCenter: "在窗口标题栏显示命令中心。",
  navigationControls: "在窗口标题栏显示后退和前进按钮。",
  layoutControls: "在窗口标题栏显示布局按钮。",
};

const aiDescription = "配置模型或 Agent 网关。保存连接后可测试。";
const browserScope = "改动立即生效，保存在此浏览器。";
const aiScope = "由本机保存，适用于所有项目。";

function matchesSearch(query: string[], ...messages: string[]) {
  const normalize = (value: string) => value.toLowerCase().replace(/[^\p{L}\p{N}]/gu, "");
  const searchable = messages
    .flatMap((message) => [message, translate("en", message)])
    .map(normalize);
  return query
    .map(normalize)
    .every((term) => term && searchable.some((text) => text.includes(term)));
}

function SettingRow({ setting, showCategory }: { setting: Setting; showCategory: boolean }) {
  const id = useId();
  const label = useRef<HTMLLabelElement>(null);
  const descriptionId = `${id}-description`;
  return (
    <div className="settings-row" data-modified={setting.modified || undefined}>
      <div className="settings-row-copy">
        {showCategory && (
          <small className="settings-row-category">{t(categories[setting.category].label)}</small>
        )}
        <div className="settings-row-heading">
          <label ref={label} htmlFor={id}>
            {t(setting.label)}
          </label>
          {setting.modified && <span className="settings-modified">{t("已修改")}</span>}
        </div>
        <p className="settings-description" id={descriptionId}>
          {t(setting.description)}
        </p>
      </div>
      <div className="settings-row-actions">
        {setting.control({ id, "aria-describedby": descriptionId })}
        {setting.modified && (
          <button
            className="settings-button"
            type="button"
            aria-label={t("恢复「{0}」默认值", { "0": t(setting.label) })}
            onClick={() => {
              setting.reset();
              label.current?.control?.focus();
            }}
          >
            {t("恢复默认值")}
          </button>
        )}
      </div>
    </div>
  );
}

export function Settings({
  api,
  titlebar,
  hidden,
  categoryRequest,
}: {
  api: WorkbenchProps["api"];
  titlebar: TitlebarSettings;
  hidden?: boolean;
  categoryRequest?: { category: Category; token: number };
}) {
  const locale = useLocale();
  const headingId = useId();
  const searchInput = useRef<HTMLInputElement>(null);
  const [category, setCategory] = useState<Category>("common");
  const [query, setQuery] = useState("");
  const [aiOpened, setAiOpened] = useState(false);
  const preferences = useUserPreferences();
  useEffect(() => {
    if (!categoryRequest) return;
    setCategory(categoryRequest.category);
    setQuery("");
  }, [categoryRequest]);

  function preference<K extends keyof UserPreferences>(
    key: K,
    info: SettingInfo,
    control: (value: UserPreferences[K], change: (value: UserPreferences[K]) => void) => Control,
  ): Setting {
    const value = preferences[key];
    const defaultValue = defaultUserPreferences[key];
    const change = (next: UserPreferences[K]) => updateUserPreferences({ [key]: next });
    return {
      ...info,
      id: key,
      modified: value !== defaultValue,
      reset: () => change(defaultValue),
      control: control(value, change),
    };
  }

  const settings: Setting[] = [
    {
      id: "locale",
      category: "general",
      label: "显示语言",
      description: "选择 Tex Studio 的界面语言。",
      keywords: "locale language",
      common: true,
      modified: locale !== defaultLocale,
      reset: () => setLocale(defaultLocale),
      control: displayLanguage(locale, setLocale),
    },
    preference(
      "autoSave",
      {
        category: "files",
        label: "自动保存",
        description: "编辑停止约一秒后，自动保存已有路径的文件。未命名文件需要手动保存。",
        keywords: "files.autoSave 自动写盘",
        common: true,
      },
      checkbox,
    ),
    preference(
      "confirmBeforeUnload",
      {
        category: "files",
        label: "离开前确认",
        description: "有未保存的更改时，关闭或离开页面前请求确认。",
        keywords: "files.confirmBeforeUnload unsaved changes close 保存提醒",
        common: true,
      },
      checkbox,
    ),
    preference(
      "editorFontSize",
      {
        category: "editor",
        label: "编辑器字号",
        description: "调整源码编辑器中文字的大小。",
        keywords: "editor.fontSize 字体大小",
        common: true,
      },
      fontSize,
    ),
    preference(
      "editorWordWrap",
      {
        category: "editor",
        label: "自动换行",
        description: "选择长行在源码编辑器中的折行方式。",
        keywords: "editor.wordWrap 折行",
        common: true,
      },
      wordWrap,
    ),
    preference(
      "editorScreenReader",
      {
        category: "editor",
        label: "屏幕阅读器模式",
        description: "强制开启源码编辑器的屏幕阅读器支持。关闭此设置时自动检测。",
        keywords: "editor.accessibilitySupport screen reader 无障碍",
      },
      checkbox,
    ),
    preference(
      "editorLineNumbers",
      {
        category: "editor",
        label: "显示行号",
        description: "在源码编辑器左侧显示行号。",
        keywords: "editor.lineNumbers",
      },
      checkbox,
    ),
    preference(
      "editorMinimap",
      {
        category: "editor",
        label: "显示代码缩略图",
        description: "在源码编辑器右侧显示代码概览。",
        keywords: "editor.minimap 小地图",
      },
      checkbox,
    ),
    preference(
      "terminalFontSize",
      {
        category: "terminal",
        label: "终端字号",
        description: "调整集成终端中文字的大小。",
        keywords: "terminal.fontSize 字体大小",
      },
      fontSize,
    ),
    ...titlebarGroups.map(({ id, label }): Setting => {
      const modified = titlebar.hidden.includes(id);
      return {
        id,
        category: "workbench",
        label,
        description: titlebarDescriptions[id],
        keywords: `workbench.titlebar.${id} 窗口标题栏`,
        modified,
        reset: () => titlebar.toggle(id),
        control: checkbox(!modified, () => titlebar.toggle(id)),
      };
    }),
  ];

  const queryTerms = query.trim().split(/\s+/).filter(Boolean);
  const searching = queryTerms.length > 0;
  const rows = settings.filter((setting) =>
    searching
      ? matchesSearch(
          queryTerms,
          setting.label,
          setting.description,
          setting.keywords,
          categories[setting.category].label,
        )
      : category === "common"
        ? setting.common
        : setting.category === category,
  );
  const aiResult =
    searching &&
    matchesSearch(
      queryTerms,
      categories.ai.label,
      aiDescription,
      "AI connection BYOK BYOA API key token base URL model API 密钥 令牌 基础 URL",
    );
  const showAi = !searching && category === "ai";
  const categoryResets: Partial<Record<Category, { modified: boolean; reset: () => void }>> = {
    workbench: { modified: titlebar.hidden.length > 0, reset: titlebar.reset },
    editor: {
      modified: settings.some((item) => item.category === "editor" && item.modified),
      reset: () => updateUserPreferences(defaultEditorPreferences),
    },
    terminal: {
      modified: preferences.terminalFontSize !== defaultUserPreferences.terminalFontSize,
      reset: () => updateUserPreferences(defaultTerminalPreferences),
    },
  };
  const resetCategory = !searching && categoryResets[category];

  function selectCategory(next: Category) {
    setQuery("");
    setCategory(next);
    if (next === "ai") setAiOpened(true);
  }

  function clearSearch() {
    setQuery("");
    searchInput.current?.focus();
  }

  return (
    <section className="settings-page" aria-label={t("设置")} hidden={hidden}>
      <div className="settings-layout">
        <div className="settings-search" role="search" aria-label={t("搜索设置")}>
          <Icon name="search" size={15} />
          <input
            ref={searchInput}
            type="search"
            aria-label={t("搜索设置")}
            placeholder={t("搜索设置")}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Escape" && query && !event.nativeEvent.isComposing) {
                event.stopPropagation();
                event.preventDefault();
                clearSearch();
              }
            }}
          />
          {query && (
            <button
              type="button"
              className="settings-clear-search"
              aria-label={t("清除设置搜索")}
              onClick={clearSearch}
            >
              <Icon name="close" size={14} />
            </button>
          )}
        </div>
        <nav className="settings-navigation" aria-label={t("设置分类")}>
          <div className="settings-categories">
            {(Object.keys(categories) as Category[]).map((id) => (
              <button
                key={id}
                type="button"
                className="settings-category"
                aria-current={!searching && category === id ? "true" : undefined}
                onClick={() => selectCategory(id)}
              >
                <Icon name={categories[id].icon} size={14} />
                {t(categories[id].label)}
              </button>
            ))}
          </div>
        </nav>
        <div className="settings-content" role="region" aria-labelledby={headingId}>
          <header className="settings-heading">
            <h1 id={headingId}>{t(searching ? "搜索结果" : categories[category].label)}</h1>
            {searching && (
              <p className="settings-description" role="status">
                {rows.length || aiResult
                  ? t("找到 {0} 项设置", { "0": rows.length + Number(aiResult) })
                  : t("没有找到匹配的设置。试试设置名称或其他关键词。")}
              </p>
            )}
            {!searching && (
              <p className="settings-description">{t(showAi ? aiScope : browserScope)}</p>
            )}
          </header>
          {rows.length > 0 && (
            <>
              {searching && (
                <p className="settings-description settings-search-scope">{t(browserScope)}</p>
              )}
              {!searching && category === "workbench" && <h2>{t("窗口标题栏")}</h2>}
              <div className="settings-card">
                {rows.map((setting) => (
                  <SettingRow key={setting.id} setting={setting} showCategory={searching} />
                ))}
              </div>
            </>
          )}
          {resetCategory && (
            <button
              className="settings-button settings-reset"
              type="button"
              disabled={!resetCategory.modified}
              onClick={resetCategory.reset}
            >
              {t("恢复此分类默认值")}
            </button>
          )}
          {aiResult && (
            <div className="settings-ai-result">
              <h2>{t("AI 连接")}</h2>
              <p className="settings-description">
                {t(aiScope)} {t(aiDescription)}
              </p>
              <button
                className="settings-button"
                type="button"
                onClick={() => selectCategory("ai")}
              >
                {t("打开 AI 连接")}
              </button>
            </div>
          )}
          {aiOpened && (
            <div hidden={!showAi}>
              <AiConnectionSettings api={api} />
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
