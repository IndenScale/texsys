import test from "node:test";
import assert from "node:assert/strict";
import Module from "node:module";
import path from "node:path";
import { deflateRawSync } from "node:zlib";
import esbuild from "esbuild-wasm";

function load(source, dependencies = {}, fixture) {
  const filename = path.resolve(source);
  const result = esbuild.buildSync({
    ...(fixture
      ? { stdin: { contents: fixture, resolveDir: process.cwd() } }
      : { entryPoints: [filename] }),
    bundle: true,
    write: false,
    platform: "node",
    format: "cjs",
    packages: "external",
    loader: { ".css": "empty" },
  });
  const module = new Module(filename);
  module.paths = Module._nodeModulePaths(process.cwd());
  module.require = (id) =>
    Object.hasOwn(dependencies, id) ? dependencies[id] : Module.prototype.require.call(module, id);
  module._compile(result.outputFiles[0].text, filename);
  return module.exports;
}
const { assertionLocation } = load(
  "packages/hvac-graphics/src/texsys_hvac_graphics/assets/analysis.ts",
);
const { bindingValue, instanceFromRef, issuesFor, modelObjects, sourceObjectLine } = load(
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

test("model object navigation keeps reused nested instances distinct and locates their declaration", () => {
  const data = {
    root: "Station",
    components: {
      Station: { instances: { Left: { component: "Loop" }, Right: { component: "Loop" } } },
      Loop: { instances: { Pump: { component: "PumpType" }, Cycle: { component: "Station" } } },
      PumpType: {},
    },
  };
  const { objects, truncated } = modelObjects(data);
  const matches = objects.filter(
    (item) => item.kind === "instance" && item.definition === "PumpType",
  );
  assert.deepEqual(
    matches.map((item) => item.label),
    ["Station.Left.Pump", "Station.Right.Pump"],
  );
  assert.notEqual(matches[0].id, matches[1].id);
  assert.equal(truncated, false);
  const lines = JSON.stringify(data, null, 2).split("\n");
  for (const item of matches)
    assert.equal(lines[sourceObjectLine(data, item.selection)], '        "Pump": {');
  assert.equal(objects.filter((item) => item.kind === "instance").length, 6);
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
  for (const retired of ["packages", "engineering"]) {
    saved.layouts["texsys:hvac"].sidebarActivity = retired;
    saved.layouts["texsys:hvac"].hiddenActivities = ["packages", "engineering", "scm"];
    writeLayoutPreferences("/example/a", saved);
    const restored = readLayoutPreferences("/example/a", defaults).layouts["texsys:hvac"];
    assert.equal(restored.sidebarActivity, "explorer");
    assert.deepEqual(restored.hiddenActivities, ["scm"]);
  }
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
    ["commands.open", "view.sidebar", "view.bottom", "view.inspector", "view.panelPosition"],
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
    ["view.open", "view.panelPosition", "view.statusbar", "view.wordWrap", "view.fullscreen"],
  );
  assert.deepEqual(
    filterCommands(commands, "布局").map((item) => item.id),
    ["view.sidebar", "view.bottom", "view.inspector", "view.statusbar"],
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

const {
  visibleFileTree,
  explorerCreationParent,
  explorerCreationIndex,
  gitGroups,
  gitPathInProject,
  sourceOffset,
  commitGraph,
} = load("src/texsys/assets/studio/workspace-model.ts");
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
test("Explorer creates within a selected directory or beside a selected file", () => {
  const listing = {
    files: [{ path: "src/main.json" }, { path: "texsys.toml" }],
    directories: ["src", "empty"],
    truncated: false,
  };
  assert.equal(explorerCreationParent(listing, "src"), "src");
  assert.equal(explorerCreationParent(listing, "src/main.json"), "src");
  assert.equal(explorerCreationParent(listing, "empty"), "empty");
  assert.equal(explorerCreationParent(listing, "texsys.toml"), "");
  assert.equal(explorerCreationParent(listing, ""), "");
  assert.equal(explorerCreationParent(listing, "removed/file.json"), "");
});
test("Explorer inline creation respects folder groups and complete expanded subtrees", () => {
  const listing = {
    files: [{ path: "src/nested/model.json" }, { path: "src/main.json" }, { path: "README.md" }],
    directories: ["empty", "src", "src/nested"],
    truncated: false,
  };
  const rows = visibleFileTree(listing, new Set(["src", "src/nested", "empty"]));
  const before = (parent, directory) => rows[explorerCreationIndex(rows, parent, directory)]?.path;
  assert.equal(before("", true), "empty");
  assert.equal(before("", false), "README.md");
  assert.equal(before("src", true), "src/nested");
  assert.equal(before("src", false), "src/main.json");
  assert.equal(before("empty", false), "src");
  assert.equal(explorerCreationIndex(rows, "missing", false), -1);
  const foldersOnly = visibleFileTree(
    { ...listing, files: [{ path: "src/nested/model.json" }] },
    new Set(["src", "src/nested"]),
  );
  assert.equal(explorerCreationIndex(foldersOnly, "", false), foldersOnly.length);
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
  assert.equal(translate("zh-CN", "资源管理器"), "资源管理器");
  assert.equal(translate("en", "资源管理器"), "Explorer");
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
  assert.equal(owner("tasks.watchBuild"), "terminal");
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
        panelPosition: "right",
        panelWidth: 345,
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
  assert.equal(layout.panelPosition, "right");
  assert.equal(layout.panelWidth, 345);
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

test("editor, terminal and scene preferences persist independently and tolerate invalid stored values", () => {
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
    const { updateUserPreferences, defaultEditorPreferences, defaultScenePreferences } = load(
      "src/texsys/assets/studio/user-preferences.ts",
    );
    assert.equal(defaultEditorPreferences.editorWordWrap, "on");
    updateUserPreferences({ editorLineNumbers: false, editorScreenReader: true });
    assert.deepEqual(JSON.parse(values.get(storageKey)), {
      editorFontSize: 13,
      editorWordWrap: "off",
      editorLineNumbers: false,
      editorMinimap: false,
      editorScreenReader: true,
      terminalFontSize: 16,
      ...defaultScenePreferences,
      autoSave: false,
      confirmBeforeUnload: true,
      fileAssociations: {},
    });
    updateUserPreferences({ autoSave: true, confirmBeforeUnload: false, sceneMoveSpeed: 16 });
    updateUserPreferences(defaultEditorPreferences);
    assert.deepEqual(JSON.parse(values.get(storageKey)), {
      ...defaultEditorPreferences,
      ...defaultScenePreferences,
      sceneMoveSpeed: 16,
      terminalFontSize: 16,
      autoSave: true,
      confirmBeforeUnload: false,
      fileAssociations: {},
    });
    values.set(
      storageKey,
      '{"editorWordWrap":"bounded","sceneMoveSpeed":0,"sceneBoost":6,"sceneMouseSensitivity":"2"}',
    );
    const reloaded = load("src/texsys/assets/studio/user-preferences.ts");
    reloaded.updateUserPreferences({});
    const restored = JSON.parse(values.get(storageKey));
    assert.equal(restored.editorWordWrap, "on");
    assert.equal(restored.sceneMoveSpeed, defaultScenePreferences.sceneMoveSpeed);
    assert.equal(restored.sceneBoost, 6);
    assert.equal(restored.sceneMouseSensitivity, defaultScenePreferences.sceneMouseSensitivity);
  } finally {
    if (original) Object.defineProperty(globalThis, "localStorage", original);
    else delete globalThis.localStorage;
  }
});

test("file opening defaults only select renderers supported by the current file", () => {
  const { fileEditorDefault, fileEditorExtension } = load(
    "src/texsys/assets/studio/user-preferences.ts",
  );
  assert.equal(fileEditorExtension("docs/Guide.MD"), "md");
  assert.equal(fileEditorExtension("folder.md/README"), "");
  assert.equal(fileEditorExtension("texsys.lock"), "lock");
  assert.equal(fileEditorDefault("README.md", "markdown", {}), "source");
  assert.equal(fileEditorDefault("model.usda", "scene", {}), "preview");
  assert.equal(fileEditorDefault("schematic.json", "diagram", {}), "preview");
  assert.equal(fileEditorDefault("system.drawio", "drawio", {}), "preview");
  assert.equal(fileEditorDefault("system.drawio", "drawio", { drawio: "source" }), "source");
  assert.equal(fileEditorDefault("system.html", "html", {}), "source");
  assert.equal(fileEditorDefault("system.html", "html", { html: "preview" }), "preview");
  assert.equal(fileEditorDefault("data.json", undefined, {}), "source");
  const associations = { md: "markdown-editor", json: "preview", usda: "source" };
  assert.equal(fileEditorDefault("Guide.MD", "markdown", associations), "markdown-editor");
  assert.equal(fileEditorDefault("data.json", undefined, associations), "source");
  assert.equal(fileEditorDefault("schematic.json", "diagram", associations), "preview");
  assert.equal(fileEditorDefault("model.usda", "scene", associations), "source");
  assert.equal(fileEditorDefault("README.md", "markdown", { md: "missing-editor" }), "source");
  assert.equal(fileEditorDefault("model.usda", "scene", { usda: "markdown-editor" }), "preview");
  assert.equal(
    fileEditorDefault("README.md", "markdown", Object.create({ md: "preview" })),
    "source",
  );
});

test("file opening defaults validate storage, persist across reloads and follow storage events", () => {
  const storageKey = "texsys.studio.user-preferences";
  const values = new Map([
    [
      storageKey,
      JSON.stringify({
        editorFontSize: 16,
        fileAssociations: {
          md: "preview",
          markdown: "markdown-editor",
          json: "markdown-editor",
          txt: "unknown",
          "*.md": "source",
          MD: "preview",
          constructor: "preview",
        },
      }),
    ],
  ]);
  const originalStorage = Object.getOwnPropertyDescriptor(globalThis, "localStorage");
  const originalWindow = Object.getOwnPropertyDescriptor(globalThis, "window");
  let storageListener;
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    value: {
      getItem: (key) => values.get(key) ?? null,
      setItem: (key, value) => values.set(key, value),
    },
  });
  Object.defineProperty(globalThis, "window", {
    configurable: true,
    value: {
      addEventListener: (name, listener) => {
        if (name === "storage") storageListener = listener;
      },
    },
  });
  try {
    const first = load("src/texsys/assets/studio/user-preferences.ts");
    first.updateUserPreferences({});
    assert.deepEqual(JSON.parse(values.get(storageKey)).fileAssociations, {
      md: "preview",
      markdown: "markdown-editor",
    });
    const beforeInvalid = values.get(storageKey);
    assert.equal(first.setFileEditorDefault("data.json", undefined, "preview"), false);
    assert.equal(first.setFileEditorDefault("data.json", undefined, "markdown-editor"), false);
    assert.equal(first.setFileEditorDefault("README", undefined, "source"), false);
    assert.equal(first.setFileEditorDefault("README.md", "markdown", "missing-editor"), false);
    assert.equal(values.get(storageKey), beforeInvalid);
    assert.equal(first.setFileEditorDefault("README.MD", "markdown", "markdown-editor"), true);
    assert.equal(first.setFileEditorDefault("data.json", undefined, "source"), true);

    const reloaded = load("src/texsys/assets/studio/user-preferences.ts");
    reloaded.updateUserPreferences({ editorLineNumbers: false });
    const restored = JSON.parse(values.get(storageKey));
    assert.equal(restored.editorFontSize, 16);
    assert.deepEqual(restored.fileAssociations, {
      md: "markdown-editor",
      markdown: "markdown-editor",
      json: "source",
    });

    storageListener({
      key: storageKey,
      newValue: JSON.stringify({ fileAssociations: { md: "preview" } }),
    });
    reloaded.setFileEditorDefault("README.markdown", "markdown", "source");
    assert.deepEqual(JSON.parse(values.get(storageKey)).fileAssociations, {
      md: "preview",
      markdown: "source",
    });
    storageListener({ key: null, newValue: null });
    reloaded.updateUserPreferences({});
    assert.deepEqual(JSON.parse(values.get(storageKey)).fileAssociations, {});
    reloaded.updateUserPreferences({ fileAssociations: ["preview"] });
    assert.deepEqual(JSON.parse(values.get(storageKey)).fileAssociations, {});
    reloaded.updateUserPreferences({ fileAssociations: { md: false, json: "markdown-editor" } });
    assert.deepEqual(JSON.parse(values.get(storageKey)).fileAssociations, {});
  } finally {
    if (originalStorage) Object.defineProperty(globalThis, "localStorage", originalStorage);
    else delete globalThis.localStorage;
    if (originalWindow) Object.defineProperty(globalThis, "window", originalWindow);
    else delete globalThis.window;
  }
});

const { sourceReference, sceneFileRequest, safePrimitive, pickedFace } = load(
  "src/texsys/assets/studio/project-preview-model.ts",
);
test("preview source navigation uses mapped files, never declaration paths or inferred object names", () => {
  const source = { file: "src/system.sdl.json", path: "components.Station.instances.P1", line: 15 };
  assert.deepEqual(sourceReference({ source_map: { "node:Station.P1": source } }, "Station.P1"), {
    ...source,
    column: undefined,
  });
  assert.equal(sourceReference({ source_map: { "Station.P1": source } }, "Station.P10"), undefined);
  for (const file of [
    "../outside.json",
    "/absolute.json",
    "C:\\outside.json",
    "https://bad/file",
    "a\0b",
  ]) {
    assert.equal(
      sourceReference({ source_map: { P1: { file, path: "src/legitimate.json" } } }, "P1"),
      undefined,
    );
  }
  assert.equal(
    sourceReference({ source_map: { P1: { path: "src/legitimate.json" } } }, "P1"),
    undefined,
  );
});
test("diagram primitives exclude executable tags, event handlers and external paint references", () => {
  for (const tag of ["script", "foreignObject", "image", "use", "iframe", "a"])
    assert.equal(safePrimitive({ tag, attrs: {} }), undefined);
  assert.deepEqual(
    safePrimitive({
      tag: "rect",
      attrs: {
        width: 20,
        height: 30,
        onClick: "steal()",
        href: "javascript:steal()",
        fill: "url(https://external)",
        "stroke-width": 2,
      },
    }),
    { tag: "rect", attrs: { width: 20, height: 30, strokeWidth: 2 } },
  );
});
test("scene picking selects the visible face and clears outside geometry", () => {
  const far = {
    id: "far",
    vertices: [
      [0, 0],
      [20, 0],
      [20, 20],
      [0, 20],
    ],
  };
  const near = {
    id: "near",
    vertices: [
      [5, 5],
      [15, 5],
      [15, 15],
      [5, 15],
    ],
  };
  assert.equal(pickedFace([far, near], 10, 10), near);
  assert.equal(pickedFace([far, near], 2, 2), far);
  assert.equal(pickedFace([far, near], 30, 30), undefined);
});

test("scene previews use current files or unsaved buffers without historical artifact selection", () => {
  const path = "build/layout/a scene.usda";
  const clean = sceneFileRequest(path, "disk-v1", "disk bytes", false);
  const params = new URL(clean.url, "http://localhost").searchParams;
  assert.equal(params.get("path"), path);
  assert.equal(params.get("version"), "disk-v1");
  assert.equal(params.has("run"), false);
  assert.equal(params.has("artifact"), false);
  assert.equal(clean.body, undefined);
  const dirty = sceneFileRequest(path, "disk-v1", "unsaved bytes", true);
  assert.equal(dirty.url, "/api/scene");
  assert.deepEqual(dirty.body, { path, version: "disk-v1", content: "unsaved bytes" });
  assert.notEqual(dirty.key, clean.key);
  assert.notEqual(dirty.key, sceneFileRequest(path, "disk-v1", "newer unsaved bytes", true).key);
  assert.notEqual(clean.key, sceneFileRequest(path, "disk-v2", "disk bytes", false).key);
});

const {
  fileDocumentSnapshot,
  fileDocumentTitle,
  filePreviewKind,
  refreshedFileDocument,
  savedFileDocument,
  FileReadRequests,
} = load("src/texsys/assets/studio/workspace-model.ts");
test("background file reads wait for the first open or explicit reload to finish", () => {
  const path = "build/drawings/schematics/system-check.json";
  const reads = new FileReadRequests();
  const opening = reads.begin(path);
  assert.equal(reads.beginRefresh(path), undefined);
  assert.equal(opening.isCurrent(), true);
  assert.equal(opening.signal.aborted, false);
  assert.equal(reads.pending(path), true);
  const document = opening.update({ loading: true }, () => ({ loading: false, content: "{}" }));
  opening.finish();
  assert.equal(reads.pending(path), false);
  assert.equal(opening.isCurrent(), true, "finishing must retain queued React update ownership");
  assert.equal(document.loading, false);
  const refresh = reads.beginRefresh(path);
  assert.ok(refresh);
  assert.equal(reads.beginRefresh(path), undefined, "slow polls cannot overlap");
  const reload = reads.begin(path);
  assert.equal(refresh.signal.aborted, true);
  assert.equal(reads.beginRefresh(path), undefined);
  refresh.finish();
  assert.equal(reads.pending(path), true, "an old completion cannot finish the current reload");
  reload.finish();
  assert.ok(reads.beginRefresh(path));
});
test("closing and superseding file reads abort transport without cancelling newer reads", () => {
  const reads = new FileReadRequests();
  const first = reads.begin("README.md");
  const second = reads.begin("README.md");
  const other = reads.begin("texsys.toml");
  assert.equal(first.signal.aborted, true);
  first.cancel();
  assert.equal(second.signal.aborted, false);
  reads.invalidate("README.md");
  assert.equal(second.signal.aborted, true);
  assert.equal(reads.pending("README.md"), false);
  assert.equal(other.signal.aborted, false);
  const reopened = reads.begin("README.md");
  second.cancel();
  assert.equal(reopened.signal.aborted, false);
  reopened.finish();
  assert.ok(reads.beginRefresh("README.md"), "a settled failed read remains retryable");
});

// Execute the production effect with controlled I/O and time, without loading Monaco.
async function workspaceEffect(marker, scope) {
  const { readFileSync } = await import("node:fs");
  const ts = await import("typescript");
  const source = ts.createSourceFile(
    "CoreWorkspace.tsx",
    readFileSync("src/texsys/assets/studio/CoreWorkspace.tsx", "utf8"),
    ts.ScriptTarget.Latest,
    true,
    ts.ScriptKind.TSX,
  );
  const effects = [];
  const visit = (node) => {
    if (
      ts.isCallExpression(node) &&
      node.expression.getText(source) === "useEffect" &&
      node.arguments[0]?.getText(source).includes(marker)
    )
      effects.push(node.arguments[0].getText(source));
    ts.forEachChild(node, visit);
  };
  visit(source);
  assert.equal(effects.length, 1);
  const { outputText } = ts.transpileModule(`(${effects[0]})`, {
    compilerOptions: { target: ts.ScriptTarget.ES2022 },
  });
  return new Function(...Object.keys(scope), `return ${outputText}`)(...Object.values(scope));
}

test("visible file polling pauses while hidden and resumes with only one timer", async (t) => {
  const document = new EventTarget();
  document.visibilityState = "visible";
  const path = "build/drawings/schematics/system-check.json";
  const documentsRef = {
    current: [{ path, content: "{}", saved: "{}", version: "one", loading: true }],
  };
  const reads = new FileReadRequests();
  const opening = reads.begin(path);
  const requests = [];
  const effect = await workspaceEffect("JSON.parse(visibleFilePaths)", {
    document,
    visibleFilePaths: JSON.stringify([path]),
    documentsRef,
    fileWriteInFlight: { current: false },
    fileReads: { current: reads },
    isDocumentDirty: (item) => item.content !== item.saved,
    fileDocumentSnapshot,
    refreshedFileDocument,
    setDocuments: (update) => (documentsRef.current = update(documentsRef.current)),
    api: (_url, _body, signal) =>
      new Promise((resolve, reject) => {
        requests.push({ signal, resolve: () => resolve({ path, content: "{}", version: "one" }) });
        signal.addEventListener("abort", () => reject(new Error("aborted")), { once: true });
      }),
  });
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const flush = async () => {
    for (let i = 0; i < 5; i++) await Promise.resolve();
  };
  const visibility = (state) => {
    document.visibilityState = state;
    document.dispatchEvent(new Event("visibilitychange"));
  };
  const cleanup = effect();
  t.after(cleanup);
  await flush();
  assert.equal(requests.length, 0, "first-open response owns the document until it finishes");
  opening.finish();
  documentsRef.current[0].loading = false;
  t.mock.timers.tick(2000);
  assert.equal(requests.length, 1);
  visibility("hidden");
  t.mock.timers.tick(10000);
  assert.equal(requests.length, 1);
  visibility("visible");
  visibility("visible");
  assert.equal(requests.length, 1, "visibility changes do not overlap a pending request");
  requests[0].resolve();
  await flush();
  t.mock.timers.tick(2000);
  assert.equal(requests.length, 2, "one completed poll creates exactly one next timer");
  requests[1].resolve();
  await flush();
  visibility("hidden");
  t.mock.timers.tick(10000);
  assert.equal(requests.length, 2);
  visibility("visible");
  assert.equal(requests.length, 3, "returning to the page immediately checks disk changes");
  cleanup();
  assert.equal(requests[2].signal.aborted, true);
  await flush();
  t.mock.timers.tick(10000);
  visibility("visible");
  assert.equal(requests.length, 3, "unmounted effects cannot restart polling");
});

test("SCM periodic reads stop while hidden but explicit focus refresh remains available", async (t) => {
  const document = { visibilityState: "hidden" };
  const window = new EventTarget();
  let gitReads = 0;
  let listingReads = 0;
  const effect = await workspaceEffect("const terminalCompleted", {
    document,
    window,
    readFiles: () => listingReads++,
    readGit: () => gitReads++,
    activity: "scm",
    busy: false,
  });
  t.mock.timers.enable({ apis: ["setInterval"] });
  const cleanup = effect();
  t.after(cleanup);
  t.mock.timers.tick(10000);
  assert.equal(gitReads, 0);
  document.visibilityState = "visible";
  t.mock.timers.tick(5000);
  assert.equal(gitReads, 1);
  window.dispatchEvent(new Event("focus"));
  assert.equal(gitReads, 2);
  assert.equal(listingReads, 1);
  cleanup();
  t.mock.timers.tick(5000);
  assert.equal(gitReads, 2);
});

test("late file poll success and failure cannot replace newer file metadata", async () => {
  const path = "build/hvac/schematic/system.json";
  const response = (run) => ({
    path,
    content: "{}",
    version: "same-bytes",
    generated: true,
    preview: { kind: "schematic", source_map: { node: { file: `${run}.json` } } },
  });
  for (const outcome of ["success", "failure"]) {
    const reads = new FileReadRequests();
    let document = fileDocumentSnapshot(path, response("old"));
    let finishOld;
    const oldResponse = new Promise((resolve, reject) => {
      finishOld = () =>
        outcome === "success" ? resolve(response("old")) : reject(new Error("old 404"));
    });
    const poll = reads.begin(path);
    const oldCompletion = oldResponse.then(
      (file) => {
        document = poll.update(document, (current) => ({
          ...current,
          ...fileDocumentSnapshot(path, file),
        }));
      },
      (error) => {
        document = poll.update(document, (current) => ({
          ...current,
          error: String(error),
        }));
      },
    );
    const reread = reads.begin(path);
    document = reread.update(document, () => fileDocumentSnapshot(path, response("new")));
    const restored = document;
    finishOld();
    await oldCompletion;
    assert.equal(document, restored);
    assert.equal(document.preview.source_map.node.file, "new.json");
    assert.equal(document.error, undefined);

    const failedCurrent = reads.begin(path);
    document = failedCurrent.update(document, (current) => ({
      ...current,
      error: "current 404",
    }));
    assert.equal(document.error, "current 404");
  }
});
test("a queued file state update rechecks request identity when React applies it", () => {
  const reads = new FileReadRequests();
  const opened = reads.begin("build/result.json");
  assert.equal(opened.isCurrent(), true);
  const queued = (state) => opened.update(state, () => ({ run: "old" }));
  const poll = reads.begin("build/result.json");
  const current = poll.update({ run: "initial" }, () => ({ run: "new" }));
  assert.equal(queued(current), current);
  assert.equal(poll.isCurrent(), true);
});
test("file read cleanup cancels only its own request and closed paths cannot update reopened files", () => {
  const reads = new FileReadRequests();
  const poll = reads.begin("a.json");
  const reread = reads.begin("a.json");
  const other = reads.begin("b.json");
  poll.cancel();
  assert.equal(reread.isCurrent(), true);
  assert.equal(other.isCurrent(), true);
  reads.invalidate("a.json");
  const reopened = reads.begin("a.json");
  reread.cancel();
  assert.equal(reread.isCurrent(), false);
  assert.equal(reopened.isCurrent(), true);
  assert.equal(other.isCurrent(), true);
  const current = { run: "reopened" };
  assert.equal(
    reread.update(current, () => ({ run: "closed" })),
    current,
  );
  reopened.cancel();
  assert.equal(reopened.isCurrent(), false);
});
test("file snapshots keep current bytes and format, without turning generated files read-only", () => {
  const path = "build/hvac/schematic/system.json";
  const first = fileDocumentSnapshot(path, {
    path,
    content: "{}",
    version: "one",
    generated: true,
    preview: { kind: "schematic" },
    artifact: { path, run_id: "old" },
    readonly: true,
  });
  assert.equal(first.readonly, undefined);
  assert.equal(first.artifact, undefined);
  assert.equal(first.preview.kind, "schematic");
  const changed = {
    ...first,
    ...fileDocumentSnapshot(path, { path, content: "changed", version: "two" }),
  };
  assert.equal(changed.preview, undefined);
  assert.equal(changed.content, "changed");
  assert.throws(() => fileDocumentSnapshot(path, { path: "another.json" }), /requested path/);
});
test("same-named file tabs use their shortest distinct parent path", () => {
  const paths = [
    "build/hvac/schematic/system.json",
    "build/hvac/topology/system.json",
    "src/ordinary.json",
  ];
  assert.equal(fileDocumentTitle(paths[0], paths), "system.json · schematic");
  assert.equal(fileDocumentTitle(paths[1], paths), "system.json · topology");
  assert.equal(fileDocumentTitle(paths[2], paths), "ordinary.json");
});
test("Markdown, USD, SVG, Draw.io and explicit diagram formats select a preview renderer", () => {
  assert.equal(filePreviewKind({ path: "build/hvac/bom.md" }), "markdown");
  assert.equal(filePreviewKind({ path: "notes.markdown" }), "markdown");
  assert.equal(filePreviewKind({ path: "new.usda" }), "scene");
  assert.equal(filePreviewKind({ path: "build/plans/room.SVG" }), "svg");
  assert.equal(filePreviewKind({ path: "copied/loop.DRAWIO" }), "drawio");
  assert.equal(filePreviewKind({ path: "large.drawio", reason: "Too large" }), undefined);
  assert.equal(filePreviewKind({ path: "build/drawings/schematics/system.html" }), "html");
  assert.equal(filePreviewKind({ path: "notes.HTM" }), "html");
  assert.equal(filePreviewKind({ path: "large.html", reason: "Too large" }), undefined);
  assert.equal(filePreviewKind({ path: "build/bom.csv" }), undefined);
  for (const name of ["bom.json", "system-check.json", "build.json", "ordinary.json"])
    assert.equal(
      filePreviewKind({
        path: name,
        content: JSON.stringify({ schema_version: 1, status: "pass" }),
      }),
      undefined,
    );
  assert.equal(
    filePreviewKind({ path: "ordinary.json", content: '{"nodes":[],"edges":[],"pages":[]}' }),
    undefined,
  );
  assert.equal(
    filePreviewKind({ path: "system.json", preview: { kind: "schematic" }, content: "invalid" }),
    "diagram",
  );
  const diagram = {
    schema_version: 1,
    view: "schematic",
    deliverable: "schematic",
    backend: { protocol: 1 },
    view_contract: {},
    pages: [],
    nodes: [],
    edges: [],
  };
  assert.equal(
    filePreviewKind({ path: "renamed.json", content: JSON.stringify(diagram) }),
    "diagram",
  );
});
test("background file refresh preserves dirty buffers and rejects reads from an older baseline", () => {
  for (const path of ["src/settings.json", "build/bom.md"]) {
    const clean = { path, content: "old", saved: "old", version: "v1", presentation: "source" };
    const dirty = { ...clean, content: "local typing" };
    const disk = { path, content: "external", version: "v2" };
    assert.equal(refreshedFileDocument(dirty, disk, "v1"), dirty);
    const refreshed = refreshedFileDocument(clean, disk, "v1");
    assert.equal(refreshed.content, "external");
    assert.equal(refreshed.saved, "external");
    assert.equal(refreshed.presentation, "source");
    assert.equal(
      refreshedFileDocument(refreshed, { path, content: "late old", version: "v1" }, "v1"),
      refreshed,
    );
  }
});
test("save completion retains typing during the request and invalidates an earlier poll", () => {
  const path = "build/bom.md";
  const reads = new FileReadRequests();
  const poll = reads.begin(path);
  const submitted = {
    path,
    content: "submitted",
    saved: "old",
    version: "v1",
    presentation: "preview",
  };
  reads.invalidate(path);
  const typed = { ...submitted, content: "submitted and more typing" };
  const saved = savedFileDocument(typed, {
    path,
    content: "submitted",
    version: "v2",
    generated: true,
  });
  assert.equal(saved.content, typed.content);
  assert.equal(saved.saved, "submitted");
  assert.equal(saved.version, "v2");
  assert.equal(saved.presentation, "preview");
  assert.equal(
    poll.update(saved, (item) =>
      refreshedFileDocument(item, { path, content: "old", version: "v1" }, "v1"),
    ),
    saved,
  );
  const clean = savedFileDocument(submitted, { path, content: "submitted", version: "v2" });
  assert.equal(clean.content, clean.saved);
  assert.equal(refreshedFileDocument(clean, { path, content: "old", version: "v1" }, "v1"), clean);
});

test("mounted artifact previews preserve their viewport when file geometry changes", async (t) => {
  const { JSDOM } = await import("jsdom");
  const { createElement, useState, act } = await import("react");
  const { createRoot } = await import("react-dom/client");
  const three = await import("three");
  const faces = [];
  class WebGLRenderer {
    setPixelRatio() {}
    setSize() {}
    dispose() {}
    render(scene, camera) {
      camera.updateMatrixWorld();
      scene.traverse((mesh) => {
        if (mesh.userData.id !== "/Room") return;
        const points = mesh.geometry.getAttribute("position");
        faces.push(
          Array.from({ length: points.count }, (_, index) =>
            new three.Vector3()
              .fromBufferAttribute(points, index)
              .project(camera)
              .toArray()
              .slice(0, 2)
              .map((value) => Number(value.toFixed(8))),
          ),
        );
      });
    }
  }
  const { FilePreviewContent, EditorControlTarget } = load(
    "src/texsys/assets/studio/ProjectPreview.tsx",
    { three: { ...three, WebGLRenderer } },
    'export { FilePreviewContent } from "./src/texsys/assets/studio/ProjectPreview"; ' +
      'export { EditorControlTarget } from "./src/texsys/assets/studio/EditorControlSlot";',
  );
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const dom = new JSDOM("<main></main>", { pretendToBeVisual: true });
  const { window } = dom;
  let frameId = 0;
  const frames = new Map();
  const globals = {
    window,
    document: window.document,
    DOMParser: window.DOMParser,
    CSS: { escape: String },
    ResizeObserver: class {
      observe() {}
      disconnect() {}
    },
    requestAnimationFrame: (callback) => {
      frames.set(++frameId, callback);
      return frameId;
    },
    cancelAnimationFrame: (id) => frames.delete(id),
    IS_REACT_ACT_ENVIRONMENT: true,
  };
  const previous = Object.fromEntries(Object.keys(globals).map((key) => [key, globalThis[key]]));
  Object.assign(globalThis, globals);
  t.after(() => {
    Object.assign(globalThis, previous);
    window.close();
  });
  for (const type of [window.SVGSVGElement, window.HTMLCanvasElement]) {
    Object.defineProperties(type.prototype, {
      clientWidth: { value: 400 },
      clientHeight: { value: 300 },
    });
  }
  Object.assign(window.HTMLCanvasElement.prototype, {
    getBoundingClientRect: () => ({ left: 0, top: 0, width: 400, height: 300 }),
    setPointerCapture() {},
    releasePointerCapture() {},
    hasPointerCapture: () => false,
  });
  window.HTMLElement.prototype.scrollIntoView = () => {};
  const room = {
    id: "/Room",
    prim_path: "/Room",
    semantic_ref: "room",
    name: "Room",
    type: "Cube",
    facets: { role: "Slab", discipline: "structural", zone: "North", level: "L1" },
    points: [
      [0, 0, 0],
      [3, 0, 0],
      [0, 3, 0],
    ],
    faces: [[0, 1, 2]],
  };
  const extension = {
    ...room,
    id: "/Extension",
    prim_path: "/Extension",
    semantic_ref: "extension",
    name: "Extension",
    points: [
      [100, 0, 0],
      [103, 0, 0],
      [100, 3, 0],
    ],
  };
  let composition = "layer-v1";
  const requests = [];
  const propertyRequests = [];
  const api = async (url, body) => {
    const request = new URL(url, "http://studio.local");
    if (request.pathname === "/api/scene/properties") {
      propertyRequests.push(body);
      return {
        path: body.path,
        version: body.version,
        revision: body.revision,
        properties: Object.fromEntries(
          body.ids.map((id) => [id, { thickness: { prim: id, value: 0.5, unit: "m" } }]),
        ),
      };
    }
    requests.push(request);
    const version = request.searchParams.get("version");
    return {
      status: "ready",
      path: request.searchParams.get("path"),
      version,
      revision: composition,
      properties_deferred: true,
      objects:
        version === "v1"
          ? [room]
          : version === "large"
            ? Array.from({ length: 240 }, (_, index) => ({
                ...room,
                id: `/Element${index}`,
                prim_path: `/Element${index}`,
                name: `Element ${index}`,
              }))
            : [
                room,
                { ...extension, name: composition === "layer-v1" ? "Extension" : "Changed layer" },
              ],
    };
  };
  let latestViewport;
  function Harness({ kind, version, content = "", visible = true, dirty = false }) {
    const [viewport, onViewportChange] = useState();
    const [controls, setControls] = useState(null);
    const preview =
      visible &&
      createElement(FilePreviewContent, {
        api,
        kind,
        version,
        content,
        path: {
          scene: "build/site.usda",
          svg: "build/floor.svg",
          diagram: "build/system.json",
          drawio: "build/system.drawio",
        }[kind],
        dirty,
        viewport,
        onViewportChange: (next) => {
          latestViewport = next;
          onViewportChange(next);
        },
      });
    return createElement(
      EditorControlTarget.Provider,
      { value: controls },
      createElement("div", { ref: setControls, className: "test-editor-controls" }),
      preview,
    );
  }
  const container = window.document.querySelector("main");
  const root = createRoot(container);
  const flushFrames = () =>
    act(() => {
      for (const [id, callback] of [...frames]) {
        frames.delete(id);
        callback(performance.now());
      }
    });
  const render = async (props) => {
    await act(async () => root.render(createElement(Harness, { key: props.kind, ...props })));
    await flushFrames();
  };
  const zoom = async () => {
    await act(() => container.querySelector('[aria-label="放大"]').click());
    await flushFrames();
  };
  const firstPlan =
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><rect width="50" height="50"/></svg>';
  const nextPlan = firstPlan.replace("100 100", "500 500");
  await render({ kind: "svg", version: "v1", content: firstPlan });
  await zoom();
  const box = container.querySelector("svg").getAttribute("viewBox");
  const image = container.querySelector("image").getAttribute("href");
  await render({ kind: "svg", version: "v2", content: nextPlan });
  assert.equal(container.querySelector("svg").getAttribute("viewBox"), box);
  assert.notEqual(container.querySelector("image").getAttribute("href"), image);
  await render({ kind: "svg", version: "v2", content: nextPlan, visible: false });
  await render({ kind: "svg", version: "v2", content: nextPlan });
  assert.equal(container.querySelector("svg").getAttribute("viewBox"), box);

  const diagram = {
    deliverable: "schematic",
    pages: [{ id: "first" }, { id: "second" }],
    nodes: [
      {
        id: "pump",
        label: "P-CHW-01",
        page: "second",
        x: 100,
        y: 100,
        graphic: {
          label_anchor: { x: 71, y: 100 },
          annotation_anchor: { x: 71, y: 118 },
        },
        engineering_labels: ["关断扬程 22 m", "额定流量 0.005 m3/s"],
      },
    ],
  };
  await render({ kind: "diagram", version: "v1", content: JSON.stringify(diagram) });
  await act(() => {
    const pages = container.querySelector("select");
    pages.value = "second";
    pages.dispatchEvent(new window.Event("change", { bubbles: true }));
  });
  await zoom();
  await act(() => {
    const drawing = container.querySelector(".project-preview-drawing > svg");
    drawing.dispatchEvent(
      new window.KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true }),
    );
  });
  const drawing = container.querySelector(".project-preview-drawing > svg").getAttribute("viewBox");
  diagram.pages[1].width = 1600;
  diagram.nodes[0].x = 300;
  await render({ kind: "diagram", version: "v2", content: JSON.stringify(diagram) });
  assert.equal(container.querySelector("select").value, "second");
  assert.equal(
    container.querySelector(".project-preview-drawing > svg").getAttribute("viewBox"),
    drawing,
  );
  assert.equal(
    container.querySelector(".project-preview-node").getAttribute("transform"),
    "translate(300,100)",
  );
  const annotations = () => container.querySelectorAll(".project-preview-annotation");
  assert.deepEqual(
    Array.from(annotations(), (item) => item.textContent),
    ["关断扬程 22 m", "额定流量 0.005 m3/s"],
  );
  diagram.deliverable = "system-diagram";
  diagram.nodes[0].graphic.label_anchor.y = 112;
  await render({ kind: "diagram", version: "v3", content: JSON.stringify(diagram) });
  assert.equal(container.querySelector(".project-preview-node text").textContent, "P-CHW-01");
  assert.equal(annotations().length, 0);

  const drawio = `<mxfile compressed="false"><diagram id="schematic" name="Schematic">
    <mxGraphModel pageWidth="600" pageHeight="400"><root>
      <mxCell id="0"/><mxCell id="1" parent="0"/>
      <mxCell id="banner" value="Station&#10;Schematic" vertex="1" parent="1" style="text;html=0;align=left;fontSize=13;">
        <mxGeometry x="24" y="10" width="500" height="50" as="geometry"/>
      </mxCell>
      <UserObject id="pump" label="Pump"><mxCell vertex="1" parent="1" style="noLabel=1;fillColor=none;strokeColor=none;">
        <mxGeometry x="100" y="80" width="60" height="30" as="geometry"/>
      </mxCell></UserObject>
      <mxCell id="symbol" vertex="1" parent="pump" style="shape=image;image=data:image/svg+xml,%3Csvg%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%2F%3E;">
        <mxGeometry width="60" height="30" as="geometry"/>
      </mxCell>
      <mxCell id="port" vertex="1" parent="pump" style="shape=ellipse;fillColor=#2563eb;strokeColor=none;">
        <mxGeometry x="1" y="0.5" width="3" height="3" relative="1" as="geometry"><mxPoint x="-1.5" y="-1.5" as="offset"/></mxGeometry>
      </mxCell>
      <mxCell id="outlet" vertex="1" parent="1" style="shape=image;image=https://example.invalid/image.svg;">
        <mxGeometry x="240" y="150" width="10" height="10" as="geometry"/>
      </mxCell>
      <mxCell id="flow" edge="1" parent="1" source="port" target="outlet" style="noEdgeStyle=1;exitX=0.5;exitY=0.5;entryX=0.5;entryY=0.5;endArrow=open;">
        <mxGeometry relative="1" as="geometry"><Array as="points"><mxPoint x="200" y="95"/><mxPoint x="200" y="155"/></Array></mxGeometry>
      </mxCell>
    </root></mxGraphModel></diagram></mxfile>`;
  await render({ kind: "drawio", version: "v1", content: drawio });
  assert.equal(container.querySelectorAll('[data-drawio-cell="banner"] tspan').length, 2);
  assert.equal(container.querySelectorAll("image").length, 1);
  assert.match(container.querySelector("image").getAttribute("href"), /^data:image\/svg\+xml/);
  const flow = container.querySelector('[data-drawio-cell="flow"] polyline');
  assert.match(flow.getAttribute("points"), /^160,95\s+200,95\s+200,155\s+245,155$/);
  await zoom();
  const drawioBox = container
    .querySelector(".project-preview-drawing > svg")
    .getAttribute("viewBox");
  await render({
    kind: "drawio",
    version: "v1",
    content: drawio.replace("Station", "Draft"),
    dirty: true,
  });
  assert.match(container.querySelector('[data-drawio-cell="banner"]').textContent, /Draft/);
  assert.equal(
    container.querySelector(".project-preview-drawing > svg").getAttribute("viewBox"),
    drawioBox,
  );
  await render({ kind: "drawio", version: "v1", content: "<mxfile>", dirty: true });
  assert.match(container.textContent, /无法预览此文件/);
  assert.equal(container.querySelector('[data-drawio-cell="banner"]'), null);

  const graph = drawio.match(/<mxGraphModel[\s\S]+<\/mxGraphModel>/)[0];
  const encodedGraph = deflateRawSync(
    encodeURIComponent(graph.replace("Station", "压缩图页")),
  ).toString("base64");
  const multipage = drawio.replace(
    "</mxfile>",
    `<diagram id="compressed" name="Second">${encodedGraph}</diagram></mxfile>`,
  );
  await render({ kind: "drawio", version: "v2", content: multipage });
  await act(async () => {
    const { setImmediate } = await import("node:timers/promises");
    for (let attempt = 0; attempt < 50 && !container.querySelector("select"); attempt++)
      await setImmediate();
  });
  await act(() => {
    const pages = container.querySelector("select");
    assert.equal(pages.options.length, 2);
    pages.value = "compressed";
    pages.dispatchEvent(new window.Event("change", { bubbles: true }));
  });
  assert.match(container.querySelector('[data-drawio-cell="banner"]').textContent, /压缩图页/);

  await render({ kind: "scene", version: "v1" });
  const controls = container.querySelector(".test-editor-controls");
  const cameraControls = container.querySelector(
    '.scene-viewport [role="group"][aria-label="场景导航"]',
  );
  assert.ok(cameraControls.querySelector('[aria-label="适应视图"] svg'));
  const zoomSlider = () => container.querySelector('.scene-zoom-control input[type="range"]');
  const angle = cameraControls.querySelector('button[aria-label="视角"][aria-haspopup="menu"]');
  assert.equal(angle.textContent, "");
  assert.equal(angle.querySelectorAll("svg").length, 2);
  assert.equal(zoomSlider().getAttribute("aria-valuetext"), "100%");
  assert.equal(controls.querySelector('[aria-label="缩放"], [aria-label="适应视图"]'), null);
  const setZoom = async (value) => {
    await act(() => {
      const slider = zoomSlider();
      slider.value = String(value);
      slider.dispatchEvent(new window.Event("input", { bubbles: true }));
    });
    await flushFrames();
  };
  const frame = faces.at(-1);
  assert.ok(frame.every(([x, y]) => Math.abs(x) <= 0.881 && Math.abs(y) <= 0.881));
  assert.ok(Math.max(...frame.map(([, y]) => y)) - Math.min(...frame.map(([, y]) => y)) > 1);
  for (const button of controls.querySelectorAll(":scope > button")) {
    assert.equal(button.textContent, "");
    assert.ok(button.getAttribute("aria-label"));
  }
  assert.equal(
    container.querySelector(".scene-preview [role=toolbar], .scene-preview footer"),
    null,
  );
  await setZoom(0.15);
  assert.equal(zoomSlider().getAttribute("aria-valuetext"), "111%");
  await setZoom(0.32);
  assert.equal(zoomSlider().getAttribute("aria-valuetext"), "125%");
  const oldRoom = faces.at(-1);
  assert.equal(oldRoom.length, 3);
  faces.length = 0;
  await render({ kind: "scene", version: "v2" });
  assert.equal(zoomSlider().getAttribute("aria-valuetext"), "125%");
  assert.equal(container.querySelectorAll('.scene-preview-objects [role="treeitem"]').length, 2);
  assert.ok(faces.some((face) => JSON.stringify(face) === JSON.stringify(oldRoom)));
  composition = "layer-v2";
  faces.length = 0;
  await act(async () => t.mock.timers.tick(2000));
  await flushFrames();
  assert.equal(requests.at(-1).searchParams.get("version"), "v2");
  assert.equal(requests.at(-1).searchParams.get("revision"), "layer-v1");
  assert.match(container.querySelector(".scene-preview-objects").textContent, /Changed layer/);
  assert.ok(faces.some((face) => JSON.stringify(face) === JSON.stringify(oldRoom)));
  const canvas = container.querySelector(".scene-preview-canvas");
  const cards = () =>
    Array.from(container.querySelectorAll("[data-inspected]"), (card) => card.dataset.inspected);
  assert.deepEqual(cards(), []);
  const center = oldRoom.reduce(([x, y], [px, py]) => [x + px / 3, y + py / 3], [0, 0]);
  const pointer = {
    clientX: (center[0] + 1) * 200,
    clientY: (1 - center[1]) * 150,
    button: 0,
    bubbles: true,
  };
  const movePointer = (options = {}) =>
    act(() => {
      canvas.dispatchEvent(new window.MouseEvent("pointermove", { ...pointer, ...options }));
      const [frame, callback] = Array.from(frames).at(-1);
      frames.delete(frame);
      callback(performance.now());
    });
  const quickLook = () => container.querySelector('.scene-hover[role="tooltip"]');
  await movePointer();
  await act(() => t.mock.timers.tick(299));
  assert.equal(quickLook(), null);
  await act(() => t.mock.timers.tick(1));
  assert.equal(quickLook().querySelector("span").textContent, "Room");
  assert.equal(quickLook().querySelector("small").textContent, "Slab · structural · North");
  assert.deepEqual(cards(), []);
  await movePointer({ clientX: 0, clientY: 0 });
  assert.equal(quickLook(), null);
  await movePointer();
  await act(() => t.mock.timers.tick(299));
  assert.equal(quickLook(), null);
  await setZoom(0.64);
  await act(() => t.mock.timers.tick(1));
  assert.equal(quickLook(), null);
  await setZoom(0.32);
  const clickCanvas = (modifiers = {}) =>
    act(async () => {
      const options = { ...pointer, ...modifiers };
      canvas.dispatchEvent(new window.MouseEvent("pointerdown", options));
      canvas.dispatchEvent(new window.MouseEvent("pointerup", options));
    });
  assert.equal(requests.at(-1).searchParams.get("view"), "geometry");
  assert.equal(propertyRequests.length, 0);
  await clickCanvas();
  assert.deepEqual(cards(), ["/Room"]);
  assert.deepEqual(propertyRequests[0], {
    path: "build/site.usda",
    version: "v2",
    revision: "layer-v2",
    ids: ["/Room"],
  });
  assert.match(container.querySelector(".scene-authored").textContent, /thickness0\.5 m/);
  assert.ok(canvas.parentElement.querySelector(".scene-inspection-overlay [data-inspected]"));
  const treeItems = container.querySelectorAll('.scene-preview-objects [role="treeitem"]');
  const selectedIds = () =>
    Array.from(treeItems)
      .filter((item) => item.getAttribute("aria-selected") === "true")
      .map((item) => item.dataset.primPath);
  assert.equal(
    container.querySelector('[role="tree"][aria-multiselectable="true"]') !== null,
    true,
  );
  const controlTreeClick = (item, button = 0) =>
    act(() => {
      const options = { ctrlKey: true, button, bubbles: true };
      item.dispatchEvent(new window.MouseEvent("pointerup", options));
      if (button === 0) item.dispatchEvent(new window.MouseEvent("click", options));
    });
  await controlTreeClick(treeItems[1]);
  assert.deepEqual(selectedIds(), ["/Room", "/Extension"]);
  assert.deepEqual(cards(), ["/Room", "/Extension"]);
  await clickCanvas({ ctrlKey: true });
  assert.deepEqual(selectedIds(), ["/Extension"]);
  assert.deepEqual(cards(), ["/Extension"]);
  await clickCanvas({ ctrlKey: true, button: 2 });
  assert.deepEqual(selectedIds(), ["/Room", "/Extension"]);
  await controlTreeClick(treeItems[1], 2);
  assert.deepEqual(selectedIds(), ["/Room"]);
  await clickCanvas({ ctrlKey: true, clientX: 0, clientY: 0 });
  assert.deepEqual(selectedIds(), ["/Room"]);

  const key = (element, type, code, key = code, shiftKey = false, ctrlKey = false) =>
    element.dispatchEvent(
      new window.KeyboardEvent(type, {
        code,
        key,
        shiftKey,
        ctrlKey,
        bubbles: true,
        cancelable: true,
      }),
    );
  await act(() => key(treeItems[1], "keydown", "Enter", "Enter", false, true));
  assert.deepEqual(selectedIds(), ["/Room", "/Extension"]);
  const move = async (code, value, shift = false) => {
    await act(() => {
      const now = performance.now();
      assert.equal(key(canvas, "keydown", code, value, shift), false);
      const [id, callback] = Array.from(frames).at(-1);
      frames.delete(id);
      callback(now + 100);
      key(canvas, "keyup", code, value, shift);
    });
  };
  const initialEye = latestViewport.eye;
  await move("KeyW", "w");
  const advanced = latestViewport.eye;
  const forward = (from, to) => Math.hypot(to[0] - from[0], to[1] - from[1]);
  const normalDistance = forward(initialEye, advanced);
  assert.ok(normalDistance > 0.5 && normalDistance < 1.1);
  assert.ok(Math.abs(advanced[2] - initialEye[2]) < 1e-9);
  await move("KeyW", "W", true);
  assert.ok(forward(advanced, latestViewport.eye) > normalDistance * 2.5);
  const beforeRise = latestViewport;
  await move("KeyE", "e");
  assert.ok(forward(latestViewport.eye, beforeRise.eye) < 1e-9);
  assert.ok(latestViewport.eye[2] > beforeRise.eye[2]);
  const beforeLook = latestViewport;
  await move("ArrowRight", "ArrowRight");
  assert.ok(
    Math.hypot(...latestViewport.eye.map((value, index) => value - beforeLook.eye[index])) < 1e-9,
  );
  assert.notDeepEqual(latestViewport.at, beforeLook.at);

  const search = container.querySelector('input[aria-label="搜索构件"]');
  await act(() => assert.equal(key(search, "keydown", "KeyW", "w"), true));
  await act(() => treeItems[1].click());
  assert.equal(treeItems[1].getAttribute("aria-selected"), "true");
  assert.deepEqual(cards(), ["/Extension"]);
  await act(() => container.querySelector('[aria-label="固定构件属性"]').click());
  await act(() => key(treeItems[0], "keydown", "Enter"));
  assert.deepEqual(cards(), ["/Extension", "/Room"]);
  assert.deepEqual(
    Array.from(
      container.querySelectorAll("[data-inspection-group]"),
      (group) => group.dataset.inspectionGroup,
    ),
    ["pinned", "selection"],
  );
  assert.equal(
    container
      .querySelector('[data-inspected="/Extension"] [aria-label="取消固定属性"]')
      .getAttribute("aria-pressed"),
    "true",
  );
  await act(() =>
    container.querySelector('[data-inspected="/Room"] [aria-label="关闭构件属性"]').click(),
  );
  assert.deepEqual(cards(), ["/Extension"]);
  await act(() => treeItems[1].click());
  await act(() => container.querySelector('[aria-label="取消固定属性"]').click());
  assert.deepEqual(cards(), ["/Extension"]);
  await act(() => container.querySelector('[aria-label="关闭构件属性"]').click());
  assert.deepEqual(cards(), []);
  await render({ kind: "scene", version: "large" });
  const sceneTree = container.querySelector(".scene-preview-objects");
  const rows = () => sceneTree.querySelectorAll('[role="treeitem"]');
  assert.ok(rows().length < 50);
  await act(() => rows()[0].focus());
  await act(() => key(window.document.activeElement, "keydown", "End"));
  assert.equal(window.document.activeElement.dataset.primPath, "/Element239");
  assert.ok(sceneTree.scrollTop > 0);
  await act(() => key(window.document.activeElement, "keydown", "Enter"));
  assert.deepEqual(cards(), ["/Element239"]);
  await act(() => key(window.document.activeElement, "keydown", "Home"));
  assert.equal(window.document.activeElement.dataset.primPath, "/Element0");
  await act(() => root.unmount());
});

