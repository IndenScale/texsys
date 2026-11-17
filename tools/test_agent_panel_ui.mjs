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
  "HTMLTextAreaElement",
  "Node",
  "Event",
  "KeyboardEvent",
  "localStorage",
  "sessionStorage",
  "requestAnimationFrame",
  "cancelAnimationFrame",
])
  globalThis[name] = dom.window[name];
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
const { RightPanel } = loadStudioModule("AgentPanel.tsx");

const ROOT = "/workspace/project";

/** In-memory Studio agent API with a long-poll event log. */
function backend() {
  const state = {
    requests: [],
    events: [],
    waiters: [],
    session: undefined,
  };
  const agents = [
    {
      id: "deepseek-harness",
      name: "DeepSeek Harness",
      description: "dsh",
      command: "dsh --profile acp",
      source: "builtin",
      available: true,
      reason: "",
      env: ["DEEPSEEK_API_KEY"],
    },
    {
      id: "codex",
      name: "Codex",
      description: "",
      command: "codex-acp",
      source: "builtin",
      available: false,
      reason: "未找到命令 codex-acp。",
      env: [],
    },
  ];
  const summary = (status) => ({
    id: "s1",
    agent: "deepseek-harness",
    agent_name: "DeepSeek Harness",
    agent_version: "0.0.1",
    title: "",
    state: status,
    error: "",
    created_at: 1,
    auth_methods: [],
    capabilities: {},
  });
  state.emit = (...events) => {
    for (const event of events) state.events.push({ seq: state.events.length + 1, ...event });
    if (events.some((event) => event.type === "state"))
      state.session = summary(events.findLast((event) => event.type === "state").state);
    for (const wake of state.waiters.splice(0)) wake();
  };
  const json = (data, status = 200) => ({ ok: status < 400, status, json: async () => data });
  globalThis.fetch = async (url, options = {}) => {
    const body = options.body ? JSON.parse(options.body) : undefined;
    state.requests.push([url, body]);
    const parsed = new URL(url, "http://localhost");
    if (parsed.pathname === "/api/agent")
      return json({
        agents,
        sessions: state.session ? [state.session] : [],
        config_path: "~/.config/texsys/studio-agents.json",
        diagnostics: [],
        root: ROOT,
      });
    if (parsed.pathname === "/api/agent/sessions") {
      state.emit({ type: "state", state: "starting", error: "" });
      return json(summary("starting"));
    }
    if (parsed.pathname === "/api/agent/events") {
      const after = Number(parsed.searchParams.get("after"));
      if (state.events.length <= after)
        await new Promise((resolve, reject) => {
          state.waiters.push(resolve);
          options.signal?.addEventListener("abort", () => reject(new Error("aborted")));
        });
      return json({
        session: state.session,
        events: state.events.filter((event) => event.seq > after),
        seq: state.events.length,
        reset: false,
      });
    }
    if (parsed.pathname === "/api/agent/prompt") {
      state.emit(
        { type: "user", text: body.text, context: body.context },
        { type: "state", state: "prompting", error: "" },
      );
      return json(summary("prompting"));
    }
    return json({ ok: true });
  };
  return state;
}

const flush = async () => {
  for (let index = 0; index < 5; index += 1)
    await act(async () => new Promise((resolve) => setTimeout(resolve, 5)));
};

async function mount(t, props = {}) {
  document.body.innerHTML = '<div id="app"></div>';
  const root = createRoot(document.getElementById("app"));
  const opened = [];
  let view = props.view ?? "agent";
  const render = () =>
    root.render(
      React.createElement(RightPanel, {
        view,
        changeView: (next) => {
          view = next;
          render();
        },
        properties: React.createElement("p", null, "文件属性内容"),
        agent: {
          enabled: props.enabled ?? true,
          visible: true,
          activeFile: "src/room.json",
          openFile: (file, line) => opened.push([file, line]),
          reviewChanges: () => opened.push(["scm"]),
          openProject: () => opened.push(["project"]),
        },
      }),
    );
  await act(async () => render());
  t.after(async () => act(async () => root.unmount()));
  return {
    opened,
    get view() {
      return view;
    },
  };
}

const text = () => document.body.textContent;
const button = (label) =>
  [...document.querySelectorAll("button")].find(
    (item) => item.textContent.includes(label) || item.getAttribute("aria-label") === label,
  );
async function type(value) {
  const area = document.querySelector(".agent-composer textarea");
  const setter = Object.getOwnPropertyDescriptor(
    dom.window.HTMLTextAreaElement.prototype,
    "value",
  ).set;
  await act(async () => {
    setter.call(area, value);
    area.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
  });
  return area;
}

test("right panel switches between properties and agent views with tab semantics", async (t) => {
  backend();
  sessionStorage.clear();
  const panel = await mount(t, { view: "properties" });
  const tabs = [...document.querySelectorAll('[role="tab"]')];
  assert.deepEqual(
    tabs.map((tab) => tab.textContent),
    ["属性", "智能体"],
  );
  assert.equal(tabs[0].getAttribute("aria-selected"), "true");
  assert.equal(document.getElementById("right-panel-view-agent").hidden, true);
  await act(async () => tabs[1].click());
  assert.equal(panel.view, "agent");
  assert.equal(document.getElementById("right-panel-view-properties").hidden, true);
});

