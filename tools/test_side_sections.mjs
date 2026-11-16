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
    loader: { ".css": "empty" },
  });
  const module = new Module(filename);
  module.paths = Module._nodeModulePaths(process.cwd());
  module._compile(result.outputFiles[0].text, filename);
  return module.exports;
}

async function setup() {
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
  dom.window.Element.prototype.setPointerCapture = function () {};
  dom.window.Element.prototype.releasePointerCapture = function () {};
  const wrapper = document.createElement("div");
  wrapper.className = "welcome-explorer";
  const heading = document.createElement("div");
  heading.className = "core-workspace-heading";
  wrapper.appendChild(heading);
  document.body.appendChild(wrapper);
  wrapper.getBoundingClientRect = () => ({ height: 700, top: 0 });
  heading.getBoundingClientRect = () => ({ height: 28, top: 0 });
  return { React, act, createRoot, wrapper };
}

function mount(ctx, props) {
  const { ExplorerSideSections } = load("src/texsys/assets/studio/ExplorerSideSections.tsx");
  const container = document.createElement("div");
  ctx.wrapper.appendChild(container);
  const root = ctx.createRoot(container);
  const commits = [];
  const view = (next) =>
    ctx.act(async () =>
      root.render(
        ctx.React.createElement(ExplorerSideSections, {
          showOutline: true,
          showTimeline: true,
          foldersVisible: true,
          onNavigate: () => {},
          initialHeights: { outline: 300, timeline: 150 },
          onCommit: (heights) => commits.push(heights),
          ...next,
          ...props,
        }),
      ),
    );
  return { container, root, commits, view };
}

const pointer = (ctx, el, type, y) =>
  ctx.act(async () =>
    el.dispatchEvent(
      new window.MouseEvent(type, { bubbles: true, cancelable: true, button: 0, clientY: y }),
    ),
  );
const key = (ctx, el, k, shiftKey = false) =>
  ctx.act(async () =>
    el.dispatchEvent(new window.KeyboardEvent("keydown", { key: k, shiftKey, bubbles: true })),
  );
const bodyHeight = (container, selector) => container.querySelector(selector).style.height;

const openSections = (ctx, container) =>
  ctx.act(async () => {
    for (const section of container.querySelectorAll("details")) {
      section.open = true;
      section.dispatchEvent(new window.Event("toggle"));
    }
  });

test("sash drag resizes the section below live and commits on release", async () => {
  const ctx = await setup();
  const { container, root, commits, view } = mount(ctx);
  await view();
  await openSections(ctx, container);
  const [first, transfer] = container.querySelectorAll(".side-sash");
  assert.ok(first && transfer, "two sashes when both sections visible");
  assert.equal(first.getAttribute("role"), "separator");
  assert.equal(first.getAttribute("aria-valuenow"), "300");
  assert.equal(bodyHeight(container, ".outline-body"), "300px");
  await pointer(ctx, first, "pointerdown", 200);
  assert.ok(document.body.classList.contains("side-sash-dragging"));
  assert.ok(first.classList.contains("dragging"));
  await pointer(ctx, first, "pointermove", 260);
  assert.equal(bodyHeight(container, ".outline-body"), "240px");
  assert.equal(bodyHeight(container, ".timeline-body"), "150px");
  await pointer(ctx, first, "pointerup", 260);
  assert.ok(!document.body.classList.contains("side-sash-dragging"));
  assert.deepEqual(commits.at(-1), { outline: 240, timeline: 150 });
  await ctx.act(() => root.unmount());
});

test("first sash clamps at body minimum and keeps tree space", async () => {
  const ctx = await setup();
  const { container, root, view } = mount(ctx);
  await view();
  await openSections(ctx, container);
  const first = container.querySelector(".side-sash");
  await pointer(ctx, first, "pointerdown", 200);
  await pointer(ctx, first, "pointermove", -2000);
  // 700 - 28 heading - (28+300 outline + 29+150 timeline) = 165 tree space; keep 60.
  assert.equal(bodyHeight(container, ".outline-body"), "405px");
  await pointer(ctx, first, "pointerup", -2000);
  await pointer(ctx, first, "pointerdown", 200);
  await pointer(ctx, first, "pointermove", 4000);
  assert.equal(bodyHeight(container, ".outline-body"), "44px");
  await pointer(ctx, first, "pointerup", 4000);
  await ctx.act(() => root.unmount());
});

test("transfer sash moves height between outline and timeline with clamps", async () => {
  const ctx = await setup();
  const { container, root, commits, view } = mount(ctx);
  await view();
  await openSections(ctx, container);
  const transfer = container.querySelectorAll(".side-sash")[1];
  await pointer(ctx, transfer, "pointerdown", 300);
  await pointer(ctx, transfer, "pointermove", 340);
  assert.equal(bodyHeight(container, ".outline-body"), "340px");
  assert.equal(bodyHeight(container, ".timeline-body"), "110px");
  await pointer(ctx, transfer, "pointermove", 4000);
  assert.equal(bodyHeight(container, ".timeline-body"), "44px");
  assert.equal(bodyHeight(container, ".outline-body"), "406px");
  await pointer(ctx, transfer, "pointerup", 4000);
  assert.deepEqual(commits.at(-1), { outline: 406, timeline: 44 });
  await ctx.act(() => root.unmount());
});