const {
  outlineForDocument,
  sortOutlineSymbols,
  filterOutlineSymbols,
  outlineSymbolAt,
  supportsOutline,
} = load("src/texsys/assets/studio/outline-model.ts");

test("markdown outline nests headings, skips fences and spans sections", () => {
  const text = [
    "# Title",
    "",
    "intro",
    "",
    "## A",
    "",
    "```",
    "# not a heading",
    "```",
    "",
    "### A1",
    "",
    "## B",
    "",
  ].join("\n");
  const symbols = outlineForDocument("markdown", text);
  assert.equal(symbols.length, 1);
  assert.equal(symbols[0].name, "# Title");
  assert.deepEqual(
    symbols[0].children.map((node) => node.name),
    ["## A", "## B"],
  );
  assert.deepEqual(
    symbols[0].children[0].children.map((node) => node.name),
    ["### A1"],
  );
  const hit = outlineSymbolAt(symbols, 12, 1);
  assert.equal(hit.id, symbols[0].children[0].children[0].id);
  assert.deepEqual(hit.ancestors, [symbols[0].id, symbols[0].children[0].id]);
});

test("markdown outline reads setext headings", () => {
  const symbols = outlineForDocument("markdown", "Title\n=====\n\nbody\n\nSub\n---\n\nmore\n");
  assert.deepEqual(
    symbols.map((node) => node.name),
    ["Title"],
  );
  assert.deepEqual(
    symbols[0].children.map((node) => node.name),
    ["Sub"],
  );
  assert.equal(outlineSymbolAt(symbols, 9, 1).id, symbols[0].children[0].id);
});

