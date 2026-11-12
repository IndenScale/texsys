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
for (const name of ["window", "document", "HTMLElement", "Node", "localStorage"])
  globalThis[name] = dom.window[name];
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
dom.window.HTMLElement.prototype.scrollIntoView = function () {};
const { createRoot } = await import("react-dom/client");

function load(relative) {
  const filename = path.resolve(relative);
  const result = esbuild.buildSync({
    entryPoints: [filename],
    bundle: true,
    write: false,
    platform: "node",
    format: "cjs",
    packages: "external",
    loader: { ".css": "empty" },
  });
  const loaded = new Module(filename);
  loaded.paths = Module._nodeModulePaths(process.cwd());
  loaded._compile(result.outputFiles[0].text, filename);
  return loaded.exports;
}
const { ExplorerTree } = load("src/texsys/assets/studio/ExplorerTree.tsx");
const { visibleFileTree } = load("src/texsys/assets/studio/workspace-model.ts");
const listing = {
  directories: ["src", "src/nested"],
  files: [{ path: "src/nested/model.json" }, { path: "README.md" }],
  truncated: false,
};

async function fixture(t, withRoot = true) {
  document.body.innerHTML = '<div id="app"></div>';
  const reactRoot = createRoot(document.getElementById("app"));
  const events = [];
  let controls;
  function Harness() {
    const [rootOpen, setRootOpen] = React.useState(true);
    const [expanded, setExpanded] = React.useState(new Set(["src", "src/nested"]));
    const [selected, select] = React.useState("");
    const [creation, setCreation] = React.useState();
    const [focusRequest, setFocusRequest] = React.useState();
    controls = { setRootOpen, setCreation, setFocusRequest, expanded, selected };
    return React.createElement(ExplorerTree, {
      entries: visibleFileTree(listing, expanded),
      root: withRoot
        ? { name: "project-a", expanded: rootOpen, toggle: () => setRootOpen(!rootOpen) }
        : undefined,
      expanded,
      selected,
      select,
      toggle: (target) =>
        setExpanded((previous) => {
          const next = new Set(previous);
          if (next.has(target)) next.delete(target);
          else next.add(target);
          return next;
        }),
      open: (target) => events.push(["open", target]),
      contextMenu: (target) => events.push(["context", target]),
      rename: () => events.push(["rename", selected]),
      remove: () => events.push(["delete", selected]),
      focusParent: () => events.push(["parent"]),
      creation,
      changeCreation: (value) => setCreation((previous) => ({ ...previous, value })),
      cancelCreation: () => setCreation(undefined),
      submitCreation: () => events.push(["create", creation.value]),
      focusRequest,
    });
  }
  await act(async () => reactRoot.render(React.createElement(Harness)));
  t.after(async () => act(async () => reactRoot.unmount()));
  const row = (target) =>
    Array.from(document.querySelectorAll("[data-tree-path]")).find(
      (element) => element.dataset.treePath === target,
    );
  return { row, events, controls: () => controls };
}
async function focus(element) {
  await act(async () => element.focus());
}
async function key(element, value) {
  await act(async () =>
    element.dispatchEvent(new dom.window.KeyboardEvent("keydown", { key: value, bubbles: true })),
  );
}

test("Project root shares one tree with correctly nested levels and one roving tab stop", async (t) => {
  const { row } = await fixture(t);
  assert.equal(document.querySelectorAll('[role="tree"]').length, 1);
  assert.equal(row("").getAttribute("aria-level"), "1");
  assert.equal(row("src").getAttribute("aria-level"), "2");
  assert.equal(row("src/nested").getAttribute("aria-level"), "3");
  assert.equal(row("src/nested/model.json").getAttribute("aria-level"), "4");
  assert.equal(document.querySelectorAll('[role="treeitem"][tabindex="0"]').length, 1);
  await focus(row("src/nested/model.json"));
  assert.equal(document.querySelectorAll('[role="treeitem"][tabindex="0"]').length, 1);
  assert.equal(row("src/nested/model.json").getAttribute("aria-selected"), "true");
  await key(row("src/nested/model.json"), "Home");
  assert.equal(document.activeElement, row(""));
  await key(row(""), "ArrowDown");
  assert.equal(document.activeElement, row("src"));
  await key(row("src"), "ArrowUp");
  assert.equal(document.activeElement, row(""));
});

test("Root collapse preserves descendants and ArrowDown never expands a collapsed root", async (t) => {
  const { row, controls, events } = await fixture(t);
  await focus(row(""));
  await key(row(""), "ArrowLeft");
  assert.equal(document.querySelectorAll('[role="treeitem"]').length, 1);
  assert.deepEqual([...controls().expanded], ["src", "src/nested"]);
  await key(row(""), "ArrowDown");
  assert.equal(row("").getAttribute("aria-expanded"), "false");
  await key(row(""), "ArrowRight");
  assert.ok(row("src/nested/model.json"));
  assert.equal(document.activeElement, row(""));
  await key(row(""), "ArrowRight");
  assert.equal(document.activeElement, row("src"));
  await key(row("src"), "ArrowLeft");
  assert.equal(row("src").getAttribute("aria-expanded"), "false");
  await key(row("src"), "ArrowLeft");
  assert.equal(document.activeElement, row(""));
  await key(row(""), "F2");
  await key(row(""), "Delete");
  assert.deepEqual(events, []);
  await act(async () =>
    row("").dispatchEvent(new dom.window.MouseEvent("contextmenu", { bubbles: true })),
  );
  assert.deepEqual(events, [["context", ""]]);
});

test("Inline draft stays below its parent and survives hiding the project root", async (t) => {
  const { controls, events, row } = await fixture(t);
  await act(async () =>
    controls().setCreation({ action: "create", parent: "src", value: "draft.json", focus: 1 }),
  );
  const input = () => document.querySelector(".core-tree-creation input");
  assert.equal(document.activeElement, input());
  assert.equal(input().value, "draft.json");
  const draft = input().closest(".core-tree-creation");
  assert.equal(draft.previousElementSibling, row("src/nested/model.json"));
  assert.equal(draft.nextElementSibling, row("README.md"));
  await act(async () => controls().setRootOpen(false));
  assert.equal(input(), null);
  await act(async () => controls().setRootOpen(true));
  assert.equal(input().value, "draft.json");
  assert.equal(document.activeElement, input());
  await key(input(), "Enter");
  assert.deepEqual(events, [["create", "draft.json"]]);
  await key(input(), "Escape");
  assert.equal(input(), null);
});

test("Rootless reuse retains its original levels, parent navigation and file actions", async (t) => {
  const { row, events } = await fixture(t, false);
  assert.equal(row(""), undefined);
  assert.equal(row("src").getAttribute("aria-level"), "1");
  assert.equal(row("src/nested/model.json").getAttribute("aria-level"), "3");
  await focus(row("src"));
  await key(row("src"), "ArrowUp");
  await focus(row("README.md"));
  await key(row("README.md"), "F2");
  await key(row("README.md"), "Delete");
  assert.deepEqual(events, [["parent"], ["rename", "README.md"], ["delete", "README.md"]]);
});
