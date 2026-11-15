import test from "node:test";
import assert from "node:assert/strict";
import Module from "node:module";
import path from "node:path";
import esbuild from "esbuild-wasm";
import React, { act } from "react";
import { JSDOM } from "jsdom";

const dom = new JSDOM("<!doctype html><body></body>", {
  url: "http://localhost/",
  pretendToBeVisual: true,
});
for (const name of [
  "window",
  "document",
  "Element",
  "HTMLElement",
  "HTMLButtonElement",
  "Node",
  "localStorage",
])
  globalThis[name] = dom.window[name];
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const { createRoot } = await import("react-dom/client");

// jsdom has no Popover API. Emulate its open/close state, focus return and light dismissal;
// all menu pointer, click, keyboard, React state, callbacks and focus behavior run unchanged.
const popovers = new Set();
const previousFocus = new WeakMap();
const nativeMatches = dom.window.HTMLElement.prototype.matches;
dom.window.HTMLElement.prototype.matches = function (selector) {
  return selector === ":popover-open" ? popovers.has(this) : nativeMatches.call(this, selector);
};
function togglePopover(element, open) {
  const wasOpen = popovers.has(element);
  if (wasOpen === open) return;
  if (open) {
    previousFocus.set(element, document.activeElement);
    popovers.add(element);
  } else {
    popovers.delete(element);
    if (element.contains(document.activeElement)) {
      document.activeElement.blur();
      previousFocus.get(element)?.focus();
    }
    previousFocus.delete(element);
  }
  const event = new dom.window.Event("toggle");
  Object.defineProperties(event, {
    oldState: { value: wasOpen ? "open" : "closed" },
    newState: { value: open ? "open" : "closed" },
  });
  element.dispatchEvent(event);
}
dom.window.HTMLElement.prototype.showPopover = function () {
  togglePopover(this, true);
};
dom.window.HTMLElement.prototype.hidePopover = function () {
  togglePopover(this, false);
};
document.addEventListener("pointerdown", (event) => {
  for (const menu of popovers)
    if (!menu.contains(event.target) && event.target.getAttribute("popovertarget") !== menu.id)
      menu.hidePopover();
});
function loadStudioModule(source, mocks = {}) {
  const filename = path.resolve("src/texsys/assets/studio", source);
  const result = esbuild.buildSync({
    entryPoints: [filename],
    bundle: true,
    write: false,
    platform: "node",
    format: "cjs",
    packages: "external",
    external: Object.keys(mocks),
    loader: { ".css": "empty" },
  });
  const loaded = new Module(filename);
  loaded.paths = Module._nodeModulePaths(process.cwd());
  loaded.require = (name) =>
    Object.hasOwn(mocks, name) ? mocks[name] : Module.prototype.require.call(loaded, name);
  loaded._compile(result.outputFiles[0].text, filename);
  return loaded.exports;
}
const { NavigatorMenu } = loadStudioModule("NavigatorMenu.tsx");

async function fixture(t) {
  document.body.innerHTML = '<div id="app"></div><button id="outside">Outside</button>';
  const root = createRoot(document.getElementById("app"));
  const choices = [];
  let dismissed = 0;
  const props = {
    label: "打开方式",
    onDismiss: () => {
      dismissed += 1;
    },
    items: [
      { label: "不可用" },
      { label: "", separator: true },
      { label: "文本编辑器", run: () => choices.push("source") },
      {
        label: "为「*.md」设置默认值",
        submenu: [
          { label: "不可用子项" },
          { label: "文本编辑器", checked: true, run: () => choices.push("default-source") },
          { label: "Markdown 预览", checked: false, run: () => choices.push("default-preview") },
          { label: "Markdown 编辑器", checked: false, run: () => choices.push("default-editor") },
        ],
      },
    ],
  };
  await act(async () => root.render(React.createElement(NavigatorMenu, props)));
  const trigger = document.querySelector('button[aria-label="打开方式"]');
  const menu = document.querySelector('[role="menu"][popover]');
  const parent = menu.querySelector('[aria-haspopup="menu"]');
  const submenu = () => menu.querySelector(".navigator-submenu");
  t.after(async () => {
    await act(async () => root.unmount());
    popovers.clear();
  });
  return { trigger, menu, parent, submenu, choices, dismissed: () => dismissed };
}
async function pointer(element, type, relatedTarget = null) {
  await act(async () =>
    element.dispatchEvent(
      new dom.window.MouseEvent(type, {
        bubbles: true,
        cancelable: true,
        relatedTarget,
      }),
    ),
  );
}
async function click(element) {
  await act(async () => element.click());
}
async function key(element, key, modifiers = {}) {
  await act(async () =>
    element.dispatchEvent(
      new dom.window.KeyboardEvent("keydown", {
        key,
        bubbles: true,
        cancelable: true,
        ...modifiers,
      }),
    ),
  );
}
async function focus(element) {
  await act(async () => element.focus());
}

