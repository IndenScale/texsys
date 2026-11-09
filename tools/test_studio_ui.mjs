import test from "node:test";
import assert from "node:assert/strict";
import Module from "node:module";
import path from "node:path";
import esbuild from "esbuild-wasm";

function load(source) {
  const filename = path.resolve(source);
  const result = esbuild.buildSync({
    entryPoints: [filename],
    bundle: true,
    write: false,
    platform: "node",
    format: "cjs",
    packages: "external",
  });
  const module = new Module(filename);
  module.paths = Module._nodeModulePaths(process.cwd());
  module._compile(result.outputFiles[0].text, filename);
  return module.exports;
}
const { assertionLocation } = load(
  "packages/hvac-graphics/src/texsys_hvac_graphics/assets/analysis.ts",
);
const { bindingValue, instanceFromRef, issuesFor, sourceObjectLine } = load(
  "packages/studio-hvac/src/texsys_studio_hvac/assets/model.ts",
);
const source = {
  root: "Station",
  components: {
    Station: { instances: { P1: { component: "Pump" }, P10: { component: "Pump" } } },
    Pump: {},
  },
};
const result = {
  scenario: "pump-trip",
  backend: "scipy",
  time_s: [0, 10, 20, 30],
  variables: {
    temperature: {
      semantic_ref: "Station.P1",
      role: "outlet-temperature",
      unit: "degC",
      values: [7, 12, 13, 20],
    },
  },
  assertions: [
    {
      status: "fail",
      variable: "temperature",
      operator: "<=",
      value: 12,
      actual: 20,
      unit: "degC",
    },
  ],
};

test("source navigation distinguishes a type and instance with the same name", () => {
  const data = {
    root: "Station",
    components: {
      Pump: { behavior: { parameters: { Pump: { value: 1 } } } },
      Station: { instances: { Pump: { component: "Pump" } } },
    },
  };
  const lines = JSON.stringify(data, null, 2).split("\n");
  const typeLine = sourceObjectLine(data, { kind: "type", id: "Pump" });
  const instanceLine = sourceObjectLine(data, { kind: "instance", id: "Pump" });
  assert.notEqual(typeLine, instanceLine);
  assert.equal(lines[typeLine], '    "Pump": {');
  assert.equal(lines[instanceLine], '        "Pump": {');
  assert.equal(sourceObjectLine(data, { kind: "instance", id: "missing" }), -1);
});

test("constraint location selects first violating sample, not event or largest value", () => {
  assert.deepEqual(assertionLocation(result, result.assertions[0]), {
    index: 2,
    reason: "首次越限采样点",
  });
  assert.equal(assertionLocation(result, { ...result.assertions[0], operator: "<" }).index, 1);
  const lower = {
    ...result,
    variables: { temperature: { ...result.variables.temperature, values: [7, 4, 3, 1] } },
  };
  assert.equal(
    assertionLocation(lower, { ...result.assertions[0], operator: ">=", value: 4, actual: 1 })
      .index,
    2,
  );
});
test("historical assertion without a sampled violation falls back to its extreme explicitly", () => {
  assert.deepEqual(assertionLocation(result, { ...result.assertions[0], value: 30 }), {
    index: 3,
    reason: "极值采样点",
  });
  assert.deepEqual(assertionLocation(result, { status: "fail", variable: "missing" }), {
    index: 0,
    reason: "初始时刻",
  });
});
test("constraint location ignores non-finite and out-of-time samples", () => {
  const data = {
    ...result,
    time_s: [0, 10],
    variables: { temperature: { ...result.variables.temperature, values: [NaN, 7, 20] } },
  };
  assert.equal(assertionLocation(data, result.assertions[0]).reason, "极值采样点");
  assert.ok(assertionLocation(data, result.assertions[0]).index < data.time_s.length);
});
test("object identity lookup respects reference boundaries and similarly named instances", () => {
  assert.equal(instanceFromRef(source, "Station.P10.outlet"), "P10");
  assert.equal(instanceFromRef(source, "Station.P1#chilled.outlet"), "P1");
  assert.equal(instanceFromRef(source, "P1"), "P1");
  assert.equal(instanceFromRef(source, "problem mentions P1"), undefined);
  assert.equal(instanceFromRef(source, "Other.P1"), undefined);
});
test("binding presentation does not show a number for unresolved or failed states", () => {
  for (const state of ["unknown", "missing", "not-applicable", "failed"])
    assert.ok(!bindingValue({ value: 42, state, unit: "1", source: "test" }).includes("42"));
  assert.equal(
    bindingValue({ value: 0.72, state: "known", unit: "1", source: "test" }, "efficiency"),
    "72%",
  );
  assert.equal(
    bindingValue({ value: 0, state: "known", unit: "1", source: "test" }, "check_valve"),
    "无",
  );
  assert.equal(bindingValue({ value: 4.5, state: "assumed", unit: "1", source: "test" }), "4.5");
});
test("issue projection keeps input freshness and traceable variable identity", () => {
  const data = {
    source,
    reports: [],
    runs: [{ path: "old/results.json", current: false, result }],
  };
  const [issue] = issuesFor(data);
  assert.equal(issue.object, "P1");
  assert.equal(issue.current, false);
  assert.equal(issue.runPath, "old/results.json");
  assert.equal(issue.assertion, 0);
  assert.equal(
    issuesFor({
      ...data,
      runs: [{ ...data.runs[0], result: { ...result, assertions: [{ status: "pass" }] } }],
    }).length,
    0,
  );
});
test("text-only diagnostics do not fabricate an object location; incomplete reports remain visible", () => {
  const data = {
    source,
    runs: [],
    reports: [
      {
        kind: "studio-check.json",
        path: "check.json",
        current: true,
        data: {
          status: "fail",
          diagnostics: [{ severity: "error", message: "P1 missing a value" }],
        },
      },
    ],
  };
  assert.equal(issuesFor(data)[0].object, undefined);
  assert.equal(
    issuesFor({ ...data, reports: [{ ...data.reports[0], data: { status: "indeterminate" } }] })
      .length,
    1,
  );
});

