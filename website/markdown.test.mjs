import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { pageMarkdown, markdownPath } from "./lib/markdown.mjs";
import { labPage } from "./content/lab.mjs";

const origin = "https://engineering.example";
const page = {
  path: "/docs/start/",
  title: "重跑一项工程检查",
  english: "Reproduce a check",
  summary: "从输入到可复核的结果。",
  related: [{ label: "证据与版本", href: "/ideas/evidence/" }],
};

test("complete page keeps metadata, heading levels and absolute article references", () => {
  const result = pageMarkdown(
    {
      ...page,
      body: `<h2>准备输入</h2><h3>确认来源</h3>
    <p>读取 <a href="../units/#flow">单位</a>、<a href="/lab/">实验</a>和<a href="#limits">本文边界</a>。</p>
    <p><a href="https://reference.example/report">外部依据</a>，<a href="mailto:review@example.com">邮件</a>。</p>`,
    },
    { origin },
  );
  assert.ok(result.startsWith("# 重跑一项工程检查\n\nReproduce a check\n\n从输入到可复核的结果。"));
  assert.match(result, /原文：\[https:\/\/engineering\.example\/docs\/start\/\]/);
  assert.match(result, /\n## 准备输入\n\n### 确认来源\n/);
  assert.ok(result.includes("[单位](https://engineering.example/docs/units/#flow)"));
  assert.ok(result.includes("[实验](https://engineering.example/lab/)"));
  assert.ok(result.includes("[本文边界](https://engineering.example/docs/start/#limits)"));
  assert.ok(result.includes("[外部依据](https://reference.example/report)"));
  assert.ok(result.includes("[邮件](mailto:review@example.com)"));
  assert.ok(
    result.endsWith("## 相关阅读\n\n- [证据与版本](https://engineering.example/ideas/evidence/)\n"),
  );
});

test("nested instructions and multiline code remain executable and structurally nested", () => {
  const result = pageMarkdown(page, {
    origin,
    body: `<ol start="3"><li>准备依赖<ul><li>检查模型</li><li>保存来源<ol><li>记录修订</li></ol></li></ul></li><li>运行检查</li></ol>
    <pre><button>复制代码按钮</button><code class="language-bash"><span data-md-exclude>行号 1</span>uv run texsys test \\
  --scenario assets/scenarios/load-step.json
printf '&lt;finished&gt;'
</code></pre>
    <pre data-language="markdown"><code>## 示例

\`\`\`python
print(1)
\`\`\`</code></pre>`,
  });
  assert.match(result, /^3\.\s+准备依赖$/m);
  assert.match(result, /^ {3,}-\s+检查模型$/m);
  assert.match(result, /^ {6,}1\.\s+记录修订$/m);
  assert.match(result, /^4\.\s+运行检查$/m);
  assert.ok(
    result.includes(
      "```bash\nuv run texsys test \\\n  --scenario assets/scenarios/load-step.json\nprintf '<finished>'\n```",
    ),
  );
  assert.ok(result.includes("````markdown\n## 示例\n\n```python\nprint(1)\n```\n````"));
  assert.ok(!result.includes("复制代码按钮") && !result.includes("行号 1"));
});

test("linked editorial cards export independent headings, paragraphs and valid links", () => {
  const result = pageMarkdown(page, {
    origin,
    body: `<div class="editorial-list">
    <a class="editorial-item" href="/ideas/design-space/"><span>01</span><div><span>Design space</span><h2>设计，是对可能性的<br>逐步约束。</h2><p>保留已知、未知与备选方案。</p></div></a>
    <a class="editorial-item" href="/ideas/evidence/"><span>02</span><div><h2>让结论保留依据</h2><p>输入改变后重新审查。</p></div></a>
    <a href="/docs/" aria-label="阅读文档"><p>没有标题的文档入口。</p></a>
    </div>`,
  });
  assert.ok(
    result.includes(
      "## [设计，是对可能性的 逐步约束。](https://engineering.example/ideas/design-space/)\n\n保留已知、未知与备选方案。",
    ),
  );
  assert.ok(
    result.includes(
      "## [让结论保留依据](https://engineering.example/ideas/evidence/)\n\n输入改变后重新审查。",
    ),
  );
  assert.ok(
    result.includes("没有标题的文档入口。\n\n[阅读文档](https://engineering.example/docs/)"),
  );
  assert.ok(!result.includes("[01\n"));
  assert.ok(!result.includes("][02"));
  const links = [...result.matchAll(/\[[^\]\n]+\]\(https:\/\/engineering\.example\/[^)]+\)/g)];
  assert.ok(links.some(([link]) => link.includes("设计，是对可能性的 逐步约束。")));
  assert.ok(links.some(([link]) => link.includes("让结论保留依据")));
});

test("headerless and empty tables export without inventing column meanings or losing rows", () => {
  const result = pageMarkdown(page, {
    origin,
    body: "<table></table><table><tr><td>基线</td><td>300 kW</td></tr><tr><td>变更</td><td>81.83 kW</td></tr></table>",
  });
  assert.ok(result.includes("|  |  |\n| --- | --- |\n| 基线 | 300 kW |\n| 变更 | 81.83 kW |"));
  assert.ok(!result.includes("<table"));
});

