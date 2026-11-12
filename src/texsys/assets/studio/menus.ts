/** Shared menu model. Presentation belongs to the platform host, command IDs stay stable. */
export type MenuItem = {
  id: string;
  label: string;
  keys?: string[];
  icon?: string;
  searchOnly?: boolean;
  keywords?: string;
  separator?: boolean;
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

const sourceEditCommands = [
  { id: "editor.undo", label: "撤销", keywords: "Undo" },
  { id: "editor.redo", label: "重做", keywords: "Redo 恢复" },
  { id: "editor.find", label: "在当前文件中查找…", keywords: "Find in current file" },
  { id: "editor.replace", label: "在当前文件中替换…", keywords: "Replace in current file" },
  { id: "editor.comment", label: "切换行注释", keywords: "Toggle Line Comment" },
  { id: "editor.selectAll", label: "全选", separator: true, keywords: "Select All" },
  { id: "editor.nextMatch", label: "添加下一个匹配项", keywords: "Add Next Match Selection" },
  { id: "editor.allMatches", label: "选择所有匹配项", keywords: "Select All Matches" },
] as const satisfies readonly MenuItem[];
export type SourceEditCommand = (typeof sourceEditCommands)[number]["id"];
const fileEditorCommands = [
  "file.new",
  "file.untitled",
  "file.saveAs",
  "file.saveAll",
  "file.revert",
  "editor.gotoLine",
  "help.shortcuts",
] as const;
export type EditorMenuCommand = (typeof fileEditorCommands)[number] | SourceEditCommand;

export function isEditorMenuCommand(id: string): id is EditorMenuCommand {
  return fileEditorCommands.some((command) => command === id) || isSourceEditCommand(id);
}

export function isSourceEditCommand(id: string): id is SourceEditCommand {
  return sourceEditCommands.some((command) => command.id === id);
}

export function sourceEditKeys(mac: boolean): Record<SourceEditCommand, string[]> {
  return {
    "editor.undo": [mac ? "⌘" : "Ctrl", "Z"],
    "editor.redo": mac ? ["⇧", "⌘", "Z"] : ["Ctrl", "Y"],
    "editor.find": [mac ? "⌘" : "Ctrl", "F"],
    "editor.replace": mac ? ["⌥", "⌘", "F"] : ["Ctrl", "H"],
    "editor.comment": [mac ? "⌘" : "Ctrl", "/"],
    "editor.selectAll": [mac ? "⌘" : "Ctrl", "A"],
    "editor.nextMatch": [mac ? "⌘" : "Ctrl", "D"],
    "editor.allMatches": mac ? ["⇧", "⌘", "L"] : ["Ctrl", "Shift", "L"],
  };
}

export const studioMenus: MenuGroup[] = [
  {
    id: "file",
    label: "文件",
    items: [
      {
        id: "file.untitled",
        label: "新建文本文件",
        icon: "new-file",
        keywords: "New Text File Untitled",
      },
      { id: "file.new", label: "新建文件…", icon: "new-file", keywords: "New File Create File" },
      {
        id: "file.newWindow",
        label: "新建窗口",
        keywords: "New Window New Tab Empty Workspace 新建标签页",
      },
      { id: "file.open", label: "打开文件…", separator: true, icon: "file", keywords: "Open File" },
      { id: "file.recent", label: "打开最近的文件…", keywords: "Open Recent Files" },
      {
        id: "document.save",
        label: "保存",
        separator: true,
        icon: "save",
        keywords: "Save File 保存文件",
      },
      { id: "file.saveAs", label: "另存为…", icon: "save", keywords: "Save As" },
      { id: "file.saveAll", label: "全部保存", keywords: "Save All" },
      { id: "file.autoSave", label: "自动保存", separator: true, keywords: "Auto Save" },
      { id: "settings.open", label: "设置…", icon: "settings", keywords: "Preferences" },
      { id: "file.revert", label: "还原文件…", separator: true, keywords: "Revert File" },
      closeDocumentCommand,
      {
        id: "file.confirmUnload",
        label: "离开前确认",
        separator: true,
        keywords: "Confirm Unsaved Changes Before Close",
      },
    ],
  },
  {
    id: "edit",
    label: "编辑",
    items: [
      ...sourceEditCommands,
      { id: "workspace.find", label: "在文件中查找…", separator: true, keywords: "Find in Files" },
      { id: "workspace.replace", label: "在文件中替换…", keywords: "Replace in Files" },
    ],
  },
  {
    id: "view",
    label: "查看",
    items: [
      { id: "commands.open", label: "命令面板…", icon: "search" },
      { id: "view.open", label: "打开视图…", icon: "search", keywords: "Open View" },
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
      {
        id: "view.toggleMaximize",
        label: "切换底部最大化",
        searchOnly: true,
        keywords: "最大化 还原 Maximize Restore",
      },
      {
        id: "view.terminal",
        label: "终端",
        icon: "terminal",
        searchOnly: true,
        keywords: "Terminal",
      },
      {
        id: "view.problems",
        label: "显示问题",
        icon: "check",
        searchOnly: true,
        keywords: "Problems",
      },
      { id: "tasks.show", label: "显示任务与日志", icon: "terminal" },
      {
        id: "view.panelPosition",
        label: "面板位置…",
        separator: true,
        keywords: "Dock Panel Position",
      },
      { id: "view.statusbar", label: "状态栏", keywords: "Status Bar 外观 布局" },
      {
        id: "view.screenReader",
        label: "屏幕阅读器模式",
        searchOnly: true,
        keywords: "Screen Reader Mode Accessibility 无障碍",
      },
      { id: "view.wordWrap", label: "自动换行", separator: true, keywords: "Word Wrap" },
      { id: "view.fullscreen", label: "全屏", keywords: "Full Screen 专注 编辑器快捷键" },
    ],
  },
  {
    id: "go",
    label: "转到",
    items: [
      { id: "files.search", label: "快速打开文件…", icon: "file", keywords: "Quick Open 搜索文件" },
      { id: "navigation.back", label: "后退", icon: "back" },
      { id: "navigation.forward", label: "前进", icon: "forward" },
      { id: "editor.gotoLine", label: "转到行／列…", keywords: "Go to Line Column" },
      {
        id: "navigation.nextIssue",
        label: "下一个问题",
        separator: true,
        keywords: "Next Problem",
        keys: ["F8"],
      },
      {
        id: "navigation.previousIssue",
        label: "上一个问题",
        keywords: "Previous Problem",
        keys: ["Shift", "F8"],
      },
      {
        id: "objects.search",
        label: "转到工程对象…",
        separator: true,
        keywords: "Go to Model Object Symbol",
      },
      { id: "objects.definition", label: "转到定义", keywords: "Go to Definition" },
      { id: "objects.references", label: "查找使用位置…", keywords: "Find References Usages" },
      { id: "project.overview", label: "项目概览", icon: "folder" },
    ],
  },
  {
    id: "run",
    label: "运行",
    items: [{ id: "tasks.terminate", label: "取消任务", keywords: "Cancel Stop Terminate Task" }],
  },
  {
    id: "terminal",
    label: "终端",
    items: [
      { id: "terminal.new", label: "新建终端", icon: "terminal", keywords: "New Terminal" },
      {
        id: "terminal.split",
        label: "拆分终端",
        icon: "split-horizontal",
        keywords: "Split Terminal",
      },
      { id: "terminal.focus", label: "聚焦终端", keywords: "Focus Terminal" },
      { id: "terminal.clear", label: "清空终端", keywords: "Clear Terminal" },
      { id: "terminal.terminate", label: "终止终端", keywords: "Kill Terminal" },
      { id: "terminal.runActiveFile", label: "运行活动文件", keywords: "Run Active File" },
      { id: "terminal.runSelectedText", label: "运行选定文本", keywords: "Run Selected Text" },
      {
        id: "tasks.build",
        label: "在终端运行 texsys build",
        keywords: "Run Build Task",
      },
    ],
  },
  {
    id: "help",
    label: "帮助",
    items: [
      { id: "help.shortcuts", label: "键盘快捷键参考…", keywords: "Keyboard Shortcuts Reference" },
      { id: "help.capabilities", label: "专业能力与范围", icon: "info" },
    ],
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
