import { readFile, readdir, stat } from "node:fs/promises";
import { createHash } from "node:crypto";
import { fileURLToPath } from "node:url";
import { isDeepStrictEqual } from "node:util";
import path from "node:path";
import { pages } from "./content/pages.mjs";
import { labPage } from "./content/lab.mjs";

// Validate the generated publication, including links and the evidence readers see.
// Browser layout and engineering solver acceptance require their own checks.
const root = fileURLToPath(new URL(".", import.meta.url));
const dist = path.join(root, "dist");
const errors = [];
const check = (condition, message) => {
  if (!condition) errors.push(message);
};
const decode = (value = "") =>
  value.replace(/&(?:amp|quot|apos|lt|gt|#39|#\d+|#x[\da-f]+);/gi, (entity) => {
    const named = {
      "&amp;": "&",
      "&quot;": '"',
      "&apos;": "'",
      "&lt;": "<",
      "&gt;": ">",
      "&#39;": "'",
    };
    if (Object.hasOwn(named, entity.toLowerCase())) return named[entity.toLowerCase()];
    const number = entity.slice(2, -1);
    const codepoint = /^x/i.test(number) ? Number.parseInt(number.slice(1), 16) : Number(number);
    return Number.isInteger(codepoint) && codepoint <= 0x10ffff
      ? String.fromCodePoint(codepoint)
      : entity;
  });
const plain = (value) => decode(value.replace(/<[^>]*>/g, "")).trim();

function attributes(tag) {
  const result = {};
  const body = tag.replace(/^<\s*[\w:-]+/, "").replace(/\/?\s*>$/, "");
  for (const match of body.matchAll(
    /([^\s=/>]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+)))?/g,
  )) {
    result[match[1].toLowerCase()] = decode(match[2] ?? match[3] ?? match[4] ?? "");
  }
  return result;
}

function tags(html) {
  // Script JSON and JavaScript are text, not HTML links or headings.
  const markup = html
    .replace(/<!--[\s\S]*?-->/g, "")
    .replace(
      /(<(?:script|style)\b(?:(?:"[^"]*"|'[^']*'|[^'">])*)>)[\s\S]*?<\/(?:script|style)\s*>/gi,
      "$1",
    );
  return [...markup.matchAll(/<([a-z][\w:-]*)\b(?:"[^"]*"|'[^']*'|[^'">])*>/gi)].map((match) => ({
    name: match[1].toLowerCase(),
    attrs: attributes(match[0]),
  }));
}

async function filesIn(directory) {
  const result = [];
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const file = path.join(directory, entry.name);
    if (entry.isDirectory()) result.push(...(await filesIn(file)));
    else if (entry.isFile()) result.push(file);
  }
  return result;
}

try {
  await stat(path.join(dist, "index.html"));
} catch {
  console.error("未找到 dist/index.html。请先运行 node build.mjs，再运行 node check.mjs。");
  process.exit(1);
}

const htmlFiles = (await filesIn(dist)).filter((file) => file.endsWith(".html")).sort();
const documents = new Map();
const origins = new Set();
for (const file of htmlFiles) {
  const relative = path.relative(dist, file).split(path.sep).join("/");
  const route = `/${relative}`.replace(/index\.html$/, "");
  const html = await readFile(file, "utf8");
  const elements = tags(html);
  const htmlTag = elements.find((element) => element.name === "html");
  check(Boolean(htmlTag?.attrs.lang), `${route}: 缺少 html lang。`);
  check(/^zh(?:-|$)/i.test(htmlTag?.attrs.lang || ""), `${route}: 中文正文应声明中文语言。`);
  const title = html.match(/<title\b[^>]*>([\s\S]*?)<\/title>/i)?.[1];
  check(Boolean(title && plain(title)), `${route}: 缺少非空 title。`);
  const descriptions = elements.filter(
    (element) => element.name === "meta" && element.attrs.name?.toLowerCase() === "description",
  );
  check(
    descriptions.length === 1 && Boolean(descriptions[0].attrs.content?.trim()),
    `${route}: 应有一个非空 description。`,
  );
  check(
    elements.filter((element) => element.name === "main").length === 1,
    `${route}: 应有一个 main。`,
  );
  check(
    elements.filter((element) => element.name === "h1").length === 1,
    `${route}: 应有一个 h1。`,
  );
  check(
    Boolean(plain(html.match(/<h1\b[^>]*>([\s\S]*?)<\/h1>/i)?.[1] || "")),
    `${route}: h1 不能为空。`,
  );
  const ids = elements.filter((element) => element.attrs.id).map((element) => element.attrs.id);
  check(new Set(ids).size === ids.length, `${route}: 存在重复 id，页面锚点可能指向错误对象。`);
  for (const element of elements.filter(
    (element) => element.name === "link" && element.attrs.rel === "canonical",
  )) {
    try {
      origins.add(new URL(element.attrs.href).origin);
    } catch {
      errors.push(`${route}: canonical URL 无效。`);
    }
  }
  documents.set(file, { route, html, elements, ids: new Set(ids) });
}