test("comparison tables retain units, links, column bars and multiple lines", () => {
  const result = pageMarkdown(page, {
    origin,
    body: `<table><thead><tr><th>工况</th><th>供冷</th><th>依据</th></tr></thead><tbody>
    <tr><th scope="row">基线</th><td>300 kW</td><td><a href="/evidence/record.json">记录</a></td></tr>
    <tr><th scope="row">变更</th><td>81.83 kW<br>250 项不满足</td><td>A | B</td></tr>
    <tr><th scope="row">恢复</th><td>300 kW</td><td>2.5 m/s；408.89 kPa</td></tr>
    </tbody></table>`,
  });
  assert.match(result, /\| 工况 \| 供冷 \| 依据 \|\n\| --- \| --- \| --- \|/);
  assert.ok(
    result.includes("| 基线 | 300 kW | [记录](https://engineering.example/evidence/record.json) |"),
  );
  assert.ok(result.includes("| 变更 | 81.83 kW<br>250 项不满足 | A \\| B |"));
  assert.ok(result.includes("| 恢复 | 300 kW | 2.5 m/s；408.89 kPa |"));
});

test("export omits interface noise and embedded JSON while retaining details", () => {
  const result = pageMarkdown(page, {
    origin,
    body: `<header class="site-header">站点导航</header><nav>栏目导航</nav>
    <div data-md-exclude><h2>面板</h2><table><tr><th>交互数值</th></tr></table></div>
    <button>复制按钮</button><script type="application/json">{"private":"embedded-state"}</script>
    <style>.hidden{}</style><svg><text>示意标签</text></svg><noscript>开启脚本</noscript>
    <aside class="article-toc">文章目录</aside><footer class="site-footer">站点页脚</footer>
    <p class="lexicon-empty" hidden>没有匹配的术语。</p><div hidden><h2>隐藏分支</h2><p>尚未展示的提示</p></div>
    <details><summary>为什么不能接受</summary><p>81.83 kW 小于 300 kW。</p><ul><li>保留失败记录</li></ul></details>`,
  });
  for (const noise of [
    "站点导航",
    "栏目导航",
    "交互数值",
    "复制按钮",
    "embedded-state",
    ".hidden",
    "示意标签",
    "开启脚本",
    "文章目录",
    "站点页脚",
    "没有匹配的术语",
    "隐藏分支",
    "尚未展示的提示",
  ]) {
    assert.ok(!result.includes(noise), `should omit ${noise}`);
  }
  assert.ok(result.includes("**为什么不能接受**\n\n81.83 kW 小于 300 kW。"));
  assert.match(result, /-\s+保留失败记录/);
});

test("the actual Lab export retains all three static result states without a baseline-only panel", () => {
  const result = pageMarkdown(labPage, { origin });
  assert.ok(result.includes("| 基线 | 完成 | 254 项满足 | 测试接受；输入变更后待复核 |"));
  assert.ok(result.includes("| 缩径与移位 | 完成 | 250 项不满足 | 拒绝接受；旧证据不再适用 |"));
  assert.ok(result.includes("| 恢复 | 完成 | 与基线一致 | 新记录待审查，再单独测试接受 |"));
  assert.ok(!result.includes("01 基线"));
  assert.ok(!result.includes('"run_id"'));
  assert.ok(result.includes("https://engineering.example/evidence/pipe-change.json"));
});

test("authored Markdown remains verbatim and a complete source owns its single H1", async () => {
  const source = await readFile(new URL("./content/manifesto.md", import.meta.url), "utf8");
  const result = pageMarkdown(
    { ...page, title: "网站展示标题", body: "<p>不可替换原作</p>" },
    { origin, sourceMarkdown: source },
  );
  assert.ok(result.startsWith(source));
  assert.equal((result.match(/^# /gm) || []).length, 1);
  assert.ok(!result.includes("# 网站展示标题"));
  assert.ok(!result.includes("不可替换原作"));
  assert.ok(result.includes("原文：[https://engineering.example/docs/start/]"));
  assert.ok(result.includes("## 相关阅读"));
  const fragment = "## 保留层级\n\n[相对原文](./source.md)\n\n- 第一项\n  - 子项\n";
  const fragmentResult = pageMarkdown(page, { origin, sourceMarkdown: fragment });
  assert.ok(fragmentResult.startsWith("# 重跑一项工程检查\n\n"));
  assert.ok(fragmentResult.includes(fragment));
});

test("Markdown paths cover the home page, sections, articles and absolute canonical URLs", () => {
  assert.equal(markdownPath("/"), "/index.md");
  assert.equal(markdownPath("/docs/"), "/docs.md");
  assert.equal(markdownPath("/ideas/foo/"), "/ideas/foo.md");
  assert.equal(
    markdownPath("https://engineering.example/ideas/foo/?lang=zh#body"),
    "/ideas/foo.md",
  );
  assert.equal(markdownPath("/404.html"), "/404.md");
  assert.equal(markdownPath("/manifesto.md"), "/manifesto.md");
});