// The exact Chrome failure sequence: mouse enter opens the submenu before click arrives.
test("hover then click keeps defaults submenu open and choosing an option executes it", async (t) => {
  const f = await fixture(t);
  await click(f.trigger);
  await pointer(f.parent, "pointerover", document.body);
  assert.ok(f.submenu());
  await click(f.parent);
  assert.ok(f.submenu());
  assert.equal(f.parent.getAttribute("aria-expanded"), "true");
  const option = [...f.submenu().querySelectorAll("button")].find((button) =>
    button.textContent.includes("Markdown 编辑器"),
  );
  await click(option);
  assert.deepEqual(f.choices, ["default-editor"]);
  assert.equal(f.menu.matches(":popover-open"), false);
  assert.equal(f.submenu(), null);
  assert.equal(f.dismissed(), 1);
});

test("hover survives the menu gap but leaving both menus closes the submenu", async (t) => {
  const f = await fixture(t);
  await click(f.trigger);
  await pointer(f.parent, "pointerover", document.body);
  const sub = f.submenu();
  await pointer(f.menu, "pointerout", document.body);
  assert.ok(f.submenu(), "pointer gap must not synchronously unmount the destination");
  await pointer(sub, "pointerover", document.body);
  await act(async () => new Promise((resolve) => setTimeout(resolve, 190)));
  assert.equal(f.submenu(), sub);
  await pointer(sub, "pointerout", document.body);
  await act(async () => new Promise((resolve) => setTimeout(resolve, 190)));
  assert.equal(f.submenu(), null);
  assert.equal(f.menu.matches(":popover-open"), true);
});

test("ArrowRight handles disabled entries and separators, and submenu arrows stay inside", async (t) => {
  const f = await fixture(t);
  await click(f.trigger);
  await focus(f.parent);
  await key(f.parent, "ArrowRight");
  const enabled = [...f.submenu().querySelectorAll("button:not(:disabled)")];
  assert.equal(document.activeElement, enabled[0]);
  await key(document.activeElement, "ArrowDown");
  assert.equal(document.activeElement, enabled[1]);
  await key(document.activeElement, "End");
  assert.equal(document.activeElement, enabled[2]);
  await key(document.activeElement, "Home");
  assert.equal(document.activeElement, enabled[0]);
  await key(document.activeElement, "ArrowUp");
  assert.equal(document.activeElement, enabled[2]);
  await key(document.activeElement, "ArrowLeft");
  assert.equal(f.submenu(), null);
  assert.equal(document.activeElement, f.parent);
  assert.equal(f.menu.matches(":popover-open"), true);
});

test("Escape first returns from submenu, then closes the parent and restores its trigger", async (t) => {
  const f = await fixture(t);
  await click(f.trigger);
  await click(f.parent);
  await key(document.activeElement, "Escape");
  assert.equal(f.submenu(), null);
  assert.equal(document.activeElement, f.parent);
  assert.equal(f.menu.matches(":popover-open"), true);
  await key(document.activeElement, "Escape");
  assert.equal(f.menu.matches(":popover-open"), false);
  assert.equal(document.activeElement, f.trigger);
});

