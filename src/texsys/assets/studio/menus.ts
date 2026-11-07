/** Shared menu model. Presentation belongs to the platform host, command IDs stay stable. */
export type MenuItem = {
  id: string;
  label: string;
  keys?: string[];
  icon?: string;
  searchOnly?: boolean;
  keywords?: string;
};
export type MenuGroup = { id: string; label: string; items: MenuItem[] };
export const closeDocumentCommand = {
  id: "document.close",
  label: "关闭当前文档",
  icon: "close",
  keywords: "Close editor 关闭标签页",
} satisfies MenuItem;

export function closeDocumentKeys(mac: boolean): string[] {
  return mac ? ["⌘", "W"] : ["Ctrl", "W"];
}

export function isCloseDocumentShortcut(event: KeyboardEvent, mac: boolean): boolean {
  return (
    event.key.toLowerCase() === "w" &&
    !event.altKey &&
    !event.shiftKey &&
    event.metaKey === mac &&
    event.ctrlKey === !mac
  );
}

export const studioMenus: MenuGroup[] = [
  {
    id: "project",
    label: "项目",
    items: [
      { id: "project.overview", label: "项目概览", icon: "folder" },
      { id: "project.refresh", label: "读取外部修改", icon: "refresh" },
      closeDocumentCommand,
    ],
  },
  {
    id: "view",
    label: "视图",
    items: [
      {
        id: "workspace.explorer",
        label: "文件资源管理器",
        icon: "files",
        keywords: "Explorer 文件树",
      },
      {
        id: "workspace.search",
        label: "搜索项目内容",
        icon: "search",
        keywords: "Search 全文搜索",
      },
      {
        id: "workspace.scm",
        label: "源代码管理",
        icon: "branch",
        keywords: "Source Control Git 版本控制",
      },
      {
        id: "view.sidebar",
        label: "资源面板",
        icon: "sidebar",
        searchOnly: true,
        keywords: "布局 侧栏 左侧",
      },
      {
        id: "view.bottom",
        label: "底部面板",
        icon: "bottom-panel",
        searchOnly: true,
        keywords: "布局",
      },
      {
        id: "view.inspector",
        label: "属性面板",
        icon: "inspector",
        searchOnly: true,
        keywords: "布局 侧栏 右侧",
      },
      { id: "view.fullscreen", label: "全屏", keywords: "Full Screen 专注 编辑器快捷键" },
    ],
  },
  {
    id: "go",
    label: "导航",
    items: [
      { id: "commands.open", label: "命令面板…", icon: "search" },
      { id: "files.search", label: "搜索文件…", icon: "file" },
      { id: "navigation.back", label: "后退", icon: "back" },
      { id: "navigation.forward", label: "前进", icon: "forward" },
    ],
  },
  {
    id: "tasks",
    label: "任务",
    items: [
      { id: "tasks.show", label: "显示任务与日志", icon: "terminal" },
      { id: "problems.show", label: "显示问题", icon: "check" },
    ],
  },
  {
    id: "help",
    label: "帮助",
    items: [{ id: "help.capabilities", label: "专业能力与范围", icon: "info" }],
  },
];
export type CommandItem = MenuItem & { group: string; disabled?: boolean; checked?: boolean };
export function filterCommands(commands: CommandItem[], query: string): CommandItem[] {
  const terms = query
    .replace(/^\s*>\s*/, "")
    .trim()
    .toLocaleLowerCase()
    .split(/\s+/)
    .filter(Boolean);
  return commands.filter(
    (command) =>
      (!command.searchOnly || terms.length > 0) &&
      terms.every((term) =>
        `${command.group} ${command.label} ${command.keywords ?? ""}`
          .toLocaleLowerCase()
          .includes(term),
      ),
  );
}