test("welcome shell explains that the agent needs the started project", async (t) => {
  backend();
  const panel = await mount(t, { enabled: false });
  assert.match(text(), /打开已启动的项目后/);
  await act(async () => button("打开已启动的项目").click());
  assert.deepEqual(panel.opened, [["project"]]);
});

test("first message starts a session, waits for ready and renders the agent turn", async (t) => {
  const server = backend();
  sessionStorage.clear();
  localStorage.clear();
  const panel = await mount(t);
  await flush();
  assert.match(text(), /使用智能体处理工程任务/);
  const catalog = [...document.querySelectorAll(".agent-catalog-item")];
  assert.equal(catalog.length, 2);
  assert.equal(catalog[0].getAttribute("aria-checked"), "true");
  assert.equal(catalog[1].disabled, true);
  assert.match(text(), /studio-agents\.json/);
  assert.match(document.querySelector(".agent-chip").textContent, /room\.json/);

  const area = await type("检查 R01 机柜的净空");
  await act(async () =>
    area.dispatchEvent(new dom.window.KeyboardEvent("keydown", { key: "Enter", bubbles: true })),
  );
  await flush();
  assert.ok(
    server.requests.some(
      ([url, body]) => url === "/api/agent/sessions" && body.agent === "deepseek-harness",
    ),
  );
  assert.ok(
    !server.requests.some(([url]) => url === "/api/agent/prompt"),
    "prompt waits for ready",
  );
  assert.match(text(), /正在启动智能体/);

  await act(async () =>
    server.emit(
      {
        type: "configured",
        configOptions: [
          {
            id: "model",
            name: "Model",
            type: "select",
            currentValue: "flash",
            options: [
              { value: "flash", name: "V4 Flash" },
              { value: "pro", name: "V4 Pro" },
            ],
          },
        ],
      },
      { type: "state", state: "ready", error: "" },
    ),
  );
  await flush();
  const prompt = server.requests.find(([url]) => url === "/api/agent/prompt");
  assert.deepEqual(prompt[1], {
    session: "s1",
    text: "检查 R01 机柜的净空",
    context: ["src/room.json"],
  });

  await act(async () =>
    server.emit(
      {
        type: "update",
        update: {
          sessionUpdate: "agent_thought_chunk",
          content: { type: "text", text: "先读文件" },
        },
      },
      {
        type: "update",
        update: {
          sessionUpdate: "tool_call",
          toolCallId: "t1",
          title: "read",
          kind: "other",
          status: "completed",
          rawInput: { file_path: `${ROOT}/src/room.json` },
          content: [{ type: "content", content: { type: "text", text: "{...}" } }],
        },
      },
      {
        type: "update",
        update: {
          sessionUpdate: "tool_call",
          toolCallId: "t2",
          title: "edit",
          kind: "other",
          status: "completed",
          rawInput: { file_path: `${ROOT}/src/room.json`, old_string: "a", new_string: "b" },
        },
      },
      {
        type: "permission",
        key: "k1",
        toolCall: { title: "运行构建" },
        options: [
          { optionId: "allow", name: "允许一次", kind: "allow_once" },
          { optionId: "reject", name: "拒绝", kind: "reject_once" },
        ],
      },
      {
        type: "update",
        update: {
          sessionUpdate: "agent_message_chunk",
          content: { type: "text", text: "净空**满足**要求。" },
        },
      },
      { type: "update", update: { sessionUpdate: "usage_update", used: 20000, size: 1000000 } },
    ),
  );
  await flush();
  assert.match(text(), /检查 R01 机柜的净空/);
  assert.match(text(), /思考过程/);
  assert.equal(document.querySelectorAll(".agent-tool").length, 2);
  assert.match(document.querySelector(".agent-message").textContent, /净空满足要求/);
  assert.equal(document.querySelector(".agent-message strong")?.textContent, "满足");
  assert.match(text(), /智能体修改了 1 个文件/);
  assert.match(document.querySelector(".agent-usage").textContent, /2%/);
  assert.match(document.querySelector(".agent-composer-bar").textContent, /V4 Flash/);
  assert.ok(document.querySelector(".agent-send.is-stop"), "stop button while prompting");

  await act(async () => document.querySelector(".agent-tool .agent-link-button").click());
  assert.deepEqual(panel.opened.at(-1), ["src/room.json", undefined]);

  await act(async () => button("允许一次").click());
  assert.ok(
    server.requests.some(
      ([url, body]) =>
        url === "/api/agent/permission" && body.key === "k1" && body.option === "allow",
    ),
  );
  await act(async () => document.querySelector(".agent-send.is-stop").click());
  assert.ok(
    server.requests.some(([url, body]) => url === "/api/agent/cancel" && body.session === "s1"),
  );

  await act(async () =>
    server.emit(
      { type: "permission_resolved", key: "k1", optionId: "allow" },
      { type: "turn_end", stopReason: "cancelled" },
      { type: "state", state: "ready", error: "" },
    ),
  );
  await flush();
  assert.match(text(), /已选择：允许一次/);
  assert.match(text(), /已停止本轮处理/);
  assert.equal(document.querySelector(".agent-send.is-stop"), null);
  await act(async () => button("审查修改").click());
  assert.deepEqual(panel.opened.at(-1), ["scm"]);
});
