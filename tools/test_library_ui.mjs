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

const { ArtifactStatus, BuildSummary } = await load("src/texsys/assets/studio/ProjectPreview.tsx");
test("a cached failed preview remains failed, and historical success does not hide the latest failure", () => {
  const base = {
    id: "bom",
    target: "bom",
    run_id: "run-old",
    current: true,
    status: "fail",
    execution: "cached",
    available: true,
  };
  const failed = renderToStaticMarkup(React.createElement(ArtifactStatus, { artifact: base }));
  assert.match(failed, /未通过/);
  assert.match(failed, /目标判定/);
  assert.doesNotMatch(failed, /工程判定|满足/);
  assert.match(failed, /复用缓存/);
  const fallback = renderToStaticMarkup(
    React.createElement(ArtifactStatus, {
      artifact: {
        ...base,
        current: false,
        fallback: true,
        status: "pass",
        latest_target: { status: "error" },
      },
    }),
  );
  assert.match(fallback, /历史产物.*已过期/);
  assert.match(fallback, /历史目标判定/);
  assert.doesNotMatch(fallback, /工程判定|满足/);
  assert.match(fallback, /本次目标判定/);
  assert.match(fallback, /执行失败/);
  assert.match(fallback, /run-old/);
});

test("build summary exposes failed and blocked targets even when they have no artifacts", () => {
  const html = renderToStaticMarkup(
    React.createElement(BuildSummary, {
      build: {
        run_id: "run-failed-without-artifacts",
        current: true,
        status: "error",
        targets: [
          { id: "equipment", status: "pass", execution: "cached", diagnostics: [] },
          {
            id: "solver",
            status: "error",
            execution: "executed",
            diagnostics: [{ rule: "E_SOLVER", message: "Solver process could not start" }],
          },
          {
            id: "report",
            status: "indeterminate",
            execution: "blocked",
            blocked_by: ["solver"],
            diagnostics: [],
          },
        ],
        diagnostics: [{ message: "Build has incomplete targets" }],
      },
    }),
  );
  for (const expected of [
    "run-failed-without-artifacts",
    "复用缓存",
    "执行失败",
    "E_SOLVER",
    "Solver process could not start",
    "已阻断",
    "阻断依赖",
    "solver",
    "无法判定",
    "Build has incomplete targets",
  ])
    assert.ok(html.includes(expected), expected);
});
