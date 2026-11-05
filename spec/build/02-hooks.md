# Hook Runner 协议

> 状态：有效。当前 protocol_version 为 `1`。架构依据见 ADR-0015。

## 声明

Hook 必须在项目清单中显式绑定 target 和 phase：

```toml
[[build.hooks]]
id = "hvac.check-bom"
target = "hvac:equipment-bom"
phase = "post"
command = ["hvac-check-bom"]
inputs = ["artifact:bom-data"]
outputs = ["build/hvac/bom-check.json"]
timeout_seconds = 60
tool_version = "0.1.0"
closure = "authoritative"
environment = []
max_output_bytes = 1000000
```

`command` 是参数数组，不经过 shell。`tool_version` 是项目锁定的脚本、entry point 或外部工具版本。`environment` 是额外允许继承的环境变量名称；Runner 不无条件继承完整环境。固定变量包括 `TZ=UTC`、`LANG=C.UTF-8`、`LC_ALL=C.UTF-8`、`PYTHONUTF8=1` 和 `PYTHONHASHSEED=0`。

输入使用 `artifact:<id>` 或 `input:<path>`。artifact 输入只适用于 post，并且必须来自当前 target 已经声明的产物。输出必须是 build 内的项目相对路径；pre 输出只能位于 target scratch。输出路径不得覆盖已有 artifact。

同一 target 的 pre 和 post 分别按声明顺序串行执行。权威 pre 未通过时跳过主步骤；主步骤未通过时跳过普通 post。权威 post 的状态参与 target 合并。

## 上下文

Runner 为每次执行创建独立 scratch，删除旧结果后写入 `context.json`，并设置：

- `TEXSYS_HOOK_CONTEXT`：上下文路径；
- `TEXSYS_HOOK_RESULT`：结构化结果路径；
- `TMPDIR`：本次 hook 的临时目录。

上下文包含 protocol_version、项目根、target、profile、scope、hook ID／phase、已解析输入及摘要、可消费 artifact、声明输出和 scratch。绝对路径只用于本次进程通信，不进入要求确定的权威证据。

## 结果

Hook 必须写出 JSON result：

```json
{
  "status": "pass",
  "diagnostics": [],
  "outputs": ["build/hvac/bom-check.json"],
  "open_items": []
}
```

`outputs` 必须与声明路径及顺序完全一致，每个文件必须实际存在。stdout 和 stderr 分别保存在 scratch 日志中，不替代结构化诊断。命令无法启动、超时、日志超限、结果缺失、协议无效、越界或缺失输出均为 `error`。非零退出没有有效 result 时也为 error；存在有效 result 时按其领域状态处理。

## 权威性与修改边界

`closure = "authoritative"` 表示 hook 是 target 的必要闭环依据，输出默认为权威产物。`closure = "diagnostic"` 表示非密闭附加诊断，输出标记为 diagnostic，hook 状态不改变权威 target 的通过状态。

Runner 在每次执行前后检查源输入、assets、项目清单、依赖锁和已有 artifact 摘要。hook 修改这些权威对象时返回 error。需要修复或规范化权威产物的脚本必须进入主推导管线，不能作为不可见 post 副作用。

`build.json` 记录 hook ID、phase、顺序、声明来源、命令及摘要、工具版本、协议版本、密闭性、输入输出摘要、状态、诊断和开放项。日志、PID 和耗时不进入权威证据。

Hook 是受信任的项目构建代码。本协议提高可复现性并减少意外副作用，**不是恶意代码安全沙箱**；不可信项目需要独立的操作系统级隔离。