test("outside dismissal and Tab clear submenu state so reopening starts clean", async (t) => {
  const f = await fixture(t);
  await click(f.trigger);
  await click(f.parent);
  await pointer(document.getElementById("outside"), "pointerdown");
  assert.equal(f.menu.matches(":popover-open"), false);
  assert.equal(f.submenu(), null);
  await click(f.trigger);
  assert.equal(f.submenu(), null);
  assert.equal(f.parent.getAttribute("aria-expanded"), "false");
  await click(f.parent);
  await key(document.activeElement, "Tab");
  assert.equal(f.menu.matches(":popover-open"), false);
  assert.equal(f.submenu(), null);
  assert.equal(f.dismissed(), 2);
});

test("menus open down-left-aligned and flip to right-aligned at the viewport edge", async (t) => {
  const f = await fixture(t);
  const rect = (left, top, width, height) => ({
    left,
    top,
    right: left + width,
    bottom: top + height,
    width,
    height,
    x: left,
    y: top,
  });
  const viewport = (width, height) => {
    Object.defineProperty(dom.window, "innerWidth", { value: width, configurable: true });
    Object.defineProperty(dom.window, "innerHeight", { value: height, configurable: true });
  };
  f.menu.getBoundingClientRect = () => rect(0, 0, 200, 260);

  viewport(1440, 900);
  f.trigger.getBoundingClientRect = () => rect(300, 40, 28, 28);
  await click(f.trigger);
  assert.equal(f.menu.style.left, "300px");
  assert.equal(f.menu.style.top, "71px");
  await click(f.trigger);

  viewport(1024, 768);
  f.trigger.getBoundingClientRect = () => rect(990, 40, 28, 28);
  await click(f.trigger);
  assert.equal(f.menu.style.left, "818px");
  await click(f.trigger);

  f.trigger.getBoundingClientRect = () => rect(300, 700, 28, 28);
  await click(f.trigger);
  assert.equal(f.menu.style.top, "437px");
});

const { ActivityBar } = loadStudioModule("atomstudio/Layout.tsx");
const { readLayoutPreferences, writeLayoutPreferences } = loadStudioModule("layout-preferences.ts");

test("activity context actions target the clicked entry, and blank space can restore it", async (t) => {
  const project = "/activity-context-fixture";
  const defaults = {
    sidebarOpen: true,
    sidebarWidth: 320,
    inspectorOpen: false,
    inspectorWidth: 320,
    bottomOpen: false,
    bottomHeight: 260,
    bottomTab: "terminal",
  };
  const items = [
    { id: "explorer", label: "资源管理器", icon: "Explorer" },
    { id: "search", label: "搜索", icon: "Search" },
    { id: "scm", label: "源代码管理", icon: "Git", count: 2 },
  ];
  const selected = [];
  function Fixture() {
    const [layout, setLayout] = React.useState(defaults);
    return React.createElement(ActivityBar, {
      active: "explorer",
      items,
      select: (id) => selected.push(id),
      layout,
      change: (patch) => {
        const next = { ...layout, ...patch };
        setLayout(next);
        writeLayoutPreferences(project, { discipline: "core", layouts: { core: next } });
      },
    });
  }
  document.body.innerHTML = '<div id="app"></div>';
  const root = createRoot(document.getElementById("app"));
  t.after(async () => {
    await act(async () => root.unmount());
    localStorage.clear();
    popovers.clear();
  });
  await act(async () => root.render(React.createElement(Fixture)));

  const tab = (id) => document.querySelector(`[data-activity-id="${id}"]`);
  const item = (label) =>
    [...popovers]
      .flatMap((menu) => [...menu.querySelectorAll("button")])
      .find((button) => [...button.children].some((child) => child.textContent.trim() === label));
  const context = async (target) => {
    const event = new dom.window.MouseEvent("contextmenu", {
      bubbles: true,
      cancelable: true,
      clientX: 18,
      clientY: 74,
    });
    await act(async () => target.dispatchEvent(event));
    assert.equal(event.defaultPrevented, true, "the app owns this context menu");
  };

  await context(tab("search"));
  assert.equal(item("隐藏“源代码管理”"), undefined);
  assert.equal(item("隐藏徽标"), undefined);
  await click(item("隐藏“搜索”"));
  assert.equal(tab("search"), null);
  assert.equal(tab("explorer").getAttribute("aria-selected"), "true");
  assert.deepEqual(selected, []);

  await context(document.querySelector(".activity-bar"));
  assert.equal(item("隐藏“资源管理器”"), undefined);
  assert.equal(item("搜索").getAttribute("aria-checked"), "false");
  await click(item("搜索"));
  assert.ok(tab("search"));
  await key(document.activeElement, "Escape");

  await focus(tab("scm"));
  await key(tab("scm"), "F10", { shiftKey: true });
  await click(item("隐藏徽标"));
  assert.equal(tab("scm").querySelector(".activity-badge"), null);
  await context(tab("scm"));
  assert.ok(item("显示徽标"));
  await key(document.activeElement, "Escape");
  assert.equal(document.activeElement?.dataset.activityId, "scm");

  await context(document.querySelector(".activity-bar"));
  await click(item("活动栏大小"));
  await click(item("紧凑"));
  await context(document.querySelector(".activity-bar"));
  await click(item("活动栏位置"));
  await click(item("底部"));
  await context(document.querySelector(".activity-bar"));
  await click(item("向右移动主侧栏"));
  const persisted = readLayoutPreferences(project, defaults).layouts.core;
  assert.equal(persisted.activityBarSize, "compact");
  assert.equal(persisted.activityBarPosition, "bottom");
  assert.equal(persisted.sidebarPosition, "right");
  assert.deepEqual(persisted.hiddenActivityBadges, ["scm"]);
  assert.deepEqual(persisted.hiddenActivities, []);
});

