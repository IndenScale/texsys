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
const { AgentPanel } = loadStudioModule("AgentPanel.tsx");

const ROOT = "/workspace/project";
const SCRATCH = "/tmp/agent-scratch";

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
  const summary = (status, agentId = "deepseek-harness") => ({
    id: "s1",
    agent: agentId,
    agent_name: agentId === "codex" ? "Codex" : "DeepSeek Harness",
    agent_version: "0.0.1",
    command: "dsh --profile acp",
    cwd: ROOT,
    title: "",
    state: status,
    error: "",
    created_at: 1,
    seq: state.events.length,
    stderr: "",
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
    if (parsed.pathname === "/api/files") {
      return json({ files: ["src/room.json", "src/cooling.json", "README.md"] });
    }
    if (parsed.pathname === "/api/agent") {
      const workspace = parsed.searchParams.get("workspace") ?? "project";
      return json({
        agents,
        sessions: state.session ? [state.session] : [],
        config_path: "~/.config/texsys/studio-agents.json",
        diagnostics: [],
        root: workspace === "empty" ? SCRATCH : ROOT,
        workspace,
      });
    }
    if (parsed.pathname === "/api/agent/sessions") {
      state.emit({ type: "state", state: "starting", error: "" });
      return json(summary("starting", body.agent));
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
  const workspace = props.workspace ?? "project";
  await act(async () =>
    root.render(
      React.createElement(AgentPanel, {
        workspace,
        visible: true,
        maximized: props.maximized ?? false,
        activeFile: workspace === "project" ? "src/room.json" : undefined,
        openFile: workspace === "project" ? (file, line) => opened.push([file, line]) : undefined,
        reviewChanges: workspace === "project" ? () => opened.push(["scm"]) : undefined,
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

test("right panel is dedicated to the agent with VS Code header actions and maximize/close", async (t) => {
  backend();
  sessionStorage.clear();
  localStorage.clear();
  const panel = await mount(t);
  await flush();
  assert.equal(document.querySelectorAll('[role="tab"]').length, 0);
  assert.doesNotMatch(text(), /文件属性/);
  assert.equal(document.querySelector(".navigator-heading strong")?.textContent, "智能体");
  assert.ok(button("新建会话"));
  assert.ok(button("新建会话选项"));
  assert.ok(button("最大化面板大小"));
  await act(async () => button("最大化面板大小").click());
  await act(async () => button("关闭智能体面板").click());
  assert.deepEqual(panel.opened, [["maximize"], ["close"]]);
});

test("empty workspace displays clean VS Code chat shell, harness picker and direct questioning", async (t) => {
  const server = backend();
  sessionStorage.clear();
  localStorage.clear();
  await mount(t, { workspace: "empty" });
  await flush();
  assert.match(text(), /使用智能体提问或探索方案/);
  assert.equal(document.querySelector(".agent-chip"), null);
  assert.equal(document.querySelector(".agent-catalog-item"), null, "no cluttered radio card wall");
  assert.match(document.querySelector(".agent-secondary-toolbar").textContent, /DeepSeek Harness/);
  assert.match(document.querySelector(".agent-secondary-toolbar").textContent, /默认权限/);

  // Open Harness picker in secondary toolbar and inspect "Learn about harnesses..." setup card.
  await act(async () => button("选择智能体").click());
  await flush();
  const harnessRows = [
    ...document.querySelectorAll(".agent-popover-harness [role='menuitemradio']"),
  ];
  assert.equal(harnessRows.length, 2);
  assert.equal(harnessRows[0].getAttribute("aria-checked"), "true");
  assert.equal(harnessRows[1].disabled, true);
  await act(async () => button("了解与配置智能体 Harness…").click());
  await flush();
  assert.match(text(), /studio-agents\.json/);

  const area = await type("你好，防静电地板集中荷载怎么校核？");
  assert.equal(area.disabled, false);
  await act(async () =>
    area.dispatchEvent(new dom.window.KeyboardEvent("keydown", { key: "Enter", bubbles: true })),
  );
  await flush();
  assert.ok(
    server.requests.some(
      ([url, body]) =>
        url === "/api/agent/sessions" &&
        body.agent === "deepseek-harness" &&
        body.workspace === "empty",
    ),
  );
  await act(async () => server.emit({ type: "state", state: "ready", error: "" }));
  await flush();
  const prompt = server.requests.find(([url]) => url === "/api/agent/prompt");
  assert.deepEqual(prompt[1], {
    session: "s1",
    text: "你好，防静电地板集中荷载怎么校核？",
    context: [],
  });
  await act(async () =>
    server.emit(
      {
        type: "update",
        update: {
          sessionUpdate: "agent_message_chunk",
          content: { type: "text", text: "可按 GB 50174 校核单脚集中荷载。" },
        },
      },
      { type: "turn_end", stopReason: "end_turn" },
      { type: "state", state: "ready", error: "" },
    ),
  );
  await flush();
  assert.match(document.querySelector(".agent-message").textContent, /GB 50174/);
});

test("tabbed model picker, context attachment, rawOutput bash expansion, permissions and SCM review work end-to-end", async (t) => {
  const server = backend();
  sessionStorage.clear();
  localStorage.clear();
  const panel = await mount(t);
  await flush();
  assert.match(text(), /使用智能体处理工程任务/);
  assert.match(document.querySelector(".agent-chip").textContent, /room\.json/);

  // Attach an additional project file via "+" Context Picker.
  await act(async () => button("添加上下文").click());
  await flush();
  await act(async () => button("cooling.json").click());
  await flush();
  assert.equal(document.querySelectorAll(".agent-composer .agent-chip").length, 2);

  await type("检查 R01 机柜的净空");
  await act(async () => button("发送").click());
  await flush();
  assert.ok(
    server.requests.some(
      ([url, body]) => url === "/api/agent/sessions" && body.agent === "deepseek-harness",
    ),
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
              { value: "flash", name: "deepseek-v4-flash" },
              { value: "pro", name: "deepseek-v4" },
            ],
          },
          {
            id: "effort",
            name: "Reasoning",
            type: "select",
            currentValue: "high",
            options: [
              { value: "medium", name: "Medium" },
              { value: "high", name: "High" },
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
    context: ["src/room.json", "src/cooling.json"],
  });

  await act(async () =>
    server.emit(
      {
        type: "update",
        update: {
          sessionUpdate: "agent_thought_chunk",
          content: { type: "text", text: "先读文件并执行命令" },
        },
      },
      {
        type: "update",
        update: {
          sessionUpdate: "tool_call",
          toolCallId: "t0",
          title: "bash",
          kind: "other",
          status: "completed",
          rawInput: { command: "pwd; echo '---'; ls -la" },
          rawOutput: { stdout: "/workspace/project\n" },
          content: [],
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
  // All 3 tool cards (including bash with rawOutput) are expandable <details>.
  assert.equal(document.querySelectorAll("details.agent-tool").length, 3);
  assert.match(document.querySelector(".agent-message").textContent, /净空满足要求/);
  assert.equal(document.querySelector(".agent-message strong")?.textContent, "满足");
  assert.match(text(), /智能体修改了 1 个文件/);
  assert.match(document.querySelector(".agent-usage").textContent, /2%/);
  assert.match(document.querySelector(".agent-composer-bar").textContent, /deepseek-v4-flash/);
  assert.match(document.querySelector(".agent-composer-bar").textContent, /High/);
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

  // Open Tabbed Model Picker ("✨") and switch model.
  await act(async () => button("选择模型").click());
  await flush();
  assert.ok(document.querySelector(".agent-popover-model"));
  await act(async () => button("deepseek-v4 · DeepSeek Harness").click());
  await flush();
  assert.ok(
    server.requests.some(
      ([url, body]) =>
        url === "/api/agent/config" && body.config === "model" && body.value === "pro",
    ),
  );

  await act(async () => button("审查修改").click());
  assert.deepEqual(panel.opened.at(-1), ["scm"]);
});