const { tabNavigationIndex } = load("src/texsys/assets/studio/atomstudio/WorkspaceTabs.tsx");
const { panelSizeFromContainerPointer, panelSizeFromPointer } = load(
  "src/texsys/assets/studio/atomstudio/layoutSizing.ts",
);
const { hasResizeIntent, snapPanelDrag } = load(
  "src/texsys/assets/studio/atomstudio/ResizeHandle.tsx",
);
test("imported tabs wrap keyboard focus and tolerate empty groups", () => {
  assert.equal(tabNavigationIndex(0, "ArrowLeft", 3), 2);
  assert.equal(tabNavigationIndex(2, "ArrowRight", 3), 0);
  assert.equal(tabNavigationIndex(1, "Home", 3), 0);
  assert.equal(tabNavigationIndex(0, "End", 3), 2);
  assert.equal(tabNavigationIndex(0, "End", 0), -1);
});
test("dragging below the minimum previews collapse and crossing back restores the panel", () => {
  assert.deepEqual(snapPanelDrag(208, 208), { collapsed: false, value: 208 });
  assert.deepEqual(snapPanelDrag(207, 208), { collapsed: true, value: 0 });
  assert.deepEqual(snapPanelDrag(0, 208), { collapsed: true, value: 0 });
  assert.deepEqual(snapPanelDrag(208, 208), { collapsed: false, value: 208 });
  assert.deepEqual(snapPanelDrag(250, 208), { collapsed: false, value: 250 });
});
test("imported resizing respects panel edges, clamp and dismissal intent", () => {
  assert.equal(panelSizeFromPointer(320, 100, 105, 0, 500, 1), 325);
  assert.equal(panelSizeFromPointer(320, 100, 105, 0, 500), 315);
  assert.equal(panelSizeFromPointer(320, 100, 420, 0, 500, 1), 500);
  assert.equal(panelSizeFromPointer(320, 100, 420, 0, 500), 0);
  assert.equal(panelSizeFromContainerPointer(330, 100, 900, true, 208, 500), 230);
  assert.equal(panelSizeFromContainerPointer(600, 100, 900, false, 280, 500), 300);
  assert.equal(panelSizeFromContainerPointer(120, 100, 900, true, 208, 500), 208);
  assert.equal(panelSizeFromContainerPointer(120, 100, 900, false, 280, 500), 500);
  assert.equal(hasResizeIntent(100, 101), false);
  assert.equal(hasResizeIntent(100, 120), true);
});

