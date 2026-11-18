import test from "node:test";
import assert from "node:assert/strict";
import Module from "node:module";
import path from "node:path";
import esbuild from "esbuild-wasm";
import React, { act } from "react";
import { JSDOM } from "jsdom";

const dom = new JSDOM("<!doctype html><head></head><body></body>", {
  url: "http://localhost/",
  pretendToBeVisual: true,
});
for (const name of [
  "window",
  "document",
  "Element",
  "HTMLElement",
  "HTMLButtonElement",
  "HTMLInputElement",
  "HTMLTextAreaElement",
  "Node",
  "Event",
  "MouseEvent",
  "KeyboardEvent",
  "localStorage",
  "sessionStorage",
  "requestAnimationFrame",
  "cancelAnimationFrame",
])
  globalThis[name] = dom.window[name];
globalThis.PointerEvent =
  dom.window.PointerEvent ??
  class PointerEvent extends dom.window.MouseEvent {
    constructor(type, init = {}) {
      super(type, init);
      this.pointerId = init.pointerId ?? 1;
      this.isPrimary = init.isPrimary ?? true;
    }
  };
dom.window.PointerEvent = globalThis.PointerEvent;
Object.defineProperty(globalThis, "navigator", { value: dom.window.navigator, configurable: true });
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
dom.window.HTMLElement.prototype.showPopover = function () {};
dom.window.HTMLElement.prototype.hidePopover = function () {};
const { createRoot } = await import("react-dom/client");

