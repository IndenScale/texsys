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
esbuild.buildSync({
  stdin: { contents: code, resolveDir: input.root, loader: "tsx" },
  bundle: true,
  outfile: path.join(input.output, "studio.js"),
  platform: "browser",
  format: "iife",
  jsx: "automatic",
  target: "es2022",
  nodePaths: [path.join(input.root, "node_modules")],
  minify: true,
  define: { "process.env.NODE_ENV": '"production"' },
  legalComments: "inline",
});