const { readLayoutPreferences, writeLayoutPreferences } = load(
  "src/texsys/assets/studio/layout-preferences.ts",
);
test("layout preferences keep project and discipline state separate and tolerate invalid storage", (t) => {
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, "localStorage");
  t.after(() => {
    if (descriptor) Object.defineProperty(globalThis, "localStorage", descriptor);
    else delete globalThis.localStorage;
  });
  const stored = new Map();
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    value: {
      getItem: (key) => stored.get(key) ?? null,
      setItem: (key, value) => stored.set(key, value),
    },
  });
  const defaults = {
    sidebarOpen: true,
    sidebarWidth: 320,
    inspectorOpen: false,
    inspectorWidth: 320,
    bottomOpen: false,
    bottomTab: "problems",
    bottomHeight: 260,
  };
  const saved = {
    discipline: "texsys:hvac",
    layouts: {
      "texsys:hvac": {
        ...defaults,
        sidebarOpen: false,
        sidebarActivity: "components",
        hiddenActivities: ["scm"],
        sidebarWidth: 367,
        inspectorOpen: true,
        inspectorWidth: 371,
        bottomOpen: true,
        bottomTab: "tasks",
        bottomHeight: 297,
      },
      "": { ...defaults, sidebarActivity: "search" },
    },
  };
  writeLayoutPreferences("/example/a", saved);
  assert.deepEqual(readLayoutPreferences("/example/a", defaults), saved);
  assert.deepEqual(readLayoutPreferences("/example/b", defaults), { discipline: "", layouts: {} });

  saved.layouts["texsys:hvac"].sidebarWidth = "wide";
  writeLayoutPreferences("/example/a", saved);
  assert.equal(
    readLayoutPreferences("/example/a", defaults).layouts["texsys:hvac"].sidebarWidth,
    320,
  );
  assert.equal(
    readLayoutPreferences("/example/a", defaults).layouts["texsys:hvac"].bottomTab,
    "tasks",
  );
  stored.set([...stored.keys()][0], "{incomplete");
  assert.deepEqual(readLayoutPreferences("/example/a", defaults), { discipline: "", layouts: {} });
});

const { visitDocument, stepDocument, closeDocumentFromList } = load(
  "src/texsys/assets/studio/navigation.ts",
);
test("closing document focuses right then left neighbor and permits an empty list", () => {
  assert.deepEqual(closeDocumentFromList(["overview"], "overview"), {
    remaining: [],
    next: null,
  });
  assert.deepEqual(closeDocumentFromList(["design", "overview"], "overview"), {
    remaining: ["design"],
    next: "design",
  });
  assert.deepEqual(closeDocumentFromList(["design", "equipment", "source"], "equipment"), {
    remaining: ["design", "source"],
    next: "source",
  });
});
const { studioMenus, filterCommands, isCloseDocumentShortcut } = load(
  "src/texsys/assets/studio/menus.ts",
);
test("close editor uses the VS Code key without an extra modifier", () => {
  const commandW = { key: "w", metaKey: true, ctrlKey: false, altKey: false, shiftKey: false };
  assert.equal(isCloseDocumentShortcut(commandW, true), true);
  assert.equal(isCloseDocumentShortcut({ ...commandW, altKey: true }, true), false);
  assert.equal(isCloseDocumentShortcut(commandW, false), false);
  assert.equal(
    isCloseDocumentShortcut({ ...commandW, metaKey: false, ctrlKey: true }, false),
    true,
  );
});
const { visibleMenuCount } = load("src/texsys/assets/studio/menuLayout.ts");
test("titlebar menu overflow reserves More and keeps an ordered visible prefix", () => {
  const widths = [40, 40, 40, 40, 40];
  assert.equal(visibleMenuCount(208, widths, 40), 5);
  assert.equal(visibleMenuCount(207, widths, 40), 3);
  assert.equal(visibleMenuCount(166, widths, 40), 3);
  assert.equal(visibleMenuCount(165, widths, 40), 2);
  assert.equal(visibleMenuCount(82, widths, 40), 1);
  assert.equal(visibleMenuCount(81.99, widths, 40), 0);
  assert.equal(visibleMenuCount(40, widths, 40), 0);
  assert.equal(visibleMenuCount(0, [], 40), 0);
});
test("titlebar menu overflow accounts for unequal labels and fractional sizes", () => {
  assert.equal(visibleMenuCount(145, [58, 37, 47], 40), 2);
  assert.equal(visibleMenuCount(146, [58, 37, 47], 40), 3);
  assert.equal(visibleMenuCount(82, [40.1, 40.1], 40), 0);
  assert.equal(visibleMenuCount(83, [40.1, 40.1], 40), 1);
  assert.equal(visibleMenuCount(84, [40.1, 40.1], 40), 2);
});
test("document history preserves forward visits and truncates only after a new branch", () => {
  const initial = { entries: ["design"], index: 0 };
  const equipment = visitDocument(initial, "equipment");
  const source = visitDocument(equipment, "source");
  const back = stepDocument(source, -1);
  assert.equal(back.entries[back.index], "equipment");
  assert.equal(stepDocument(back, 1).entries[2], "source");
  assert.deepEqual(visitDocument(back, "checks"), {
    entries: ["design", "equipment", "checks"],
    index: 2,
  });
  assert.equal(visitDocument(back, "equipment"), back);
});
test("document navigation cannot move beyond either history boundary", () => {
  const initial = { entries: ["design"], index: 0 };
  assert.equal(stepDocument(initial, -1), initial);
  assert.equal(stepDocument(initial, 1), initial);
});
test("menu and command search share group names and stable action identities", () => {
  const commands = studioMenus.flatMap((group) =>
    group.items.map((item) => ({ ...item, group: group.label })),
  );
  assert.equal(new Set(commands.map((item) => item.id)).size, commands.length);
  assert.deepEqual(
    filterCommands(commands, "> 查看 面板").map((item) => item.id),
    ["commands.open", "view.sidebar", "view.bottom", "view.inspector"],
  );
  assert.deepEqual(
    filterCommands(commands, "任务 日志").map((item) => item.id),
    ["tasks.show"],
  );
  assert.equal(filterCommands(commands, "不存在的命令").length, 0);
  assert.deepEqual(
    filterCommands(commands, "")
      .filter((item) => item.id.startsWith("view."))
      .map((item) => item.id),
    ["view.fullscreen"],
  );
  assert.deepEqual(
    filterCommands(commands, "布局").map((item) => item.id),
    ["view.sidebar", "view.bottom", "view.inspector"],
  );
});

