# 架构决策记录

ADR（Architecture Decision Record）用于记录对项目结构、依赖、接口或长期演化有重要影响的决策，以及作出决策时的背景、取舍和后果。

## 原则

- 一项重要决策对应一份 ADR，避免把多个无关决策捆在一起。
- ADR 记录“为什么这样决定”；规范文档描述“当前如何工作”。
- 已接受的决策需要改变时，新增 ADR 取代旧决策，不悄悄改写历史。
- ADR 应简洁、具体，并记录负面影响、风险和验证要求。

## 状态

- `提议`：仍在讨论，尚未形成约束。
- `已接受`：当前有效，后续设计和实现应遵守。
- `已弃用`：不再适用，但没有被特定 ADR 直接取代。
- `已取代`：由另一份 ADR 取代，必须链接到后继决策。

## 编号与命名

```text
adr/NNNN-short-title.md
```

编号递增且不复用。新文档可从 [模板](TEMPLATE.md) 开始。

## 决策索引

| 编号                                                                     | 决策                                                        | 状态                                   |
| ------------------------------------------------------------------------ | ----------------------------------------------------------- | -------------------------------------- |
| [0001](0001-openusd-native-scene-geometry.md)                            | 采用 OpenUSD 与 USD BRep 作为原生场景和几何技术栈           | 提议（待 spike 门禁）                  |
| [0002](0002-design-time-vs-operation-time.md)                            | 调度属于运行期，不进入 SDL 设计模型                         | 已接受                                 |
| [0003](0003-python-and-typer-toolchain.md)                               | 采用 Python 与 Typer 作为参考实现和 CLI 技术栈              | 已接受（0004 补充）                    |
| [0004](0004-uv-only-python-workflow.md)                                  | 统一使用 uv 管理 Python 环境与命令入口                      | 已接受                                 |
| [0005](0005-executable-examples.md)                                      | 以可执行 examples 统一参考用例与端到端桩数据                | 已接受                                 |
| [0006](0006-design-dependency-management.md)                             | 分离设计依赖的声明、锁定与同步                              | 已接受                                 |
| [0007](0007-formal-checking-and-formatting.md)                           | 以 JSON 交换子集启动形式检查与格式化                        | 已接受                                 |
| [0008](0008-six-stage-sysda-model.md)                                    | 采用六阶段 SysDA 模型并区分三类仿真                         | 已接受                                 |
| [0009](0009-equipment-and-topology-refinement.md)                        | 建立设备与拓扑的分层映射规则                                | 已接受                                 |
| [0010](0010-build-test-sim-and-package-boundaries.md)                    | 划分 build、test、sim 与领域包边界                          | 已接受（0011 补充）                    |
| [0011](0011-stage-scoped-build-targets.md)                               | 采用按阶段解析目标的分阶段构建                              | 已接受（0013 补充）                    |
| [0012](0012-multidisciplinary-interface-contracts.md)                    | 采用接口契约支持多专业协同构建                              | 已接受                                 |
| [0013](0013-target-specific-build-derivations.md)                        | 采用目标特定的构建推导与交付视图                            | 已接受（0014、0015、0016、0017 补充）  |
| [0014](0014-target-bom-and-configuration-baselines.md)                   | 采用目标 BOM 与配置事实快照                                 | 已接受（0016 补充）                    |
| [0015](0015-contract-bound-hook-runner.md)                               | 采用受构建契约约束的 Hook Runner                            | 已接受                                 |
| [0016](0016-compose-bom-from-neutral-kernel-and-discipline-contracts.md) | 以通用 BOM 内核和专业契约组合替代继承                       | 已接受                                 |
| [0017](0017-schematic-projection-symbol-resolution-and-rendering.md)     | 采用契约组合的系统原理图投影、图元解析与渲染                | 已接受（待冷站 spike 验证；0018 补充） |
| [0018](0018-deliverable-semantics-and-baseline-gates.md)                 | 以组成、拓扑、行为与空间界定工程交付物并采用基线准出门禁    | 已接受                                 |
| [0019](0019-first-executable-hvac-schematic-slice.md)                    | 以单页根网络启动 HVAC 原理图可执行切片                      | 已接受；0017／0018 的首个实现子集      |
| [0020](0020-python-physics-and-typescript-engineering-graphics.md)       | Python 物理行为与 TypeScript/React 工程图元；清除旧草稿实现 | 已接受                                 |
