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
const { panelSizeFromContainerPointer } = load(
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
test("panel edge snap has hysteresis and stays stable near either threshold", () => {
  assert.deepEqual(snapPanelDrag(47, false, 208), { collapsed: true, value: 0 });
  assert.deepEqual(snapPanelDrag(60, true, 208), { collapsed: true, value: 0 });
  assert.deepEqual(snapPanelDrag(73, true, 208), { collapsed: false, value: 73 });
  assert.deepEqual(snapPanelDrag(60, false, 208), { collapsed: false, value: 60 });
  assert.deepEqual(snapPanelDrag(250, false, 208), { collapsed: false, value: 250 });
});
test("imported resizing respects panel edges, clamp and dismissal intent", () => {
  assert.equal(panelSizeFromContainerPointer(330, 100, 900, true, 208, 500), 230);
  assert.equal(panelSizeFromContainerPointer(600, 100, 900, false, 280, 500), 300);
  assert.equal(panelSizeFromContainerPointer(120, 100, 900, true, 208, 500), 208);
  assert.equal(panelSizeFromContainerPointer(120, 100, 900, false, 280, 500), 500);
  assert.equal(hasResizeIntent(100, 101), false);
  assert.equal(hasResizeIntent(100, 120), true);
});

const { visitDocument, stepDocument } = load("src/texsys/assets/studio/navigation.ts");
const { studioMenus, filterCommands } = load("src/texsys/assets/studio/menus.ts");
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
    filterCommands(commands, "> 视图 面板").map((item) => item.id),
    ["view.sidebar", "view.bottom", "view.inspector"],
  );
  assert.deepEqual(
    filterCommands(commands, "任务 日志").map((item) => item.id),
    ["tasks.show"],
  );
  assert.equal(filterCommands(commands, "不存在的命令").length, 0);
  assert.ok(filterCommands(commands, "").every((item) => !item.id.startsWith("view.")));
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

const { filterFiles } = load("src/texsys/assets/studio/search.ts");
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