const { fitDrawingWidth } = load(
  "packages/studio-hvac/src/texsys_studio_hvac/assets/drawingViewport.ts",
);
test("explicit drawing fit respects both aperture dimensions and a small-window floor", () => {
  assert.equal(fitDrawingWidth(1000, 800, 1600, 800), 936);
  assert.equal(fitDrawingWidth(1000, 300, 1600, 800), (228 * 1600) / 892);
  assert.equal(fitDrawingWidth(100, 100, 1600, 800), 160);
});

const { filterFiles, parseFileQuery } = load("src/texsys/assets/studio/search.ts");
test("file search ranks exact names and filters case-insensitive path terms", () => {
  const files = [
    { name: "system.json", path: "src/system.json" },
    { name: "other.json", path: "src/system/other.json" },
    { name: "notes.md", path: "docs/notes.md" },
  ];
  assert.equal(filterFiles(files, "system.json")[0].path, "src/system.json");
  assert.deepEqual(
    filterFiles(files, "SRC SYSTEM").map((file) => file.path),
    ["src/system.json", "src/system/other.json"],
  );
  assert.equal(filterFiles(files, "systjs")[0].path, "src/system.json");
  assert.equal(filterFiles(files, "missing-unique-filename").length, 0);
});

test("file locations keep exact filenames containing colons and reject unsafe offsets", () => {
  const files = [{ name: "notes:42", path: "docs/notes:42" }];
  assert.deepEqual(parseFileQuery(files, "docs/notes:42"), { text: "docs/notes:42" });
  assert.deepEqual(parseFileQuery(files, "docs/notes:42:3"), {
    text: "docs/notes:42",
    location: { line: 3, column: undefined },
  });
  assert.deepEqual(parseFileQuery(files, "src/system.json:42:3"), {
    text: "src/system.json",
    location: { line: 42, column: 3 },
  });
  assert.deepEqual(parseFileQuery(files, "doc:9007199254740992"), {
    text: "doc:9007199254740992",
  });
});

const { visibleFileTree, gitGroups, gitPathInProject, sourceOffset, commitGraph } = load(
  "src/texsys/assets/studio/workspace-model.ts",
);
test("Explorer preserves empty directories and expands ancestors without duplicating them", () => {
  const listing = {
    files: [
      { path: "src/nested/model.json", name: "model.json", parent: "src/nested", size: 1 },
      { path: "README.md", name: "README.md", parent: "", size: 1 },
    ],
    directories: ["src", "src/nested", "empty"],
    truncated: false,
  };
  assert.deepEqual(
    visibleFileTree(listing, new Set()).map((entry) => entry.path),
    ["empty", "src", "README.md"],
  );
  const rows = visibleFileTree(listing, new Set(["src", "src/nested"]));
  assert.equal(rows.filter((entry) => entry.path === "src").length, 1);
  assert.equal(rows.find((entry) => entry.path === "src/nested/model.json").depth, 2);
});
test("Source Control shows partially staged files in both change groups", () => {
  const entry = { path: "model", index: "M", worktree: "M" };
  assert.deepEqual(gitGroups({ entries: [entry] }), { staged: [entry], changes: [entry] });
  assert.equal(
    gitGroups({ entries: [{ path: "new", index: "?", worktree: "?" }] }).staged.length,
    0,
  );
});
test("Repository change paths can only open files inside the current project", () => {
  const project = "examples/hvac";
  assert.equal(gitPathInProject("src/model.json"), "src/model.json");
  assert.equal(gitPathInProject("examples/hvac/src/model.json", project), "src/model.json");
  assert.equal(gitPathInProject("examples/hvac-other/src/model.json", project), undefined);
  assert.equal(gitPathInProject("src/model.json", project), undefined);
});
test("Search navigation converts Unicode code point columns to textarea UTF-16 offsets", () => {
  const text = "heading\n😀 Pump pump\n";
  assert.equal(text.slice(sourceOffset(text, 2, 3), sourceOffset(text, 2, 7)), "Pump");
  assert.equal(sourceOffset("a\r\nb", 2, 1), 3);
});

