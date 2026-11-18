import test from "node:test";
import assert from "node:assert/strict";
import Module from "node:module";
import path from "node:path";
import esbuild from "esbuild-wasm";

function load(source) {
  const filename = path.resolve(source);
  const result = esbuild.buildSync({
    entryPoints: [filename],
    bundle: true,
    write: false,
    platform: "node",
    format: "cjs",
    packages: "external",
    loader: { ".css": "empty" },
  });
  const module = new Module(filename);
  module.paths = Module._nodeModulePaths(process.cwd());
  module._compile(result.outputFiles[0].text, filename);
  return module.exports;
}

const {
  applyAgentEvents,
  cleanModelName,
  cleanProviderName,
  configChoices,
  contextWindowConfigOption,
  diffStats,
  emptyTranscript,
  formatContextTokens,
  formatThinkingLabel,
  inferModelContextTokens,
  lineDiff,
  mergeModelGroups,
  modelCatalog,
  nonModelConfigOptions,
  presentTool,
  projectPath,
  reasoningConfigOption,
} = load("src/texsys/assets/studio/agent-model.ts");

const root = "/workspace/project";
const update = (seq, payload) => ({ seq, type: "update", update: payload });

test("message and thought chunks merge by message while turns stay separate", () => {
  const transcript = applyAgentEvents(
    emptyTranscript(),
    [
      { seq: 1, type: "state", state: "ready", error: "" },
      { seq: 2, type: "user", text: "检查机柜", context: ["src/room.json"] },
      update(3, {
        sessionUpdate: "agent_thought_chunk",
        messageId: "a",
        content: { type: "text", text: "思考" },
      }),
      update(4, {
        sessionUpdate: "agent_message_chunk",
        messageId: "b",
        content: { type: "text", text: "Hello " },
      }),
      update(5, {
        sessionUpdate: "agent_message_chunk",
        messageId: "b",
        content: { type: "text", text: "world" },
      }),
      update(6, {
        sessionUpdate: "agent_message_chunk",
        messageId: "c",
        content: { type: "text", text: "Next" },
      }),
      { seq: 7, type: "turn_end", stopReason: "end_turn" },
    ],
    root,
  );
  assert.deepEqual(
    transcript.items.map((item) => [item.kind, item.text ?? item.stopReason]),
    [
      ["user", "检查机柜"],
      ["thought", "思考"],
      ["agent", "Hello world"],
      ["agent", "Next"],
      ["turn", "end_turn"],
    ],
  );
  assert.deepEqual(transcript.items[0].context, ["src/room.json"]);
  assert.equal(transcript.state, "ready");
});

test("events are idempotent by sequence and a reset rebuilds from the log", () => {
  const events = [
    update(1, { sessionUpdate: "agent_message_chunk", content: { type: "text", text: "A" } }),
  ];
  const once = applyAgentEvents(emptyTranscript(), events, root);
  const twice = applyAgentEvents(once, events, root);
  assert.equal(twice.items[0].text, "A");
  const reset = applyAgentEvents(
    twice,
    [update(5, { sessionUpdate: "agent_message_chunk", content: { type: "text", text: "B" } })],
    root,
    true,
  );
  assert.deepEqual(
    reset.items.map((item) => item.text),
    ["B"],
  );
});

test("tool calls merge updates and record changed project files", () => {
  const transcript = applyAgentEvents(
    emptyTranscript(),
    [
      update(1, {
        sessionUpdate: "tool_call",
        toolCallId: "t1",
        title: "edit",
        kind: "other",
        status: "in_progress",
        rawInput: { file_path: `${root}/src/rack.json`, old_string: "a\n", new_string: "b\n" },
      }),
      update(2, { sessionUpdate: "tool_call_update", toolCallId: "t1", status: "completed" }),
      update(3, {
        sessionUpdate: "tool_call",
        toolCallId: "t2",
        title: "Edit outside",
        kind: "edit",
        status: "completed",
        locations: [{ path: "/etc/hosts" }],
        content: [{ type: "diff", path: `${root}/README.md`, oldText: "x", newText: "y" }],
      }),
      {
        seq: 4,
        type: "fs",
        op: "write",
        path: "src/new.json",
        created: true,
        oldText: null,
        newText: "{}",
      },
      { seq: 5, type: "fs", op: "read", path: "src/rack.json" },
    ],
    root,
  );
  const tools = transcript.items.filter((item) => item.kind === "tool");
  assert.equal(tools.length, 2);
  assert.equal(tools[0].status, "completed");
  assert.deepEqual(transcript.changed, ["src/rack.json", "README.md", "src/new.json"]);
  assert.ok(transcript.fileChanges >= 3);
  const view = presentTool(tools[0], root);
  assert.equal(view.path, "src/rack.json");
  assert.equal(view.icon, "edit");
  assert.deepEqual(view.diffs, [
    { path: "src/rack.json", oldText: "a\n", newText: "b\n", partial: true },
  ]);
  assert.equal(presentTool(tools[1], root).diffs[0].path, "README.md");
});

