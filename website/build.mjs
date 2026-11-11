import { mkdir, readFile, writeFile, cp, rm } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { pages } from "./content/pages.mjs";
import { labPage, labPreview } from "./content/lab.mjs";
import { productPages } from "./content/product.mjs";
import { pageMarkdown, markdownPath } from "./lib/markdown.mjs";

const root = fileURLToPath(new URL(".", import.meta.url));
const dist = path.join(root, "dist");
const origin = "https://movement-engineering-20261111.hfu7565ytru.chatgpt.site";
const markdownOutputs = new Map();
const pageRegistry = new Map();
const esc = (s = "") =>
  s
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
const plain = (s = "") =>
  s
    .replace(/<[^>]*>/g, " ")
    .replace(/\s+/g, " ")
    .trim();
const mark =
  '<svg viewBox="0 0 28 28" aria-hidden="true"><path d="M2 24V4h6l6 11 6-11h6v20h-6V14l-6 11-6-11v10Z" fill="currentColor"/></svg>';
const arrow =
  '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h14M13 6l6 6-6 6" fill="none" stroke="currentColor" stroke-width="1.5"/></svg>';
const searchIcon =
  '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="m16 16 5 5" stroke="currentColor" stroke-width="1.5"/></svg>';
const inline = (s) =>
  esc(s)
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\*([^*]+)\*/g, "<em>$1</em>");
// The supplied manifesto uses only headings, paragraphs, emphasis and rules.
function manifestoHTML(markdown) {
  let chapter = 0;
  return markdown
    .split(/\n\s*\n/)
    .map((block) => {
      if (block.startsWith("# ")) return "";
      if (block.startsWith("## "))
        return `<h2 id="chapter-${++chapter}">${inline(block.slice(3))}</h2>`;
      if (block.startsWith("### ")) return `<h3>${inline(block.slice(4))}</h3>`;
      if (block.trim() === "---") return "<hr>";
      return `<p>${inline(block).replaceAll("\n", "<br>")}</p>`;
    })
    .join("\n");
}
const manifestoSource = await readFile(path.join(root, "content/manifesto.md"), "utf8");
const manifesto = {
  path: "/manifesto/",
  section: "Ideas",
  title: "让复杂系统像软件一样被开发。",
  english: "Make complex systems developable.",
  summary:
    "从数据中心开始，探索物理世界的下一代开发环境。一份关于模块、约束、持续反馈，以及工程师判断的宣言。",
  body: `<div class="callout">这份宣言提出我们希望检验的命题。<a href="/texsys/">TexSys</a> 是目前的工程实践；已有证据与边界见 <a href="/lab/pipe-change/">管路变更实验</a>。</div>${manifestoHTML(manifestoSource)}`,
  sourceMarkdown: manifestoSource,
  related: [
    { href: "/ideas/design-space/", label: "设计是对可能性的逐步约束" },
    { href: "/system/", label: "探索工程开发环境" },
    { href: "/lab/pipe-change/", label: "让主张接受一次实验" },
  ],
};
const entries = [...pages, ...productPages, manifesto, labPage];
const nav = [
  ["Ideas", "/ideas/"],
  ["System", "/system/"],
  ["Lab", "/lab/"],
  ["Lexicon", "/lexicon/"],
  ["TexSys", "/texsys/"],
  ["文档", "/docs/"],
];
function header(section) {
  return `<a class="skip-link" href="#main">跳到正文</a><header class="site-header"><a class="wordmark" href="/" aria-label="Movement 首页">${mark}<span>Movement</span><span class="wordmark-period">.</span></a><nav class="desktop-nav" aria-label="主导航">${nav.map(([label, href]) => `<a href="${href}" ${section === label || (section === "Docs" && label === "文档") ? 'aria-current="page"' : ""}>${label}</a>`).join("")}</nav><div class="header-actions"><button class="search-trigger" aria-label="搜索网站" data-open-search>${searchIcon}<kbd>⌘ K</kbd></button><a href="/manifesto/" class="header-manifesto">阅读宣言 ${arrow}</a><button class="menu-trigger" aria-label="展开导航" aria-expanded="false" aria-controls="mobile-nav"><span></span><span></span></button></div></header><nav id="mobile-nav" class="mobile-nav" aria-label="移动导航" hidden>${nav.map(([label, href]) => `<a href="${href}">${label}</a>`).join("")}<a href="/manifesto/">Manifesto · 宣言</a><a href="/participate/">参与讨论</a></nav>`;
}
function footer() {
  return `<footer class="site-footer"><div class="footer-top"><a class="wordmark" href="/">${mark}<span>Movement.</span></a><p>思想被发展，系统被构建，主张被检验。</p><a href="/participate/">参与这场探索 ${arrow}</a></div><div class="footer-bottom"><span>Make complex systems developable.</span><nav aria-label="次级导航"><a href="/manifesto/">Manifesto</a><a href="/journal/">Journal</a><a href="/participate/">Participate</a></nav><span>从数据中心开始。</span></div></footer>`;
}
function searchDialog() {
  return `<dialog class="search-dialog" aria-labelledby="search-title"><div class="search-top"><label id="search-title" for="site-search">搜索思想、机制与术语</label><button class="search-close" aria-label="关闭搜索">Esc</button></div><div class="search-input-wrap">${searchIcon}<input id="site-search" type="search" placeholder="试试：反注、设计空间、证据…" autocomplete="off"></div><p class="search-count" role="status" aria-live="polite">输入关键词，探索相互关联的内容。</p><div class="search-results"></div></dialog>`;
}
function document(page, body, type = "") {
  const exportable = page.path !== "/404.html";
  let markdown = "";
  if (exportable) {
    markdown = pageMarkdown(page, {
      origin,
      body: page.body || body.replace(/<h1[^>]*>[\s\S]*?<\/h1>/, ""),
      sourceMarkdown: page.sourceMarkdown,
    });
    markdownOutputs.set(markdownPath(page.path), markdown);
    pageRegistry.set(page.path, page);
  }
  const markdownData = exportable
    ? `<script type="application/json" id="page-markdown">${JSON.stringify(markdown).replaceAll("<", "\\u003c")}</script>`
    : "";
  return `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="theme-color" content="#ffffff"><title>${esc(page.title)} — Movement</title><meta name="description" content="${esc(page.summary)}"><meta property="og:type" content="${type.includes("article") ? "article" : "website"}"><meta property="og:title" content="${esc(page.title)} — Movement"><meta property="og:description" content="${esc(page.summary)}"><meta property="og:url" content="${origin}${page.path}"><meta property="og:image" content="${origin}/social.svg"><link rel="icon" href="/favicon.svg" type="image/svg+xml"><link rel="canonical" href="${origin}${page.path}">${exportable ? `<link rel="alternate" type="text/markdown" href="${markdownPath(page.path)}" title="Markdown">` : ""}<link rel="stylesheet" href="/style.css"><link rel="stylesheet" href="/docs.css"><link rel="stylesheet" href="/lab.css"><script type="module" src="/app.js"></script><script type="module" src="/lab.js"></script></head><body class="${type}"><div class="reading-progress" aria-hidden="true"></div>${header(page.section)}<main id="main">${body}</main>${footer()}${searchDialog()}${markdownData}<dialog class="copy-fallback" aria-labelledby="copy-fallback-title"><div><h2 id="copy-fallback-title">手动复制 Markdown</h2><button data-close-copy aria-label="关闭手动复制">关闭</button></div><p>浏览器未允许自动复制。内容已选中，按 ⌘C 或 Ctrl+C 复制；也可以下载 Markdown 文件。</p><textarea aria-label="页面 Markdown" readonly></textarea></dialog></body></html>`;
}
function copyTools(page) {
  return `<div class="page-tools"><button type="button" class="copy-page" data-copy-page aria-label="复制本页 Markdown"><svg viewBox="0 0 20 20" aria-hidden="true"><rect x="7" y="7" width="10" height="11" rx="2" fill="none" stroke="currentColor"/><path d="M12 7V4a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2v7a2 2 0 0 0 2 2h3" fill="none" stroke="currentColor"/></svg><span>复制 Markdown</span></button><details class="page-tool-menu"><summary aria-label="更多页面操作">⌄</summary><div><a href="${markdownPath(page.path)}" target="_blank" rel="noopener">打开 Markdown</a><a href="${markdownPath(page.path)}" download>下载 Markdown</a><button type="button" data-copy-link>复制页面链接</button></div></details><span class="copy-status" role="status" aria-live="polite"></span></div>`;
}
function related(items = []) {
  return `<section class="related"><h2>关联阅读</h2>${items.map((item) => `<a href="${item.href}">${item.label}${arrow}</a>`).join("")}</section>`;
}
const shortTitle = (p) =>
  p.navTitle ||
  {
    "/manifesto/": "宣言",
    "/ideas/design-space/": "设计空间与承诺",
    "/ideas/feedback/": "实现信息与模型反馈",
    "/ideas/engineering-evidence/": "工程结论与证据",
    "/texsys/": "认识 TexSys",
    "/docs/": "文档概览",
    "/docs/quick-start/": "快速开始",
    "/docs/engineering-workflow/": "工程变更工作流",
    "/docs/evidence/": "理解结果与证据",
    "/system/compiler/": "Engineering Compiler",
    "/system/ide/": "Tex Studio 与 IDE",
    "/system/pdk/": "工程能力包",
    "/lab/pipe-change/": "管路变更实验",
    "/journal/first-feedback-loop/": "第一个反馈循环",
    "/participate/": "参与讨论",
  }[p.path] ||
  p.title.replace(/[。？]$/, "");