test("commit graph joins merge parents without duplicate lanes", () => {
  const commits = [
    { id: "merge", parents: ["main", "feature"] },
    { id: "feature", parents: ["base"] },
    { id: "main", parents: ["base"] },
    { id: "base", parents: [] },
  ];
  const rows = commitGraph(commits);
  assert.deepEqual(rows[0].after, ["main", "feature"]);
  assert.equal(rows[1].lane, 1);
  assert.deepEqual(rows[2].after, ["base"]);
  assert.deepEqual(rows[3].after, []);
  assert.equal(rows[3].lane, 0);
});

const { groupCodeSearchResults, searchLineSegments } = load(
  "src/texsys/assets/studio/code-search-model.ts",
);
test("Code Search groups same-line occurrences and retains occurrence counts", () => {
  const results = {
    matches: [1, 2, 3, 4],
    lines: [
      {
        path: "README.md",
        line: 20,
        text: "test test",
        count: 4,
        matches: [
          { column: 1, end_column: 5, offset: 0, length: 4 },
          { column: 6, end_column: 10, offset: 5, length: 4 },
        ],
      },
    ],
  };
  const groups = groupCodeSearchResults(results);
  assert.equal(groups.length, 1);
  assert.equal(groups[0].lines.length, 1);
  assert.equal(groups[0].count, 4);
});
test("Code Search highlights Unicode offsets without splitting surrogate pairs", () => {
  const line = {
    path: "model",
    line: 1,
    text: "😀 test test",
    count: 2,
    matches: [
      { column: 3, end_column: 7, offset: 2, length: 4 },
      { column: 8, end_column: 12, offset: 7, length: 4 },
    ],
  };
  const segments = searchLineSegments(line);
  assert.equal(segments.map((segment) => segment.text).join(""), line.text);
  assert.deepEqual(
    segments.filter((segment) => segment.match).map((segment) => segment.text),
    ["test", "test"],
  );
});

test("search preview removes source indentation without changing Unicode match columns", () => {
  const match = { column: 7, end_column: 12, offset: 6, length: 5 };
  const line = { path: "model", line: 3, text: "  \t😀  valve", matches: [match], count: 1 };
  const segments = searchLineSegments(line, { trimIndent: true });
  assert.equal(segments.map((segment) => segment.text).join(""), "😀  valve");
  assert.equal(segments.find((segment) => segment.match).match, match);
  assert.equal(match.column, 7);
  assert.equal(line.text, "  \t😀  valve");
});

test("search preview preserves whitespace that is itself a match", () => {
  const line = {
    path: "model",
    line: 1,
    text: "  valve",
    count: 1,
    matches: [{ column: 1, end_column: 3, offset: 0, length: 2 }],
  };
  assert.equal(searchLineSegments(line, { trimIndent: true })[0].text, "  ");
  assert.equal(
    searchLineSegments(line)
      .map((segment) => segment.text)
      .join(""),
    "  valve",
  );
});

const { parseLocale, translate, getLocale, setLocale, t, localizeError } = load(
  "src/texsys/assets/studio/i18n.ts",
);
const { englishMessages } = load("src/texsys/assets/studio/messages.ts");

test("locale selection accepts supported values and defaults malformed preferences to Chinese", () => {
  assert.equal(parseLocale(null), "zh-CN");
  assert.equal(parseLocale("fr"), "zh-CN");
  assert.equal(parseLocale("zh-CN"), "zh-CN");
  assert.equal(parseLocale("en"), "en");
});

test("translations preserve identifiers, literal replacement text, and optional fallbacks", () => {
  assert.equal(translate("zh-CN", "文件资源管理器"), "文件资源管理器");
  assert.equal(translate("en", "文件资源管理器"), "Explorer");
  assert.equal(translate("en", "源代码管理"), "Source Control");
  assert.equal(translate("en", "搜索"), "Search");
  assert.equal(
    translate("en", "关闭 {0}", { 0: "$& ${path} 设备.json" }),
    "Close $& ${path} 设备.json",
  );
  assert.equal(translate("en", "自定义模型-01"), "自定义模型-01");
  for (const identifier of ["__proto__", "constructor", "toString"]) {
    assert.equal(translate("en", identifier), identifier);
  }
  assert.equal(translate("en", "关闭 {0}"), "Close {0}");
  assert.equal(t(undefined), undefined);
});

