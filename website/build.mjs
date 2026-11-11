import { mkdir, readFile, writeFile, cp, rm } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { pages } from './content/pages.mjs';
import { labPage, labPreview } from './content/lab.mjs';

const root = fileURLToPath(new URL('.', import.meta.url));
const dist = path.join(root, 'dist');
const origin = 'https://movement-engineering-20261111.hfu7565ytru.chatgpt.site';
const esc = (s = '') => s.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;');
const plain = (s = '') => s.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim();
const mark = '<svg viewBox="0 0 28 28" aria-hidden="true"><path d="M2 24V4h6l6 11 6-11h6v20h-6V14l-6 11-6-11v10Z" fill="currentColor"/></svg>';
const arrow = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h14M13 6l6 6-6 6" fill="none" stroke="currentColor" stroke-width="1.5"/></svg>';
const searchIcon = '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="m16 16 5 5" stroke="currentColor" stroke-width="1.5"/></svg>';
const inline = (s) => esc(s).replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>').replace(/\*([^*]+)\*/g, '<em>$1</em>');
// The supplied manifesto uses only headings, paragraphs, emphasis and rules.
function manifestoHTML(markdown) {
  let chapter = 0;
  return markdown.split(/\n\s*\n/).map(block => {
    if (block.startsWith('# ')) return '';
    if (block.startsWith('## ')) return `<h2 id="chapter-${++chapter}">${inline(block.slice(3))}</h2>`;
    if (block.startsWith('### ')) return `<h3>${inline(block.slice(4))}</h3>`;
    if (block.trim() === '---') return '<hr>';
    return `<p>${inline(block).replaceAll('\n', '<br>')}</p>`;
  }).join('\n');
}
const manifesto = {
  path: '/manifesto/', section: 'Ideas', title: '让复杂系统像软件一样被开发。', english: 'Make complex systems developable.',
  summary: '从数据中心开始，探索物理世界的下一代开发环境。一份关于模块、约束、持续反馈，以及工程师判断的宣言。',
  body: `<div class="callout">这份宣言提出我们希望检验的命题。原型的已知能力与边界，见 <a href="/lab/pipe-change/">管路变更实验</a>。<a href="/manifesto.md" download>下载宣言原文</a></div>${manifestoHTML(await readFile(path.join(root, 'content/manifesto.md'), 'utf8'))}`,
  related: [{ href: '/ideas/design-space/', label: '设计是对可能性的逐步约束' }, { href: '/system/', label: '探索工程开发环境' }, { href: '/lab/pipe-change/', label: '让主张接受一次实验' }]
};
const entries = [...pages, manifesto, labPage];
const nav = [['Ideas', '/ideas/'], ['System', '/system/'], ['Lab', '/lab/'], ['Lexicon', '/lexicon/']];
function header(section) {
  return `<a class="skip-link" href="#main">跳到正文</a><header class="site-header"><a class="wordmark" href="/" aria-label="Movement 首页">${mark}<span>Movement</span><span class="wordmark-period">.</span></a><nav class="desktop-nav" aria-label="主导航">${nav.map(([label, href]) => `<a href="${href}" ${section === label ? 'aria-current="page"' : ''}>${label}</a>`).join('')}</nav><div class="header-actions"><button class="search-trigger" aria-label="搜索网站" data-open-search>${searchIcon}<kbd>⌘ K</kbd></button><a href="/manifesto/" class="header-manifesto">阅读宣言 ${arrow}</a><button class="menu-trigger" aria-label="展开导航" aria-expanded="false" aria-controls="mobile-nav"><span></span><span></span></button></div></header><nav id="mobile-nav" class="mobile-nav" aria-label="移动导航" hidden>${nav.map(([label, href]) => `<a href="${href}">${label}</a>`).join('')}<a href="/manifesto/">Manifesto · 宣言</a><a href="/participate/">参与讨论</a></nav>`;
}
function footer() {
  return `<footer class="site-footer"><div class="footer-top"><a class="wordmark" href="/">${mark}<span>Movement.</span></a><p>思想被发展，系统被构建，主张被检验。</p><a href="/participate/">参与这场探索 ${arrow}</a></div><div class="footer-bottom"><span>Make complex systems developable.</span><nav aria-label="次级导航"><a href="/manifesto/">Manifesto</a><a href="/journal/">Journal</a><a href="/participate/">Participate</a></nav><span>从数据中心开始。</span></div></footer>`;
}
function searchDialog() {
  return `<dialog class="search-dialog" aria-labelledby="search-title"><div class="search-top"><label id="search-title" for="site-search">搜索思想、机制与术语</label><button class="search-close" aria-label="关闭搜索">Esc</button></div><div class="search-input-wrap">${searchIcon}<input id="site-search" type="search" placeholder="试试：反注、设计空间、证据…" autocomplete="off"></div><p class="search-count" role="status" aria-live="polite">输入关键词，探索相互关联的内容。</p><div class="search-results"></div></dialog>`;
}
function document(page, body, type = '') {
  return `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="theme-color" content="#ffffff"><title>${esc(page.title)} — Movement</title><meta name="description" content="${esc(page.summary)}"><meta property="og:type" content="${type === 'article' ? 'article' : 'website'}"><meta property="og:title" content="${esc(page.title)} — Movement"><meta property="og:description" content="${esc(page.summary)}"><meta property="og:url" content="${origin}${page.path}"><meta property="og:image" content="${origin}/social.svg"><link rel="icon" href="/favicon.svg" type="image/svg+xml"><link rel="canonical" href="${origin}${page.path}"><link rel="stylesheet" href="/style.css"><script type="module" src="/app.js"></script><script type="module" src="/lab.js"></script></head><body class="${type}"><div class="reading-progress" aria-hidden="true"></div>${header(page.section)}<main id="main">${body}</main>${footer()}${searchDialog()}</body></html>`;
}
function related(items = []) {
  return `<section class="related"><h2>继续探索</h2>${items.map(item => `<a href="${item.href}">${item.label}${arrow}</a>`).join('')}</section>`;
}
function article(page) {
  const headings = [...page.body.matchAll(/<h2\s+id="([^"]+)"[^>]*>([\s\S]*?)<\/h2>/g)];
  const toc = headings.length ? `<aside class="article-toc"><p>本文内容</p><nav aria-label="文章目录">${headings.map(h => `<a href="#${h[1]}">${plain(h[2])}</a>`).join('')}</nav><a class="toc-back" href="#main">回到顶部 ↑</a></aside>` : '';
  return document(page, `<div class="article-heading page-wrap"><div class="breadcrumb"><a href="/${page.section.toLowerCase()}/">${page.section}</a><span>/</span><span>${page.path === '/manifesto/' ? 'Manifesto' : page.english}</span></div><h1>${page.title}</h1><p class="article-english" lang="en">${page.english || ''}</p><p class="article-summary">${page.summary}</p></div><div class="article-layout page-wrap"><article class="prose">${page.body}${related(page.related)}</article>${toc}</div>`, 'article');
}
function rows(list, options = {}) {
  return `<div class="editorial-list ${options.className || ''}">${list.map((p, i) => `<a class="editorial-item" href="${p.path}" ${options.filter ? `data-filter-text="${esc(`${p.title} ${p.english} ${p.summary}`)}"` : ''}><span class="item-index">${String(i + 1).padStart(2, '0')}</span><div><span class="item-english">${p.english}</span><h2>${p.title}</h2><p>${p.summary}</p></div>${arrow}</a>`).join('')}</div>`;
}
function indexPage({ section, title, english, summary, path: url, content }) {
  return document({ section, title, summary, path: url }, `<div class="index-heading page-wrap"><p class="section-location">${section} <span>/ ${english}</span></p><h1>${title}</h1><p>${summary}</p></div><div class="page-wrap index-body">${content}</div>`, 'index-page');
}
function structureGraphic() {
  return `<div class="structure-art" aria-label="原理、几何与实现通过模型连接并形成反馈"><svg viewBox="0 0 470 510" role="img" aria-labelledby="structure-title"><title id="structure-title">三个工程世界，共同的开发循环</title><defs><pattern id="dots" width="24" height="24" patternUnits="userSpaceOnUse"><circle cx="1" cy="1" r=".8" fill="#c9cdd6"/></pattern></defs><rect width="470" height="510" fill="url(#dots)"/><g class="art-scaffold" fill="none" stroke="#c2c8d3" stroke-width="1"><path d="M45 157 230 54 422 157 237 260Z M45 277 230 174 422 277 237 380Z M45 397 230 294 422 397 237 500Z"/><path d="M45 157V397 M230 54V294 M422 157V397 M237 260V500" stroke-dasharray="4 6"/><path d="m92 131 190 103m-144-129 191 104m-145-130 191 105M91 183 276 80M140 209 325 106M188 234 373 132"/></g><g class="art-layer art-layer-one" fill="none" stroke="#2457e7" stroke-width="2"><path d="m140 157 90-50 96 50-90 51Z"/><path d="M185 132v50m47-75v101m48-77v52m-140-26h186" stroke-width="1" opacity=".5"/><circle cx="140" cy="157" r="5" fill="white"/><circle cx="230" cy="107" r="5" fill="white"/><circle cx="326" cy="157" r="5" fill="white"/><circle cx="236" cy="208" r="5" fill="white"/></g><g class="art-layer art-layer-two" fill="#fff" stroke="#2457e7" stroke-width="1.5"><path d="m125 256 51-28 51 28-51 29Z m51 29v53l51-29v-53m-102 0v53l51 29"/><path d="m249 256 51-28 51 28-51 29Z m51 29v53l51-29v-53m-102 0v53l51 29"/><path d="m187 310 51-28 51 28-51 29Z m51 29v53l51-29v-53m-102 0v53l51 29"/></g><g class="art-layer art-layer-three" fill="none" stroke="#2457e7" stroke-width="2"><path d="m132 399 98-55 109 61-102 57Z M132 399l105 63m-56-90 108 61m-108-7 104-55"/><path d="m212 403 17 9 34-35" stroke-width="4"/></g><g fill="#2457e7"><circle class="art-pulse" cx="237" cy="260" r="4"/><path d="m350 200 22 12v119l-22 12" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="m348 340 4 8 6-9"/></g><g font-family="Helvetica Neue,Arial,sans-serif" font-size="11" fill="#5d6472"><text x="22" y="114">PRINCIPLE</text><text x="22" y="258">GEOMETRY</text><text x="22" y="392">REALIZATION</text><text x="383" y="270" transform="rotate(90 383 270)">FEEDBACK</text></g></svg><div class="art-caption"><span class="blue-dot"></span> 三个世界，一个持续反馈的系统。</div></div>`;
}
function home() {
  const ideas = pages.filter(p => p.section === 'Ideas');
  const terms = pages.filter(p => p.section === 'Lexicon').slice(0, 6);
  return document({ path: '/', title: '让复杂系统像软件一样被开发', section: '', summary: 'Movement 是一场关于工程开发的探索。从数据中心开始，让思想、工程工具与可检验的证据相互支撑。' }, `
    <section class="home-hero page-wrap"><div class="hero-copy"><div class="hero-intro"><span class="blue-dot"></span> 一场关于工程开发的探索</div><h1>让复杂系统<br>像软件一样<br>被开发<span class="blue-period">。</span></h1><p class="hero-english" lang="en">Make complex systems<br>developable.</p><div class="hero-actions"><a class="button button-primary" href="/manifesto/">阅读宣言 ${arrow}</a><a class="text-link" href="/lab/pipe-change/">走进实验室 ${arrow}</a></div></div>${structureGraphic()}<div class="hero-bottom"><span>从数据中心开始，探索物理世界的下一代开发环境。</span><a href="#proposition" aria-label="向下探索">向下探索 ↓</a></div></section>
    <section id="proposition" class="proposition page-wrap"><div class="section-side"><span>我们的命题</span><span class="section-side-en">The proposition</span></div><div class="proposition-content"><h2>我们已经能设计芯片。<br>为什么还不能以同样的方式<br>开发一座数据中心？</h2><p>专业工具让每个领域变得强大。但一项设计跨越原理、空间与实现时，工程师仍需自己维持它们之间的关系。</p><p>我们希望把这些关系变成可理解、可计算、可检验的工程对象。让每一次修改，都能得到连续的反馈。</p><a class="text-link" href="/manifesto/#chapter-1">读懂这个问题 ${arrow}</a></div></section>
    <section class="home-ideas page-wrap"><div class="section-heading"><div><span class="section-location">Ideas / 思想</span><h2>从三个判断出发。</h2></div><a class="text-link" href="/ideas/">全部专题 ${arrow}</a></div>${rows(ideas)}</section>
    <section class="home-system"><div class="page-wrap"><div class="section-heading"><div><span class="section-location">System / 系统</span><h2>把工具连起来。<br>让反馈持续发生。</h2></div><p>同一套工程对象与证据机制，<br>连接工作区、工具链和专业能力。</p></div><div class="system-flow"><a href="/system/ide/"><span>工程师的工作区</span><h3>Engineering IDE</h3><p>理解、修改、比较与审查。</p><small>Tex Studio</small>${arrow}</a><div class="flow-connector" aria-hidden="true">⇄</div><a href="/system/compiler/"><span>持续检查的工具链</span><h3>Engineering Compiler</h3><p>组织变更、求解、诊断和证据。</p><small>texsys CLI</small>${arrow}</a><div class="flow-connector" aria-hidden="true">⇄</div><a href="/system/pdk/"><span>可复用的专业知识</span><h3>Engineering PDK</h3><p>描述模型、接口、规则与适用边界。</p><small>领域能力包</small>${arrow}</a></div><div class="system-note"><span class="blue-dot"></span><span>正在构建中的工程基础设施。<a href="/system/">查看架构与当前边界</a></span></div></div></section>
    <section class="home-lab page-wrap"><div class="section-heading"><div><span class="section-location">Lab / 实验</span><h2>一个修改。<br>一连串需要重审的结论。</h2></div><p>从真实运行记录中，<br>观察一根管路如何改变供冷结果。</p></div>${labPreview}<div class="lab-follow"><p>概念设施水案例 · 保存结果回放 · 非实时求解</p><a class="text-link" href="/lab/pipe-change/">打开实验记录 ${arrow}</a></div></section>
    <section class="home-lexicon page-wrap"><div><span class="section-location">Lexicon / 共同语言</span><h2>让我们从<br>同一个定义开始。</h2><p>概念需要清楚的定义，也需要明确的边界。<br>从术语出发，找到它背后的思想与实验。</p><a class="text-link" href="/lexicon/">浏览术语库 ${arrow}</a></div><div class="term-links">${terms.map(p => `<a href="${p.path}"><span>${p.title}<small>${p.english}</small></span>${arrow}</a>`).join('')}</div></section>
    <section class="join-strip page-wrap"><span>一个开放的问题</span><h2>下一次工程决定，<br>可以更有依据。</h2><div><p>带着一个真实问题、一条反例，<br>或一项可以复现的实验，参与这场探索。</p><a class="button button-primary" href="/participate/">参与讨论 ${arrow}</a></div></section>`, 'home');
}
await rm(dist, { recursive: true, force: true });
await mkdir(dist, { recursive: true });
await cp(path.join(root, 'public'), dist, { recursive: true });
await cp(path.join(root, 'content/manifesto.md'), path.join(dist, 'manifesto.md'));
const outputs = new Map([['/', home()]]);
for (const page of entries) outputs.set(page.path, article(page));
outputs.set('/ideas/', indexPage({ section: 'Ideas', title: '工程开发，值得重新思考。', english: 'Ideas worth testing.', summary: '思想提供方向，实践暴露问题。从设计空间、反馈循环与工程证据出发，建立一套可以被质疑、被检验的方法。', path: '/ideas/', content: `<a class="manifesto-feature" href="/manifesto/"><div><span>我们的宣言</span><h2>让复杂系统<br>像软件一样被开发。</h2><p>十二个章节，一个共同的起点。</p></div>${arrow}</a>${rows(pages.filter(p => p.section === 'Ideas'))}` }));
outputs.set('/system/', indexPage({ section: 'System', title: '复杂系统的开发环境。', english: 'An environment for engineering.', summary: '把对象、依赖、变更、求解和证据组织起来，让专业工具在同一条开发循环中协作。Tex Studio、texsys CLI 与领域包共同探索这套机制。', path: '/system/', content: `<div class="system-architecture"><span>修改设计</span><b>→</b><span>解释影响</span><b>→</b><span>检查与求解</span><b>→</b><span>理解结果</span><b>→</b><span>修正或接受</span><b>↺</b></div>${rows(pages.filter(p => p.section === 'System'))}<div class="callout"><strong>当前阶段：部分技术原型。</strong><p>管路变更案例已建立可重跑的概念验证。通用工程语义合并、真实资料验证、第二项目复用和正式工程接受仍需继续验证。</p><a href="/lab/pipe-change/">检查当前案例的证据与限制</a></div>` }));
outputs.set('/lab/', indexPage({ section: 'Lab', title: '让主张，接受检验。', english: 'Claims are tested here.', summary: '每个实验都应说明输入是什么、发生了什么、依据在哪里，以及我们还不知道什么。从一个可重跑的工程变更开始。', path: '/lab/', content: `<a class="experiment-feature" href="/lab/pipe-change/"><span class="experiment-kind"><span class="blue-dot"></span> 真实 CLI 运行记录 / 概念模型</span><h2>一根管路改变，<br>哪些结论需要重来？</h2><p>缩小设施水管径，观察几何提取、供冷检查、证据失效与修复后的重新审查。</p><span class="text-link">打开实验 ${arrow}</span></a><div class="lab-method"><h2>怎样读一份实验记录</h2><div><h3>输入与条件</h3><p>先识别概念假设、单位、工况和源修订。</p></div><div><h3>结果与反例</h3><p>计算完成与工程满足分别记录，保留失败证据。</p></div><div><h3>适用范围</h3><p>结论只能在声明的条件与覆盖范围内使用。</p></div></div>` }));
outputs.set('/lexicon/', indexPage({ section: 'Lexicon', title: '一种共同的工程语言。', english: 'A shared language.', summary: '定义概念，说明边界，连接思想与实现。每个词条都是这套知识网络的一个入口。', path: '/lexicon/', content: `<div class="lexicon-toolbar"><label for="lexicon-filter">查找术语</label><input id="lexicon-filter" type="search" placeholder="搜索中文或英文术语" autocomplete="off"><span id="lexicon-count" role="status">16 个基础术语</span></div>${rows(pages.filter(p => p.section === 'Lexicon'), { className: 'lexicon-list', filter: true })}<p class="lexicon-empty" hidden>没有匹配的术语。试试“证据”“模型”或英文关键词。</p>` }));
outputs.set('/journal/', indexPage({ section: 'Journal', title: '记录每一次认识的改变。', english: 'Work, in the open.', summary: '记录实验发现、当前边界，以及下一步值得检验的问题。进展的单位，是更清楚的认识和更完整的证据。', path: '/journal/', content: rows(pages.filter(p => p.section === 'Journal')) }));
for (const [url, html] of outputs) {
  const dir = path.join(dist, url);
  await mkdir(dir, { recursive: true });
  await writeFile(path.join(dir, 'index.html'), html);
}
await writeFile(path.join(dist, '404.html'), document({path:'/404/', title:'这个页面还没有抵达', summary:'返回 Movement，继续探索工程开发。'}, '<div class="not-found page-wrap"><span>404</span><h1>这条路径暂时没有内容。</h1><p>从思想、系统、实验或术语重新出发。</p><a class="button button-primary" href="/">回到首页</a></div>'));
await writeFile(path.join(dist, 'search-index.json'), JSON.stringify(entries.map(p => ({ path:p.path, title:p.title, english:p.english, section:p.section, summary:p.summary, text:plain(p.body) })), null, 2));
await writeFile(path.join(dist, 'sitemap.xml'), `<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">${[...outputs.keys()].map(p => `<url><loc>${origin}${p}</loc></url>`).join('')}</urlset>`);
await writeFile(path.join(dist, 'robots.txt'), `User-agent: *\nAllow: /\nSitemap: ${origin}/sitemap.xml\n`);
console.log(`Built ${outputs.size} pages and a 404 page into ${dist}`);
