import test from "node:test";
import assert from "node:assert/strict";
import Module from "node:module";
import path from "node:path";
import esbuild from "esbuild-wasm";

const filename = path.resolve("src/texsys/assets/studio/editor-groups.ts");
const result = esbuild.buildSync({
  entryPoints: [filename],
  bundle: true,
  write: false,
  platform: "node",
  format: "cjs",
});
const loaded = new Module(filename);
loaded._compile(result.outputFiles[0].text, filename);
const {
  emptyEditorGroup,
  openEditorView,
  updateEditorView,
  closeEditorView,
  removeEditorDocument,
  renameEditorDocument,
  editorViewCount,
  resolvedEditorPresentation,
  initializeEditorView,
  closeEditorsInOrder,
} = loaded.exports;

function splitDocument() {
  let groups = [emptyEditorGroup()];
  groups = openEditorView(groups, "primary", "README.md", { presentation: "source" });
  return openEditorView(groups, "secondary", "README.md", { presentation: "preview" });
}

test("same document keeps independent opening modes and navigation in both groups", () => {
  let groups = splitDocument();
  groups = openEditorView(groups, "secondary", "notes.md");
  groups = openEditorView(groups, "secondary", "README.md");
  assert.equal(groups[0].views[0].presentation, "source");
  assert.equal(groups[1].views[0].presentation, "preview");
  groups = updateEditorView(groups, "secondary", "README.md", {
    cursorLine: 20,
    presentation: "source",
  });
  assert.equal(groups[0].views[0].cursorLine, undefined);
  assert.equal(groups[1].views[0].cursorLine, 20);
  assert.equal(editorViewCount(groups, "README.md"), 2);
});

test("a file preview keeps a different viewport in each group when switching tabs", () => {
  const path = "build/floor.svg";
  const room = { kind: "drawing", x: 50, y: 75, scale: 2 };
  const level = { kind: "drawing", x: 125, y: 200, scale: 0.6 };
  let groups = openEditorView([emptyEditorGroup()], "primary", path, { viewport: room });
  groups = openEditorView(groups, "secondary", path, { viewport: level });
  groups = openEditorView(groups, "primary", "texsys.toml");
  groups = openEditorView(groups, "primary", path);
  assert.deepEqual(groups[0].views.find((view) => view.path === path).viewport, room);
  assert.deepEqual(groups[1].views.find((view) => view.path === path).viewport, level);
});

test("closing one shared file view keeps the document reference and other group tab", () => {
  let groups = splitDocument();
  groups = closeEditorView(groups, "secondary", "README.md");
  assert.equal(editorViewCount(groups, "README.md"), 1);
  assert.equal(groups.length, 1);
  assert.equal(groups[0].activePath, "README.md");
  groups = closeEditorView(groups, "primary", "README.md");
  assert.equal(editorViewCount(groups, "README.md"), 0);
  assert.equal(groups[0].activePath, null);
});

test("closing a tab selects the adjacent tab only in its own group", () => {
  let groups = splitDocument();
  groups = openEditorView(groups, "primary", "left.md");
  groups = openEditorView(groups, "secondary", "right.md");
  groups = closeEditorView(groups, "primary", "left.md");
  assert.equal(groups[0].activePath, "README.md");
  assert.equal(groups[1].activePath, "right.md");
  groups = removeEditorDocument(groups, "README.md");
  assert.equal(groups[0].activePath, null);
  assert.equal(groups[1].activePath, "right.md");
});

test("Save As and rename move every view while preserving each opening mode", () => {
  const groups = renameEditorDocument(splitDocument(), "README.md", "guide.md");
  assert.equal(editorViewCount(groups, "README.md"), 0);
  assert.equal(editorViewCount(groups, "guide.md"), 2);
  assert.deepEqual(
    groups.map((group) => group.activePath),
    ["guide.md", "guide.md"],
  );
  assert.deepEqual(
    groups.map((group) => group.views[0].presentation),
    ["source", "preview"],
  );
});

