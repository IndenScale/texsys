# 构建 target 契约

> 状态：有效。本文定义当前可执行的 target 选择、结果和证据子集。架构依据见 ADR-0011、ADR-0012、ADR-0013 和 [ADR-0027](../../docs/adr/0027-incremental-build-and-project-preview.md)。

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
- `artifacts`：稳定 artifact ID、项目相对路径、`authoritative`／`diagnostic` 类型、完整性及可选预览描述；
- `diagnostics`：结构化诊断；
- `open_items`：开放项及影响；
- `details`：target 特定的确定性摘要。

`status` 表达所选目标契约的判定。模型编译、图纸投影或预期反例回归的 `pass` 不能提升为工程满足；只有实际工程检查目标才可据其声明范围解释工程结论。Studio 通用构建预览显示“目标判定／通过”，具体物理判据由对应报告表达。

产物必须位于项目 build 目录并实际存在。artifact ID 在同一 target 内唯一，路径本身不能代替稳定 ID。`complete` 表达产物完整性；工程判定为 `fail` 时，图纸、场景或检查报告仍可能完整。生产者对未解析对象、缺失几何等结构缺口显式标注不完整及诊断，不能通过成功序列化掩盖缺失。

## 增量执行与失败分支

目标按依赖图执行，前置目标未满足契约时记录 `blocked` 和阻断原因，不执行该目标；其他独立目标继续执行。目标的 `execution` 区分 `executed`、`cached` 和 `blocked`，`status` 仍表示契约判定。项目检查结果纳入总体状态，可判定的工程不满足不自动阻断所有无关产物的生成。无法读取的项目清单、失效锁和不安全路径仍拒绝构建。

每个目标的缓存依据实现身份、运行环境、profile、scope、声明输入及上游产物摘要。没有声明输入范围时保守使用完整输入集；声明范围需要包含该目标真实读取的源与资产，不得省略隐式依赖。目标实现或输入变化使缓存失效。缓存命中校验内容，并恢复缺失的原始输出。完整且可重现的失败判定可以复用；异常、输入漂移或无法固定外部依赖的钩子不能作为成功缓存。

`TargetDefinition.input_patterns` 可以是项目相对路径模式元组，或根据 `BuildContext` 返回模式的函数；未声明时为 `None`。`cache` 可以显式关闭复用，或按上下文返回是否可缓存。`cache_environment` 声明影响结果的环境变量名，缓存记录其值的摘要，不存储原始值。工具链记录 Python、平台、发行包版本及实际实现文件身份；依赖外部求解器或其他未固定状态的目标须补齐输入契约或关闭缓存，不能仅因命令相同便认为可复用。

## build.json v3

构建证据至少包含：

- 请求 target 和解析后的 target、profile、scope、解析来源及依赖；
- 实际执行的包名称和版本；
- 源输入路径与 SHA-256 摘要；
- 每个 target 的执行状态、工程判定、缓存身份、阻断原因、诊断、开放项、详情和 hook 证据；
- 每个产物的 target、artifact ID、类型、原始路径、快照路径、摘要、完整性及预览描述；
- 本次 `run_id`、输入与工具身份摘要、输入一致性。

每次构建在 `build/runs/<run_id>/` 保存独立运行清单与输入、产物快照，`build/build.json` 是最新运行入口。运行身份与缓存命中情况允许不同；相同输入和工具下的产物内容应保持可重复。构建 `pass` 只表示所选 target 及 profile、scope 闭环，不表示整个项目、采购、现场验收或现实运维已经完成。

`build/` 可以从空目录或缺失状态重建。构建前后核对清单、锁文件、源文件、资产及锁定依赖的内容。若执行期间检测到输入变化，构建报告 `E_INPUT_CHANGED`，本次结果不作为当前输入的成功缓存。CLI 构建读取工作目录，需保持输入稳定。后台 Studio 任务使用固定输入副本。

## 文件预览

产物可声明 `preview` 元数据，标明可用的渲染格式和源对象映射。执行状态、工程判定、完整性、输入适用性和历史来源继续保存在构建记录中；文件编辑器不为这些信息创建专用结果页面或状态栏。

按 [ADR-0028](../../docs/adr/0028-file-based-artifact-preview.md)，产物使用实际文件路径打开独立文档，和普通文件共用编辑、保存、重命名与“预览 / 源代码”布局。Markdown、图纸和场景只替换预览内容的渲染实现；普通 JSON 使用通用代码编辑器。预览读取所选文件的实际内容，不能用另一产物或历史快照替代。生成文件手工修改不会反向写回设计声明，后续 build 可以依据源文件重建覆盖；不可变历史快照仍保留在内部运行目录。
