import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";

const input = JSON.parse(fs.readFileSync(0, "utf8"));
const require = createRequire(path.join(input.root, "package.json"));
const esbuild = require("esbuild-wasm");
fs.mkdirSync(input.output, { recursive: true });
const code = [
  `import { startStudio } from ${JSON.stringify(input.host)};`,
  ...input.plugins.map((entry, index) => `import plugin${index} from ${JSON.stringify(entry)};`),
  `startStudio([${input.plugins.map((_, index) => `plugin${index}`).join(",")}]);`,
].join("\n");
const shared = {
  bundle: true,
  platform: "browser",
  format: "iife",
  target: "es2022",
  nodePaths: [path.join(input.root, "node_modules")],
  minify: true,
  define: { "process.env.NODE_ENV": '"production"' },
  legalComments: "inline",
};
const workers = {
  "monaco-editor/editor/editor.worker.js": "studio-editor-worker",
  "monaco-editor/language/json/json.worker.js": "studio-json-worker",
};
await Promise.all([
  esbuild.build({
    ...shared,
    entryPoints: Object.fromEntries(
      Object.entries(workers).map(([entry, output]) => [output, require.resolve(entry)]),
    ),
    outdir: input.output,
  }),
  esbuild.build({
    ...shared,
    stdin: { contents: code, resolveDir: input.root, loader: "tsx" },
    outfile: path.join(input.output, "studio.js"),
    jsx: "automatic",
    loader: { ".ttf": "file" },
    assetNames: "studio-[name]",
    plugins: [
      {
        name: "studio-editor-workers",
        setup(build) {
          // Vite handles these imports directly in HMR mode.
          build.onResolve({ filter: /^monaco-editor\/.*\.worker\.js\?worker$/ }, ({ path }) => {
            const worker = workers[path.slice(0, -"?worker".length)];
            return worker ? { path: worker, namespace: "studio-editor-worker" } : null;
          });
          build.onLoad({ filter: /.*/, namespace: "studio-editor-worker" }, ({ path }) => ({
            contents: `export default function () { return new Worker(${JSON.stringify(`/${path}.js`)}); }`,
          }));
        },
      },
    ],
  }),
]);