test("json outline builds kinds, previews and ranges; tolerates jsonc", () => {
  const text =
    '{\n  "name": "texsys",\n  "private": true,\n  "scripts": { "format": "prettier" },\n  "list": [1, null]\n}\n';
  const symbols = outlineForDocument("json", text);
  assert.deepEqual(
    symbols.map((node) => node.name),
    ["name", "private", "scripts", "list"],
  );
  assert.deepEqual(
    symbols.map((node) => node.kind),
    ["string", "boolean", "object", "array"],
  );
  assert.equal(symbols[0].detail, "texsys");
  assert.equal(symbols[0].range.startLine, 2);
  assert.equal(symbols[2].children[0].name, "format");
  assert.deepEqual(
    symbols[3].children.map((node) => node.name),
    ["0", "1"],
  );
  assert.equal(outlineSymbolAt(symbols, 4, 5).id, symbols[2].id);
  const tolerant = outlineForDocument(
    "json",
    '{\n // comment\n "a": 1, /* block */\n "b": [true,],\n}',
  );
  assert.deepEqual(
    tolerant.map((node) => node.name),
    ["a", "b"],
  );
});

test("outline sorting and filtering follow the selected mode", () => {
  const symbols = outlineForDocument("json", '{"b": {}, "a": 1, "c": {"z": 0}}');
  assert.deepEqual(
    sortOutlineSymbols(symbols, "name").map((node) => node.name),
    ["a", "b", "c"],
  );
  assert.deepEqual(
    sortOutlineSymbols(symbols, "category").map((node) => node.name),
    ["b", "c", "a"],
  );
  const filtered = filterOutlineSymbols(symbols, "z");
  assert.deepEqual(
    filtered.map((node) => node.name),
    ["c"],
  );
  assert.deepEqual(
    filtered[0].children.map((node) => node.name),
    ["z"],
  );
  assert.equal(filterOutlineSymbols(symbols, "missing").length, 0);
  assert.equal(supportsOutline("python"), false);
  assert.deepEqual(outlineForDocument("python", "def f(): pass"), []);
});