test("shell tools present their command and terminal output is tracked", () => {
  const transcript = applyAgentEvents(
    emptyTranscript(),
    [
      update(1, {
        sessionUpdate: "tool_call",
        toolCallId: "b",
        title: "bash",
        kind: "other",
        status: "completed",
        rawInput: { command: "uv run texsys build", workdir: root },
        content: [{ type: "terminal", terminalId: "term-1" }],
      }),
      {
        seq: 2,
        type: "terminal",
        terminalId: "term-1",
        command: "uv run texsys build",
        cwd: ".",
        output: "",
        truncated: false,
      },
      {
        seq: 3,
        type: "terminal",
        terminalId: "term-1",
        output: "ok\n",
        truncated: false,
        exitStatus: { exitCode: 0, signal: null },
      },
    ],
    root,
  );
  const view = presentTool(transcript.items[0], root);
  assert.equal(view.icon, "terminal");
  assert.equal(view.command, "uv run texsys build");
  assert.deepEqual(view.terminals, ["term-1"]);
  assert.equal(transcript.terminals["term-1"].output, "ok\n");
  assert.equal(transcript.terminals["term-1"].command, "uv run texsys build");
  assert.equal(transcript.terminals["term-1"].exitStatus.exitCode, 0);
});

test("permissions settle, plans replace within a turn and session options update", () => {
  const transcript = applyAgentEvents(
    emptyTranscript(),
    [
      {
        seq: 1,
        type: "configured",
        configOptions: [
          {
            id: "model",
            name: "Model",
            type: "select",
            currentValue: "m2",
            options: [
              {
                group: "g",
                name: "G",
                options: [
                  { value: "m1", name: "One" },
                  { value: "m2", name: "Two" },
                ],
              },
            ],
          },
        ],
        modes: {
          currentModeId: "ask",
          availableModes: [
            { id: "ask", name: "Ask" },
            { id: "code", name: "Code" },
          ],
        },
      },
      { seq: 2, type: "user", text: "go", context: [] },
      update(3, { sessionUpdate: "plan", entries: [{ content: "a", status: "pending" }] }),
      update(4, { sessionUpdate: "plan", entries: [{ content: "a", status: "completed" }] }),
      {
        seq: 5,
        type: "permission",
        key: "k1",
        toolCall: { title: "Write" },
        options: [{ optionId: "ok", name: "Allow", kind: "allow_once" }],
      },
      { seq: 6, type: "permission_resolved", key: "k1", optionId: "ok" },
      update(7, { sessionUpdate: "current_mode_update", currentModeId: "code" }),
      update(8, {
        sessionUpdate: "available_commands_update",
        availableCommands: [{ name: "review" }],
      }),
      update(9, { sessionUpdate: "usage_update", used: 50, size: 1000 }),
    ],
    root,
  );
  const plans = transcript.items.filter((item) => item.kind === "plan");
  assert.equal(plans.length, 1);
  assert.equal(plans[0].entries[0].status, "completed");
  const permission = transcript.items.find((item) => item.kind === "permission");
  assert.equal(permission.settled, true);
  assert.equal(permission.optionId, "ok");
  assert.equal(transcript.modes.currentModeId, "code");
  assert.deepEqual(transcript.commands, [{ name: "review" }]);
  assert.deepEqual(transcript.usage, { used: 50, size: 1000 });
  assert.deepEqual(
    configChoices(transcript.configOptions[0]).map((choice) => choice.value),
    ["m1", "m2"],
  );
});

