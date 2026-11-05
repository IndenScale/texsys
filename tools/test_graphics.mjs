import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import Module from "node:module";
import esbuild from "esbuild-wasm";

const source = "packages/hvac-graphics/src/texsys_hvac_graphics/assets/symbols.ts";
const compiled = esbuild.transformSync(fs.readFileSync(source, "utf8"), {
  loader: "ts",
  format: "cjs",
}).code;
const module = new Module(source);
module._compile(compiled, source);
const { resolveGraphic } = module.exports;
function node(kind, names = ["inlet", "outlet"]) {
  return {
    component: "CustomDefinition",
    symbol: { requested: { graphic_kind: kind } },
    ports: names.map((name, i) => ({
      id: String(i),
      name,
      kind: name.startsWith("cooling-") ? "cooling-water" : "chilled-water",
      direction: "inout",
    })),
  };
}
for (const kind of ["pump", "tower", "load", "pipe", "valve", "tank"]) {
  test(`${kind}: full symbol and anchors remain coherent under parameter changes`, () => {
    const input = node(kind),
      original = JSON.stringify(input);
    const first = resolveGraphic(input),
      second = resolveGraphic(input, "schematic", 1.5);
    assert.notEqual(first.template, "neutral");
    assert.equal(second.width, first.width * 1.5);
    assert.equal(second.height, first.height);
    assert.ok(first.primitives.length >= 3);
    first.ports.forEach((port, i) => {
      assert.equal(second.ports[i].x, port.x * 1.5);
      assert.equal(second.ports[i].y, port.y);
      assert.ok(port.x >= 0 && port.x <= first.width && port.y >= 0 && port.y <= first.height);
    });
    assert.equal(JSON.stringify(input), original);
  });
}
test("chiller descriptor supports renamed definitions with four physical nozzles", () => {
  const graphic = resolveGraphic(
    node("chiller", ["chilled-in", "chilled-out", "cooling-in", "cooling-out"]),
  );
  assert.equal(graphic.template, "chiller");
  assert.equal(new Set(graphic.ports.map((port) => `${port.x},${port.y}`)).size, 4);
  assert.equal(graphic.primitives.filter((p) => p.tag === "ellipse").length, 4);
});
test("incompatible media, direction and extra ports use neutral graphics without losing ports", () => {
  for (const mutate of [
    (n) => (n.ports[0].kind = "air"),
    (n) => (n.ports[0].direction = "out"),
    (n) => n.ports.push({ ...n.ports[0], id: "extra", name: "service" }),
  ]) {
    const input = node("pump");
    mutate(input);
    const graphic = resolveGraphic(input);
    assert.equal(graphic.template, "neutral");
    assert.equal(graphic.ports.length, input.ports.length);
  }
});
test("invalid graphic scale is rejected; system view omits engineering annotation space", () => {
  const input = node("pump");
  assert.throws(() => resolveGraphic(input, "system", NaN));
  assert.throws(() => resolveGraphic(input, "system", 0));
  assert.notEqual(
    resolveGraphic(input, "system").label_anchor.y,
    resolveGraphic(input, "schematic").label_anchor.y,
  );
});