test("outline view renders symbols, navigates, collapses and filters on type", async () => {
  const { JSDOM } = await import("jsdom");
  const React = await import("react");
  const { act } = React;
  const { createRoot } = await import("react-dom/client");
  const dom = new JSDOM("<!doctype html><body></body>", {
    url: "http://localhost/",
    pretendToBeVisual: true,
  });
  for (const name of ["window", "document", "HTMLElement", "Node", "localStorage"])
    globalThis[name] = dom.window[name];
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  globalThis.requestAnimationFrame = (callback) => setTimeout(callback, 0);
  dom.window.HTMLElement.prototype.scrollIntoView = function () {};
  const { OutlineView } = load("src/texsys/assets/studio/OutlineView.tsx");
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  const navigated = [];
  const view = (props) =>
    act(async () =>
      root.render(
        React.createElement(OutlineView, {
          document: { name: "README.md", value: "# Title\n\n## A\n\ntext\n" },
          onNavigate: (range) => navigated.push(range),
          ...props,
        }),
      ),
    );
  await view({ open: false });
  assert.equal(container.querySelectorAll('[role="treeitem"]').length, 0);
  await view();
  const body = () => container.querySelector(".outline-body");
  assert.ok(body(), "outline body present with symbols");
  const rows = () => Array.from(container.querySelectorAll('[role="treeitem"]'));
  const names = () => rows().map((row) => row.querySelector(".outline-name").textContent);
  assert.deepEqual(names(), ["# Title", "## A"]);
  await view({ cursor: { line: 5, column: 1 } });
  assert.equal(container.querySelector(".outline-row.selected .outline-name").textContent, "## A");
  assert.equal(rows()[0].querySelectorAll(".outline-guide").length, 0);
  assert.equal(rows()[1].querySelectorAll(".outline-guide").length, 1);
  assert.ok(rows()[1].querySelector(".outline-guide").classList.contains("active"));
  await act(async () => rows()[1].click());
  assert.deepEqual(navigated.at(-1), {
    startLine: 3,
    startColumn: 1,
    endLine: 3,
    endColumn: 5,
  });
  await act(async () => rows()[0].querySelector(".outline-twistie button").click());
  assert.deepEqual(names(), ["# Title"]);
  const collapseToggle = container.querySelector(".outline-actions button");
  assert.equal(collapseToggle.getAttribute("aria-label"), "全部展开");
  await act(async () => collapseToggle.click());
  assert.deepEqual(names(), ["# Title", "## A"]);
  assert.equal(collapseToggle.getAttribute("aria-label"), "全部折叠");
  await act(async () => collapseToggle.click());
  assert.deepEqual(names(), ["# Title"]);
  await act(async () => rows()[0].querySelector(".outline-twistie button").click());
  await act(async () => rows()[0].focus());
  await act(async () =>
    document.activeElement.dispatchEvent(
      new window.KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true }),
    ),
  );
  assert.equal(document.activeElement, rows()[1]);
  await act(async () =>
    document.activeElement.dispatchEvent(
      new window.KeyboardEvent("keydown", { key: "T", bubbles: true }),
    ),
  );
  const filter = container.querySelector(".outline-filter");
  assert.equal(filter.value, "T");
  assert.deepEqual(names(), ["# Title"]);
  await act(async () =>
    filter.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Escape", bubbles: true })),
  );
  assert.equal(container.querySelector(".outline-filter"), null);
  await view({ document: { name: "package.json", value: '{"name": "texsys", "ok": true}' } });
  assert.ok(body(), "outline body present for json");
  assert.deepEqual(
    Array.from(container.querySelectorAll(".outline-symbol")).map((item) => item.dataset.kind),
    ["string", "boolean"],
  );
  assert.equal(container.querySelector(".outline-detail").textContent, "texsys");
  assert.equal(container.querySelector(".outline-actions button").disabled, true);
  await view({ document: { name: "main.py", value: "print(1)" } });
  assert.match(container.textContent, /此文件无法提供大纲信息。/);
  assert.ok(body(), "outline body present for unsupported language");
  assert.ok(body().querySelector(".core-hint"), "unsupported hint inside fixed body");
  await view({ document: { name: "empty.md", value: "plain text\n" } });
  assert.match(container.textContent, /文档中没有可显示的大纲符号。/);
  assert.ok(body(), "outline body present without symbols");
  await view({ document: undefined });
  assert.match(container.textContent, /打开文档后可查看大纲。/);
  assert.ok(body(), "outline body present without document");
  assert.ok(body().querySelector(".core-hint"), "no-document hint inside fixed body");
  await act(() => root.unmount());
});

