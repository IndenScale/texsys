import test from "node:test";
import assert from "node:assert/strict";
import Module from "node:module";
import path from "node:path";
import esbuild from "esbuild-wasm";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

const filename = path.resolve("src/texsys/assets/studio/MarkdownPreview.tsx");
const output = esbuild.buildSync({
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
loaded._compile(output.outputFiles[0].text, filename);
const { MarkdownPreview, markdownLink } = loaded.exports;
const html = (content, props = {}) =>
  renderToStaticMarkup(React.createElement(MarkdownPreview, { content, ...props }));

test("Markdown renders BOM tables and current source content without an artifact manifest", () => {
  const original = "# 暖通系统 BOM\n\n| 类型 | 数量 |\n| --- | ---: |\n| pump | 2 ea |";
  const first = html(original);
  assert.match(first, /<h1[^>]*>暖通系统 BOM<\/h1>/);
  assert.match(first, /<table>/);
  assert.match(first, /text-align:right/);
  assert.match(first, /<td[^>]*>2 ea<\/td>/);
  const changed = html(original.replace("2 ea", "3 ea"));
  assert.match(changed, /<td[^>]*>3 ea<\/td>/);
  assert.doesNotMatch(changed, /2 ea|来源运行|目标判定|只读/);
});

test("Markdown preserves nested lists, fenced code, emphasis and decoded entities", () => {
  const result = html(
    '3. First\n   - **strong** and *emphasis*\n\n> quoted &amp; text\n\n`x < y`\n\n```json\n{"value": "<tag>"}\n```',
  );
  assert.match(result, /<ol start="3">/);
  assert.match(result, /<ul>/);
  assert.match(result, /<strong>strong<\/strong>/);
  assert.match(result, /<em>emphasis<\/em>/);
  assert.match(result, /<blockquote><p>quoted &amp; text<\/p>/);
  assert.match(result, /<code>x &lt; y<\/code>/);
  assert.match(result, /<pre><code>\{&quot;value&quot;: &quot;&lt;tag&gt;&quot;\}<\/code><\/pre>/);
  assert.doesNotMatch(result, /&amp;amp;|&amp;lt;/);
});

test("Markdown never interprets raw HTML or unsafe links as executable markup", () => {
  const result = html(
    "<script>alert(1)</script>\n\n[click](javascript:alert%281%29)\n\n[entity](jav&#x61;script:evil)\n\n![bad](data:image/svg+xml,evil)\n\n<img src=x onerror=alert(1)>",
  );
  assert.doesNotMatch(result, /<script|<img|href="javascript:|src="data:/i);
  assert.match(result, /&lt;script&gt;/);
  assert.match(result, /&lt;img src=x onerror=alert\(1\)&gt;/);
  assert.match(result, /click/);
});

test("Markdown file links stay inside the project and preserve safe external destinations", () => {
  assert.deepEqual(markdownLink("../src/system.json#model", "docs/readme.md"), {
    href: "src/system.json",
    kind: "file",
  });
  assert.deepEqual(markdownLink("./part%20one.md", "docs/readme.md"), {
    href: "docs/part one.md",
    kind: "file",
  });
  assert.deepEqual(markdownLink("https://example.com/a?b=1&amp;c=2"), {
    href: "https://example.com/a?b=1&c=2",
    kind: "external",
  });
  for (const href of [
    "../../secret",
    "%2e%2e/%2e%2e/secret",
    "//evil.test/x",
    "javascript:evil",
    "jav&#x61;script:evil",
    "java\nscript:evil",
    "file:///tmp/a",
    "data:text/html,x",
    "https:\\evil.test",
  ])
    assert.equal(markdownLink(href, "docs/readme.md"), undefined, href);
  assert.match(
    html("[source](../src/system.json)", { path: "docs/readme.md", onOpenFile() {} }),
    /title="src\/system.json"/,
  );
  assert.match(html("[web](https://example.com/)"), /rel="noreferrer noopener"/);
});