test("Explorer headings manage views while the project tree keeps its own context", async (t) => {
  // The source editor uses browser workers. The fixture renders the real Explorer and menu
  // and uses the normal file-selection path without mounting the editor surface.
  const { CoreWorkspaceProvider, useCoreWorkspace } = loadStudioModule("CoreWorkspace.tsx", {
    "./SourceEditor": {
      supportsSourceLineComments: () => false,
      watchSourceProblems: () => () => {},
      revealSourceOutline: () => {},
    },
    "./MarkdownEditor": {},
    "./extension-snippets": { useExtensionSnippets: () => {} },
  });
  const defaults = {
    sidebarOpen: true,
    sidebarWidth: 280,
    inspectorOpen: false,
    inspectorWidth: 320,
    bottomOpen: false,
    bottomHeight: 240,
    bottomTab: "terminal",
  };
  const state = {
    project: { name: "fixture", path: "/fixture", revision: "test" },
    plugins: [],
    data: {},
    jobs: [],
    diagnostics: [],
  };
  const responses = {
    "/api/extensions": { installed: [], autoUpdate: false, ignored: [] },
    "/api/extensions/recommendations": { ids: [], workspace: [] },
    "/api/files": {
      files: [{ path: "README.md" }, { path: "texsys.toml" }],
      directories: [],
      truncated: false,
    },
    "/api/design-index": {
      root: null,
      definitions: {},
      library: [],
      diagnostics: [],
      tag_errors: [],
      instance_tags: {},
    },
    "/api/scm?scope=project": { available: false, reason: "Fixture", entries: [] },
    "/api/scm/history?limit=50&path=README.md": { commits: [], truncated: false },
    "/api/scm/history?limit=50&path=texsys.toml": { commits: [], truncated: false },
    "/api/file?path=README.md": { path: "README.md", content: "# Fixture", version: "1" },
    "/api/file?path=texsys.toml": { path: "texsys.toml", content: "", version: "1" },
  };
  const api = async (url) => responses[url];
  const noop = () => {};
  function Sidebar() {
    return useCoreWorkspace().sidebar([]);
  }
  function Fixture({ buildRevision }) {
    const [layout, setLayout] = React.useState(defaults);
    return React.createElement(
      CoreWorkspaceProvider,
      {
        state: { ...state, project: { ...state.project, build_revision: buildRevision } },
        api,
        layout,
        changeLayout: (patch) => setLayout((current) => ({ ...current, ...patch })),
        refresh: noop,
        notify: noop,
        titlebarSettings: { hidden: [], toggle: noop, reset: noop },
        shortcutCommands: [],
        hasDomain: false,
        showEditorPicker: noop,
      },
      React.createElement(Sidebar),
    );
  }
  document.body.innerHTML = '<div id="app"></div>';
  const root = createRoot(document.getElementById("app"));
  t.after(async () => {
    await act(async () => root.unmount());
    localStorage.clear();
    popovers.clear();
  });
  await act(async () => root.render(React.createElement(Fixture)));

  const row = (path) => document.querySelector(`[data-tree-path="${path}"]`);
  const workspace = document.querySelector(".core-workspace-toggle");
  const title = document.querySelector(".core-sidebar > .navigator-heading");
  const trigger = title.querySelector('[aria-haspopup="menu"]');
  const menuItems = () => [...popovers].flatMap((menu) => [...menu.querySelectorAll("button")]);
  const item = (text) => menuItems().find((button) => button.textContent.trim() === text);
  const context = async (target) => {
    const event = new dom.window.MouseEvent("contextmenu", { bubbles: true, cancelable: true });
    await act(async () => target.dispatchEvent(event));
    assert.equal(event.defaultPrevented, true);
  };
  const dismiss = () => key(document.activeElement, "Escape");

  await click(row("README.md"));
  await click(row("texsys.toml"));
  await context(workspace);
  assert.equal(item("隐藏“文件夹”").disabled, true);
  assert.equal(item("文件夹").getAttribute("aria-checked"), "true");
  assert.equal(item("打开的编辑器").getAttribute("aria-checked"), "false");
  assert.equal(item("重命名…"), undefined);
  await click(item("打开的编辑器"));
  assert.equal(item("打开的编辑器").getAttribute("aria-checked"), "true");
  await dismiss();
  assert.equal(document.activeElement, workspace);
  assert.equal(row("texsys.toml").getAttribute("aria-selected"), "true");
  const editors = document.querySelector(".core-open-editors");
  await click(editors.querySelector('[title="README.md"]'));
  assert.equal(editors.querySelector('[aria-current="true"]').textContent, "README.md");
  assert.equal(workspace.getAttribute("aria-expanded"), "true");

  await context(title.querySelector("strong"));
  assert.ok(item("大纲"));
  assert.ok(item("时间线"));
  assert.equal(item("活动栏位置"), undefined);
  assert.equal(item("隐藏“文件夹”"), undefined);
  const titleItems = menuItems().map((button) => button.textContent);
  await dismiss();
  await click(trigger);
  assert.deepEqual(
    menuItems().map((button) => button.textContent),
    titleItems,
  );
  await dismiss();
  await focus(workspace);
  await key(workspace, "F10", { shiftKey: true });
  assert.ok(item("隐藏“文件夹”"));
  await dismiss();
  assert.equal(document.activeElement, workspace);

  await context(row(""));
  assert.ok(item("新建文件…"));
  assert.equal(item("打开的编辑器"), undefined);
  await dismiss();

  assert.ok(document.querySelector(".welcome-outline-section"));
  const timelineSection = document.querySelector(".welcome-timeline-section");
  assert.ok(timelineSection);
  await act(async () => {});
  assert.match(timelineSection.textContent, /此文件暂无提交历史。/);
  await click(trigger);
  await click(item("大纲"));
  assert.equal(document.querySelector(".welcome-outline-section"), null);
  await click(item("大纲"));
  assert.ok(document.querySelector(".welcome-outline-section"));
  await click(item("时间线"));
  assert.equal(document.querySelector(".welcome-timeline-section"), null);
  await click(item("时间线"));
  assert.ok(document.querySelector(".welcome-timeline-section"));
  await dismiss();

  assert.equal(row("drawings"), null);
  responses["/api/files"] = {
    files: [...responses["/api/files"].files, { path: "drawings/room.svg" }],
    directories: ["drawings"],
    truncated: false,
  };
  await act(async () => root.render(React.createElement(Fixture, { buildRevision: "new-build" })));
  assert.ok(row("drawings"));
});