test("failures surface as notices", () => {
  const transcript = applyAgentEvents(
    emptyTranscript(),
    [{ seq: 1, type: "state", state: "exited", error: "智能体进程已退出（退出码 3）。" }],
    root,
  );
  assert.equal(transcript.items[0].kind, "notice");
  assert.match(transcript.items[0].text, /退出码 3/);
});

test("line diff keeps changed lines with collapsed context", () => {
  const before = Array.from({ length: 12 }, (_, index) => `line ${index}`).join("\n");
  const after = before.replace("line 6", "line six");
  const diff = lineDiff(before, after);
  assert.deepEqual(diffStats(diff), { added: 1, removed: 1 });
  assert.equal(diff[0].type, "gap");
  assert.ok(diff.some((line) => line.type === "add" && line.text === "line six"));
  assert.deepEqual(diffStats(lineDiff("", "a\nb\n")), { added: 2, removed: 0 });
});

test("project paths stay inside the project root", () => {
  assert.equal(projectPath(`${root}/src/a.json`, root), "src/a.json");
  assert.equal(projectPath(`${root}-other/a.json`, root), undefined);
  assert.equal(projectPath(root, root), undefined);
  assert.equal(projectPath("./src/b.json", root), "src/b.json");
});

test("rawOutput falls back cleanly for agents that do not emit content blocks", () => {
  const transcript = applyAgentEvents(
    emptyTranscript(),
    [
      update(1, {
        sessionUpdate: "tool_call",
        toolCallId: "dsh-1",
        title: "bash",
        kind: "other",
        status: "completed",
        rawInput: { command: "pwd; ls -la" },
        rawOutput: { stdout: "/workspace/project\n", stderr: "" },
        content: [],
      }),
    ],
    root,
  );
  const view = presentTool(transcript.items[0], root);
  assert.equal(view.command, "pwd; ls -la");
  assert.deepEqual(view.text, ["/workspace/project\n"]);
});

test("modelCatalog normalizes both ACP configOptions and ACP models", () => {
  const fromConfig = applyAgentEvents(
    emptyTranscript(),
    [
      {
        seq: 1,
        type: "configured",
        configOptions: [
          {
            id: "model",
            name: "Model",
            type: "select",
            currentValue: "deepseek-v4-flash",
            options: [{ value: "deepseek-v4-flash", name: "deepseek-v4-flash" }],
          },
          {
            id: "effort",
            name: "Effort",
            type: "select",
            currentValue: "high",
            options: [{ value: "high", name: "High" }],
          },
        ],
      },
    ],
    root,
  );
  assert.equal(modelCatalog(fromConfig)?.source, "config");
  assert.equal(modelCatalog(fromConfig)?.currentLabel, "deepseek-v4-flash");
  assert.equal(nonModelConfigOptions(fromConfig).length, 1);

  const fromModels = applyAgentEvents(
    emptyTranscript(),
    [
      {
        seq: 1,
        type: "configured",
        configOptions: [],
        models: {
          currentModelId: "sonnet",
          availableModels: [
            { modelId: "sonnet", name: "Claude Sonnet" },
            { modelId: "opus", name: "Claude Opus" },
          ],
        },
      },
      update(2, { sessionUpdate: "current_model_update", currentModelId: "opus" }),
    ],
    root,
  );
  assert.equal(modelCatalog(fromModels)?.source, "models");
  assert.equal(modelCatalog(fromModels)?.currentLabel, "Claude Opus");
  assert.ok(fromModels.debugLog.length >= 2);
});

