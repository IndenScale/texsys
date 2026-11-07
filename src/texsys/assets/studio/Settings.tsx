import React, { useId } from "react";
import { Icon } from "./ui";
import { setLocale, t, useLocale, type Locale } from "./i18n";
import "./settings.css";

export function Settings() {
  const locale = useLocale();
  const id = useId();
  return (
    <section className="settings-page" role="tabpanel" aria-label={t("设置")}>
      <div className="settings-layout">
        <nav className="settings-navigation" aria-label={t("设置分类")}>
          <div className="settings-navigation-title">{t("设置")}</div>
          <div role="tablist" aria-label={t("设置分类")} aria-orientation="vertical">
            <button
              id={`${id}-general-tab`}
              className="settings-category"
              role="tab"
              aria-selected="true"
              aria-controls={`${id}-general`}
            >
              <Icon name="settings" size={16} />
              {t("常规")}
            </button>
          </div>
        </nav>
        <div
          className="settings-content"
          id={`${id}-general`}
          role="tabpanel"
          aria-labelledby={`${id}-general-tab`}
        >
          <h1>{t("常规")}</h1>
          <section className="settings-card" aria-labelledby={`${id}-language-title`}>
            <div className="settings-card-row">
              <div className="settings-card-description">
                <h2 id={`${id}-language-title`}>{t("显示语言")}</h2>
                <p>{t("选择 Tex Studio 的界面语言。")}</p>
              </div>
              <select
                aria-label={t("显示语言")}
                aria-describedby={`${id}-language-note`}
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
            </div>
            <p className="settings-card-note" id={`${id}-language-note`}>
              {t("立即应用，并在此浏览器中记住选择。")}
            </p>
          </section>
        </div>
      </div>
    </section>
  );
}
