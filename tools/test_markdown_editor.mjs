import test from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";

const dom = new JSDOM("<!doctype html><html><body></body></html>", {
  pretendToBeVisual: true,
  url: "http://localhost/",
});
for (const name of [
  "window",
  "document",
  "navigator",
  "Node",
  "Element",
  "HTMLElement",
  "MutationObserver",
  "CustomEvent",
  "Event",
  "EventTarget",
  "DOMParser",
  "getComputedStyle",
  "requestAnimationFrame",
  "cancelAnimationFrame",
])
  Object.defineProperty(globalThis, name, { value: dom.window[name], configurable: true });
for (const name of ["addEventListener", "removeEventListener", "dispatchEvent"])
  globalThis[name] = dom.window[name].bind(dom.window);
test.after(() => dom.window.close());
const { createMarkdownEditor } = await import("../src/texsys/assets/studio/markdown-editor.ts");
const { editorViewCtx } = await import("@milkdown/kit/core");
const { undo } = await import("@milkdown/kit/prose/history");

async function create(value, onChange = () => {}) {
  const root = document.createElement("div");
  document.body.append(root);
  const controller = await createMarkdownEditor({
    root,
    value,
    label: "Markdown 编辑器",
    onChange,
  });
  return { controller, root, view: controller.editor.action((ctx) => ctx.get(editorViewCtx)) };
}

test("visual Markdown opens editable headings, tables and raw HTML without changing the source", async () => {
  const changes = [];
  const source =
    "# 标题\n\n| 型号 | 数量 |\n| --- | --- |\n| Pump | 2 |\n\n<script>throw 1</script>\n";
  const { controller, root } = await create(source, (value) => changes.push(value));
  try {
    assert.equal(root.querySelector("h1").textContent, "标题");
    assert.equal(root.querySelectorAll("table td").length, 2);
    assert.equal(root.querySelector('[role="textbox"]').getAttribute("contenteditable"), "true");
    assert.equal(root.querySelector("script"), null);
    assert.match(root.textContent, /<script>throw 1<\/script>/);
    assert.deepEqual(changes, []);
  } finally {
    await controller.destroy();
    root.remove();
  }
});

test("visual edits publish synchronously, mirror into a second view and preserve native undo", async () => {
  const changes = [];
  const left = await create("# 标题\n");
  const right = await create("# 标题\n", (value) => {
    changes.push(value);
    left.controller.setValue(value);
  });
  try {
    right.view.dispatch(right.view.state.tr.insertText("新的", 1));
    assert.match(changes.at(-1), /^# 新的标题/);
    assert.equal(left.root.querySelector("h1").textContent, "新的标题");
    assert.equal(undo(right.view.state, right.view.dispatch), true);
    assert.equal(right.root.querySelector("h1").textContent, "标题");
    assert.equal(left.root.querySelector("h1").textContent, "标题");
  } finally {
    await left.controller.destroy();
    await right.controller.destroy();
    left.root.remove();
    right.root.remove();
  }
});

test("external source updates do not echo edits and rapid destruction is idempotent", async () => {
  const changes = [];
  const { controller, root, view } = await create("before\n", (value) => changes.push(value));
  try {
    controller.setValue("## after\n\n- first\n- second\n");
    assert.equal(root.querySelector("h2").textContent, "after");
    assert.equal(root.querySelectorAll("li").length, 2);
    assert.deepEqual(changes, []);
    view.dispatch(view.state.tr.insertText("!", 2));
    assert.equal(changes.length, 1);
    assert.match(changes[0], /a!fter/);
  } finally {
    await controller.destroy();
    await controller.destroy();
    root.remove();
  }
});