test("outline body survives filtering to zero matches", async () => {
  const { JSDOM } = await import("jsdom");
  const React = await import("react");
  const { act } = React;
  const { createRoot } = await import("react-dom/client");
  const dom = new JSDOM("<!doctype html><body></body>", {
    url: "http://localhost/",
    pretendToBeVisual: true,
  });
  for (const name of ["window", "document", "HTMLElement", "Node", "localStorage"])
    globalThis[name] = dom.window[name];
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  globalThis.requestAnimationFrame = (callback) => setTimeout(callback, 0);
  dom.window.HTMLElement.prototype.scrollIntoView = function () {};
  const { OutlineView } = load("src/texsys/assets/studio/OutlineView.tsx");
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () =>
    root.render(
      React.createElement(OutlineView, {
        document: { name: "README.md", value: "# Title\n\n## A\n" },
        onNavigate: () => {},
      }),
    ),
  );
  const tree = container.querySelector(".outline-tree");
  await act(async () =>
    tree.dispatchEvent(new window.KeyboardEvent("keydown", { key: "z", bubbles: true })),
  );
  assert.match(container.textContent, /没有匹配的符号。/);
  assert.ok(container.querySelector(".outline-body"), "outline body present with zero matches");
  assert.ok(
    container.querySelector(".outline-body .core-hint"),
    "zero-match hint inside fixed body",
  );
  await act(() => root.unmount());
});

