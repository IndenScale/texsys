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
const { createRoot } = await import("react-dom/client");

// jsdom has no Popover API. Emulate only its open/close state and native light dismissal;
// all menu pointer, click, keyboard, React state, callbacks and focus behavior run unchanged.
const popovers = new Set();
const nativeMatches = dom.window.HTMLElement.prototype.matches;
dom.window.HTMLElement.prototype.matches = function (selector) {
  return selector === ":popover-open" ? popovers.has(this) : nativeMatches.call(this, selector);
};
function togglePopover(element, open) {
  const wasOpen = popovers.has(element);
  if (wasOpen === open) return;
  if (open) popovers.add(element);
  else popovers.delete(element);
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
const filename = path.resolve("src/texsys/assets/studio/NavigatorMenu.tsx");
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
const { NavigatorMenu } = loaded.exports;

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
async function key(element, key) {
  await act(async () =>
    element.dispatchEvent(
      new dom.window.KeyboardEvent("keydown", {
        key,
        bubbles: true,
        cancelable: true,
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