test("welcome explorer title menu manages view visibility like the reference", async (t) => {
  const { WelcomeWorkspace } = loadStudioModule("WelcomeWorkspace.tsx", {
    "./atomstudio/Layout": {
      ActivityBar: () => null,
      WorkbenchLayout: ({ sidebar, children }) =>
        React.createElement("div", null, sidebar, children),
    },
    "./atomstudio/WorkspaceTabs": {
      WorkspaceTabStrip: ({ tabs }) =>
        React.createElement(
          "div",
          { className: "mock-tabs" },
          tabs.map((tab) => React.createElement("span", { key: tab.id }, tab.label)),
        ),
    },
    "./BottomPanel": { WorkbenchBottomPanel: () => null },
    "./CoreWorkspace": {
      coreItems: [{ id: "explorer", label: "资源管理器", icon: "files" }],
      coreSidebarTitles: {
        explorer: "资源管理器",
        search: "搜索",
        scm: "源代码管理",
        extensions: "扩展",
        library: "部件库",
        instances: "项目实例",
      },
      isCoreActivity: (id) => id === "explorer",
    },
    "./SourceEditor": { SourceEditor: () => null, revealSourceOutline: () => {} },
    "./welcome-files": {
      readLocalRecents: async () => [],
      rememberLocalEntry: async () => [],
      forgetLocalEntry: async () => [],
      listLocalFolder: async () => [],
      readLocalText: async () => "",
      requestFileAccess: async () => true,
      localPickers: { showDirectoryPicker: async () => ({ kind: "directory", name: "fixture" }) },
    },
    "./file-excludes": {
      compileExcludes: () => () => false,
      readWorkspaceExcludes: async () => () => false,
    },
  });
  const defaults = {
    sidebarOpen: true,
    inspectorOpen: false,
    bottomOpen: false,
    bottomTab: "terminal",
    sidebarWidth: 320,
    inspectorWidth: 320,
    bottomHeight: 240,
  };
  function Fixture() {
    const [layout, setLayout] = React.useState(defaults);
    return React.createElement(WelcomeWorkspace, {
      layout,
      changeLayout: (patch) => setLayout((current) => ({ ...current, ...patch })),
      openProject: () => {},
      openSettings: () => {},
      notify: () => {},
      onEditorState: () => {},
    });
  }
  document.body.innerHTML = '<div id="app"></div>';
  const root = createRoot(document.getElementById("app"));
  t.after(async () => {
    await act(async () => root.unmount());
    popovers.clear();
  });
  await act(async () => root.render(React.createElement(Fixture)));

  const trigger = document.querySelector('.core-sidebar [aria-label="资源管理器更多操作"]');
  await click(trigger);
  const menu = [...popovers].find((m) => m.getAttribute("aria-label") === "资源管理器更多操作");
  const items = () => [...menu.querySelectorAll(":scope > button")];
  assert.deepEqual(
    items().map((button) => button.textContent.trim()),
    ["文件夹", "大纲", "时间线"],
  );
  assert.ok(
    items().every(
      (button) =>
        button.getAttribute("role") === "menuitemcheckbox" &&
        button.getAttribute("aria-checked") === "true",
    ),
  );
  const outline = () => document.querySelector(".welcome-outline-section");
  const timeline = () =>
    [...document.querySelectorAll(".welcome-side-section")].find((node) =>
      node.textContent.includes("时间线"),
    );
  const heading = () => document.querySelector(".core-workspace-heading");

  await click(items()[1]);
  assert.equal(outline(), null);
  assert.equal(items()[1].getAttribute("aria-checked"), "false");
  await click(items()[2]);
  assert.equal(document.querySelector(".welcome-side-sections"), null);
  await click(items()[0]);
  assert.equal(heading(), null);
  await click(items()[0]);
  await click(items()[1]);
  await click(items()[2]);
  assert.ok(heading() && outline() && timeline());
  await key(document.activeElement, "Escape");
  assert.equal(menu.matches(":popover-open"), false);

  const welcomeNew = [...document.querySelectorAll(".welcome-page button")].find((button) =>
    button.textContent.includes("新建文本文件"),
  );
  assert.ok(welcomeNew);
  await click(welcomeNew);
  assert.ok(
    [...document.querySelectorAll(".mock-tabs span")].some((span) =>
      span.textContent.includes("未命名-1.txt"),
    ),
  );
  const openFolderButton = [...document.querySelectorAll(".welcome-explorer-actions button")].find(
    (button) => button.textContent.trim() === "打开文件夹",
  );
  await click(openFolderButton);
  assert.ok(document.querySelector('.core-workspace-tools [aria-label="新建文本文件"]'));
});
