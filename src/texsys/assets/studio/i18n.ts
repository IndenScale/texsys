import { useSyncExternalStore } from "react";
import { englishMessages } from "./messages";

export type Locale = "zh-CN" | "en";
export const localeStorageKey = "texsys.studio.locale";
export const supportedLocales: readonly Locale[] = ["zh-CN", "en"];

export function parseLocale(value: string | null): Locale {
  return value === "en" ? "en" : "zh-CN";
}

function readLocale(): Locale {
  try {
    return parseLocale(localStorage.getItem(localeStorageKey));
  } catch {
    return "zh-CN";
  }
}

let locale: Locale = readLocale();
const listeners = new Set<() => void>();
export const getLocale = () => locale;

export function setLocale(next: Locale) {
  if (!supportedLocales.includes(next)) return;
  try {
    localStorage.setItem(localeStorageKey, next);
  } catch {
    // The selected language still works when browser storage is unavailable.
  }
  if (locale === next) return;
  locale = next;
  listeners.forEach((listener) => listener());
}

if (typeof window !== "undefined") {
  window.addEventListener("storage", (event) => {
    if (event.key !== localeStorageKey && event.key !== null) return;
    const next = event.key === null ? "zh-CN" : parseLocale(event.newValue);
    if (locale === next) return;
    locale = next;
    listeners.forEach((listener) => listener());
  });
}

export function useLocale(): Locale {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    getLocale,
    () => "zh-CN",
  );
}

export function translate(
  language: Locale,
  message: string,
  values: Record<string, unknown> = {},
): string {
  const template =
    language === "en" && Object.hasOwn(englishMessages, message)
      ? englishMessages[message]
      : message;
  return template.replace(/\{(\w+)\}/g, (placeholder, key: string) =>
    Object.hasOwn(values, key) ? String(values[key]) : placeholder,
  );
}

export function t(message: string, values?: Record<string, unknown>): string;
export function t(message: undefined, values?: Record<string, unknown>): undefined;
export function t(
  message: string | undefined,
  values?: Record<string, unknown>,
): string | undefined;
export function t(
  message: string | undefined,
  values?: Record<string, unknown>,
): string | undefined {
  return message === undefined ? undefined : translate(locale, message, values);
}

/** The server appends details such as a path or a regex parser error to these prefixes. */
export function localizeError(message: string): string {
  const aiStatus = /^AI 服务返回 HTTP (\d{3})，请检查连接设置或服务状态。$/.exec(message);
  if (aiStatus) return t("AI 服务返回 HTTP {0}，请检查连接设置或服务状态。", { "0": aiStatus[1] });
  const prefix = [
    "正则表达式无效：",
    "替换表达式无效：",
    "替换后文件超过 1 MiB：",
    "无法执行 Git：",
  ].find((candidate) => message.startsWith(candidate));
  return prefix ? t(prefix) + message.slice(prefix.length) : t(message);
}