let linkCount = 0;
for (const document of documents.values()) {
  for (const element of document.elements) {
    for (const name of ["href", "src"]) {
      const value = element.attrs[name];
      if (value === undefined) continue;
      check(value.trim().length > 0, `${document.route}: ${element.name}[${name}] 为空。`);
      if (!value.trim()) continue;
      if (/^javascript:/i.test(value)) {
        errors.push(`${document.route}: 发布页面包含 javascript: 链接。`);
        continue;
      }
      let url;
      try {
        url = new URL(value, `https://publication-check.invalid${document.route}`);
      } catch {
        errors.push(`${document.route}: 无法解析 ${name}=${value}`);
        continue;
      }
      if (!["http:", "https:"].includes(url.protocol)) continue;
      if (url.origin !== "https://publication-check.invalid" && !origins.has(url.origin)) continue;
      linkCount++;
      let pathname;
      try {
        pathname = decodeURIComponent(url.pathname);
      } catch {
        errors.push(`${document.route}: 无效 URL 编码 ${value}`);
        continue;
      }
      let target = path.resolve(dist, `.${pathname}`);
      if (target !== dist && !target.startsWith(dist + path.sep)) {
        errors.push(`${document.route}: 链接越过发布目录 ${value}`);
        continue;
      }
      try {
        const info = await stat(target);
        if (info.isDirectory()) target = path.join(target, "index.html");
        check((await stat(target)).isFile(), `${document.route}: 链接目标不是文件 ${value}`);
      } catch {
        errors.push(`${document.route}: 找不到 ${name}=${value}`);
        continue;
      }
      if (url.hash && documents.has(target)) {
        let fragment;
        try {
          fragment = decodeURIComponent(url.hash.slice(1));
        } catch {
          errors.push(`${document.route}: 无效锚点编码 ${value}`);
          continue;
        }
        check(documents.get(target).ids.has(fragment), `${document.route}: 锚点不存在 ${value}`);
      }
    }
  }
}