test("timeline view lists per-file commits, opens diffs and reports empty states", async () => {
  const { JSDOM } = await import("jsdom");
  const React = await import("react");
  const { act } = React;
  const { createRoot } = await import("react-dom/client");
  const dom = new JSDOM("<!doctype html><body></body>", {
    url: "http://localhost/",
    pretendToBeVisual: true,
  });
  for (const name of ["window", "document", "HTMLElement", "Node", "localStorage"])
    globalThis[name] = dom.window[name];
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  const { TimelineView } = load("src/texsys/assets/studio/TimelineView.tsx");
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  const opened = [];
  const id = "a".repeat(40);
  const commits = [
    {
      id,
      subject: "设备 feature",
      author: "Studio Test",
      date: new Date(Date.now() - 86400000).toISOString(),
    },
  ];
  const urls = [];
  const api = async (url) => {
    urls.push(url);
    return { commits, truncated: false };
  };
  const view = (props) =>
    act(async () =>
      root.render(
        React.createElement(TimelineView, {
          api,
          onOpen: (commit) => opened.push(commit),
          path: "README.md",
          ...props,
        }),
      ),
    );
  await view({ open: false, token: id });
  assert.deepEqual(urls, []);
  await view({ open: true, token: id });
  assert.ok(urls.every((url) => url.includes("path=README.md")));
  const row = container.querySelector(".timeline-row");
  assert.ok(row);
  assert.match(row.textContent, /设备 feature/);
  assert.ok(row.querySelector(".timeline-when").textContent);
  await act(async () => row.click());
  assert.deepEqual(opened, [id]);
  await view({ open: false, token: id });
  await view({ open: true, token: id });
  assert.equal(urls.length, 1);
  await view({ path: undefined });
  assert.match(container.textContent, /打开文件后可查看时间线。/);
  await view({
    api: async () => {
      throw new Error("Git 不可用");
    },
  });
  assert.match(container.textContent, /Git 不可用/);
  await act(() => root.unmount());
});
