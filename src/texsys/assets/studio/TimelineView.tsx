import React, { useEffect, useState } from "react";
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
}: {
  path?: string;
  api: <T>(url: string) => Promise<T>;
  token?: unknown;
  onOpen: (id: string) => void;
}) {
  const [commits, setCommits] = useState<TimelineCommit[]>();
  const [error, setError] = useState("");
  useEffect(() => {
    if (!path) {
      setCommits(undefined);
      setError("");
      return;
    }
    let current = true;
    setCommits(undefined);
    setError("");
    api<{ commits: TimelineCommit[] }>(`/api/scm/history?limit=50&path=${encodeURIComponent(path)}`)
      .then((result) => {
        if (current) setCommits(result.commits);
      })
      .catch((cause) => {
        if (current) {
          setCommits([]);
          setError(cause instanceof Error ? cause.message : String(cause));
        }
      });
    return () => {
      current = false;
    };
  }, [path, api, token]);

  return (
    <details className="welcome-side-section welcome-timeline-section">
      <summary>
        <Icon name="chevron" size={14} />
        <span>{t("时间线")}</span>
      </summary>
      <div className="timeline-body">
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
