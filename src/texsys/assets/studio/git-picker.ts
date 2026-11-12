import { useEffect, useRef, useState } from "react";
import { getLocale, t } from "./i18n";
import type { CommandItem } from "./menus";

type GitRef = {
  ref: string;
  kind: "local" | "remote" | "tag";
  name: string;
  commit: string;
  author: string;
  subject: string;
  time: number | null;
  upstream: string;
  current: boolean;
  remote_branch: string;
  github: { repository: string; ref: string } | null;
};
type GitRefs = { head: { ref: string; commit: string }; refs: GitRef[] };
type GitStart = { ref: string; commit: string } | { revision: string };
type GitStep =
  { mode: "checkout" | "base" | "detach" } | { mode: "name"; start?: GitStart; tracking?: boolean };
type GitAction = "checkout" | "create" | "track" | "detach";

const commandPrefix = "studio.git:";
const endpoint = "/api/scm/branches";
const sha = /^[0-9a-f]{4,64}$/i;
const refLabels = { local: "本地分支", remote: "远程分支", tag: "标签" } as const;
const refIcons = { local: "branch", remote: "cloud", tag: "tag" };

function githubLinks(remote: NonNullable<GitRef["github"]>) {
  const ref = remote.ref.split("/").map(encodeURIComponent).join("/");
  const target = `${remote.repository}/tree/${ref}`;
  return [
    { label: t("在 GitHub 上打开"), icon: "github", href: `https://github.com/${target}` },
    {
      label: t("在 vscode.dev 上签出"),
      icon: "globe",
      href: `https://vscode.dev/github/${target}`,
    },
  ];
}

function relativeCommitTime(time: number) {
  const minutes = Math.floor((Date.now() / 1000 - time) / 60);
  if (minutes < 1) return t("刚刚");
  const format = new Intl.RelativeTimeFormat(getLocale(), { numeric: "always" });
  if (minutes < 60) return format.format(-minutes, "minute");
  if (minutes < 1440) return format.format(-Math.floor(minutes / 60), "hour");
  return format.format(-Math.floor(minutes / 1440), "day");
}

export function useGitPicker({
  api,
  open,
  focus,
  dirty,
  notify,
  refresh,
}: {
  api: <T>(path: string, body?: unknown, signal?: AbortSignal) => Promise<T>;
  open: boolean;
  focus: () => void;
  dirty: boolean;
  notify: (message: string, error?: boolean) => void;
  refresh: () => Promise<void>;
}) {
  const [step, setStep] = useState<GitStep>({ mode: "checkout" });
  const [request, setRequest] = useState(0);
  const [listing, setListing] = useState<GitRefs>();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const running = useRef(false);

  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    void api<GitRefs>(endpoint, undefined, controller.signal)
      .then((result) => {
        if (!controller.signal.aborted) setListing(result);
      })
      .catch((error: Error) => {
        if (!controller.signal.aborted) setError(error.message);
      });
    return () => controller.abort();
  }, [api, open, request]);

  const move = (next: GitStep) => {
    setStep(next);
    setError("");
    focus();
  };
  const start = () => {
    setListing(undefined);
    setRequest((value) => value + 1);
    move({ mode: "checkout" });
  };
  const run = async (action: GitAction, target?: GitStart, name?: string, inline = false) => {
    if (!listing || dirty || running.current) return false;
    running.current = true;
    setBusy(true);
    setError("");
    try {
      const result = await api<{ name: string; commit: string }>(endpoint, {
        action,
        head: listing.head,
        ...target,
        name,
      });
      notify(
        result.name
          ? t("当前分支：{0}", { "0": result.name })
          : t("已分离签出 {0}", { "0": result.commit.slice(0, 8) }),
      );
      return true;
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : String(reason);
      if (inline) setError(message);
      else notify(message, true);
      return false;
    } finally {
      await refresh();
      running.current = false;
      setBusy(false);
    }
  };

  const refs = listing?.refs ?? [];
  const needsTrackingName = (item: GitRef) =>
    item.kind === "remote" &&
    !refs.some((ref) => ref.upstream === item.ref) &&
    refs.some((ref) => ref.kind === "local" && ref.name === item.remote_branch);

  const actionItem = (
    id: string,
    label: string,
    icon: string,
    unavailable = false,
  ): CommandItem => ({
    id: commandPrefix + id,
    label: t(label),
    icon,
    group: "",
    keepOpen: true,
    disabled: dirty || busy || !listing || unavailable,
  });
  const items: CommandItem[] =
    step.mode === "name"
      ? []
      : [
          ...(step.mode === "checkout"
            ? [
                actionItem("create", "创建新分支…", "plus"),
                actionItem("base", "从指定引用创建分支…", "plus", !listing?.head.commit),
                actionItem("detach", "分离签出…", "compare", !listing?.head.commit),
              ]
            : []),
          ...refs.map((item) => ({
            id: commandPrefix + item.ref,
            label: item.name,
            group: t(item.current ? "当前分支" : refLabels[item.kind]),
            icon: refIcons[item.kind],
            detail: [item.author, item.commit.slice(0, 8), item.subject]
              .filter(Boolean)
              .join(" · "),
            keywords: item.commit,
            secondaryLabel: item.time === null ? undefined : relativeCommitTime(item.time),
            links: item.github ? githubLinks(item.github) : undefined,
            checked: item.current || undefined,
            disabled: dirty || busy || (step.mode === "checkout" && item.current),
            keepOpen: step.mode === "base" || (step.mode === "checkout" && needsTrackingName(item)),
          })),
        ];
  const invoke = (id: string) => {
    const command = id.slice(commandPrefix.length);
    if (command === "create") move({ mode: "name" });
    else if (command === "base" || command === "detach") move({ mode: command });
    else {
      const item = refs.find((item) => item.ref === command);
      if (!item) return;
      const target = { ref: item.ref, commit: item.commit };
      if (step.mode === "base") move({ mode: "name", start: target });
      else if (step.mode === "checkout" && needsTrackingName(item))
        move({ mode: "name", start: target, tracking: true });
      else void run(step.mode === "detach" ? "detach" : "checkout", target);
    }
  };

  const input =
    step.mode === "name"
      ? {
          label: t("新分支名称"),
          placeholder: t("输入新分支名称…"),
          actionLabel: t("创建并签出分支"),
          canSubmit: (value: string) => !!value && !dirty && !busy,
          submit: (value: string) =>
            run(step.tracking ? "track" : "create", step.start, value, true),
        }
      : step.mode === "base" || step.mode === "detach"
        ? {
            label: t(step.mode === "base" ? "选择起点" : "选择分离签出的提交"),
            placeholder: t("选择引用或输入提交哈希…"),
            actionLabel: t(step.mode === "base" ? "使用此提交作为起点" : "分离签出此提交"),
            canSubmit: (value: string) => sha.test(value) && !dirty && !busy,
            submit: async (value: string) => {
              if (step.mode === "detach")
                return run("detach", { revision: value }, undefined, true);
              move({ mode: "name", start: { revision: value } });
              return false;
            },
          }
        : undefined;
  const status = error
    ? { message: error, error: true }
    : !listing
      ? { message: t("正在读取 Git 引用…") }
      : dirty
        ? { message: t("请先保存或关闭未保存的文件，再切换分支。") }
        : busy
          ? { message: t("正在签出…") }
          : step.mode === "name"
            ? { message: t("输入名称后按 Enter。") }
            : undefined;
  return { start, invoke, items, input, status };
}

export function isGitPickerCommand(id: string) {
  return id.startsWith(commandPrefix);
}
