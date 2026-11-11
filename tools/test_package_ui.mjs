import test from "node:test";
import assert from "node:assert/strict";
import Module from "node:module";
import path from "node:path";
import esbuild from "esbuild-wasm";

const filename = path.resolve("src/texsys/assets/studio/package-types.ts");
const output = esbuild.buildSync({
  entryPoints: [filename],
  bundle: true,
  write: false,
  platform: "node",
  format: "cjs",
});
const loaded = new Module(filename);
loaded.paths = Module._nodeModulePaths(process.cwd());
loaded._compile(output.outputFiles[0].text, filename);
const { matchesInstalledPackage, packageSelectionAction, packageMessages } = loaded.exports;
const installed = {
  name: "cooling",
  version: "1.0",
  digest: "sha256:one",
  registry: "/registry",
  source: "registry",
  direct: true,
  installed: true,
};

test("package testing requires the exact installed version, contents and origin", () => {
  assert.equal(matchesInstalledPackage(installed, installed), true);
  for (const change of [
    { version: "2.0" },
    { digest: "sha256:two" },
    { registry: "/another-registry" },
  ]) {
    assert.equal(matchesInstalledPackage({ ...installed, ...change }, installed), false);
  }
  assert.equal(matchesInstalledPackage(installed, { ...installed, installed: false }), false);
  assert.equal(matchesInstalledPackage(undefined, installed), false);
});

test("a transitive or local dependency is added explicitly, not sent as a direct registry update", () => {
  assert.equal(packageSelectionAction(installed), "update");
  assert.equal(packageSelectionAction({ ...installed, direct: false }), "add");
  assert.equal(packageSelectionAction({ ...installed, source: "path" }), "add");
  assert.equal(packageSelectionAction(undefined), "add");
});

test("runtime requirement details remain visible when a success message is empty", () => {
  const messages = packageMessages([
    { requirement: "scipy>=1.12", status: "satisfied", installed_version: "1.18", message: "" },
  ]);
  assert.ok(messages.some((message) => message.includes("scipy>=1.12")));
  assert.ok(messages.some((message) => message.includes("1.18")));
  assert.ok(messages.every((message) => message.trim()));
  assert.deepEqual(packageMessages({ message: "Missing dependency" }), ["Missing dependency"]);
});