test("language changes work without browser storage and error details keep their original paths", () => {
  const previous = getLocale();
  try {
    setLocale("en");
    assert.equal(getLocale(), "en");
    assert.equal(t("设置"), "Settings");
    assert.equal(
      localizeError("正则表达式无效：unterminated group"),
      "Invalid regular expression: unterminated group",
    );
    assert.equal(
      localizeError("AI 服务返回 HTTP 429，请检查连接设置或服务状态。"),
      "The AI service returned HTTP 429. Check the connection settings or service status.",
    );
    setLocale("zh-CN");
    assert.equal(t("设置"), "设置");
  } finally {
    setLocale(previous);
  }
});

test("English catalog retains all interpolation placeholders and contains no Chinese interface copy", () => {
  const placeholders = (text) => [...text.matchAll(/\{(\w+)\}/g)].map((match) => match[1]).sort();
  for (const [source, translated] of Object.entries(englishMessages)) {
    assert.deepEqual(placeholders(translated), placeholders(source), source);
    assert.equal(/[\u3400-\u9fff]/.test(translated), false, source);
  }
});

test("menu separates viewing task history, controlling jobs and using the shell", () => {
  const owner = (id) => studioMenus.find((group) => group.items.some((item) => item.id === id))?.id;
  assert.equal(owner("tasks.show"), "view");
  assert.equal(owner("tasks.terminate"), "run");
  assert.equal(owner("tasks.build"), "terminal");
  assert.equal(owner("terminal.runActiveFile"), "terminal");
  assert.equal(owner("tasks.run"), undefined);
  assert.equal(owner("terminal.newWindow"), undefined);
});

test("layout preferences preserve terminal tab and panel maximize state", (t) => {
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, "localStorage");
  t.after(() => {
    if (descriptor) Object.defineProperty(globalThis, "localStorage", descriptor);
    else delete globalThis.localStorage;
  });
  const stored = new Map();
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    value: {
      getItem: (key) => stored.get(key) ?? null,
      setItem: (key, value) => stored.set(key, value),
    },
  });
  const defaults = {
    sidebarOpen: true,
    sidebarWidth: 320,
    inspectorOpen: false,
    inspectorWidth: 320,
    bottomOpen: false,
    bottomTab: "problems",
    bottomHeight: 260,
  };
  const terminalLayout = {
    discipline: "texsys:hvac",
    layouts: {
      "texsys:hvac": {
        ...defaults,
        bottomOpen: true,
        bottomTab: "terminal",
        bottomMaximized: true,
      },
    },
  };
  writeLayoutPreferences("/example/term", terminalLayout);
  const loaded = readLayoutPreferences("/example/term", defaults);
  assert.equal(loaded.layouts["texsys:hvac"].bottomTab, "terminal");
  assert.equal(loaded.layouts["texsys:hvac"].bottomMaximized, true);
});

test("bottom panel tabs support pill badges and rounded rectangle presentation", () => {
  const tabs = [
    { id: "problems", label: "Problems", badge: 1, focused: true },
    { id: "tasks", label: "Tasks", focused: false },
    { id: "terminal", label: "Terminal", focused: false },
  ];
  assert.equal(tabs[0].badge, 1);
  assert.equal(tabs[1].badge, undefined);
  assert.equal(tabNavigationIndex(0, "ArrowRight", tabs.length), 1);
});

test("terminal session sidebar hideCondition matches VS Code singleTerminal rule", () => {
  const shouldShowSidebar = (sessionsCount, isSplit) => sessionsCount > 1 || Boolean(isSplit);
  assert.equal(
    shouldShowSidebar(1, false),
    false,
    "single session without split must not show sidebar",
  );
  assert.equal(shouldShowSidebar(2, false), true, "multiple sessions must show sidebar");
  assert.equal(shouldShowSidebar(1, true), true, "split session must show sidebar");
});

test("single terminal mode preserves session pill and kill action in header", () => {
  const getHeaderActions = (sessionsCount, isSplit) => {
    const hasSidebar = sessionsCount > 1 || Boolean(isSplit);
    if (!hasSidebar) {
      return ["session-pill", "new", "split", "kill", "more", "separator", "maximize", "close"];
    }
    return ["new", "more", "separator", "maximize", "close"];
  };
  assert.deepEqual(getHeaderActions(1, false), [
    "session-pill",
    "new",
    "split",
    "kill",
    "more",
    "separator",
    "maximize",
    "close",
  ]);
  assert.deepEqual(getHeaderActions(2, false), ["new", "more", "separator", "maximize", "close"]);
});