// Catch route collisions before Map overwriting can hide a content page.
const buildSource = await readFile(path.join(root, "build.mjs"), "utf8");
const indexRoutes = [...buildSource.matchAll(/outputs\.set\(\s*['"]([^'"]+)['"]/g)].map(
  (match) => match[1],
);
const routes = [
  "/",
  "/manifesto/",
  ...indexRoutes,
  ...pages.map((page) => page.path),
  labPage.path,
];
const routeSet = new Set();
for (const route of routes) {
  check(
    route.startsWith("/") && route.endsWith("/") && !route.includes(".."),
    `无效内容路由：${route}`,
  );
  check(!routeSet.has(route), `重复内容路由会覆盖页面：${route}`);
  routeSet.add(route);
  check(documents.has(path.join(dist, route, "index.html")), `内容路由没有构建输出：${route}`);
}
check(
  htmlFiles.length === routeSet.size + 1,
  "构建页面数量与声明路由不一致（另含一个 404 页面）。",
);

try {
  const searchIndex = JSON.parse(await readFile(path.join(dist, "search-index.json"), "utf8"));
  check(Array.isArray(searchIndex), "search-index.json 必须是数组。");
  const indexed = new Set();
  for (const item of searchIndex) {
    check(
      typeof item.path === "string" && routeSet.has(item.path),
      `搜索结果指向不存在的页面：${item.path}`,
    );
    check(!indexed.has(item.path), `搜索索引包含重复路径：${item.path}`);
    check(
      Boolean(item.title?.trim() && item.summary?.trim()),
      `搜索结果缺少标题或摘要：${item.path}`,
    );
    indexed.add(item.path);
  }
} catch (error) {
  errors.push(`搜索索引无法读取：${error.message}`);
}

try {
  const evidencePath = path.join(dist, "evidence/pipe-change.json");
  const evidenceText = await readFile(evidencePath, "utf8");
  const evidence = JSON.parse(evidenceText);
  const { baseline, changed, repaired } = evidence.states;
  check(
    evidence.record_kind === "saved-concept-prototype-replay",
    "证据必须明确是概念原型的保存结果回放。",
  );
  check(!/\/Users\/|\/private\/|[A-Z]:\\Users\\/.test(evidenceText), "公开证据包含机器绝对路径。");
  check(/^[a-f0-9]{40}$/.test(evidence.source_commit), "证据缺少固定源码提交。");
  check(
    Array.isArray(evidence.limitations) && evidence.limitations.length > 0,
    "证据缺少适用边界。",
  );
  check(
    evidence.verification.status === "pass" && evidence.verification.commands_executed === 15,
    "公开 CLI 验证记录不完整。",
  );
  check(
    evidence.verification.old_acceptance_after_change === "needs-review",
    "输入变化后旧接受必须转为待复核。",
  );
  check(evidence.verification.failed_acceptance_exit_code !== 0, "失败证据的接受应被拒绝。");
  check(evidence.verification.changed_route_lengths === 96, "几何变更数量与本次冻结记录不符。");
  check(
    evidence.verification.baseline_repaired_checks_equal === true,
    "缺少基线与修复检查一致的记录。",
  );
  for (const [key, record] of Object.entries(evidence.states)) {
    check(record.execution === "completed", `${key}: 执行完成必须与工程判定分别记录。`);
    check(
      record.acceptance_at_run_completion === "unreviewed",
      `${key}: 求解完成不能自动成为工程接受。`,
    );
    check(Boolean(record.recorded_follow_up), `${key}: 缺少后续接受／复核状态说明。`);
    check(
      /^sha256:[a-f0-9]{64}$/.test(record.revision) &&
        /^sha256:[a-f0-9]{64}$/.test(record.evidence_digest),
      `${key}: 修订或证据摘要不完整。`,
    );
    check(record.total_checks === 254, `${key}: 检查总数与冻结记录不符。`);
  }
  check(
    baseline.assessment === "pass" && repaired.assessment === "pass",
    "基线与修复的工程判定应满足。",
  );
  check(
    baseline.failed_checks === 0 && repaired.failed_checks === 0,
    "基线与修复不应包含失败检查。",
  );
  check(
    changed.assessment === "fail" && changed.failed_checks === 250,
    "变更运行应保留 250 项不满足的工程检查。",
  );
  check(
    changed.selected_check.operator === ">=" &&
      changed.selected_check.actual < changed.selected_check.limit,
    "变更反例必须实际违反供冷要求。",
  );
  check(
    Math.abs(changed.selected_check.actual - 81.82788575080825) < 1e-8 &&
      changed.selected_check.limit === 300,
    "变更供冷数值与冻结记录不符。",
  );
  check(
    isDeepStrictEqual(baseline.selected_check, repaired.selected_check),
    "修复后的代表性检查与基线不同。",
  );
  check(
    new Set(Object.values(evidence.states).map((record) => record.run_id)).size === 3,
    "三个运行必须有不同的运行 ID。",
  );
  check(
    baseline.evidence_digest !== repaired.evidence_digest,
    "新运行应保留独立证据摘要，不能继承旧证据。",
  );
  const script = await readFile(path.join(dist, "evidence/verify_engineering_poc.py"));
  const declaredScript = evidence.source_files.find(
    (source) => source.path === "tools/verify_engineering_poc.py",
  );
  check(
    declaredScript?.sha256 === createHash("sha256").update(script).digest("hex"),
    "下载重跑脚本与所声明源码摘要不符。",
  );
  for (const document of documents.values()) {
    for (const embedded of document.html.matchAll(
      /<script\b[^>]*data-lab-records[^>]*>([\s\S]*?)<\/script>/g,
    )) {
      check(
        isDeepStrictEqual(JSON.parse(embedded[1]), evidence.states),
        `${document.route}: 页面回放与下载证据不一致。`,
      );
    }
  }
} catch (error) {
  errors.push(`工程证据无法核对：${error.message}`);
}

if (errors.length) {
  console.error(
    `静态发布检查失败（${errors.length} 项）：\n${errors.map((error) => `- ${error}`).join("\n")}`,
  );
  process.exitCode = 1;
} else {
  console.log(
    `静态发布检查通过：${routeSet.size} 个内容页面 + 404；${linkCount} 个本地链接／资源；搜索索引、页面语义与三状态工程证据一致。`,
  );
  console.log("边界：本检查不替代 Chrome 视觉／交互验收、外部 URL 可用性或真实工程验证。");
}
