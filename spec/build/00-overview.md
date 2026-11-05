# 构建 target 契约

> 状态：有效。本文定义当前可执行的 target 选择、结果和证据子集。架构依据见 ADR-0011、ADR-0012、ADR-0013。

特定交付物契约见 [Target BOM](01-target-bom.md)、[Hook Runner](02-hooks.md) 和 [HVAC 系统图／原理图](03-system-schematic.md)。

## 选择构建目标

`texsys build` 支持重复的 `--target`、单个 `--profile` 和 JSON 对象形式的 `--scope`：

```bash
uv run texsys build -p examples/hvac-cold-station --target hvac:equipment-bom --profile feasibility --scope '{"include":"all"}'
```

未指定 `--target` 时，依次使用：

1. `build.default_targets`；
2. `disciplines.<name>.current_stage` 对应的 `build.stage_targets`；
3. 仍无法确定时拒绝构建。

```toml
[build]
default_targets = ["hvac:equipment-bom"]

[build.profiles]
"hvac:equipment-bom" = "feasibility"

[build.scopes]
"hvac:equipment-bom" = { include = "all" }

[build.stage_targets]
"hvac:concept" = ["hvac:equipment-bom"]
```

CLI 的 profile 和 scope 覆盖清单值。target 声明自己的 profile 集合、默认 profile、默认 scope 和直接依赖。解析器按依赖拓扑顺序执行，同一 target 每次构建只执行一次，未知 target、缺失 profile、非法 profile 和依赖环均显式失败。当前尚未执行 ADR-0012 的跨专业 `interface_bindings`，不得把直接 target 依赖当作跨专业接口契约。

## TargetResult 与产物

领域包通过 `TargetDefinition` 注册 target，builder 返回 `TargetResult`：

- `status`：`pass`、`fail`、`indeterminate` 或 `error`；
- `artifacts`：稳定 artifact ID、项目相对路径和 `authoritative`／`diagnostic` 类型；
- `diagnostics`：结构化诊断；
- `open_items`：开放项及影响；
- `details`：target 特定的确定性摘要。

产物必须位于项目 build 目录并实际存在。artifact ID 在同一 target 内唯一，路径本身不能代替稳定 ID。target 未闭环时，已生成产物标记为 `partial`。

## build.json v2

构建证据至少包含：

- 请求 target 和解析后的 target、profile、scope、解析来源及依赖；
- 实际执行的包名称和版本；
- 源输入路径与 SHA-256 摘要；
- 每个 target 的状态、诊断、开放项、详情和 hook 证据；
- 每个产物的 target、artifact ID、类型、路径、摘要及 partial 状态。

证据不包含开始时间、结束时间、进程 ID 或运行耗时。权威输入、依赖锁和后端版本不变时，权威产物和证据应保持确定性。构建 `pass` 只表示所选 target 及 profile、scope 闭环，不表示整个项目、采购、现场验收或现实运维已经完成。