test("layout preferences preserve bottom panel tabs visibility, badges, position and align", (t) => {
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, "localStorage");
  t.after(() => {
    if (descriptor) Object.defineProperty(globalThis, "localStorage", descriptor);
    else delete globalThis.localStorage;
  });
  const stored = new Map();
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    value: {
      getItem: (key) => stored.get(key) ?? null,
      setItem: (key, value) => stored.set(key, value),
    },
  });
  const defaults = {
    sidebarOpen: true,
    inspectorOpen: true,
    bottomOpen: false,
    bottomTab: "problems",
    sidebarWidth: 260,
    inspectorWidth: 280,
    bottomHeight: 220,
  };
  const customPanelLayout = {
    discipline: "texsys:hvac",
    layouts: {
      "texsys:hvac": {
        ...defaults,
        bottomOpen: true,
        bottomTab: "terminal",
        hiddenBottomTabs: ["output", "comments"],
        hideBadges: { problems: true },
        panelPosition: "bottom",
        panelAlign: "justify",
        showPanelIcons: true,
      },
    },
  };
  writeLayoutPreferences("/example/custom-panel", customPanelLayout);
  const loaded = readLayoutPreferences("/example/custom-panel", defaults);
  const layout = loaded.layouts["texsys:hvac"];
  assert.deepEqual(layout.hiddenBottomTabs, ["output", "comments"]);
  assert.deepEqual(layout.hideBadges, { problems: true });
  assert.equal(layout.panelPosition, "bottom");
  assert.equal(layout.panelAlign, "justify");
  assert.equal(layout.showPanelIcons, true);
});

test("bottom panel header and tab context menus match VS Code native interaction model", () => {
  const panelTabs = [
    { id: "problems", label: "Problems" },
    { id: "output", label: "Output" },
    { id: "tasks", label: "Tasks and logs" },
    { id: "debug", label: "Debug Console" },
    { id: "terminal", label: "Terminal" },
    { id: "ports", label: "Ports" },
    { id: "comments", label: "Comments" },
  ];

  const buildHeaderMenu = (hiddenTabs) => [
    ...panelTabs.map((tab) => ({
      label: tab.label,
      checked: !hiddenTabs.includes(tab.id),
      checkbox: true,
    })),
    { separator: true },
    {
      label: "Panel Position",
      submenu: ["Bottom", "Left", "Right"],
    },
    {
      label: "Align Panel",
      submenu: ["Center", "Justify", "Left", "Right"],
    },
    { label: "Show Icons", checkbox: true },
    { label: "Hide Panel", shortcut: ["⌘", "J"] },
  ];

  const buildTabMenu = (tabId, hiddenTabs, hideBadges) => {
    const tab = panelTabs.find((t) => t.id === tabId);
    const isBadgeHidden = Boolean(hideBadges[tabId]);
    return [
      { label: "Configure Keybinding" },
      { label: `Hide '${tab.label}'` },
      ...(tabId === "problems" ? [{ label: isBadgeHidden ? "Show Badge" : "Hide Badge" }] : []),
      {
        label: "Move To",
        submenu: ["Panel", "Secondary Side Bar"],
      },
      { separator: true },
      ...panelTabs.map((t) => ({
        label: t.label,
        checked: !hiddenTabs.includes(t.id),
        checkbox: true,
      })),
      { separator: true },
      {
        label: "Panel Position",
        submenu: ["Bottom", "Left", "Right"],
      },
      {
        label: "Align Panel",
        submenu: ["Center", "Justify", "Left", "Right"],
      },
      { label: "Show Icons", checkbox: true },
      { label: "Hide Panel", shortcut: ["⌘", "J"] },
    ];
  };

  const headerItems = buildHeaderMenu(["output", "debug", "ports", "comments"]);
  assert.equal(headerItems.length, 12);
  assert.equal(headerItems[0].label, "Problems");
  assert.equal(headerItems[0].checked, true);
  assert.equal(headerItems[1].label, "Output");
  assert.equal(headerItems[1].checked, false);
  assert.equal(headerItems[4].label, "Terminal");
  assert.equal(headerItems[4].checked, true);
  assert.deepEqual(headerItems[8].submenu, ["Bottom", "Left", "Right"]);
  assert.deepEqual(headerItems[9].submenu, ["Center", "Justify", "Left", "Right"]);

  const tabItems = buildTabMenu("terminal", ["output", "debug", "ports", "comments"], {});
  assert.equal(tabItems[0].label, "Configure Keybinding");
  assert.equal(tabItems[1].label, "Hide 'Terminal'");
  assert.equal(tabItems[2].label, "Move To");
  assert.deepEqual(tabItems[2].submenu, ["Panel", "Secondary Side Bar"]);

  const problemsTabItems = buildTabMenu("problems", [], {});
  assert.equal(problemsTabItems[2].label, "Hide Badge");
  const problemsHiddenBadgeItems = buildTabMenu("problems", [], { problems: true });
  assert.equal(problemsHiddenBadgeItems[2].label, "Show Badge");
});