test("Markdown defaults to text, rendered diagrams default to preview, and explicit modes win", () => {
  assert.equal(resolvedEditorPresentation("markdown"), "source");
  assert.equal(resolvedEditorPresentation("markdown", "preview"), "preview");
  assert.equal(resolvedEditorPresentation("diagram"), "preview");
  assert.equal(resolvedEditorPresentation("scene", "source"), "source");
  assert.equal(resolvedEditorPresentation(undefined, "preview"), "source");
});

test("new views capture defaults once and reopening keeps their chosen editor", () => {
  let groups = openEditorView([emptyEditorGroup()], "primary", "README.md");
  groups = initializeEditorView(groups, "primary", "README.md", "markdown-editor");
  groups = initializeEditorView(groups, "primary", "README.md", "preview");
  assert.equal(groups[0].views[0].presentation, "markdown-editor");
  groups = openEditorView(groups, "secondary", "README.md");
  groups = initializeEditorView(groups, "secondary", "README.md", "preview");
  assert.equal(groups[1].views[0].presentation, "preview");
  assert.equal(groups[0].views[0].presentation, "markdown-editor");
});

test("Markdown Editor remains a real opening mode only for Markdown", () => {
  assert.equal(resolvedEditorPresentation("markdown", "markdown-editor"), "markdown-editor");
  assert.equal(resolvedEditorPresentation("diagram", "markdown-editor"), "source");
  assert.equal(resolvedEditorPresentation(undefined, "markdown-editor"), "source");
});

test("closing a group waits for a dirty editor and stops immediately when it is cancelled", async () => {
  const calls = [];
  let confirm;
  const tab = (id, close) => ({ id, label: id, select() {}, close });
  const closing = closeEditorsInOrder([
    tab("clean", () => {
      calls.push("clean");
      return true;
    }),
    tab("dirty", () => {
      calls.push("dirty");
      return new Promise((resolve) => {
        confirm = resolve;
      });
    }),
    tab("later", () => {
      calls.push("later");
      return true;
    }),
  ]);
  await Promise.resolve();
  assert.deepEqual(calls, ["clean", "dirty"]);
  confirm(false);
  assert.equal(await closing, false);
  assert.deepEqual(calls, ["clean", "dirty"]);
});

test("closing a group only advances after the dirty editor has finished saving", async () => {
  let finishSave;
  let saved = false;
  let secondClosed = false;
  const closing = closeEditorsInOrder([
    {
      id: "first",
      label: "first",
      select() {},
      close: () =>
        new Promise((resolve) => {
          finishSave = () => {
            saved = true;
            resolve(true);
          };
        }),
    },
    {
      id: "second",
      label: "second",
      select() {},
      close: () => {
        assert.equal(saved, true);
        secondClosed = true;
        return true;
      },
    },
  ]);
  await Promise.resolve();
  assert.equal(secondClosed, false);
  finishSave();
  assert.equal(await closing, true);
  assert.equal(secondClosed, true);
});

test("closing one group retains a dirty document still viewed by another group", async () => {
  let groups = splitDocument();
  const documents = new Map([["README.md", { content: "unsaved", saved: "original" }]]);
  let requestedSave = false;
  assert.equal(
    await closeEditorsInOrder([
      {
        id: "README.md",
        label: "README.md",
        dirty: true,
        select() {},
        close: () => {
          const draft = documents.get("README.md");
          if (editorViewCount(groups, "README.md") === 1 && draft.content !== draft.saved)
            requestedSave = true;
          groups = closeEditorView(groups, "primary", "README.md");
          if (editorViewCount(groups, "README.md") === 0) documents.delete("README.md");
          return true;
        },
      },
    ]),
    true,
  );
  assert.equal(requestedSave, false);
  assert.equal(editorViewCount(groups, "README.md"), 1);
  assert.equal(documents.get("README.md").content, "unsaved");
});
