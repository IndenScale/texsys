import React, { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { Icon } from "./ui";
import { NavigatorMenu, type NavigatorMenuItem } from "./NavigatorMenu";
import { InlineTabStrip } from "./atomstudio/WorkspaceTabs";
import { t } from "./i18n";
import type { StudioLayout } from "./sdk";
import {
  TerminalPane,
  type TerminalControls,
  type TerminalInfo,
  type TerminalProfile,
  type TerminalSession,
} from "./TerminalPane";
import { useUserPreferences } from "./user-preferences";
import "./terminal.css";

function createSession(profile: TerminalProfile = "default", command?: string): TerminalSession {
  return { id: crypto.randomUUID(), profile, command };
}

function sessionName(session: TerminalSession) {
  return (
    session.name ??
    session.info?.shell ??
    (session.profile === "default" ? "Shell" : session.profile)
  );
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
  dirty = true,
  notify,
}: {
  layout: StudioLayout;
  changeLayout: (patch: Partial<StudioLayout>) => void;
  problemsContent?: ReactNode;
  problemsCount?: number;
  openChecks?: () => void;
  taskPanel: ReactNode;
  dirty?: boolean;
  notify?: (message: string, isError?: boolean) => void;
}) {
  const { terminalFontSize } = useUserPreferences();
  const [sessions, setSessions] = useState<TerminalSession[]>(() => [createSession()]);
  const [primaryId, setPrimaryId] = useState(() => sessions[0].id);
  const [splitSessionId, setSplitSessionId] = useState<string | null>(null);
  const [focusedPane, setFocusedPane] = useState<"primary" | "secondary">("primary");
  const controls = useRef(new Map<string, TerminalControls>()).current;
  const panel = useRef<HTMLDivElement>(null);
  const [focusRequest, setFocusRequest] = useState(0);
  const focusedRequest = useRef(0);
  const primary = sessions.find((session) => session.id === primaryId) ?? sessions[0];
  const splitSession = sessions.find((session) => session.id === splitSessionId);
  const activeSession = (focusedPane === "secondary" ? splitSession : primary) ?? primary;
  const activeSessionId = activeSession?.id ?? "";
  const showSessionsSidebar = sessions.length > 1 || Boolean(splitSession);

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
      <div className="terminal-hover-title">{sessionName(sess)}</div>
      <div className="terminal-hover-divider" />
      {sess.info && (
        <div className="terminal-hover-meta">
          <div className="terminal-hover-row">
            <span>{t("进程 ID (PID)：{pid}", { pid: sess.info.pid })}</span>
          </div>
          <div className="terminal-hover-row">
            <span>{t("命令行：{command}", { command: sess.info.commandLine })}</span>
          </div>
        </div>
      )}
    </div>
  );

  const addSession = useCallback((profile: TerminalProfile = "default", command?: string) => {
    const next = createSession(profile, command);
    setSessions((current) => [...current, next]);
    setPrimaryId(next.id);
    setFocusedPane("primary");
  }, []);

  const selectSession = (id: string) => {
    if (id === splitSessionId) {
      setFocusedPane("secondary");
    } else {
      setPrimaryId(id);
      setFocusedPane("primary");
    }
    controls.get(id)?.focus();
  };

  const closeSession = useCallback(
    (id: string) => {
      const next = sessions.filter((session) => session.id !== id);
      setSessions(next);
      if (id === splitSessionId) {
        setSplitSessionId(null);
        setFocusedPane("primary");
      } else if (id === primary?.id) {
        const replacement = next.find((session) => session.id !== splitSessionId) ?? next[0];
        setPrimaryId(replacement?.id ?? "");
        if (replacement?.id === splitSessionId) setSplitSessionId(null);
        setFocusedPane("primary");
      }
    },
    [sessions, primary?.id, splitSessionId],
  );

  const toggleSplit = useCallback(() => {
    if (splitSessionId) {
      setSplitSessionId(null);
      setFocusedPane("primary");
    } else if (primary) {
      const next = createSession(primary.profile);
      setSessions((current) => [...current, next]);
      setSplitSessionId(next.id);
      setFocusedPane("secondary");
    } else {
      addSession();
    }
  }, [addSession, primary, splitSessionId]);

  const clearSession = useCallback((id: string) => controls.get(id)?.clear(), [controls]);
  const terminalReady = useCallback((id: string, info: TerminalInfo) => {
    setSessions((current) =>
      current.map((session) => (session.id === id ? { ...session, info } : session)),
    );
  }, []);

  useEffect(() => {
    const inActiveWorkbench = () =>
      !!panel.current && !panel.current.closest(".professional-workbench[hidden]");
    const handleNew = () => {
      if (inActiveWorkbench()) addSession();
    };
    const handleSplit = () => {
      if (inActiveWorkbench()) toggleSplit();
    };
    const handleClear = () => {
      if (!inActiveWorkbench()) return;
      if (activeSessionId) clearSession(activeSessionId);
      else notify?.(t("没有可用的终端会话"));
    };
    const handleFocus = () => {
      if (!inActiveWorkbench()) return;
      if (!activeSessionId) addSession();
      setFocusRequest((value) => value + 1);
    };
    const handleTerminate = () => {
      if (!inActiveWorkbench()) return;
      if (activeSessionId) closeSession(activeSessionId);
      else notify?.(t("没有可用的终端会话"));
    };
    const handleRunCommand = (event: Event) => {
      const { command } = (event as CustomEvent<{ command: string }>).detail;
      if (inActiveWorkbench() && command?.trim()) addSession("default", command);
    };
    window.addEventListener("texsys:terminal:new", handleNew);
    window.addEventListener("texsys:terminal:split", handleSplit);
    window.addEventListener("texsys:terminal:clear", handleClear);
    window.addEventListener("texsys:terminal:focus", handleFocus);
    window.addEventListener("texsys:terminal:terminate", handleTerminate);
    window.addEventListener("texsys:terminal:runCommand", handleRunCommand);
    return () => {
      window.removeEventListener("texsys:terminal:new", handleNew);
      window.removeEventListener("texsys:terminal:split", handleSplit);
      window.removeEventListener("texsys:terminal:clear", handleClear);
      window.removeEventListener("texsys:terminal:focus", handleFocus);
      window.removeEventListener("texsys:terminal:terminate", handleTerminate);
      window.removeEventListener("texsys:terminal:runCommand", handleRunCommand);
    };
  }, [activeSessionId, addSession, clearSession, closeSession, notify, toggleSplit]);
  useEffect(() => {
    if (
      focusedRequest.current === focusRequest ||
      !layout.bottomOpen ||
      layout.bottomTab !== "terminal"
    )
      return;
    const frame = requestAnimationFrame(() => {
      const terminal = controls.get(activeSessionId);
      if (terminal) {
        terminal.focus();
        focusedRequest.current = focusRequest;
      }
    });
    return () => cancelAnimationFrame(frame);
  }, [activeSessionId, controls, focusRequest, layout.bottomOpen, layout.bottomTab, sessions]);

  const runActiveFile = () => window.dispatchEvent(new Event("texsys:terminal:runActiveFile"));
  const runSelectedText = () => {
    const selection = controls.get(activeSessionId)?.getSelection();
    if (selection?.trim()) addSession("default", selection);
    else window.dispatchEvent(new Event("texsys:terminal:runSelectedText"));
  };

  // Header profile and action menus
  const profileMenuItems: NavigatorMenuItem[] = [
    {
      label: t("新建终端"),
      icon: "terminal",
      shortcut: mac ? ["⌃", "⇧", "`"] : ["Ctrl", "Shift", "`"],
      run: () => addSession(),
    },
    {
      label: t("新建终端窗口"),
      shortcut: mac ? ["⌃", "⌥", "⇧", "`"] : ["Ctrl", "Alt", "Shift", "`"],
      run: () => addSession(),
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
      icon: "terminal",
      run: () => addSession("zsh"),
    },
    {
      label: t("清空终端"),
      separator: true,
      shortcut: [mac ? "⌘" : "Ctrl", "K"],
      run: () => clearSession(activeSessionId),
    },
  ];

  const sessionsMenuItems: NavigatorMenuItem[] = [
    ...sessions.map((sess) => ({
      label: sessionName(sess),
      icon: "terminal",
      checked: sess.id === activeSessionId,
      run: () => selectSession(sess.id),
    })),
    {
      label: t("新建终端"),
      separator: true,
      icon: "plus",
      run: () => addSession(),
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
      run: () => controls.get(activeSessionId)?.scrollLines(-5),
    },
    {
      label: t("滚动到下一命令"),
      shortcut: mac ? ["⇧", "⌘", "↓"] : ["Shift", "Ctrl", "↓"],
      run: () => controls.get(activeSessionId)?.scrollLines(5),
    },
    {
      label: t("清空终端"),
      shortcut: [mac ? "⌘" : "Ctrl", "K"],
      run: () => clearSession(activeSessionId),
    },
    {
      label: t("运行活动文件"),
      run: runActiveFile,
    },
    {
      label: t("运行选定文本"),
      run: runSelectedText,
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
            const next = window.prompt(t("重命名…"), sess ? sessionName(sess) : "Shell");
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
      const terminal = controls.get(contextMenu.sessionId);
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
            const selection = terminal?.getSelection();
            if (selection) void navigator.clipboard.writeText(selection);
          },
        },
        {
          label: t("复制为 HTML"),
          run: () => {
            const selection = terminal?.getSelection();
            if (!selection) return;
            const pre = document.createElement("pre");
            pre.textContent = selection;
            void navigator.clipboard.write([
              new ClipboardItem({ "text/html": new Blob([pre.outerHTML], { type: "text/html" }) }),
            ]);
          },
        },
        {
          label: t("粘贴"),
          shortcut: mac ? ["⌘", "V"] : ["Ctrl", "V"],
          run: async () => {
            if (!terminal) return;
            const text = await navigator.clipboard.readText();
            terminal.focus();
            terminal.paste(text);
          },
        },
        {
          label: t("全选"),
          shortcut: mac ? ["⌘", "A"] : ["Ctrl", "A"],
          run: () => terminal?.selectAll(),
        },
        {
          label: t("清空"),
          separator: true,
          shortcut: mac ? ["⌘", "K"] : ["Ctrl", "K"],
          run: () => terminal?.clear(),
        },
        {
          label: t("终止终端"),
          separator: true,
          run: () => closeSession(contextMenu.sessionId),
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
          run: contextMenu.actionId ? () => notify?.(t("隐藏")) : undefined,
        },
        {
          label: t("聚焦终端"),
          separator: true,
          checked: true,
          checkbox: true,
          run: () => controls.get(activeSessionId)?.focus(),
        },
        {
          label: t("新建终端"),
          checked: true,
          checkbox: true,
          run: () => addSession(),
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
          run: () => clearSession(activeSessionId),
        },
        {
          label: t("运行活动文件"),
          run: runActiveFile,
        },
        {
          label: t("运行选定文本"),
          run: runSelectedText,
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
            setContextMenu({
              type: "tab",
              tabId: id,
              point: { x: event.clientX, y: event.clientY },
            });
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
                  label={sessionName(activeSession)}
                  items={sessionsMenuItems}
                  placement="start"
                  direction="down"
                  triggerClassName="terminal-header-session"
                  triggerIcon={
                    <>
                      <span className="terminal-session-icon">
                        <Icon name="terminal" size={15} strokeWidth={1.5} />
                      </span>
                      <span className="terminal-session-title">{sessionName(activeSession)}</span>
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
                onClick={() => addSession()}
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
        <div
          className="terminal-panel-body"
          role="region"
          aria-label={t("终端")}
          style={{ fontSize: terminalFontSize }}
        >
          <div className="terminal-view-area" ref={panel}>
            {sessions.map((session) => {
              const isSecondary = session.id === splitSessionId;
              return (
                <TerminalPane
                  key={session.id}
                  session={session}
                  visible={
                    layout.bottomOpen &&
                    layout.bottomTab === "terminal" &&
                    (isSecondary || session.id === primary?.id)
                  }
                  focused={session.id === activeSessionId}
                  split={Boolean(splitSession) && !isSecondary}
                  fontSize={terminalFontSize}
                  controls={controls}
                  onFocus={() => setFocusedPane(isSecondary ? "secondary" : "primary")}
                  onReady={terminalReady}
                  onContextMenu={(event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    setContextMenu({
                      type: "terminal-viewport",
                      sessionId: session.id,
                      point: { x: event.clientX, y: event.clientY },
                    });
                  }}
                />
              );
            })}
          </div>

          {/* Terminal layout component: Sessions list on the right (hidden when only 1 terminal session and no split, matching VS Code singleTerminal hideCondition) */}
          {showSessionsSidebar && (
            <aside className="terminal-sessions-sidebar" role="tablist" aria-label={t("终端会话")}>
              {sessions.map((sess, idx) => {
                const branch =
                  splitSession &&
                  (sess.id === primary?.id ? "┌" : sess.id === splitSession.id ? "└" : null);
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
                    onClick={() => selectSession(sess.id)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        selectSession(sess.id);
                      }
                    }}
                  >
                    {branch && (
                      <span className="terminal-tree-branch" aria-hidden="true">
                        {branch}
                      </span>
                    )}
                    <span className="terminal-row-icon">
                      <Icon name="terminal" size={15} strokeWidth={1.5} />
                    </span>
                    <span className="terminal-row-name">
                      {sessions.length > 1 ? `${idx + 1}: ${sessionName(sess)}` : sessionName(sess)}
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
