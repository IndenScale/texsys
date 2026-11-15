import test from "node:test";
import assert from "node:assert/strict";
import Module from "node:module";
import path from "node:path";
import esbuild from "esbuild-wasm";
import { JSDOM } from "jsdom";

const dom = new JSDOM("<!doctype html><body></body>", { url: "http://localhost/" });
for (const name of ["window", "document"]) globalThis[name] = dom.window[name];

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
const { compileExcludes, DEFAULT_EXCLUDES } = load("src/texsys/assets/studio/file-excludes.ts");
const { listLocalFolder, readLocalText } = load("src/texsys/assets/studio/welcome-files.ts");

test("client exclude contract matches the server defaults", () => {
  const excluded = compileExcludes();
  for (const relative of [
    ".DS_Store",
    "website/.DS_Store",
    "node_modules",
    "a/node_modules",
    ".git",
    "x/.venv",
  ])
    assert.equal(excluded(relative), true, relative);
  for (const relative of [".editorconfig", "website/build.mjs", "a/node_modules/x", "README.md"])
    assert.equal(excluded(relative), false, relative);
  assert.equal(DEFAULT_EXCLUDES["**/.DS_Store"], true);
});

test("workspace settings override defaults with JSONC tolerance", () => {
  const excluded = compileExcludes(
    '{ /* c */ "files.exclude": {"**/node_modules": false, ".editorconfig": true, "**/*.log": true, }, }',
  );
  assert.equal(excluded("node_modules"), false);
  assert.equal(excluded("a/node_modules"), false);
  assert.equal(excluded(".editorconfig"), true);
  assert.equal(excluded("x/debug.log"), true);
  assert.equal(excluded(".DS_Store"), true);
  assert.equal(compileExcludes("{ invalid")(".DS_Store"), true);
});

function folder(entries) {
  return {
    kind: "directory",
    async *values() {
      for (const [name, kind] of entries) yield { name, kind };
    },
  };
}

test("local folder listing applies the shared visibility contract", async () => {
  const handle = folder([
    ["README.md", "file"],
    [".DS_Store", "file"],
    ["node_modules", "directory"],
    ["src", "directory"],
    [".editorconfig", "file"],
  ]);
  const entries = await listLocalFolder(handle, "", compileExcludes());
  assert.deepEqual(
    entries.map((entry) => entry.name),
    ["src", ".editorconfig", "README.md"],
  );
  const nested = await listLocalFolder(
    folder([
      [".DS_Store", "file"],
      ["model.json", "file"],
    ]),
    "src",
    compileExcludes(),
  );
  assert.deepEqual(
    nested.map((entry) => entry.name),
    ["model.json"],
  );
});

test("local text reading uses the server limit and reasons", async () => {
  const file = (bytes) => ({
    size: bytes.byteLength ?? bytes.length,
    arrayBuffer: async () =>
      bytes instanceof Uint8Array ? bytes.buffer : new TextEncoder().encode(bytes).buffer,
  });
  assert.equal(await readLocalText({ getFile: async () => file("hello") }), "hello");
  await assert.rejects(
    () => readLocalText({ getFile: async () => file(new Uint8Array([0, 1])) }),
    /该文件不能以 UTF-8 文本编辑。/,
  );
  await assert.rejects(
    () => readLocalText({ getFile: async () => file(new Uint8Array(1024 * 1024 + 1)) }),
    /文件超过 1 MiB，暂不提供文本编辑。/,
  );
});
