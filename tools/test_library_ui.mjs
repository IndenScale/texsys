import test from "node:test";
import assert from "node:assert/strict";
import Module from "node:module";
import path from "node:path";
import esbuild from "esbuild-wasm";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

async function load(source) {
  const filename = path.resolve(source);
  const output = await esbuild.build({
    entryPoints: [filename],
    bundle: true,
    write: false,
    platform: "node",
    format: "cjs",
    packages: "external",
    loader: { ".css": "empty" },
    plugins: [
      {
        name: "omit-test-portals",
        setup(build) {
          build.onResolve({ filter: /^react-dom$/ }, () => ({ path: "portal", namespace: "test" }));
          build.onLoad({ filter: /.*/, namespace: "test" }, () => ({
            contents: "export const createPortal = () => null;",
          }));
        },
      },
    ],
  });
  const loaded = new Module(filename);
  loaded.paths = Module._nodeModulePaths(process.cwd());
  loaded._compile(output.outputFiles[0].text, filename);
  return loaded.exports;
}
const { DesignDocument } = await load("src/texsys/assets/studio/DesignNavigation.tsx");
const { requestedLibrarySource } = await load("src/texsys/assets/studio/LibraryFolders.tsx");

test("adding an external directory selects its validated mapping after installed sources", () => {
  const installed = {
    id: "installed",
    name: "pump",
    path: "/project/build/dependencies/pump",
    locked: true,
  };
  const external = {
    id: "external",
    name: "electrical",
    path: "/canonical/packages/electrical",
    locked: false,
  };
  const index = {
    sources: [installed, external],
    requested_sources: [{ folder: "../electrical-alias", source_id: "external" }],
  };
  assert.equal(requestedLibrarySource(index, "../electrical-alias"), external);
  assert.equal(
    requestedLibrarySource({ ...index, requested_sources: [] }, "../missing"),
    undefined,
  );
  assert.equal(requestedLibrarySource({ sources: [installed] }, "../missing"), undefined);
  assert.equal(
    requestedLibrarySource(
      { ...index, requested_sources: [{ folder: "../missing", source_id: "absent" }] },
      "../missing",
    ),
    undefined,
  );
});

const source = {
  id: "vendor",
  name: "pump",
  path: "/vendor",
  version: "2.0",
  digest: "sha256:two",
  kind: "component",
  locked: false,
  cli_hint: "texsys add '/vendor'",
};
function componentHtml(locked, references = []) {
  return renderToStaticMarkup(
    React.createElement(DesignDocument, {
      index: {
        root: null,
        sources: [{ ...source, locked }],
        references,
        definitions: {
          pump: {
            name: "Pump",
            source: "vendor",
            entry: "src/library/models.json#pump",
            instances: [],
            model: { family: "pump", manufacturer: "fixture", model: "Pump" },
          },
        },
        library: [{ definition: "pump", kind: "models", owner: null }],
      },
      selection: { definition: "pump" },
      view: "model",
      setView() {},
      openFile() {},
      referenceEntry() {},
    }),
  );
}

test("an unimported component can be inspected but has no reference or upgrade button", () => {
  const html = componentHtml(false);
  assert.match(html, /请先在终端导入/);
  assert.match(html, /texsys add/);
  assert.doesNotMatch(html, /<button\b/);
});

test("a locked component can be referenced and outdated references are reviewed separately", () => {
  assert.match(componentHtml(true), /引用到项目/);
  const html = componentHtml(true, [
    {
      package: "pump",
      entry: "src/library/models.json#pump",
      version: "1.0",
      digest: "sha256:one",
    },
  ]);
  assert.match(html, /复核部件引用/);
  assert.doesNotMatch(html, /更新项目库|升级依赖/);
});

test("model documents retain their object information without project artifact tabs", () => {
  const html = renderToStaticMarkup(
    React.createElement(DesignDocument, {
      index: {
        root: "Plant",
        definitions: {
          Plant: {
            name: "Plant",
            file: "src/model.json",
            instances: [],
            ports: { inlet: { kind: "water", direction: "in" } },
            behavior: null,
            connections: [],
          },
        },
        sources: [],
        library: [],
        references: [],
      },
      selection: { occurrence: [] },
      openFile() {},
      referenceEntry() {},
    }),
  );
  assert.match(html, /Plant/);
  assert.match(html, /inlet/);
  assert.doesNotMatch(html, /role="tab"|拓扑图|原理图|物料清单|三维布置|交付物/);
});
const { FilePreviewContent } = await load("src/texsys/assets/studio/ProjectPreview.tsx");
function renderFilePreview(content, kind = "diagram") {
  return renderToStaticMarkup(
    React.createElement(FilePreviewContent, {
      path: "build/schematic/system.json",
      kind,
      content,
      version: "current",
      dirty: true,
      preview: { kind: "schematic" },
      api() {
        throw new Error("No snapshot or discovery requests allowed");
      },
      openSource() {},
      openFile() {},
    }),
  );
}
test("diagram preview renders the current file buffer without a specialized document header or build status", () => {
  const diagram = (label) =>
    JSON.stringify({
      pages: [{ id: "page", width: 300, height: 200 }],
      nodes: [
        { id: "node", semantic_ref: "Plant.Pump", label, x: 10, y: 20, width: 100, height: 50 },
      ],
      edges: [],
    });
  const original = renderFilePreview(diagram("Original pump"));
  const edited = renderFilePreview(diagram("Edited unsaved pump"));
  assert.match(original, /Original pump/);
  assert.match(edited, /Edited unsaved pump/);
  assert.doesNotMatch(edited, /Original pump|目标判定|来源运行|复用缓存|只读|<header/);
  assert.match(edited, /<svg/);
  const invalid = renderFilePreview("invalid json");
  assert.match(invalid, /无法预览此文件/);
  assert.doesNotMatch(invalid, /Original pump|Edited unsaved pump|project-preview-drawing/);
});
test("Markdown uses the same preview content slot and renders the current edited table", () => {
  const html = renderFilePreview(
    "# BOM\n\n| 名称 | 数量 |\n| --- | --- |\n| 水泵 | 3 |",
    "markdown",
  );
  assert.match(html, /<table>/);
  assert.match(html, /水泵/);
  assert.match(html, /<td[^>]*>3<\/td>/);
  assert.doesNotMatch(html, /目标判定|来源运行|只读|<header/);
});
