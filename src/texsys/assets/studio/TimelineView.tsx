import React, { useEffect, useRef, useState } from "react";
import { getLocale, t } from "./i18n";
import { Icon } from "./ui";
import "./timeline-view.css";

export type TimelineCommit = {
  id: string;
  subject: string;
  author: string;
  date: string;
};

function relativeTime(date: string): string {
  const then = new Date(date).getTime();
  if (Number.isNaN(then)) return "";
  const seconds = Math.round((then - Date.now()) / 1000);
  const units: [Intl.RelativeTimeFormatUnit, number][] = [
    ["year", 31536000],
    ["month", 2592000],
    ["day", 86400],
    ["hour", 3600],
    ["minute", 60],
  ];
  const format = new Intl.RelativeTimeFormat(getLocale() === "en" ? "en" : "zh-CN", {
    numeric: "auto",
  });
  for (const [unit, step] of units) {
    if (Math.abs(seconds) >= step) return format.format(Math.round(seconds / step), unit);
  }
  return format.format(seconds, "second");
}

export function TimelineView({
  path,
  api,
  token,
  onOpen,
  open,
  onOpenChange,
  bodyHeight,
}: {
  path?: string;
  api: <T>(url: string, body?: unknown, signal?: AbortSignal) => Promise<T>;
  token?: string;
  onOpen: (id: string) => void;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  bodyHeight?: number;
}) {
  const [commits, setCommits] = useState<TimelineCommit[]>();
  const [error, setError] = useState("");
  const cache = useRef(new Map<string, TimelineCommit[]>());
  useEffect(() => {
    if (!path || open === false) {
      setCommits(undefined);
      setError("");
      return;
    }
    const key = JSON.stringify([path, token]);
    const previous = token && cache.current.get(key);
    if (previous) {
      setCommits(previous);
      setError("");
      return;
    }
    const controller = new AbortController();
    setCommits(undefined);
    setError("");
    api<{ commits: TimelineCommit[] }>(
      `/api/scm/history?limit=50&path=${encodeURIComponent(path)}`,
      undefined,
      controller.signal,
    )
      .then((result) => {
        if (controller.signal.aborted) return;
        cache.current.set(key, result.commits);
        if (cache.current.size > 32) {
          const oldest = cache.current.keys().next();
          if (!oldest.done) cache.current.delete(oldest.value);
        }
        setCommits(result.commits);
      })
      .catch((cause) => {
        if (!controller.signal.aborted) {
          setCommits([]);
          setError(cause instanceof Error ? cause.message : String(cause));
        }
      });
    return () => {
      controller.abort();
    };
  }, [path, api, token, open]);

  return (
    <details
      className="welcome-side-section welcome-timeline-section"
      open={open}
      onToggle={(event) => onOpenChange?.(event.currentTarget.open)}
    >
      <summary>
        <Icon name="chevron" size={14} />
        <span>{t("时间线")}</span>
      </summary>
      <div
        className="timeline-body"
        style={bodyHeight === undefined ? undefined : { height: bodyHeight }}
      >
        {!path ? (
          <p className="core-hint">{t("打开文件后可查看时间线。")}</p>
        ) : !commits ? (
          <p className="core-hint">{t("正在读取提交历史…")}</p>
        ) : error ? (
          <p className="core-hint">{error}</p>
        ) : commits.length === 0 ? (
          <p className="core-hint">{t("此文件暂无提交历史。")}</p>
        ) : (
          commits.map((commit) => (
            <button
              key={commit.id}
              type="button"
              className="timeline-row"
              title={`${commit.subject} · ${commit.author}`}
              onClick={() => onOpen(commit.id)}
            >
              <span className="timeline-icon" aria-hidden="true">
                <Icon name="history" size={16} />
              </span>
              <span className="timeline-subject">{commit.subject}</span>
              <small className="timeline-when">{relativeTime(commit.date)}</small>
            </button>
          ))
        )}
      </div>
    </details>
  );
}
