const menu = document.querySelector(".menu-trigger");
const mobileNav = document.querySelector("#mobile-nav");
menu?.addEventListener("click", () => {
  const expanded = menu.getAttribute("aria-expanded") === "true";
  menu.setAttribute("aria-expanded", String(!expanded));
  menu.setAttribute("aria-label", expanded ? "展开导航" : "收起导航");
  mobileNav.hidden = expanded;
});

const dialog = document.querySelector(".search-dialog");
const search = document.querySelector("#site-search");
const results = document.querySelector(".search-results");
const count = document.querySelector(".search-count");
let searchIndex;
let searchPromise;
let searchSequence = 0;
let priorFocus;
async function loadSearch() {
  if (!searchPromise)
    searchPromise = fetch("/search-index.json")
      .then((r) => {
        if (!r.ok) throw new Error("Search unavailable");
        return r.json();
      })
      .catch((error) => {
        searchPromise = null;
        throw error;
      });
  searchIndex = await searchPromise;
  return searchIndex;
}
function showResults(items) {
  results.replaceChildren();
  for (const item of items) {
    const a = document.createElement("a");
    a.className = "search-result";
    a.href = item.path;
    const small = document.createElement("small");
    small.textContent = `${item.section} / ${item.english}`;
    const title = document.createElement("h3");
    title.textContent = item.title;
    const summary = document.createElement("p");
    summary.textContent = item.summary;
    a.append(small, title, summary);
    results.append(a);
  }
}
async function updateSearch() {
  const sequence = ++searchSequence;
  const q = search.value.trim().toLowerCase();
  if (!q) {
    count.textContent = "输入关键词，探索相互关联的内容。";
    results.replaceChildren();
    return;
  }
  count.textContent = "正在查找…";
  try {
    const index = await loadSearch();
    if (sequence !== searchSequence) return;
    const words = q.split(/\s+/);
    const matches = index
      .map((item) => {
        const title = `${item.title} ${item.english}`.toLowerCase();
        const summary = item.summary.toLowerCase();
        const text = `${title} ${summary} ${item.text}`.toLowerCase();
        return {
          item,
          score: words.every((w) => text.includes(w))
            ? words.reduce((n, w) => n + (title.includes(w) ? 10 : summary.includes(w) ? 4 : 1), 0)
            : 0,
        };
      })
      .filter((x) => x.score)
      .sort((a, b) => b.score - a.score);
    count.textContent = matches.length
      ? `找到 ${matches.length} 个相关页面${matches.length > 12 ? "，显示前 12 项" : ""}`
      : "没有找到相关页面。试试更短的关键词，如“证据”或“反注”。";
    showResults(matches.slice(0, 12).map((x) => x.item));
  } catch {
    if (sequence === searchSequence)
      count.textContent = "搜索索引暂时无法加载，请重新输入或通过顶部导航浏览。";
  }
}
function openSearch() {
  priorFocus = document.activeElement;
  if (!dialog.open) dialog.showModal();
  document.body.style.overflow = "hidden";
  search.focus();
}
document
  .querySelectorAll("[data-open-search]")
  .forEach((button) => button.addEventListener("click", openSearch));
document.querySelector(".search-close")?.addEventListener("click", () => dialog.close());
dialog?.addEventListener("click", (event) => {
  if (event.target === dialog) {
    const r = dialog.getBoundingClientRect();
    if (
      event.clientX < r.left ||
      event.clientX > r.right ||
      event.clientY < r.top ||
      event.clientY > r.bottom
    )
      dialog.close();
  }
});
dialog?.addEventListener("close", () => {
  document.body.style.overflow = "";
  priorFocus?.focus();
});
search?.addEventListener("input", updateSearch);
search?.addEventListener("keydown", (event) => {
  if (event.key === "ArrowDown") {
    event.preventDefault();
    results.querySelector("a")?.focus();
  }
  if (event.key === "Enter") results.querySelector("a")?.click();
});
results?.addEventListener("keydown", (event) => {
  if (!["ArrowDown", "ArrowUp"].includes(event.key)) return;
  const links = [...results.querySelectorAll("a")];
  const current = links.indexOf(document.activeElement);
  if (current < 0) return;
  event.preventDefault();
  if (event.key === "ArrowUp" && current === 0) search.focus();
  else
    links[
      Math.max(0, Math.min(links.length - 1, current + (event.key === "ArrowDown" ? 1 : -1)))
    ]?.focus();
});
document.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    openSearch();
  }
  if (event.key === "Escape" && !mobileNav.hidden) {
    mobileNav.hidden = true;
    menu.setAttribute("aria-expanded", "false");
    menu.setAttribute("aria-label", "展开导航");
    menu.focus();
  }
});