test("sash keyboard adjusts and commits, double-click resets", async () => {
  const ctx = await setup();
  const { container, root, commits, view } = mount(ctx);
  await view();
  await openSections(ctx, container);
  const first = container.querySelector(".side-sash");
  await key(ctx, first, "ArrowDown");
  assert.equal(bodyHeight(container, ".outline-body"), "292px");
  await key(ctx, first, "ArrowUp", true);
  assert.equal(bodyHeight(container, ".outline-body"), "324px");
  assert.deepEqual(commits.at(-1), { outline: 324, timeline: 150 });
  await ctx.act(async () =>
    first.dispatchEvent(new window.MouseEvent("dblclick", { bubbles: true })),
  );
  assert.equal(bodyHeight(container, ".outline-body"), "307px");
  assert.equal(bodyHeight(container, ".timeline-body"), "150px");
  await ctx.act(() => root.unmount());
});

test("a collapsed section follows the first pixel of movement and folds at the starting point", async () => {
  const ctx = await setup();
  const { container, root, commits, view } = mount(ctx);
  await view();
  const outline = container.querySelector(".welcome-outline-section");
  const timeline = container.querySelector(".welcome-timeline-section");
  const first = container.querySelector(".side-sash");
  assert.equal(outline.open, false);
  assert.equal(timeline.open, false);
  assert.equal(first.getAttribute("aria-valuenow"), "0");
  await pointer(ctx, first, "pointerdown", 200);
  assert.equal(outline.open, false, "pressing the handle does not unfold the pane");
  await pointer(ctx, first, "pointermove", 210);
  assert.equal(outline.open, false, "dragging towards the collapsed body cannot shrink it");
  await pointer(ctx, first, "pointerup", 210);
  assert.deepEqual(commits, []);
  await pointer(ctx, first, "pointerdown", 200);
  await pointer(ctx, first, "pointermove", 198);
  assert.equal(outline.open, true);
  assert.equal(timeline.open, false);
  assert.equal(bodyHeight(container, ".outline-body"), "2px");
  await pointer(ctx, first, "pointermove", 200);
  assert.equal(outline.open, false);
  assert.equal(bodyHeight(container, ".outline-body"), "300px", "remembered height is restored");
  await pointer(ctx, first, "pointermove", 160);
  await pointer(ctx, first, "pointerup", 160);
  assert.deepEqual(commits, [{ outline: 40, timeline: 150 }]);
  await view({ activeDocument: { name: "README.md", value: "# T\n\n## A\n" } });
  assert.equal(bodyHeight(container, ".outline-body"), "40px");
  await view({ activeDocument: { name: "main.py", value: "print(1)" } });
  assert.equal(bodyHeight(container, ".outline-body"), "40px");
  assert.equal(bodyHeight(container, ".timeline-body"), "150px");
  await ctx.act(() => root.unmount());
});

test("single visible section gets one sash; hidden sections render nothing", async () => {
  const ctx = await setup();
  const { container, root, view } = mount(ctx);
  await view({ showOutline: false });
  await openSections(ctx, container);
  assert.equal(container.querySelectorAll(".side-sash").length, 1);
  const first = container.querySelector(".side-sash");
  await pointer(ctx, first, "pointerdown", 100);
  await pointer(ctx, first, "pointermove", 160);
  assert.equal(bodyHeight(container, ".timeline-body"), "90px");
  await pointer(ctx, first, "pointerup", 160);
  await view({ showOutline: false, showTimeline: false });
  assert.equal(container.querySelector(".welcome-side-sections"), null);
  await ctx.act(() => root.unmount());
});

test("timeline render slot receives controlled open and live body height", async () => {
  const ctx = await setup();
  let controls;
  const { container, root, view } = mount(ctx, {
    renderTimeline: (c) => {
      controls = c;
      return ctx.React.createElement("div", {
        className: "slot-timeline",
        "data-open": c.open,
        style: { height: c.bodyHeight },
      });
    },
  });
  await view();
  const slot = () => container.querySelector(".slot-timeline");
  assert.equal(slot().style.height, "150px");
  assert.equal(slot().dataset.open, "false");
  const transfer = container.querySelectorAll(".side-sash")[1];
  await pointer(ctx, transfer, "pointerdown", 300);
  assert.equal(slot().dataset.open, "false");
  await pointer(ctx, transfer, "pointermove", 270);
  assert.equal(slot().dataset.open, "true");
  assert.equal(slot().style.height, "30px");
  assert.equal(container.querySelector(".welcome-outline-section").open, false);
  await pointer(ctx, transfer, "pointerup", 270);
  assert.equal(controls.bodyHeight, 30);
  await ctx.act(() => root.unmount());
});

test("release deactivates the sash immediately; lost capture finishes the drag", async () => {
  const ctx = await setup();
  const { container, root, commits, view } = mount(ctx);
  await view();
  await openSections(ctx, container);
  const first = container.querySelector(".side-sash");
  await pointer(ctx, first, "pointerdown", 200);
  await pointer(ctx, first, "pointermove", 240);
  await pointer(ctx, first, "pointerup", 240);
  assert.notEqual(document.activeElement, first, "pointer release blurs the sash");
  assert.ok(!first.classList.contains("dragging"));
  assert.ok(!document.body.classList.contains("side-sash-dragging"));
  assert.deepEqual(commits.at(-1), { outline: 260, timeline: 150 });
  const transfer = container.querySelectorAll(".side-sash")[1];
  await pointer(ctx, transfer, "pointerdown", 300);
  await pointer(ctx, transfer, "pointermove", 320);
  await ctx.act(async () =>
    transfer.dispatchEvent(new window.Event("lostpointercapture", { bubbles: true })),
  );
  assert.ok(!transfer.classList.contains("dragging"), "lost capture clears dragging");
  assert.ok(!document.body.classList.contains("side-sash-dragging"));
  assert.deepEqual(commits.at(-1), { outline: 280, timeline: 130 });
  await ctx.act(() => root.unmount());
});