const readingGroups = [
  { title: "开始", paths: ["/docs/", "/texsys/", "/docs/quick-start/"] },
  {
    title: "工程实践",
    paths: ["/docs/engineering-workflow/", "/docs/evidence/", "/lab/pipe-change/"],
  },
  { title: "系统机制", paths: ["/system/compiler/", "/system/ide/", "/system/pdk/"] },
  {
    title: "思想与方法",
    paths: ["/manifesto/", ...pages.filter((p) => p.section === "Ideas").map((p) => p.path)],
  },
  {
    title: "术语",
    paths: pages.filter((p) => p.section === "Lexicon").map((p) => p.path),
    collapsible: true,
  },
  {
    title: "研究与参与",
    paths: pages.filter((p) => ["Journal", "Participate"].includes(p.section)).map((p) => p.path),
  },
];
function docsSidebar(page) {
  return `<aside class="docs-sidebar" id="docs-navigation"><a class="docs-brand" href="/docs/"><span class="docs-brand-mark">T</span><span>TexSys <small>文档与工程知识</small></span></a><button class="sidebar-search" data-open-search>${searchIcon}<span>搜索文档</span><kbd>⌘ K</kbd></button><nav aria-label="文档栏目">${readingGroups
    .map((g) => {
      const links = g.paths
        .map((url) => entries.find((p) => p.path === url))
        .filter(Boolean)
        .map(
          (p) =>
            `<a href="${p.path}" ${page.path === p.path ? 'aria-current="page"' : ""}>${shortTitle(p)}</a>`,
        )
        .join("");
      return g.collapsible
        ? `<details class="sidebar-group" ${g.paths.includes(page.path) ? "open" : ""}><summary>${g.title}<span>16</span></summary><div>${links}</div></details>`
        : `<div class="sidebar-group"><p>${g.title}</p>${links}</div>`;
    })
    .join("")}</nav><a class="sidebar-home" href="/">返回 Movement 首页</a></aside>`;
}
function pagination(page) {
  const readingPath = readingGroups.flatMap((group) => group.paths);
  const index = readingPath.indexOf(page.path);
  if (index < 0) return "";
  return `<nav class="docs-pagination" aria-label="相邻文档">${[
    readingPath[index - 1],
    readingPath[index + 1],
  ]
    .map((url, i) => {
      const next = entries.find((p) => p.path === url);
      return next
        ? `<a href="${next.path}"><small>${i === 0 ? "上一页" : "下一页"}</small><span>${shortTitle(next)}</span></a>`
        : "<span></span>";
    })
    .join("")}</nav>`;
}
function article(page) {
  const headings = [...page.body.matchAll(/<h2\s+id="([^"]+)"[^>]*>([\s\S]*?)<\/h2>/g)];
  const toc = headings.length
    ? `<aside class="docs-toc article-toc"><p>本页内容</p><nav aria-label="文章目录">${headings.map((h) => `<a href="#${h[1]}">${plain(h[2])}</a>`).join("")}</nav><a class="toc-back" href="#main">回到顶部 ↑</a></aside>`
    : "<aside></aside>";
  const bodyWithAnchors = page.body.replace(
    /<h2\s+id="([^"]+)"[^>]*>([\s\S]*?)<\/h2>/g,
    (_, id, text) =>
      `<h2 id="${id}">${text}<a class="heading-anchor" href="#${id}" aria-label="链接到：${esc(plain(text))}">#</a></h2>`,
  );
  return document(
    page,
    `<div class="docs-mobile-bar"><button class="docs-nav-toggle" aria-expanded="false" aria-controls="docs-navigation">☰ 文档目录</button><span>${page.section === "Docs" ? "TexSys 文档" : page.section}</span></div><div class="docs-shell">${docsSidebar(page)}<div class="docs-content"><header class="docs-heading"><div class="docs-heading-top"><div class="breadcrumb"><a href="${page.section === "Docs" ? "/docs/" : "/" + page.section.toLowerCase() + "/"}">${page.section === "Docs" ? "TexSys 文档" : page.section}</a><span>/</span><span>${shortTitle(page)}</span></div>${copyTools(page)}</div><h1>${page.title}</h1><p class="docs-english" lang="en">${page.english || ""}</p><p class="docs-summary">${page.summary}</p>${headings.length ? `<details class="docs-inline-toc"><summary>本页目录</summary><nav aria-label="本页章节">${headings.map((h) => `<a href="#${h[1]}">${plain(h[2])}</a>`).join("")}</nav></details>` : ""}</header><article class="prose docs-prose">${bodyWithAnchors}${related(page.related)}${pagination(page)}</article></div>${toc}</div>`,
    "article docs-page",
  );
}
function rows(list, options = {}) {
  return `<div class="editorial-list ${options.className || ""}">${list.map((p, i) => `<a class="editorial-item" href="${p.path}" ${options.filter ? `data-filter-text="${esc(`${p.title} ${p.english} ${p.summary}`)}"` : ""}><span class="item-index">${String(i + 1).padStart(2, "0")}</span><div><span class="item-english">${p.english}</span><h2>${p.title}</h2><p>${p.summary}</p></div>${arrow}</a>`).join("")}</div>`;
}
function indexPage({ section, title, english, summary, path: url, content }) {
  return document(
    { section, title, english, summary, path: url, body: content },
    `<div class="index-heading page-wrap"><p class="section-location">${section} <span>/ ${english}</span></p><h1>${title}</h1><p>${summary}</p>${copyTools({ path: url })}</div><div class="page-wrap index-body">${content}</div>`,
    "index-page",
  );
}
function structureGraphic() {
  return `<figure class="structure-art model-feedback" data-md-exclude><img src="/model-feedback.svg" width="480" height="450" alt="工程反馈机制：从设计模型形成空间布局，提取实际布局参数回到模型，重新计算并检查结果。物理规律保持不变。"><figcaption>更新模型的参数与假设，重新验证设计。<span>反馈机制示意</span></figcaption></figure>`;
}
function home() {
  const ideas = pages.filter((p) => p.section === "Ideas");
  const terms = pages.filter((p) => p.section === "Lexicon").slice(0, 6);
  return document(
    {
      path: "/",
      title: "让复杂系统像软件一样被开发",
      section: "",
      summary:
        "Movement 是一场关于工程开发的探索。从数据中心开始，让思想、工程工具与可检验的证据相互支撑。",
    },
    `
    <section class="home-hero page-wrap"><div class="hero-copy"><div class="hero-intro"><span class="blue-dot"></span> 一场关于工程开发的探索</div><h1>让复杂系统<br>像软件一样<br>被开发<span class="blue-period">。</span></h1><p class="hero-english" lang="en">Make complex systems<br> developable.</p><div class="hero-actions"><a class="button button-primary" href="/manifesto/">阅读宣言 ${arrow}</a><a class="text-link" href="#lab-preview">查看工程实验 ${arrow}</a></div></div>${structureGraphic()}<div class="hero-bottom"><span>从数据中心开始，探索物理世界的下一代开发环境。</span><a href="#proposition" aria-label="向下探索">向下探索 ↓</a></div></section>
    <section class="relationship-strip page-wrap"><div><span>Movement</span><p>探讨复杂系统如何被持续开发。</p><a href="/manifesto/">方法与宣言</a></div><span class="relationship-connector" aria-hidden="true">⇄</span><div><span>TexSys</span><p>用工具与实验检验这些方法。</p><a href="/texsys/">认识工具链</a></div><span class="relationship-connector" aria-hidden="true">→</span><div><span>Tex Studio · CLI · 领域包</span><p>工程工作区、自动化与专业能力。</p><a href="/docs/quick-start/">开始使用</a></div></section><section id="proposition" class="proposition page-wrap"><div class="section-side"><span>我们的命题</span><span class="section-side-en">The proposition</span></div><div class="proposition-content"><h2>我们已经能设计芯片。<br>为什么还不能以同样的方式<br>开发一座数据中心？</h2><p>专业工具让每个领域变得强大。但一项设计跨越原理、空间与实现时，工程师仍需自己维持它们之间的关系。</p><p>我们希望把这些关系变成可理解、可计算、可检验的工程对象。让每一次修改，都能得到连续的反馈。</p><a class="text-link" href="/manifesto/#chapter-1">读懂这个问题 ${arrow}</a></div></section>
    <section class="home-ideas page-wrap"><div class="section-heading"><div><span class="section-location">Ideas / 思想</span><h2>从三个判断出发。</h2></div><a class="text-link" href="/ideas/">全部专题 ${arrow}</a></div>${rows(ideas)}</section>
    <section class="home-system"><div class="page-wrap"><div class="section-heading"><div><span class="section-location">System / 系统</span><h2>把工具连起来。<br>让反馈持续发生。</h2></div><p>同一套工程对象与证据机制，<br>连接工作区、工具链和专业能力。</p></div><div class="system-flow"><a href="/system/ide/"><span>工程师的工作区</span><h3>Engineering IDE</h3><p>理解、修改、比较与审查。</p><small>Tex Studio</small>${arrow}</a><div class="flow-connector" aria-hidden="true">⇄</div><a href="/system/compiler/"><span>持续检查的工具链</span><h3>Engineering Compiler</h3><p>组织变更、求解、诊断和证据。</p><small>texsys CLI</small>${arrow}</a><div class="flow-connector" aria-hidden="true">⇄</div><a href="/system/pdk/"><span>可复用的专业知识</span><h3>Engineering PDK</h3><p>描述模型、接口、规则与适用边界。</p><small>领域能力包</small>${arrow}</a></div><div class="system-note"><span class="blue-dot"></span><span>TexSys 是这些机制的当前实践。<a href="/texsys/">认识 TexSys</a><a href="/docs/quick-start/">从文档开始使用</a></span></div></div></section>
    <section class="home-lab page-wrap" id="lab-preview"><div class="section-heading"><div><span class="section-location">Lab / TexSys 实验</span><h2>管径改变，供冷结论也会改变。</h2></div><a class="text-link" href="/lab/pipe-change/#experiment">检查实验与来源 ${arrow}</a></div>${labPreview}<div class="lab-follow"><p>概念设施水案例 · 保存结果回放 · 非实时求解</p><a class="text-link" href="/lab/pipe-change/">打开实验记录 ${arrow}</a></div></section>
    <section class="home-lexicon page-wrap"><div><span class="section-location">Lexicon / 共同语言</span><h2>让我们从<br>同一个定义开始。</h2><p>概念需要清楚的定义，也需要明确的边界。<br>从术语出发，找到它背后的思想与实验。</p><a class="text-link" href="/lexicon/">浏览术语库 ${arrow}</a></div><div class="term-links">${terms.map((p) => `<a href="${p.path}"><span>${p.title}<small>${p.english}</small></span>${arrow}</a>`).join("")}</div></section>
    <section class="join-strip page-wrap"><span>一个开放的问题</span><h2>下一次工程决定，<br>可以更有依据。</h2><div><p>带着一个真实问题、一条反例，<br>或一项可以复现的实验，参与这场探索。</p><a class="button button-primary" href="/participate/">参与讨论 ${arrow}</a></div></section>`,
    "home",
  );
}
await rm(dist, { recursive: true, force: true });
await mkdir(dist, { recursive: true });
await cp(path.join(root, "public"), dist, { recursive: true });
await writeFile(
  path.join(dist, "discussion-template.md"),
  `\uFEFF${(await readFile(path.join(root, "public/discussion-template.md"), "utf8")).replace(/^\uFEFF/, "")}`,
);

