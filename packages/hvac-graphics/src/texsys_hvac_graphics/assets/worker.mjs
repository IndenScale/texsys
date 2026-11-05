import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const root = process.argv[2];
const elkPath = process.argv[3];
const require = createRequire(path.join(root, "package.json"));
const esbuild = require("esbuild-wasm");
const ELK = require(elkPath);
const assets = path.dirname(fileURLToPath(import.meta.url));
const request = JSON.parse(fs.readFileSync(0, "utf8"));
if (request.schema_version !== 1) throw new Error("Unsupported graphics worker protocol.");
const inputs = [
  "types.ts",
  "symbols.ts",
  "graphics.tsx",
  "viewer.tsx",
  "simulation.tsx",
  "server.tsx",
  "style.css",
  "worker.mjs",
];
const hash = crypto.createHash("sha256");
for (const name of inputs) hash.update(name).update(fs.readFileSync(path.join(assets, name)));
for (const name of ["react", "react-dom", "esbuild-wasm"])
  hash.update(fs.readFileSync(path.join(root, "node_modules", name, "package.json")));
const sourceDigest = hash.digest("hex");
const cache = path.join(root, "build", ".graphics-cache", sourceDigest);
fs.mkdirSync(cache, { recursive: true });

function compile(entry, platform, filename) {
  const destination = path.join(cache, filename);
  if (!fs.existsSync(destination)) {
    const result = esbuild.buildSync({
      entryPoints: [path.join(assets, entry)],
      absWorkingDir: assets,
      bundle: true,
      platform,
      format: platform === "node" ? "cjs" : "iife",
      target: "es2022",
      jsx: "automatic",
      nodePaths: [path.join(root, "node_modules")],
      write: false,
      minify: true,
      define: { "process.env.NODE_ENV": '"production"' },
      legalComments: "inline",
    });
    const temporary = destination + "." + process.pid;
    fs.writeFileSync(temporary, result.outputFiles[0].contents);
    fs.renameSync(temporary, destination);
  }
  return destination;
}

const serverFile = compile("server.tsx", "node", "server.cjs");
const server = require(serverFile);
const document = request.document;
const metadata = {
  renderer: "react-svg/1",
  source_digest: `sha256:${sourceDigest}`,
  react: require("react/package.json").version,
  react_dom: require("react-dom/package.json").version,
  esbuild: esbuild.version,
  node_version: process.version,
  server_digest: `sha256:${crypto.createHash("sha256").update(fs.readFileSync(serverFile)).digest("hex")}`,
};

if (request.operation === "prepare") {
  server.prepareGraphics(document);
  const options = request.layout_options;
  const graph = {
    id: "system",
    layoutOptions: options,
    children: document.nodes.map((node) => ({
      id: node.id,
      width: node.width,
      height: node.height,
      layoutOptions: { "elk.portConstraints": "FIXED_POS" },
      ports: node.ports.map((port) => ({
        id: port.id,
        x: port.x,
        y: port.y,
        width: 0,
        height: 0,
        layoutOptions: { "elk.port.side": port.side },
      })),
    })),
    edges: document.edges.map((edge) => ({
      id: edge.id,
      sources: [edge.source],
      targets: [edge.target],
    })),
  };
  const result = await new ELK().layout(graph);
  const nodes = new Map(result.children.map((node) => [node.id, node]));
  const edges = new Map(result.edges.map((edge) => [edge.id, edge]));
  const round = (value) => Math.round(value * 1000) / 1000;
  for (const node of document.nodes) {
    node.x = round(nodes.get(node.id).x);
    node.y = round(nodes.get(node.id).y);
  }
  for (const edge of document.edges) {
    const sections = edges.get(edge.id).sections ?? [];
    if (sections.length !== 1) throw new Error(`Expected one routed section: ${edge.id}`);
    const section = sections[0];
    edge.points = [section.startPoint, ...(section.bendPoints ?? []), section.endPoint].map(
      (point) => ({ x: round(point.x), y: round(point.y) }),
    );
    const pair = edge.points
      .slice(1)
      .map((point, index) => [edge.points[index], point])
      .sort(
        (a, b) =>
          Math.abs(b[0].x - b[1].x) +
          Math.abs(b[0].y - b[1].y) -
          (Math.abs(a[0].x - a[1].x) + Math.abs(a[0].y - a[1].y)),
      )[0];
    edge.label_position = {
      x: round((pair[0].x + pair[1].x) / 2),
      y: round((pair[0].y + pair[1].y) / 2 - 15),
    };
  }
  process.stdout.write(JSON.stringify({ schema_version: 1, document, metadata }));
} else if (request.operation === "render") {
  const browser = fs.readFileSync(compile("viewer.tsx", "browser", "viewer.js"), "utf8");
  const style = fs.readFileSync(path.join(assets, "style.css"), "utf8");
  process.stdout.write(
    JSON.stringify({
      schema_version: 1,
      svg: server.renderSvg(document),
      html: server.renderHtml(document, browser, style, request.results),
      metadata,
    }),
  );
} else {
  throw new Error(`Unknown graphics operation: ${request.operation}`);
}