test("modelCatalog preserves stacked Provider groups, strips verbose harness suffixes, and formats slider labels", () => {
  assert.equal(cleanProviderName("DeepSeek Harness"), "DeepSeek");
  assert.equal(cleanProviderName("Antigravity (ACP)"), "Antigravity");
  assert.deepEqual(cleanModelName("deepseek-v4-flash · DeepSeek Harness"), {
    name: "deepseek-v4-flash",
    inferredProvider: "DeepSeek",
  });
  assert.deepEqual(
    cleanModelName(
      "claude-opus-5-5-medium · Antigravity (ACP)",
      '["anthropic","claude-opus-5-5-medium"]',
    ),
    {
      name: "claude-opus-5-5-medium",
      inferredProvider: "anthropic",
    },
  );
  assert.deepEqual(cleanModelName("omlx/qwen3.8-27b-huihui"), {
    name: "qwen3.8-27b-huihui",
    inferredProvider: "omlx",
  });

  const groupedTranscript = applyAgentEvents(
    emptyTranscript(),
    [
      {
        seq: 1,
        type: "configured",
        configOptions: [
          {
            id: "model",
            name: "Model",
            category: "model",
            type: "select",
            currentValue: '["deepseek","DeepSeek-V4.1-Flash"]',
            options: [
              {
                group: "deepseek",
                name: "DeepSeek Harness",
                options: [
                  {
                    value: '["deepseek","DeepSeek-V4.1-Flash"]',
                    name: "DeepSeek-V4.1-Flash · DeepSeek Harness",
                  },
                  {
                    value: '["deepseek","DeepSeek-V4-Pro"]',
                    name: "DeepSeek-V4-Pro · DeepSeek Harness",
                  },
                ],
              },
              {
                group: "kimi-coding",
                name: "kimi-coding",
                options: [
                  { value: '["kimi-coding","Kimi K3"]', name: "Kimi K3" },
                  { value: '["kimi-coding","Kimi K3-256K"]', name: "Kimi K3-256K" },
                ],
              },
            ],
          },
          {
            id: "reasoning_effort",
            name: "Reasoning effort",
            category: "thought_level",
            type: "select",
            currentValue: "high",
            options: [
              { value: "off", name: "Off" },
              { value: "low", name: "Low" },
              { value: "high", name: "High" },
              { value: "max", name: "Max" },
            ],
          },
          {
            id: "context_window",
            name: "Context window",
            category: "context_window",
            type: "select",
            currentValue: "200000",
            options: [
              { value: "64000", name: "64K" },
              { value: "200000", name: "200K" },
              { value: "1000000", name: "1M" },
            ],
          },
        ],
      },
    ],
    root,
  );

  const catalog = modelCatalog(groupedTranscript, "DeepSeek Harness", "deepseek-harness");
  assert.equal(catalog?.currentLabel, "DeepSeek-V4.1-Flash");
  assert.deepEqual(
    catalog?.groups.map((group) => [group.label, group.options.map((opt) => opt.name)]),
    [
      ["DeepSeek", ["DeepSeek-V4.1-Flash", "DeepSeek-V4-Pro"]],
      ["kimi-coding", ["Kimi K3", "Kimi K3-256K"]],
    ],
  );
  assert.equal(reasoningConfigOption(groupedTranscript)?.id, "reasoning_effort");
  assert.equal(contextWindowConfigOption(groupedTranscript)?.id, "context_window");

  const stacked = mergeModelGroups(catalog?.groups, [
    {
      key: "deepseek",
      label: "DeepSeek",
      options: [
        {
          value: "dup",
          name: "DeepSeek-V4-Pro",
          group: "DeepSeek",
          groupKey: "deepseek",
          agentId: "other",
        },
      ],
    },
    {
      key: "omlx",
      label: "omlx",
      options: [
        {
          value: "qwen",
          name: "qwen3.8-27b-huihui",
          group: "omlx",
          groupKey: "omlx",
          agentId: "other",
        },
      ],
    },
  ]);
  assert.deepEqual(
    stacked.map((group) => [group.label, group.options.map((opt) => opt.name)]),
    [
      ["DeepSeek", ["DeepSeek-V4.1-Flash", "DeepSeek-V4-Pro"]],
      ["kimi-coding", ["Kimi K3", "Kimi K3-256K"]],
      ["omlx", ["qwen3.8-27b-huihui"]],
    ],
  );

  assert.equal(formatThinkingLabel("High", "zh-CN"), "高");
  assert.equal(formatThinkingLabel("Off", "zh-CN"), "关闭");
  assert.equal(formatThinkingLabel("high", "en"), "High");
  assert.equal(formatContextTokens(200000), "200K");
  assert.equal(formatContextTokens(262144), "256K");
  assert.equal(formatContextTokens(1000000), "1M");
  assert.equal(inferModelContextTokens("Kimi K3-256K", undefined), "256K");
  assert.equal(inferModelContextTokens("deepseek-v4-flash", 1000000), "1M");
});