const outputs = new Map([["/", home()]]);
for (const page of entries) outputs.set(page.path, article(page));
outputs.set(
  "/ideas/",
  indexPage({
    section: "Ideas",
    title: "工程开发，值得重新思考。",
    english: "Ideas worth testing.",
    summary:
      "思想提供方向，实践暴露问题。从设计空间、反馈循环与工程证据出发，建立一套可以被质疑、被检验的方法。",
    path: "/ideas/",
    content: `<a class="manifesto-feature" href="/manifesto/"><div><span>我们的宣言</span><h2>让复杂系统<br>像软件一样被开发。</h2><p>十二个章节，一个共同的起点。</p></div>${arrow}</a>${rows(pages.filter((p) => p.section === "Ideas"))}`,
  }),
);
outputs.set(
  "/system/",
  indexPage({
    section: "System",
    title: "复杂系统的开发环境。",
    english: "An environment for engineering.",
    summary:
      "把对象、依赖、变更、求解和证据组织起来，让专业工具在同一条开发循环中协作。Tex Studio、texsys CLI 与领域包共同探索这套机制。",
    path: "/system/",
    content: `<div class="system-architecture"><span>修改设计</span><b>→</b><span>解释影响</span><b>→</b><span>检查与求解</span><b>→</b><span>理解结果</span><b>→</b><span>修正或接受</span><b>↺</b></div>${rows(pages.filter((p) => p.section === "System"))}<div class="callout"><strong>当前阶段：部分技术原型。</strong><p>管路变更案例已建立可重跑的概念验证。通用工程语义合并、真实资料验证、第二项目复用和正式工程接受仍需继续验证。</p><a href="/lab/pipe-change/">检查当前案例的证据与限制</a></div>`,
  }),
);
outputs.set(
  "/lab/",
  indexPage({
    section: "Lab",
    title: "让主张，接受检验。",
    english: "Claims are tested here.",
    summary:
      "每个实验都应说明输入是什么、发生了什么、依据在哪里，以及我们还不知道什么。从一个可重跑的工程变更开始。",
    path: "/lab/",
    content: `<a class="experiment-feature" href="/lab/pipe-change/"><span class="experiment-kind"><span class="blue-dot"></span> 真实 CLI 运行记录 / 概念模型</span><h2>一根管路改变，<br>哪些结论需要重来？</h2><p>缩小设施水管径，观察几何提取、供冷检查、证据失效与修复后的重新审查。</p><span class="text-link">打开实验 ${arrow}</span></a><div class="lab-method"><h2>怎样读一份实验记录</h2><div><h3>输入与条件</h3><p>先识别概念假设、单位、工况和源修订。</p></div><div><h3>结果与反例</h3><p>计算完成与工程满足分别记录，保留失败证据。</p></div><div><h3>适用范围</h3><p>结论只能在声明的条件与覆盖范围内使用。</p></div></div>`,
  }),
);
outputs.set(
  "/lexicon/",
  indexPage({
    section: "Lexicon",
    title: "一种共同的工程语言。",
    english: "A shared language.",
    summary: "定义概念，说明边界，连接思想与实现。每个词条都是这套知识网络的一个入口。",
    path: "/lexicon/",
    content: `<div class="lexicon-toolbar"><label for="lexicon-filter">查找术语</label><input id="lexicon-filter" type="search" placeholder="搜索中文或英文术语" autocomplete="off"><span id="lexicon-count" role="status">16 个基础术语</span></div>${rows(
      pages.filter((p) => p.section === "Lexicon"),
      { className: "lexicon-list", filter: true },
    )}<p class="lexicon-empty" hidden>没有匹配的术语。试试“证据”“模型”或英文关键词。</p>`,
  }),
);
outputs.set(
  "/journal/",
  indexPage({
    section: "Journal",
    title: "记录每一次认识的改变。",
    english: "Work, in the open.",
    summary:
      "记录实验发现、当前边界，以及下一步值得检验的问题。进展的单位，是更清楚的认识和更完整的证据。",
    path: "/journal/",
    content: rows(pages.filter((p) => p.section === "Journal")),
  }),
);
for (const [url, markdown] of markdownOutputs) {
  await mkdir(path.dirname(path.join(dist, url)), { recursive: true });
  // Plain Markdown has no HTML charset declaration. The UTF-8 signature also
  // works on static hosts that omit charset from text/markdown responses.
  await writeFile(path.join(dist, url), `\uFEFF${markdown}`);
}
await writeFile(
  path.join(dist, "llms.txt"),
  `# Movement 与 TexSys\n\nMovement 承载方法与知识；TexSys 是当前工程实践。以下入口提供完整 Markdown。\n\n${[...pageRegistry.values()].map((p) => `- [${p.title}](${origin}${markdownPath(p.path)}): ${p.summary}`).join("\n")}\n`,
);
for (const [url, html] of outputs) {
  const dir = path.join(dist, url);
  await mkdir(dir, { recursive: true });
  await writeFile(path.join(dir, "index.html"), html);
}
await writeFile(
  path.join(dist, "404.html"),
  document(
    {
      path: "/404.html",
      title: "这个页面还没有抵达",
      summary: "返回 Movement，继续探索工程开发。",
    },
    '<div class="not-found page-wrap"><span>404</span><h1>这条路径暂时没有内容。</h1><p>从思想、系统、实验或术语重新出发。</p><a class="button button-primary" href="/">回到首页</a></div>',
  ),
);
await writeFile(
  path.join(dist, "search-index.json"),
  JSON.stringify(
    entries.map((p) => ({
      path: p.path,
      title: p.title,
      english: p.english,
      section: p.section,
      summary: p.summary,
      text: plain(p.body),
    })),
    null,
    2,
  ),
);
await writeFile(
  path.join(dist, "sitemap.xml"),
  `<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">${[...outputs.keys()].map((p) => `<url><loc>${origin}${p}</loc></url>`).join("")}</urlset>`,
);
await writeFile(
  path.join(dist, "robots.txt"),
  `User-agent: *\nAllow: /\nSitemap: ${origin}/sitemap.xml\n`,
);
console.log(`Built ${outputs.size} pages and a 404 page into ${dist}`);