const filter = document.querySelector("#lexicon-filter");
filter?.addEventListener("input", () => {
  const query = filter.value.trim().toLowerCase();
  let visible = 0;
  document.querySelectorAll("[data-filter-text]").forEach((item) => {
    item.hidden = !item.dataset.filterText.toLowerCase().includes(query);
    if (!item.hidden) visible++;
  });
  document.querySelector("#lexicon-count").textContent =
    `${visible} 个${query ? "匹配" : "基础"}术语`;
  document.querySelector(".lexicon-empty").hidden = visible > 0;
});
if (document.body.classList.contains("article")) {
  const progress = document.querySelector(".reading-progress");
  const headings = [...document.querySelectorAll(".prose h2[id]")];
  const tocLinks = [...document.querySelectorAll(".article-toc nav a")];
  let scheduled = false;
  const update = () => {
    const total = document.documentElement.scrollHeight - innerHeight;
    progress.style.width = `${total > 0 ? Math.min(100, (scrollY / total) * 100) : 100}%`;
    const active =
      headings.filter((h) => h.getBoundingClientRect().top < 170).at(-1) || headings[0];
    tocLinks.forEach((a) => a.classList.toggle("is-current", a.hash === `#${active?.id}`));
    scheduled = false;
  };
  addEventListener(
    "scroll",
    () => {
      if (!scheduled) {
        scheduled = true;
        requestAnimationFrame(update);
      }
    },
    { passive: true },
  );
  addEventListener("resize", update);
  update();
}

const docsToggle = document.querySelector(".docs-nav-toggle");
const docsNav = document.querySelector(".docs-sidebar");
const closeDocsNav = () => {
  docsNav?.classList.remove("is-open");
  docsToggle?.setAttribute("aria-expanded", "false");
};
docsToggle?.addEventListener("click", () => {
  const open = docsNav.classList.toggle("is-open");
  docsToggle.setAttribute("aria-expanded", String(open));
});
document.addEventListener("click", (event) => {
  if (!docsNav?.contains(event.target) && !docsToggle?.contains(event.target)) closeDocsNav();
  document.querySelectorAll(".page-tool-menu[open]").forEach((menu) => {
    if (!menu.contains(event.target)) menu.open = false;
  });
});
document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  if (docsNav?.classList.contains("is-open")) {
    closeDocsNav();
    docsToggle.focus();
  }
  document.querySelectorAll(".page-tool-menu[open]").forEach((menu) => {
    menu.open = false;
    menu.querySelector("summary").focus();
  });
});
document.querySelectorAll(".docs-inline-toc a").forEach((a) =>
  a.addEventListener("click", () => {
    a.closest("details").open = false;
  }),
);

const fallback = document.querySelector(".copy-fallback");
let copyFocus;
function manualCopy(text) {
  copyFocus = document.activeElement;
  fallback.querySelector("textarea").value = text;
  fallback.showModal();
  const textarea = fallback.querySelector("textarea");
  textarea.focus();
  textarea.select();
}
document.querySelector("[data-close-copy]")?.addEventListener("click", () => fallback.close());
fallback?.addEventListener("close", () => copyFocus?.focus());
const copyStates = new WeakMap();
function copyContent(text, button, successText = "已复制") {
  const status = button.closest(".page-tools")?.querySelector(".copy-status");
  const label = button.querySelector("span") || button;
  let state = copyStates.get(button);
  if (!state) {
    state = { original: label.textContent, timer: null };
    copyStates.set(button, state);
  }
  const success = () => {
    clearTimeout(state.timer);
    label.textContent = successText;
    if (status) status.textContent = "内容已复制到剪贴板";
    state.timer = setTimeout(() => {
      label.textContent = state.original;
      if (status) status.textContent = "";
    }, 2400);
  };
  if (!navigator.clipboard?.writeText) {
    manualCopy(text);
    return;
  }
  navigator.clipboard
    .writeText(text)
    .then(success)
    .catch(() => manualCopy(text));
}
document.querySelectorAll("[data-copy-page]").forEach((button) =>
  button.addEventListener("click", () => {
    const node = document.querySelector("#page-markdown");
    if (!node) return;
    try {
      copyContent(JSON.parse(node.textContent), button, "已复制 Markdown");
    } catch {
      const status = button.closest(".page-tools").querySelector(".copy-status");
      status.textContent = "内容暂时无法读取，请从菜单下载 Markdown。";
    }
  }),
);
document.querySelectorAll("[data-copy-link]").forEach((button) =>
  button.addEventListener("click", () => {
    copyContent(location.href, button, "已复制链接");
  }),
);
for (const block of document.querySelectorAll(".prose pre")) {
  const code = block.querySelector("code");
  const text = code?.textContent || block.textContent;
  const language = /language-([\w-]+)/.exec(code?.className || "")?.[1];
  const caption = document.createElement("span");
  caption.className = "code-language";
  caption.textContent = language || "命令 / 代码";
  caption.setAttribute("aria-hidden", "true");
  const button = document.createElement("button");
  button.type = "button";
  button.className = "copy-code";
  button.textContent = "复制";
  button.setAttribute("aria-label", "复制代码");
  button.addEventListener("click", () => copyContent(text, button));
  block.append(caption, button);
}