function loadStudioModule(source) {
  const filename = path.resolve("src/texsys/assets/studio", source);
  const result = esbuild.buildSync({
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
  loaded._compile(result.outputFiles[0].text, filename);
  return loaded.exports;
}
const { AgentPanel } = loadStudioModule("AgentPanel.tsx");

const ROOT = "/workspace/project";
const SCRATCH = "/tmp/agent-scratch";
const NOW = Date.now() / 1000;

const modelOption = (current = '["deepseek-official","deepseek-v4-flash"]') => ({
  id: "model",
  name: "Model",
  category: "model",
  type: "select",
  currentValue: current,
  options: [
    {
      group: "deepseek-official",
      name: "DeepSeek",
      options: [
        { value: '["deepseek-official","deepseek-v4-flash"]', name: "deepseek-v4-flash" },
        {
          value: '["deepseek-official","deepseek-v4-pro"]',
          name: "deepseek-v4-pro",
          description: "适合复杂工程推理。",
        },
      ],
    },
  ],
});
const reasoningOption = (current = "high") => ({
  id: "reasoning_effort",
  name: "Reasoning effort",
  category: "thought_level",
  type: "select",
  currentValue: current,
  options: ["off", "low", "high", "max"].map((value) => ({
    value,
    name: value[0].toUpperCase() + value.slice(1),
  })),
});

/** In-memory Studio agent API (ADR-0035) with one long-poll event log per session. */
function backend({ available = true, history = [] } = {}) {
  const state = { requests: [], sessions: new Map(), history: [...history], next: 1 };
  const summaryOf = (session) => ({
    id: session.id,
    workspace: session.workspace,
    title: session.title,
    state: session.state,
    error: "",
    created_at: NOW,
    updated_at: NOW,
    seq: session.events.length,
    cwd: ROOT,
    stderr: "",
    kernel: { name: "DeepSeek Harness", version: "0.0.1" },
    resumed: session.resumed,
  });
  state.emit = (id, ...events) => {
    const session = state.sessions.get(id);
    for (const event of events) {
      session.events.push({ seq: session.events.length + 1, ...event });
      if (event.type === "state") session.state = event.state;
      if (event.type === "configured") session.config = event.configOptions;
    }
    for (const wake of session.waiters.splice(0)) wake();
  };
  const create = (id, workspace, replay = []) => {
    const session = {
      id,
      workspace,
      title: "",
      state: "starting",
      events: [],
      waiters: [],
      config: [],
      resumed: replay.length > 0,
    };
    state.sessions.set(id, session);
    state.emit(id, ...replay, { type: "state", state: "starting", error: "" });
    setTimeout(
      () =>
        state.emit(
          id,
          { type: "configured", configOptions: [modelOption(), reasoningOption()] },
          { type: "state", state: "ready", error: "" },
        ),
      1,
    );
    return session;
  };
  state.reply = (id, text) =>
    state.emit(
      id,
      {
        type: "update",
        update: { sessionUpdate: "agent_message_chunk", content: { type: "text", text } },
      },
      { type: "turn_end", stopReason: "end_turn" },
      { type: "state", state: "ready", error: "" },
    );
  const json = (data, status = 200) => ({ ok: status < 400, status, json: async () => data });
  globalThis.fetch = async (url, options = {}) => {
    const body = options.body ? JSON.parse(options.body) : undefined;
    state.requests.push([url, body]);
    const parsed = new URL(url, "http://localhost");
    const path = parsed.pathname;
    if (path === "/api/files")
      return json({ files: ["src/room.json", "src/cooling.json", "README.md"] });
    if (path === "/api/agent") {
      const workspace = parsed.searchParams.get("workspace") ?? "project";
      return json({
        kernel: {
          id: "deepseek-harness",
          name: "DeepSeek Harness",
          command: "dsh --profile acp",
          available,
          reason: available ? "" : "未找到命令 dsh。",
        },
        diagnostics: [],
        sessions: [...state.sessions.values()]
          .filter((item) => item.state !== "closed")
          .map(summaryOf),
        history: state.history,
        root: workspace === "empty" ? SCRATCH : ROOT,
        workspace,
      });
    }
    if (path === "/api/agent/sessions") {
      const id = `s${state.next++}`;
      return json(summaryOf(create(id, body.workspace)));
    }
    if (path === "/api/agent/resume") {
      const entry = state.history.find((item) => item.id === body.session);
      const session = create(body.session, body.workspace, [
        { type: "user", text: entry.title, context: [] },
        {
          type: "update",
          update: {
            sessionUpdate: "agent_message_chunk",
            content: { type: "text", text: "旧回答" },
          },
        },
        { type: "turn_end", stopReason: "end_turn" },
        { type: "history", count: 3 },
      ]);
      session.title = entry.title;
      return json(summaryOf(session));
    }
    if (path === "/api/agent/events") {
      const session = state.sessions.get(parsed.searchParams.get("session"));
      if (!session) return json({ error: "智能体会话不存在或已关闭。" }, 400);
      const after = Number(parsed.searchParams.get("after"));
      if (session.events.length <= after)
        await new Promise((resolve, reject) => {
          session.waiters.push(resolve);
          options.signal?.addEventListener("abort", () => reject(new Error("aborted")));
        });
      return json({
        session: summaryOf(session),
        events: session.events.filter((event) => event.seq > after),
        seq: session.events.length,
        reset: false,
      });
    }
    if (path === "/api/agent/prompt") {
      const session = state.sessions.get(body.session);
      session.title ||= body.text;
      state.emit(
        body.session,
        { type: "user", text: body.text, context: body.context },
        { type: "state", state: "prompting", error: "" },
      );
      return json(summaryOf(session));
    }
    if (path === "/api/agent/config") {
      const session = state.sessions.get(body.session);
      const options = session.config.map((option) =>
        option.id === body.config ? { ...option, currentValue: body.value } : option,
      );
      state.emit(body.session, { type: "configured", configOptions: options });
      return json({ ok: true });
    }
    if (path === "/api/agent/close") {
      state.emit(body.session, { type: "state", state: "closed", error: "" });
      return json({ closed: body.session });
    }
    if (path === "/api/agent/history/delete") {
      state.history = state.history.filter((item) => item.id !== body.session);
      return json({ deleted: body.session });
    }
    return json({ ok: true });
  };
  state.posts = (endpoint) =>
    state.requests.filter(([url, body]) => url === endpoint && body).map(([, body]) => body);
  return state;
}

const flush = async (rounds = 6) => {
  for (let index = 0; index < rounds; index += 1)
    await act(async () => new Promise((resolve) => setTimeout(resolve, 5)));
};

async function mount(t, props = {}) {
  document.body.innerHTML = '<div id="app"></div>';
  const root = createRoot(document.getElementById("app"));
  const opened = [];
  const workspace = props.workspace ?? "project";
  await act(async () =>
    root.render(
      React.createElement(AgentPanel, {
        workspace,
        visible: true,
        activeFile: workspace === "project" ? "src/room.json" : undefined,
        openFile: (file, line) => opened.push([file, line]),
        reviewChanges: () => opened.push(["scm"]),
        openProject: () => opened.push(["project"]),
        onToggleMaximize: () => opened.push(["maximize"]),
        onClose: () => opened.push(["close"]),
      }),
    ),
  );
  t.after(async () => act(async () => root.unmount()));
  return { opened };
}

const text = () => document.body.textContent;
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const button = (label) =>
  $$("button").find(
    (item) => item.getAttribute("aria-label") === label || item.textContent.trim() === label,
  );
const tabs = () => $$('[role="tab"]').map((tab) => tab.querySelector(".ag-tab-title").textContent);
async function click(element) {
  await act(async () => {
    element.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
  });
}
async function type(value) {
  const area = $(".ag-input");
  const setter = Object.getOwnPropertyDescriptor(
    dom.window.HTMLTextAreaElement.prototype,
    "value",
  ).set;
  await act(async () => {
    setter.call(area, value);
    area.selectionStart = area.selectionEnd = value.length;
    area.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
  });
  return area;
}
async function key(name, init = {}) {
  await act(async () => {
    $(".ag-input").dispatchEvent(
      new dom.window.KeyboardEvent("keydown", { key: name, bubbles: true, ...init }),
    );
  });
}
function reset() {
  sessionStorage.clear();
  localStorage.clear();
}

test("panel opens on a quiet hero with session tabs and no harness, mode or billing chrome", async (t) => {
  const api = backend();
  reset();
  await mount(t);
  await flush();
  assert.deepEqual(tabs(), ["新会话"]);
  assert.equal($('[role="tab"]').getAttribute("aria-selected"), "true");
  for (const label of ["新建会话", "对话历史", "更多智能体操作"]) assert.ok(button(label), label);
  assert.equal($$(".ag-tabbar-actions > button").length, 3);
  assert.match(text(), /看清系统/);
  assert.match($(".ag-hero-title").textContent, /验证每一次修改/);
  assert.equal($(".ag-hero-accent").textContent, "验证");
  assert.ok(button("解释 room.json 的作用与结构"));
  assert.equal($(".ag-input").placeholder, "规划与修改，@ 添加上下文，/ 使用命令");
  assert.doesNotMatch(text(), /Harness|默认权限|智能问答|专家团|用量|Credit|倍/);
  assert.equal($(".ag-picker-label").textContent, "DeepSeek");
  assert.equal(button("发送").disabled, true);
  assert.equal(
    api.posts("/api/agent/sessions").length,
    0,
    "no kernel process before the first message",
  );
  assert.ok($(".ag-chip"), "current file attached as a chip");
});

test("suggestions only fill the composer; Enter starts the kernel and sends with context", async (t) => {
  const api = backend();
  reset();
  await mount(t);
  await flush();
  await click(button("运行构建，解读失败的检查"));
  assert.equal($(".ag-input").value, "运行构建，解读失败的检查");
  assert.equal(api.posts("/api/agent/prompt").length, 0);

  await type("检查冷通道温度");
  await key("Enter");
  await flush(10);
  assert.equal(api.posts("/api/agent/sessions").length, 1);
  assert.deepEqual(api.posts("/api/agent/prompt"), [
    { session: "s1", text: "检查冷通道温度", context: ["src/room.json"] },
  ]);
  assert.deepEqual(tabs(), ["检查冷通道温度"]);
  assert.ok(button("停止"), "send turns into stop while prompting");
  await act(async () => api.reply("s1", "冷通道温度 **正常**。"));
  await flush();
  assert.match($(".ag-message").textContent, /冷通道温度/);
  assert.ok(button("复制回答"));
  assert.equal($(".ag-hero"), null);
});

test("@ mentions add project files as chips and / lists kernel commands", async (t) => {
  const api = backend();
  reset();
  await mount(t);
  await flush();
  await type("对比 @coo");
  await flush();
  const menu = $(".ag-mention-menu");
  assert.ok(menu, "mention menu opens");
  assert.match(menu.textContent, /cooling\.json/);
  await key("Enter");
  await flush();
  assert.equal($(".ag-input").value, "对比 ");
  assert.deepEqual(
    $$(".ag-composer .ag-chip").map((chip) => chip.textContent),
    ["room.json", "cooling.json"],
  );
  await type("对比两个文件");
  await key("Enter");
  await flush(10);
  assert.deepEqual(api.posts("/api/agent/prompt")[0].context, [
    "src/room.json",
    "src/cooling.json",
  ]);
});

test("model menu lists names only, explains on hover and edits thinking in a side card", async (t) => {
  const api = backend();
  reset();
  await mount(t);
  await flush();
  await click($(".ag-picker"));
  await flush(8);
  assert.equal(api.posts("/api/agent/sessions").length, 1, "opening the menu warms the kernel");
  assert.ok($(".ag-hero"), "warm-up keeps the hero");
  assert.deepEqual(tabs(), ["新会话"]);
  const menu = $(".ag-model-menu");
  assert.deepEqual(
    $$(".ag-model-menu .ag-menu-item-label").map((item) => item.textContent),
    ["deepseek-v4-flash", "deepseek-v4-pro"],
  );
  assert.doesNotMatch(menu.textContent, /x$|倍|折扣|自定义/);
  assert.equal($(".ag-model-menu .ag-menu-label"), null, "single provider has no group label");

  const pro = $$(".ag-model-menu .ag-menu-item").find((item) => /v4-pro/.test(item.textContent));
  await act(async () =>
    pro.dispatchEvent(new dom.window.MouseEvent("mouseover", { bubbles: true })),
  );
  assert.match($(".ag-flyout").textContent, /适合复杂工程推理/);
  assert.match($(".ag-flyout").textContent, /提供方DeepSeek/);

  await click(button("模型设置"));
  const settings = $(".ag-flyout.is-settings");
  assert.match(settings.textContent, /思考模式/);
  assert.equal(settings.querySelector('[role="switch"]').getAttribute("aria-checked"), "true");
  const low = [...settings.querySelectorAll(".ag-menu-item")].find((item) =>
    /低/.test(item.textContent),
  );
  await click(low);
  await flush();
  assert.deepEqual(api.posts("/api/agent/config").at(-1), {
    session: "s1",
    config: "reasoning_effort",
    value: "low",
  });
  assert.equal(JSON.parse(localStorage.getItem("texsys.agent.modelPrefs")).reasoning, "low");

  // A brand-new session adopts the remembered thinking level before its first prompt.
  await click(button("新建会话"));
  assert.deepEqual(tabs(), ["新会话"], "the blank warm tab is reused");
  await act(async () =>
    document.dispatchEvent(new dom.window.KeyboardEvent("keydown", { key: "Escape" })),
  );
});

test("tabs, history resume and delete keep each conversation separate", async (t) => {
  const api = backend({
    history: [
      { id: "h1", title: "核算冷却塔", created_at: NOW - 60, updated_at: NOW - 60, live: false },
      {
        id: "h2",
        title: "旧的拓扑讨论",
        created_at: NOW - 86400 * 40,
        updated_at: NOW - 86400 * 40,
        live: false,
      },
    ],
  });
  reset();
  await mount(t);
  await flush();
  await type("第一个会话");
  await key("Enter");
  await flush(10);
  await click(button("新建会话"));
  assert.deepEqual(tabs(), ["第一个会话", "新会话"]);
  assert.ok($(".ag-hero"));

  await click(button("对话历史"));
  await flush();
  const picker = $(".ag-history");
  assert.match(picker.textContent, /对话历史/);
  assert.deepEqual(
    $$(".ag-history .ag-menu-label").map((label) => label.textContent),
    ["今天", "更早"],
  );
  const row = $$(".ag-history .ag-menu-item").find((item) => /核算冷却塔/.test(item.textContent));
  await click(row);
  await flush(10);
  assert.deepEqual(api.posts("/api/agent/resume"), [{ session: "h1", workspace: "project" }]);
  assert.deepEqual(tabs(), ["第一个会话", "核算冷却塔"], "resume reuses the blank tab");
  assert.match(text(), /旧回答/);
  assert.ok($(".ag-history-divider"));

  // Delete needs confirmation.
  await click(button("对话历史"));
  await flush();
  const target = $$(".ag-history-row").find((item) => /旧的拓扑讨论/.test(item.textContent));
  await click(target.querySelector(".ag-history-delete"));
  assert.match($(".ag-history-confirm").textContent, /删除此记录？/);
  await click($(".ag-history-confirm .is-danger"));
  await flush();
  assert.deepEqual(api.posts("/api/agent/history/delete"), [{ session: "h2" }]);

  // Closing a tab ends its kernel process.
  const first = $$('[role="tab"]')[0];
  await click(first.querySelector(".ag-tab-close"));
  await flush();
  assert.deepEqual(api.posts("/api/agent/close"), [{ session: "s1" }]);
  assert.deepEqual(tabs(), ["核算冷却塔"]);
});

test("empty workspace explains isolation and a missing kernel disables the composer", async (t) => {
  backend({ available: false });
  reset();
  await mount(t, { workspace: "empty" });
  await flush();
  assert.match(text(), /当前未打开项目/);
  assert.match(text(), /DeepSeek Harness 不可用/);
  assert.match(text(), /npm install -g @deepseek-ai\/dsh/);
  assert.equal($(".ag-input").disabled, true);
  assert.equal($(".ag-chip"), null);
  assert.equal($(".ag-input").placeholder, "提问或讨论方案，/ 使用命令");
});
