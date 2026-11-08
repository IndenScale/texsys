import React, { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { Icon } from "./ui";
import { NavigatorMenu, type NavigatorMenuItem } from "./NavigatorMenu";
import { InlineTabStrip } from "./atomstudio/WorkspaceTabs";
import { t } from "./i18n";
import type { StudioLayout } from "./sdk";
import "./terminal.css";

export type TerminalOutputLine = {
  id: string;
  type: "command" | "output" | "error" | "system";
  prompt?: {
    project: string;
    branch: string;
    dirty: boolean;
    venv: string;
  };
  text: string;
};

export type TerminalSession = {
  id: string;
  name: string;
  cwd: string;
  shell: string;
  pid: number;
  commandLine: string;
  integration: string;
  history: TerminalOutputLine[];
  commandHistory: string[];
  historyIndex: number;
  input: string;
};

let sessionCounter = 0;

function createSession(
  name = "zsh",
  project = "texsys",
  _branch = "main",
  _dirty = true,
): TerminalSession {
  sessionCounter += 1;
  const id = `term-${Date.now()}-${sessionCounter}`;
  return {
    id,
    name,
    cwd: project,
    shell: name,
    pid: 78000 + sessionCounter,
    commandLine: `/bin/${name} -il`,
    integration: "Rich",
    history: [],
    commandHistory: [],
    historyIndex: -1,
    input: "",
  };
}

export type PanelTabDefinition = {
  id: string;
  label: string;
  icon: string;
};

export const PANEL_TABS: PanelTabDefinition[] = [
  { id: "problems", label: "问题", icon: "warning" },
  { id: "output", label: "输出", icon: "file" },
  { id: "tasks", label: "任务与日志", icon: "system" },
  { id: "debug", label: "调试控制台", icon: "code" },
  { id: "terminal", label: "终端", icon: "terminal" },
  { id: "ports", label: "端口", icon: "cube" },
  { id: "comments", label: "注释", icon: "info" },
];

export type BottomPanelContextMenu =
  | { type: "header"; point: { x: number; y: number } }
  | { type: "tab"; tabId: string; point: { x: number; y: number } }
  | { type: "session"; sessionId: string; point: { x: number; y: number } }
  | { type: "terminal-viewport"; sessionId: string; point: { x: number; y: number } }
  | { type: "actions-toolbar"; actionId?: string; point: { x: number; y: number } };

export function WorkbenchBottomPanel({
  layout,
  changeLayout,
  problemsContent,
  problemsCount = 0,
  openChecks,
  taskPanel,
  project = "texsys",
  branch = "main",
  dirty = true,
  api,
  notify,
}: {
  layout: StudioLayout;
  changeLayout: (patch: Partial<StudioLayout>) => void;
  problemsContent?: ReactNode;
  problemsCount?: number;
  openChecks?: () => void;
  taskPanel: ReactNode;
  project?: string;
  branch?: string;
  dirty?: boolean;
  api?: <T>(path: string, body?: unknown) => Promise<T>;
  notify?: (message: string, isError?: boolean) => void;
}) {
  const [sessions, setSessions] = useState<TerminalSession[]>(() => [
    createSession("zsh", project, branch, dirty),
  ]);
  const [activeSessionId, setActiveSessionId] = useState<string>(() => sessions[0]?.id ?? "");
  const [splitSessionId, setSplitSessionId] = useState<string | null>(null);
  const [focusedPane, setFocusedPane] = useState<"primary" | "secondary">("primary");

  const activeSession = sessions.find((s) => s.id === activeSessionId) ?? sessions[0];
  const splitSession = splitSessionId ? sessions.find((s) => s.id === splitSessionId) ?? null : null;
  const showSessionsSidebar = sessions.length > 1 || Boolean(splitSession);

  const primaryInputRef = useRef<HTMLInputElement>(null);
  const secondaryInputRef = useRef<HTMLInputElement>(null);
  const primaryScrollRef = useRef<HTMLDivElement>(null);
  const secondaryScrollRef = useRef<HTMLDivElement>(null);

  const mac = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform);

  const [hoveredSessionId, setHoveredSessionId] = useState<string | null>(null);
  const hoverTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const handleSessionMouseEnter = (id: string) => {
    if (hoverTimeoutRef.current) clearTimeout(hoverTimeoutRef.current);
    hoverTimeoutRef.current = setTimeout(() => {
      setHoveredSessionId(id);
    }, 200);
  };

  const handleSessionMouseLeave = () => {
    if (hoverTimeoutRef.current) clearTimeout(hoverTimeoutRef.current);
    hoverTimeoutRef.current = setTimeout(() => {
      setHoveredSessionId(null);
    }, 150);
  };

  useEffect(() => {
    return () => {
      if (hoverTimeoutRef.current) clearTimeout(hoverTimeoutRef.current);
    };
  }, []);

  const renderHoverDetails = (sess: TerminalSession) => (
    <div
      className="terminal-hover-card"
      role="tooltip"
      onMouseEnter={() => {
        if (hoverTimeoutRef.current) clearTimeout(hoverTimeoutRef.current);
        setHoveredSessionId(sess.id);
      }}
      onMouseLeave={handleSessionMouseLeave}
    >
      <div className="terminal-hover-title">{sess.name}</div>
      <div className="terminal-hover-divider" />
      <div className="terminal-hover-meta">
        <div className="terminal-hover-row">
          <span>{t("进程 ID (PID)：{pid}", { pid: sess.pid })}</span>
        </div>
        <div className="terminal-hover-row">
          <span>{t("命令行：{command}", { command: sess.commandLine })}</span>
        </div>
      </div>
      <div className="terminal-hover-divider" />
      <div className="terminal-hover-status">
        {t("Shell 集成：丰富")}
      </div>
      <div className="terminal-hover-actions">
        <button
          type="button"
          className="terminal-hover-link"
          onClick={() => {
            setHoveredSessionId(null);
            window.dispatchEvent(new CustomEvent("texsys:terminal:environment"));
          }}
        >
          {t("显示环境贡献")}
        </button>
        <button
          type="button"
          className="terminal-hover-link"
          onClick={() => {
            setHoveredSessionId(null);
            window.dispatchEvent(new CustomEvent("texsys:terminal:details"));
          }}
        >
          {t("显示详情")}
        </button>
      </div>
    </div>
  );

  // Auto-scroll on new history items
  useEffect(() => {
    if (primaryScrollRef.current) {
      primaryScrollRef.current.scrollTop = primaryScrollRef.current.scrollHeight;
    }
  }, [activeSession?.history.length]);

  useEffect(() => {
    if (secondaryScrollRef.current) {
      secondaryScrollRef.current.scrollTop = secondaryScrollRef.current.scrollHeight;
    }
  }, [splitSession?.history.length]);

  const addSession = useCallback(
    (name = "zsh") => {
      const next = createSession(name, project, branch, dirty);
      setSessions((prev) => [...prev, next]);
      setActiveSessionId(next.id);
      setTimeout(() => primaryInputRef.current?.focus(), 50);
    },
    [project, branch, dirty],
  );

  const closeSession = useCallback(
    (id: string) => {
      setSessions((prev) => {
        const next = prev.filter((s) => s.id !== id);
        if (next.length === 0) {
          const fresh = createSession("zsh", project, branch, dirty);
          setActiveSessionId(fresh.id);
          setSplitSessionId(null);
          return [fresh];
        }
        if (activeSessionId === id) {
          const idx = prev.findIndex((s) => s.id === id);
          const candidate = next[Math.min(idx, next.length - 1)];
          setActiveSessionId(candidate?.id ?? next[0].id);
        }
        if (splitSessionId === id) {
          setSplitSessionId(null);
        }
        return next;
      });
    },
    [activeSessionId, splitSessionId, project, branch, dirty],
  );

  const toggleSplit = useCallback(() => {
    if (splitSessionId) {
      setSplitSessionId(null);
      setFocusedPane("primary");
    } else {
      const next = createSession("zsh", project, branch, dirty);
      setSessions((prev) => [...prev, next]);
      setSplitSessionId(next.id);
      setFocusedPane("secondary");
      setTimeout(() => secondaryInputRef.current?.focus(), 50);
    }
  }, [splitSessionId, project, branch, dirty]);

  const clearSession = useCallback((targetId: string) => {
    setSessions((prev) =>
      prev.map((s) => (s.id === targetId ? { ...s, history: [] } : s)),
    );
  }, []);

  const runCommandOnSession = useCallback(
    async (targetId: string, cmdStr: string) => {
      const trimmed = cmdStr.trim();
      const current = sessions.find((s) => s.id === targetId);
      if (!current) return;

      const cmdId = `cmd-${Date.now()}`;
      const cmdLine: TerminalOutputLine = {
        id: cmdId,
        type: "command",
        prompt: { project, branch, dirty, venv: "texsys" },
        text: cmdStr,
      };

      if (!trimmed) {
        setSessions((prev) =>
          prev.map((s) =>
            s.id === targetId
              ? {
                  ...s,
                  history: [...s.history, cmdLine],
                  input: "",
                }
              : s,
          ),
        );
        return;
      }

      if (trimmed === "clear") {
        setSessions((prev) =>
          prev.map((s) =>
            s.id === targetId
              ? {
                  ...s,
                  history: [],
                  commandHistory: [trimmed, ...s.commandHistory.filter((c) => c !== trimmed)],
                  historyIndex: -1,
                  input: "",
                }
              : s,
          ),
        );
        return;
      }

      let outputText = "";
      let isError = false;
      const args = trimmed.split(/\s+/);
      const rootCommand = args[0];

      if (trimmed === "texsys" || trimmed === "texsys --help" || trimmed === "texsys -h") {
        outputText = [
          "Usage: texsys [OPTIONS] COMMAND [ARGS]...",
          "",
          "  texsys: reference implementation, toolchain and ecosystem for SDL",
          "",
          "Options:",
          "  --version  Show the version and exit.",
          "  --help     Show this message and exit.",
          "",
          "Commands:",
          "  studio     Launch the Tex Studio workbench.",
          "  check      Run project-level model validation and rule checks.",
          "  build      Compile diagrams, drawings, and engineering deliverables.",
          "  simulate   Execute dynamic simulation scenarios.",
        ].join("\n");
      } else if (trimmed === "texsys --version" || trimmed === "texsys -v") {
        outputText = "texsys 0.1.0";
      } else if (trimmed.startsWith("texsys check")) {
        outputText = [
          "Checking system definition in /Users/indenscale/workspace/texsys...",
          "✔ Validating SDL component topologies",
          "✔ Checking hydraulic and thermal parameters",
          "✔ Model validation completed: 0 errors, 1 warning (Drawing review pending)",
        ].join("\n");
      } else if (trimmed.startsWith("texsys build")) {
        outputText = [
          "Building engineering deliverables...",
          "✔ Generating system diagram (SVG)",
          "✔ Generating equipment table (CSV)",
          "✔ Build completed successfully in 0.42s",
        ].join("\n");
      } else if (trimmed.startsWith("texsys simulate")) {
        outputText = [
          "Running dynamic simulation scenarios...",
          "✔ Scenario: pump-trip (backend: scipy)",
          "✔ Simulation completed in 1.15s. Results available in simulation viewer.",
        ].join("\n");
      } else if (trimmed === "git status") {
        if (api) {
          try {
            const scm = await api<{ entries: Array<{ path: string; index: string; worktree: string }> }>("/api/scm");
            const modified = scm.entries.map((e) => `\tmodified:   ${e.path}`);
            outputText = [
              `On branch ${branch}`,
              `Your branch is up to date with 'origin/${branch}'.`,
              ...(modified.length
                ? [
                    "",
                    "Changes not staged for commit:",
                    '  (use "git add <file>..." to update what will be committed)',
                    '  (use "git restore <file>..." to discard changes in working directory)',
                    ...modified,
                    "",
                    'no changes added to commit (use "git add" to stage)',
                  ]
                : ["", "nothing to commit, working tree clean"]),
            ].join("\n");
          } catch {
            outputText = `On branch ${branch}\nnothing to commit, working tree clean`;
          }
        } else {
          outputText = `On branch ${branch}\nnothing to commit, working tree clean`;
        }
      } else if (trimmed === "git branch") {
        outputText = `* ${branch}`;
      } else if (trimmed.startsWith("git log")) {
        if (api) {
          try {
            const log = await api<Array<{ hash: string; summary: string; author: string; time: string }>>("/api/scm/history?limit=3");
            outputText = log
              .map(
                (c) =>
                  `commit ${c.hash}\nAuthor: ${c.author}\nDate:   ${c.time}\n\n    ${c.summary}`,
              )
              .join("\n\n");
          } catch {
            outputText = `commit 16ac2f0e (HEAD -> ${branch})\nAuthor: indenscale\n\n    feat: implement terminal module`;
          }
        } else {
          outputText = `commit 16ac2f0e (HEAD -> ${branch})\nAuthor: indenscale\n\n    feat: implement terminal module`;
        }
      } else if (trimmed === "pwd") {
        outputText = "/Users/indenscale/workspace/texsys";
      } else if (trimmed === "ls" || trimmed === "ls -la") {
        if (api) {
          try {
            const fileData = await api<{ files: Array<{ name: string; path: string }> }>("/api/files");
            const topLevel = Array.from(new Set(fileData.files.map((f) => f.path.split("/")[0]))).sort();
            outputText = topLevel.join("  ");
          } catch {
            outputText = "AGENTS.md  docs  examples  packages  src  tools  pyproject.toml";
          }
        } else {
          outputText = "AGENTS.md  docs  examples  packages  src  tools  pyproject.toml";
        }
      } else if (trimmed.startsWith("echo ")) {
        outputText = trimmed.slice(5).replace(/^['"]|['"]$/g, "");
      } else if (trimmed === "python --version" || trimmed === "python -V" || trimmed === "python3 --version") {
        outputText = "Python 3.12.8";
      } else if (trimmed === "node --version" || trimmed === "node -v") {
        outputText = "v24.11.0";
      } else if (trimmed === "uv --version") {
        outputText = "uv 0.5.11";
      } else if (trimmed === "history") {
        outputText = current.commandHistory
          .slice()
          .reverse()
          .map((cmd, i) => `  ${i + 1}  ${cmd}`)
          .join("\n");
      } else if (trimmed === "help") {
        outputText = "Available commands: texsys, git, ls, pwd, cat, echo, clear, python, node, uv, history, help";
      } else {
        if (api) {
          try {
            const res = await api<{ stdout: string; stderr: string; code: number }>("/api/terminal", {
              command: trimmed,
              cwd: "",
            });
            outputText = res.stdout || res.stderr || "";
            if (res.code !== 0) isError = true;
          } catch {
            outputText = `zsh: command not found: ${rootCommand}`;
            isError = true;
          }
        } else {
          outputText = `zsh: command not found: ${rootCommand}`;
          isError = true;
        }
      }

      const outLine: TerminalOutputLine = {
        id: `out-${Date.now()}`,
        type: isError ? "error" : "output",
        text: outputText,
      };

      setSessions((prev) =>
        prev.map((s) =>
          s.id === targetId
            ? {
                ...s,
                history: outputText ? [...s.history, cmdLine, outLine] : [...s.history, cmdLine],
                commandHistory: [trimmed, ...s.commandHistory.filter((c) => c !== trimmed)],
                historyIndex: -1,
                input: "",
              }
            : s,
        ),
      );
    },
    [sessions, project, branch, dirty, api],
  );

  // Global event listeners
  useEffect(() => {
    const handleNew = () => addSession("zsh");
    const handleSplit = () => toggleSplit();
    const handleClear = () => clearSession(activeSessionId);
    const handleRunCommand = (e: Event) => {
      const customEvent = e as CustomEvent<{ command: string }>;
      if (customEvent.detail?.command) {
        void runCommandOnSession(activeSessionId, customEvent.detail.command);
      }
    };

    window.addEventListener("texsys:terminal:new", handleNew);
    window.addEventListener("texsys:terminal:split", handleSplit);
    window.addEventListener("texsys:terminal:clear", handleClear);
    window.addEventListener("texsys:terminal:runCommand", handleRunCommand);

    return () => {
      window.removeEventListener("texsys:terminal:new", handleNew);
      window.removeEventListener("texsys:terminal:split", handleSplit);
      window.removeEventListener("texsys:terminal:clear", handleClear);
      window.removeEventListener("texsys:terminal:runCommand", handleRunCommand);
    };
  }, [addSession, toggleSplit, clearSession, activeSessionId, runCommandOnSession]);

  const handleKeyDown = (
    event: React.KeyboardEvent<HTMLInputElement>,
    targetSession: TerminalSession,
    _isSecondary = false,
  ) => {
    if (event.key === "Enter") {
      event.preventDefault();
      void runCommandOnSession(targetSession.id, targetSession.input);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      const nextIdx = Math.min(
        targetSession.historyIndex + 1,
        targetSession.commandHistory.length - 1,
      );
      if (nextIdx >= 0) {
        const val = targetSession.commandHistory[nextIdx];
        setSessions((prev) =>
          prev.map((s) =>
            s.id === targetSession.id
              ? { ...s, historyIndex: nextIdx, input: val }
              : s,
          ),
        );
      }
    } else if (event.key === "ArrowDown") {
      event.preventDefault();
      const nextIdx = targetSession.historyIndex - 1;
      if (nextIdx >= 0) {
        const val = targetSession.commandHistory[nextIdx];
        setSessions((prev) =>
          prev.map((s) =>
            s.id === targetSession.id
              ? { ...s, historyIndex: nextIdx, input: val }
              : s,
          ),
        );
      } else {
        setSessions((prev) =>
          prev.map((s) =>
            s.id === targetSession.id
              ? { ...s, historyIndex: -1, input: "" }
              : s,
          ),
        );
      }
    } else if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      clearSession(targetSession.id);
    } else if (event.ctrlKey && event.key.toLowerCase() === "c") {
      event.preventDefault();
      const abortLine: TerminalOutputLine = {
        id: `abort-${Date.now()}`,
        type: "command",
        prompt: { project, branch, dirty, venv: "texsys" },
        text: targetSession.input + "^C",
      };
      setSessions((prev) =>
        prev.map((s) =>
          s.id === targetSession.id
            ? { ...s, history: [...s.history, abortLine], input: "", historyIndex: -1 }
            : s,
        ),
      );
    } else if (event.ctrlKey && event.key.toLowerCase() === "u") {
      event.preventDefault();
      setSessions((prev) =>
        prev.map((s) => (s.id === targetSession.id ? { ...s, input: "" } : s)),
      );
    }
  };

  const renderPane = (
    session: TerminalSession,
    isSecondary: boolean,
    inputRef: React.RefObject<HTMLInputElement | null>,
    scrollRef: React.RefObject<HTMLDivElement | null>,
  ) => {
    const isFocused =
      (!splitSessionId && !isSecondary) ||
      (splitSessionId && (isSecondary ? focusedPane === "secondary" : focusedPane === "primary"));

    return (
      <div
        className={`terminal-pane ${splitSessionId && !isSecondary ? "is-split" : ""} ${
          isFocused ? "is-focused" : ""
        }`}
        onClick={() => {
          setFocusedPane(isSecondary ? "secondary" : "primary");
          inputRef.current?.focus();
        }}
      >
        <div className="terminal-viewport" ref={scrollRef}>
          {session.history.map((line) => {
            if (line.type === "system") {
              return (
                <div key={line.id} className="terminal-line terminal-system">
                  {line.text}
                </div>
              );
            }
            if (line.type === "command") {
              return (
                <div key={line.id} className="terminal-line">
                  <span className="terminal-prompt-prefix">
                    <span className="term-dot-circle">○</span>
                    <span className="term-venv">(texsys)</span>
                    <span className="term-arrow">→</span>
                    <span className="term-folder">{line.prompt?.project ?? project}</span>
                    <span className="term-git">
                      git:(
                      <span className={`term-branch ${line.prompt?.dirty ? "is-dirty" : ""}`}>
                        {line.prompt?.branch ?? branch}
                      </span>
                      )
                    </span>
                    {line.prompt?.dirty && <span className="term-dirty">✗</span>}
                  </span>
                  <span className="term-cmd-text">{line.text}</span>
                </div>
              );
            }
            return (
              <pre
                key={line.id}
                className={`terminal-output ${line.type === "error" ? "is-error" : ""}`}
              >
                {line.text}
              </pre>
            );
          })}

          <div className="terminal-input-row">
            <span className="terminal-prompt-prefix">
              <span className="term-dot-circle">○</span>
              <span className="term-venv">(texsys)</span>
              <span className="term-arrow">→</span>
              <span className="term-folder">{project}</span>
              <span className="term-git">
                git:(<span className={`term-branch ${dirty ? "is-dirty" : ""}`}>{branch}</span>)
              </span>
              {dirty && <span className="term-dirty">✗</span>}
            </span>
            <div className="terminal-input-wrapper">
              <input
                ref={inputRef}
                className="terminal-real-input"
                type="text"
                autoComplete="off"
                autoCorrect="off"
                autoCapitalize="off"
                spellCheck="false"
                value={session.input}
                onChange={(e) => {
                  const val = e.target.value;
                  setSessions((prev) =>
                    prev.map((s) => (s.id === session.id ? { ...s, input: val } : s)),
                  );
                }}
                onKeyDown={(e) => handleKeyDown(e, session, isSecondary)}
              />
            </div>
          </div>
        </div>
      </div>
    );
  };

  // Header profile and action menus
  const profileMenuItems: NavigatorMenuItem[] = [
    {
      label: t("新建终端"),
      icon: "terminal",
      shortcut: mac ? ["⌃", "⇧", "`"] : ["Ctrl", "Shift", "`"],
      run: () => addSession("zsh"),
    },
    {
      label: t("新建终端窗口"),
      shortcut: mac ? ["⌃", "⌥", "⇧", "`"] : ["Ctrl", "Alt", "Shift", "`"],
      run: () => addSession("zsh"),
    },
    {
      label: t("拆分终端"),
      icon: "split-horizontal",
      shortcut: [mac ? "⌘" : "Ctrl", "\\"],
      run: () => toggleSplit(),
    },
    {
      label: "bash",
      separator: true,
      icon: "terminal",
      run: () => addSession("bash"),
    },
    {
      label: "zsh",
      checked: true,
      icon: "terminal",
      run: () => addSession("zsh"),
    },
    {
      label: "JavaScript Debug Terminal",
      icon: "terminal",
      run: () => addSession("js-debug"),
    },
    {
      label: "texsys (Python venv)",
      icon: "terminal",
      run: () => addSession("texsys"),
    },
    {
      label: t("清空终端"),
      separator: true,
      shortcut: [mac ? "⌘" : "Ctrl", "K"],
      run: () => clearSession(activeSessionId),
    },
    {
      label: t("运行任务…"),
      separator: true,
      run: () => window.dispatchEvent(new CustomEvent("texsys:tasks:run")),
    },
    {
      label: t("配置任务…"),
      run: () => window.dispatchEvent(new CustomEvent("texsys:tasks:configure")),
    },
  ];

  const sessionsMenuItems: NavigatorMenuItem[] = [
    ...sessions.map((sess) => ({
      label: `${sess.name} (${sess.id.slice(-4)})`,
      icon: "terminal",
      checked: sess.id === activeSessionId,
      run: () => {
        setActiveSessionId(sess.id);
        setFocusedPane("primary");
        primaryInputRef.current?.focus();
      },
    })),
    {
      label: t("新建终端"),
      separator: true,
      icon: "plus",
      run: () => addSession("zsh"),
    },
    {
      label: t("清空终端"),
      icon: "trash",
      shortcut: [mac ? "⌘" : "Ctrl", "K"],
      run: () => clearSession(activeSessionId),
    },
  ];

  const moreMenuItems: NavigatorMenuItem[] = [
    {
      label: t("滚动到上一命令"),
      shortcut: mac ? ["⇧", "⌘", "↑"] : ["Shift", "Ctrl", "↑"],
      run: () => {
        if (primaryScrollRef.current) {
          primaryScrollRef.current.scrollTop -= 80;
        }
      },
    },
    {
      label: t("滚动到下一命令"),
      shortcut: mac ? ["⇧", "⌘", "↓"] : ["Shift", "Ctrl", "↓"],
      run: () => {
        if (primaryScrollRef.current) {
          primaryScrollRef.current.scrollTop += 80;
        }
      },
    },
    {
      label: t("清空终端"),
      shortcut: [mac ? "⌘" : "Ctrl", "K"],
      run: () => clearSession(activeSessionId),
    },
    {
      label: t("运行活动文件"),
      run: () => {
        window.dispatchEvent(
          new CustomEvent("texsys:terminal:runCommand", {
            detail: { command: "texsys check" },
          }),
        );
      },
    },
    {
      label: t("运行选定文本"),
      run: () => {
        const sel = window.getSelection()?.toString()?.trim();
        if (sel) {
          window.dispatchEvent(
            new CustomEvent("texsys:terminal:runCommand", {
              detail: { command: sel },
            }),
          );
        }
      },
    },
    {
      label: t("转到最近目录…"),
      separator: true,
      shortcut: [mac ? "⌘" : "Ctrl", "G"],
      run: () => {},
    },
    {
      label: t("运行最近命令…"),
      shortcut: mac ? ["⌃", "⌥", "R"] : ["Ctrl", "Alt", "R"],
      run: () => {},
    },
  ];

  const [contextMenu, setContextMenu] = useState<BottomPanelContextMenu | null>(null);

  const hiddenTabs = layout.hiddenBottomTabs ?? ["output", "debug", "ports", "comments"];
  const hideBadges = layout.hideBadges ?? {};
  const showPanelIcons = layout.showPanelIcons ?? false;
  const panelPosition = layout.panelPosition ?? "bottom";
  const panelAlign = layout.panelAlign ?? "left";

  const visibleTabs = PANEL_TABS.filter((tab) => !hiddenTabs.includes(tab.id));

  const toggleTabVisibility = (tabId: string) => {
    const isCurrentlyHidden = hiddenTabs.includes(tabId);
    let nextHidden: string[];
    if (isCurrentlyHidden) {
      nextHidden = hiddenTabs.filter((id) => id !== tabId);
    } else {
      if (visibleTabs.length <= 1) return;
      nextHidden = [...hiddenTabs, tabId];
      if (layout.bottomTab === tabId) {
        const nextActive = PANEL_TABS.find((t) => !nextHidden.includes(t.id));
        if (nextActive) {
          changeLayout({ bottomTab: nextActive.id });
        }
      }
    }
    changeLayout({ hiddenBottomTabs: nextHidden });
  };

  const buildContextMenuItems = (): NavigatorMenuItem[] => {
    if (!contextMenu) return [];

    // 1. Terminal Session Context Menu (VS Code Terminal Tab Context Menu)
    if (contextMenu.type === "session") {
      const sess = sessions.find((s) => s.id === contextMenu.sessionId) ?? activeSession;
      return [
        {
          label: t("拆分终端"),
          shortcut: mac ? ["⌘", "\\"] : ["Ctrl", "\\"],
          run: () => toggleSplit(),
        },
        {
          label: t("将终端移动到编辑器区域"),
          run: () => notify?.(t("将终端移动到编辑器区域")),
        },
        {
          label: t("将终端移动到新窗口"),
          run: () => notify?.(t("将终端移动到新窗口")),
        },
        {
          label: t("更改颜色…"),
          separator: true,
          run: () => notify?.(t("更改颜色…")),
        },
        {
          label: t("更改图标…"),
          run: () => notify?.(t("更改图标…")),
        },
        {
          label: t("重命名…"),
          shortcut: ["↩"],
          run: () => {
            const next = window.prompt(t("重命名…"), sess?.name ?? "zsh");
            if (next && next.trim()) {
              setSessions((prev) =>
                prev.map((s) => (s.id === contextMenu.sessionId ? { ...s, name: next.trim() } : s)),
              );
            }
          },
        },
        {
          label: t("切换大小以适应内容宽度"),
          shortcut: mac ? ["⌥", "Z"] : ["Alt", "Z"],
          run: () => notify?.(t("切换大小以适应内容宽度")),
        },
        {
          label: t("终止终端"),
          separator: true,
          shortcut: mac ? ["⌘", "⌫"] : ["Ctrl", "Backspace"],
          run: () => closeSession(contextMenu.sessionId),
        },
      ];
    }

    // 2. Terminal Viewport Context Menu (VS Code Terminal Body Context Menu)
    if (contextMenu.type === "terminal-viewport") {
      return [
        {
          label: t("将终端选定内容或输出添加到对话"),
          shortcut: mac ? ["⌘", "L"] : ["Ctrl", "L"],
          run: () => notify?.(t("将终端选定内容或输出添加到对话")),
        },
        {
          label: t("复制"),
          separator: true,
          shortcut: mac ? ["⌘", "C"] : ["Ctrl", "C"],
          run: () => {
            const sel = window.getSelection()?.toString();
            if (sel) {
              navigator.clipboard?.writeText(sel);
            } else if (activeSession?.input) {
              navigator.clipboard?.writeText(activeSession.input);
            }
          },
        },
        {
          label: t("复制为 HTML"),
          run: () => {
            const sel = window.getSelection()?.toString();
            if (sel) {
              navigator.clipboard?.writeText(`<pre><code>${sel}</code></pre>`);
            }
          },
        },
        {
          label: t("粘贴"),
          shortcut: mac ? ["⌘", "V"] : ["Ctrl", "V"],
          run: () => {
            navigator.clipboard?.readText().then((text) => {
              if (text && primaryInputRef.current) {
                primaryInputRef.current.value += text;
                primaryInputRef.current.dispatchEvent(new Event("input", { bubbles: true }));
                primaryInputRef.current.focus();
              }
            });
          },
        },
        {
          label: t("全选"),
          shortcut: mac ? ["⌘", "A"] : ["Ctrl", "A"],
          run: () => {
            primaryInputRef.current?.select();
          },
        },
        {
          label: t("清空"),
          separator: true,
          shortcut: mac ? ["⌘", "K"] : ["Ctrl", "K"],
          run: () => {
            setSessions((prev) =>
              prev.map((s) => (s.id === activeSessionId ? { ...s, history: [] } : s)),
            );
          },
        },
        {
          label: t("终止终端"),
          separator: true,
          run: () => closeSession(activeSessionId),
        },
        {
          label: t("切换大小以适应内容宽度"),
          separator: true,
          shortcut: mac ? ["⌥", "Z"] : ["Alt", "Z"],
          run: () => notify?.(t("切换大小以适应内容宽度")),
        },
      ];
    }

    // 3. Header Actions Toolbar Context Menu (VS Code Toolbar Customize Context Menu)
    if (contextMenu.type === "actions-toolbar") {
      return [
        {
          label: t("隐藏"),
          disabled: !contextMenu.actionId,
          run: contextMenu.actionId ? () => notify?.(t("隐藏")) : undefined,
        },
        {
          label: t("聚焦终端"),
          separator: true,
          checked: true,
          checkbox: true,
          run: () => primaryInputRef.current?.focus(),
        },
        {
          label: t("新建终端"),
          checked: true,
          checkbox: true,
          run: () => addSession("zsh"),
        },
        {
          label: t("拆分终端"),
          checked: Boolean(splitSession),
          checkbox: true,
          run: () => toggleSplit(),
        },
        {
          label: t("终止终端"),
          checked: true,
          checkbox: true,
          run: () => closeSession(activeSessionId),
        },
        {
          label: t("滚动到上一命令"),
          run: () => {},
        },
        {
          label: t("滚动到下一命令"),
          run: () => {},
        },
        {
          label: t("清空终端"),
          run: () => {
            setSessions((prev) =>
              prev.map((s) => (s.id === activeSessionId ? { ...s, history: [] } : s)),
            );
          },
        },
        {
          label: t("运行活动文件"),
          run: () => {
            window.dispatchEvent(
              new CustomEvent("texsys:terminal:runCommand", { detail: { command: "texsys check" } }),
            );
          },
        },
        {
          label: t("运行选定文本"),
          run: () => {
            const sel = window.getSelection()?.toString()?.trim();
            if (sel) {
              window.dispatchEvent(
                new CustomEvent("texsys:terminal:runCommand", { detail: { command: sel } }),
              );
            }
          },
        },
        {
          label: t("开始听写"),
          run: () => notify?.(t("开始听写")),
        },
        {
          label: t("重置菜单"),
          separator: true,
          run: () => {},
        },
      ];
    }

    // 4 & 5. Header Blank or Tab Context Menu
    const items: NavigatorMenuItem[] = [];

    // If right-clicked on a specific tab (VS Code Tab Context Menu)
    if (contextMenu.type === "tab") {
      const targetTabDef = PANEL_TABS.find((t) => t.id === contextMenu.tabId);
      const targetTabName = targetTabDef ? t(targetTabDef.label) : contextMenu.tabId;
      const targetHasBadge = contextMenu.tabId === "problems";
      const isBadgeHidden = Boolean(hideBadges[contextMenu.tabId]);

      // 1. Configure Keybinding
      items.push({
        label: t("配置键绑定"),
        run: () => {
          window.dispatchEvent(new CustomEvent("texsys:command:openKeybindings"));
          notify?.(`${t("配置键绑定")}: ${targetTabName}`);
        },
      });

      // 2. Hide '{tabName}'
      items.push({
        label: t("隐藏“{name}”", { name: targetTabName }),
        run: visibleTabs.length > 1 ? () => toggleTabVisibility(contextMenu.tabId) : undefined,
      });

      // 3. Hide Badge / Show Badge (for tabs with badge)
      if (targetHasBadge) {
        items.push({
          label: isBadgeHidden ? t("显示徽标") : t("隐藏徽标"),
          run: () => {
            changeLayout({
              hideBadges: {
                ...hideBadges,
                [contextMenu.tabId]: !isBadgeHidden,
              },
            });
          },
        });
      }

      // 4. Move To >
      items.push({
        label: t("移动至"),
        submenu: [
          {
            label: t("面板"),
            checked: true,
            run: () => {},
          },
          {
            label: t("辅助侧边栏"),
            checked: false,
            run: () => {
              notify?.(t("辅助侧边栏"));
            },
          },
        ],
      });

      // Separator before panel views list
      items.push({ label: "", separator: true });
    }

    // Panel views visibility check list (Problems, Output, Tasks and logs, Debug Console, Terminal, Ports, Comments)
    PANEL_TABS.forEach((viewTab) => {
      const isChecked = !hiddenTabs.includes(viewTab.id);
      items.push({
        label: t(viewTab.label),
        checked: isChecked,
        checkbox: true,
        run: () => toggleTabVisibility(viewTab.id),
      });
    });

    // Separator before panel options
    items.push({
      label: "",
      separator: true,
    });

    // Panel Position >
    items.push({
      label: t("面板位置"),
      submenu: [
        {
          label: t("底部"),
          checked: panelPosition === "bottom",
          run: () => changeLayout({ panelPosition: "bottom" }),
        },
        {
          label: t("左侧"),
          checked: panelPosition === "left",
          run: () => changeLayout({ panelPosition: "left" }),
        },
        {
          label: t("右侧"),
          checked: panelPosition === "right",
          run: () => changeLayout({ panelPosition: "right" }),
        },
      ],
    });

    // Align Panel >
    items.push({
      label: t("对齐面板"),
      submenu: [
        {
          label: t("居中"),
          checked: panelAlign === "center",
          run: () => changeLayout({ panelAlign: "center" }),
        },
        {
          label: t("两端对齐"),
          checked: panelAlign === "justify",
          run: () => changeLayout({ panelAlign: "justify" }),
        },
        {
          label: t("靠左"),
          checked: panelAlign === "left",
          run: () => changeLayout({ panelAlign: "left" }),
        },
        {
          label: t("靠右"),
          checked: panelAlign === "right",
          run: () => changeLayout({ panelAlign: "right" }),
        },
      ],
    });

    // Show Icons
    items.push({
      label: t("显示图标"),
      checked: showPanelIcons,
      checkbox: true,
      run: () => changeLayout({ showPanelIcons: !showPanelIcons }),
    });

    // Hide Panel (⌘J)
    items.push({
      label: t("隐藏面板"),
      shortcut: mac ? ["⌘", "J"] : ["Ctrl", "J"],
      run: () => changeLayout({ bottomOpen: false, bottomMaximized: false }),
    });

    return items;
  };

  const handleHeaderDoubleClick = (event: React.MouseEvent<HTMLDivElement>) => {
    if ((event.target as HTMLElement).closest("button")) return;
    changeLayout({ bottomMaximized: !layout.bottomMaximized, bottomOpen: true });
  };

  const handleHeaderContextMenu = (event: React.MouseEvent<HTMLDivElement>) => {
    const actionsToolbar = (event.target as HTMLElement).closest(".bottom-panel-actions");
    if (actionsToolbar) {
      event.preventDefault();
      event.stopPropagation();
      const btn = (event.target as HTMLElement).closest("button");
      const actionId = btn?.getAttribute("aria-label") ?? btn?.getAttribute("title");
      setContextMenu({
        type: "actions-toolbar",
        actionId: actionId ?? undefined,
        point: { x: event.clientX, y: event.clientY },
      });
      return;
    }
    if ((event.target as HTMLElement).closest(".terminal-header-session-wrapper")) {
      return;
    }
    if ((event.target as HTMLElement).closest("button")) return;
    event.preventDefault();
    setContextMenu({ type: "header", point: { x: event.clientX, y: event.clientY } });
  };

  return (
    <>
      <div
        className="bottom-panel-header"
        onDoubleClick={handleHeaderDoubleClick}
        onContextMenu={handleHeaderContextMenu}
      >
        <InlineTabStrip
          className="bottom-tabs"
          label={t("底部工具")}
          showIcons={showPanelIcons}
          align={panelAlign}
          tabs={visibleTabs.map((tabDef) => {
            const isBadgeHidden = Boolean(hideBadges[tabDef.id]);
            let badge: number | string | undefined = undefined;
            if (tabDef.id === "problems" && !isBadgeHidden && problemsCount > 0) {
              badge = problemsCount;
            }
            return {
              id: tabDef.id,
              label: t(tabDef.label),
              icon: tabDef.icon,
              badge,
              focused: layout.bottomTab === tabDef.id,
            };
          })}
          onSelect={(id) => changeLayout({ bottomTab: id })}
          onContextMenu={(event, id) => {
            setContextMenu({ type: "tab", tabId: id, point: { x: event.clientX, y: event.clientY } });
          }}
        />

        {layout.bottomTab === "terminal" ? (
          <div
            className="bottom-panel-actions"
            role="toolbar"
            aria-label={t("终端操作")}
            onContextMenu={(e) => {
              e.preventDefault();
              e.stopPropagation();
              const btn = (e.target as HTMLElement).closest("button");
              const actionId = btn?.getAttribute("aria-label") ?? btn?.getAttribute("title");
              setContextMenu({
                type: "actions-toolbar",
                actionId: actionId ?? undefined,
                point: { x: e.clientX, y: e.clientY },
              });
            }}
          >
            {/* Session indicator / switcher in single-terminal mode (VS Code Image 4) */}
            {/* Session indicator / switcher in single-terminal mode (VS Code Image 4) */}
            {!showSessionsSidebar && activeSession && (
              <div
                className="terminal-header-session-wrapper"
                onMouseEnter={() => handleSessionMouseEnter(activeSession.id)}
                onMouseLeave={handleSessionMouseLeave}
                onContextMenu={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  setContextMenu({
                    type: "session",
                    sessionId: activeSession.id,
                    point: { x: e.clientX, y: e.clientY },
                  });
                }}
              >
                <NavigatorMenu
                  label={activeSession.name}
                  items={sessionsMenuItems}
                  placement="start"
                  direction="down"
                  triggerClassName="terminal-header-session"
                  triggerIcon={
                    <>
                      <span className="terminal-session-icon">
                        <Icon name="terminal" size={15} strokeWidth={1.5} />
                      </span>
                      <span className="terminal-session-title">{activeSession.name}</span>
                      {dirty && (
                        <span className="terminal-session-warning" title={t("有未暂存变更")}>
                          <Icon name="warning" size={13} strokeWidth={1.5} />
                        </span>
                      )}
                    </>
                  }
                />
                {hoveredSessionId === activeSession.id && renderHoverDetails(activeSession)}
              </div>
            )}

            {/* New Terminal split button: + ⌵ */}
            <div className="panel-split-button">
              <button
                type="button"
                className="panel-split-main"
                title={`${t("新建终端")} ${mac ? "(⌃⇧`)" : "(Ctrl+Shift+`)"}`}
                aria-label={t("新建终端")}
                onClick={() => addSession("zsh")}
              >
                <Icon name="plus" size={16} strokeWidth={1.5} />
              </button>
              <NavigatorMenu
                label={t("新建终端与配置")}
                title={t("新建终端与配置")}
                items={profileMenuItems}
                placement="start"
                direction="down"
                triggerClassName="panel-split-chevron"
                triggerIcon={<Icon name="chevron-down" size={14} strokeWidth={1.5} />}
              />
            </div>

            {/* Split Terminal button when single terminal */}
            {!showSessionsSidebar && (
              <button
                type="button"
                className="panel-header-action"
                title={`${t("拆分终端")} ${mac ? "(⌘\\)" : "(Ctrl+\\)"}`}
                aria-label={t("拆分终端")}
                onClick={toggleSplit}
              >
                <Icon name="split-horizontal" size={16} strokeWidth={1.5} />
              </button>
            )}

            {/* Kill / Close Terminal button when single terminal */}
            {!showSessionsSidebar && (
              <button
                type="button"
                className="panel-header-action"
                title={t("终止终端")}
                aria-label={t("终止终端")}
                onClick={() => closeSession(activeSessionId)}
              >
                <Icon name="trash" size={16} strokeWidth={1.5} />
              </button>
            )}

            {/* More actions menu */}
            <NavigatorMenu
              label={t("更多")}
              title={t("更多操作…")}
              items={moreMenuItems}
              placement="end"
              direction="down"
              triggerClassName="panel-header-action"
              triggerIcon={<Icon name="more" size={16} strokeWidth={1.5} />}
            />

            <div className="panel-actions-separator" aria-hidden="true" />

            {/* Maximize / Restore Panel button */}
            <button
              type="button"
              className="panel-header-action"
              title={layout.bottomMaximized ? t("还原面板大小") : t("最大化面板大小")}
              aria-label={layout.bottomMaximized ? t("还原面板大小") : t("最大化面板大小")}
              onClick={() =>
                changeLayout({ bottomMaximized: !layout.bottomMaximized, bottomOpen: true })
              }
            >
              <Icon
                name={layout.bottomMaximized ? "panel-restore" : "panel-maximize"}
                size={16}
                strokeWidth={1.5}
              />
            </button>

            {/* Close Panel button */}
            <button
              type="button"
              className="panel-header-action"
              title={t("关闭面板")}
              aria-label={t("关闭面板")}
              onClick={() => changeLayout({ bottomOpen: false, bottomMaximized: false })}
            >
              <Icon name="close" size={16} strokeWidth={1.5} />
            </button>
          </div>
        ) : (
          <div className="bottom-panel-actions" role="toolbar" aria-label={t("面板操作")}>
            {layout.bottomTab === "problems" && openChecks && (
              <>
                <button type="button" className="text-button" onClick={openChecks}>
                  {t("完整检查")}
                  <Icon name="arrow" size={13} />
                </button>
                <div className="panel-actions-separator" aria-hidden="true" />
              </>
            )}

            <button
              type="button"
              className="panel-header-action"
              title={layout.bottomMaximized ? t("还原面板大小") : t("最大化面板大小")}
              aria-label={layout.bottomMaximized ? t("还原面板大小") : t("最大化面板大小")}
              onClick={() =>
                changeLayout({ bottomMaximized: !layout.bottomMaximized, bottomOpen: true })
              }
            >
              <Icon
                name={layout.bottomMaximized ? "panel-restore" : "panel-maximize"}
                size={16}
                strokeWidth={1.5}
              />
            </button>

            <button
              type="button"
              className="panel-header-action"
              title={t("关闭面板")}
              aria-label={t("关闭面板")}
              onClick={() => changeLayout({ bottomOpen: false, bottomMaximized: false })}
            >
              <Icon name="close" size={16} strokeWidth={1.5} />
            </button>
          </div>
        )}
      </div>

      {contextMenu && (
        <NavigatorMenu
          label={
            contextMenu.type === "session"
              ? t("终端会话操作")
              : contextMenu.type === "terminal-viewport"
                ? t("终端操作")
                : contextMenu.type === "actions-toolbar"
                  ? t("终端操作")
                  : contextMenu.type === "tab"
                    ? t("选项卡操作")
                    : t("面板操作")
          }
          point={contextMenu.point}
          onDismiss={() => setContextMenu(null)}
          items={buildContextMenuItems()}
        />
      )}

      <div className="bottom-panel-content" hidden={layout.bottomTab !== "problems"}>
        {problemsContent}
      </div>

      <div className="bottom-panel-content" hidden={layout.bottomTab !== "output"}>
        <div className="bottom-panel-empty-view">
          <div className="bottom-panel-empty-text">
            <Icon name="file" size={24} />
            <p>{t("输出")}</p>
          </div>
        </div>
      </div>

      <div className="bottom-panel-content" hidden={layout.bottomTab !== "tasks"}>
        {taskPanel}
      </div>

      <div className="bottom-panel-content" hidden={layout.bottomTab !== "debug"}>
        <div className="bottom-panel-empty-view">
          <div className="bottom-panel-empty-text">
            <Icon name="code" size={24} />
            <p>{t("调试控制台")}</p>
          </div>
        </div>
      </div>

      <div className="bottom-panel-content" hidden={layout.bottomTab !== "ports"}>
        <div className="bottom-panel-empty-view">
          <div className="bottom-panel-empty-text">
            <Icon name="cube" size={24} />
            <p>{t("端口")}</p>
          </div>
        </div>
      </div>

      <div className="bottom-panel-content" hidden={layout.bottomTab !== "comments"}>
        <div className="bottom-panel-empty-view">
          <div className="bottom-panel-empty-text">
            <Icon name="info" size={24} />
            <p>{t("注释")}</p>
          </div>
        </div>
      </div>

      <div
        className="bottom-panel-content terminal-tab-content"
        hidden={layout.bottomTab !== "terminal"}
      >
        <div className="terminal-panel-body" role="region" aria-label={t("终端")}>
          <div
            className="terminal-view-area"
            onContextMenu={(e) => {
              e.preventDefault();
              e.stopPropagation();
              setContextMenu({
                type: "terminal-viewport",
                sessionId: activeSessionId,
                point: { x: e.clientX, y: e.clientY },
              });
            }}
          >
            {activeSession && renderPane(activeSession, false, primaryInputRef, primaryScrollRef)}
            {splitSession && renderPane(splitSession, true, secondaryInputRef, secondaryScrollRef)}
          </div>

          {/* Terminal layout component: Sessions list on the right (hidden when only 1 terminal session and no split, matching VS Code singleTerminal hideCondition) */}
          {showSessionsSidebar && (
            <aside className="terminal-sessions-sidebar" role="tablist" aria-label={t("终端会话")}>
              {sessions.map((sess, idx) => {
                const isSplitItem = Boolean(splitSession);
                const branch = isSplitItem ? (idx === 0 ? "┌" : "└") : null;
                const isActive = sess.id === activeSessionId;
                return (
                  <div
                    key={sess.id}
                    className={`terminal-session-row ${isActive ? "is-active" : ""}`}
                    role="tab"
                    aria-selected={isActive}
                    tabIndex={0}
                    onMouseEnter={() => handleSessionMouseEnter(sess.id)}
                    onMouseLeave={handleSessionMouseLeave}
                    onContextMenu={(e) => {
                      e.preventDefault();
                      e.stopPropagation();
                      setContextMenu({
                        type: "session",
                        sessionId: sess.id,
                        point: { x: e.clientX, y: e.clientY },
                      });
                    }}
                    onClick={() => {
                      setActiveSessionId(sess.id);
                      setFocusedPane("primary");
                      primaryInputRef.current?.focus();
                    }}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        setActiveSessionId(sess.id);
                        setFocusedPane("primary");
                        primaryInputRef.current?.focus();
                      }
                    }}
                  >
                    {branch && (
                      <span className="terminal-tree-branch" aria-hidden="true">{branch}</span>
                    )}
                    <span className="terminal-row-icon">
                      <Icon name="terminal" size={15} strokeWidth={1.5} />
                    </span>
                    <span className="terminal-row-name">
                      {sessions.length > 1 ? `${idx + 1}: ${sess.name}` : sess.name}
                    </span>
                    {dirty && idx === 0 && (
                      <span className="terminal-row-warning" title={t("有未暂存变更")}>
                        <Icon name="warning" size={13} strokeWidth={1.5} />
                      </span>
                    )}
                    <div className="terminal-row-tools">
                      <button
                        type="button"
                        className="terminal-row-tool"
                        title={`${t("拆分终端")} ${mac ? "(⌘\\)" : "(Ctrl+\\)"}`}
                        aria-label={t("拆分终端")}
                        onClick={(e) => {
                          e.stopPropagation();
                          toggleSplit();
                        }}
                      >
                        <Icon name="split-horizontal" size={15} strokeWidth={1.5} />
                      </button>
                      <button
                        type="button"
                        className="terminal-row-tool"
                        title={t("终止终端")}
                        aria-label={t("终止终端")}
                        onClick={(e) => {
                          e.stopPropagation();
                          closeSession(sess.id);
                        }}
                      >
                        <Icon name="trash" size={15} strokeWidth={1.5} />
                      </button>
                    </div>
                    {hoveredSessionId === sess.id && renderHoverDetails(sess)}
                  </div>
                );
              })}
            </aside>
          )}
        </div>
      </div>
    </>
  );
}