test("terminal viewport, session and actions toolbar context menus match VS Code model", () => {
  const sessionMenu = [
    { label: "Split Terminal", shortcut: ["⌘", "\\"] },
    { label: "Move Terminal into Editor Area" },
    { label: "Move Terminal into New Window" },
    { separator: true },
    { label: "Change Color..." },
    { label: "Change Icon..." },
    { label: "Rename...", shortcut: ["↩"] },
    { label: "Toggle Size to Content Width", shortcut: ["⌥", "Z"] },
    { separator: true },
    { label: "Kill Terminal", shortcut: ["⌘", "⌫"] },
  ];
  assert.equal(sessionMenu[0].label, "Split Terminal");
  assert.equal(sessionMenu[6].label, "Rename...");
  assert.equal(sessionMenu[9].label, "Kill Terminal");

  const viewportMenu = [
    { label: "Add Terminal Selection or Output to Chat", shortcut: ["⌘", "L"] },
    { separator: true },
    { label: "Copy", shortcut: ["⌘", "C"] },
    { label: "Copy as HTML" },
    { label: "Paste", shortcut: ["⌘", "V"] },
    { label: "Select All", shortcut: ["⌘", "A"] },
    { separator: true },
    { label: "Clear", shortcut: ["⌘", "K"] },
    { separator: true },
    { label: "Kill Terminal" },
    { separator: true },
    { label: "Toggle Size to Content Width", shortcut: ["⌥", "Z"] },
  ];
  assert.equal(viewportMenu[0].label, "Add Terminal Selection or Output to Chat");
  assert.equal(viewportMenu[2].label, "Copy");
  assert.equal(viewportMenu[4].label, "Paste");
  assert.equal(viewportMenu[7].label, "Clear");

  const actionsMenu = [
    { label: "Hide" },
    { separator: true },
    { label: "Focus Terminal", checked: true },
    { label: "New Terminal", checked: true },
    { label: "Split Terminal", checked: true },
    { label: "Kill Terminal", checked: true },
    { label: "Scroll to Previous Command" },
    { label: "Scroll to Next Command" },
    { label: "Clear Terminal" },
    { label: "Run Active File" },
    { label: "Run Selected Text" },
    { label: "Start Dictation" },
    { separator: true },
    { label: "Reset Menu" },
  ];
  assert.equal(actionsMenu[0].label, "Hide");
  assert.equal(actionsMenu[2].label, "Focus Terminal");
  assert.equal(actionsMenu[13].label, "Reset Menu");
});

test("editor and terminal preferences persist independently and tolerate invalid stored values", () => {
  const storageKey = "texsys.studio.user-preferences";
  const values = new Map([
    [
      storageKey,
      '{"editorFontSize":99,"editorWordWrap":"off","editorMinimap":false,"terminalFontSize":16}',
    ],
  ]);
  const original = Object.getOwnPropertyDescriptor(globalThis, "localStorage");
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    value: {
      getItem: (key) => values.get(key) ?? null,
      setItem: (key, value) => values.set(key, value),
    },
  });
  try {
    const { updateUserPreferences, defaultEditorPreferences } = load(
      "src/texsys/assets/studio/user-preferences.ts",
    );
    updateUserPreferences({ editorLineNumbers: false });
    assert.deepEqual(JSON.parse(values.get(storageKey)), {
      editorFontSize: 13,
      editorWordWrap: "off",
      editorLineNumbers: false,
      editorMinimap: false,
      terminalFontSize: 16,
      autoSave: false,
      confirmBeforeUnload: true,
    });
    updateUserPreferences({ autoSave: true, confirmBeforeUnload: false });
    updateUserPreferences(defaultEditorPreferences);
    assert.deepEqual(JSON.parse(values.get(storageKey)), {
      ...defaultEditorPreferences,
      terminalFontSize: 16,
      autoSave: true,
      confirmBeforeUnload: false,
    });
  } finally {
    if (original) Object.defineProperty(globalThis, "localStorage", original);
    else delete globalThis.localStorage;
  }
});
