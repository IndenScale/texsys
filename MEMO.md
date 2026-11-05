# 项目备忘录

本文件是**索引**，不是正文。定位、范围、决策与未决议题分别由下列权威文档承载，此处不重复定义。

## 名称与定位

- **texsys**：来自 **textual system**。
- 三者关系：**SysDA** 是设计自动化方法论；**SDL** 是 SYSTEM 描述语言与语义标准；**texsys** 是 SDL 的参考实现、一体化工具链与生态。

> SDL 的定位：面向 SysDA 的系统描述语言与描述标准，是设计意图与系统语义的稳定、声明式入口，**不是**完整的设计自动化平台、评估器、求解器或生成算法。
> 因此 texsys 是 SysDA 的语义基础，不等同于 SysDA 本身。

## 当前范围

首个验证场景为**暖通（HVAC）冷启动**：系统原理图与粗粒度布置，以及散热／通风／结露的近似环境条件；元件数量少，几何与环境误差容忍度较高。调度属运营侧，不在本期设计模型内。

完整边界、精度假设与验收口径见 [当前范围](docs/scope.md)。范围外内容不得借"通用性"之名回流。

当前实现进度见 [项目进度审查](docs/progress.md)；系统图／原理图已完成首个可执行切片，入口与限制见 [出图构建契约](spec/build/03-system-schematic.md)。

## 已定决策

| 决策                                                       | 状态              | 位置                                                                                                                          |
| ---------------------------------------------------------- | ----------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| 采用 OpenUSD 与 USD BRep 作为原生场景和几何技术栈          | 提议（待 spike）  | [ADR-0001](docs/adr/0001-openusd-native-scene-geometry.md)                                                                    |
| 调度属运营侧，不进入 SDL 设计模型                          | 已接受            | [ADR-0002](docs/adr/0002-design-time-vs-operation-time.md)                                                                    |
| 采用 Python 与 Typer 作为参考实现和 CLI 技术栈             | 已接受            | [ADR-0003](docs/adr/0003-python-and-typer-toolchain.md)                                                                       |
| 统一使用 uv 管理 Python 环境与命令入口                     | 已接受            | [ADR-0004](docs/adr/0004-uv-only-python-workflow.md)                                                                          |
| 以可执行 examples 统一参考用例与端到端桩数据               | 已接受            | [ADR-0005](docs/adr/0005-executable-examples.md)                                                                              |
| 分离设计依赖的声明、锁定与同步                             | 已接受            | [ADR-0006](docs/adr/0006-design-dependency-management.md)                                                                     |
| 以 JSON 交换子集启动形式检查与格式化                       | 已接受            | [ADR-0007](docs/adr/0007-formal-checking-and-formatting.md)                                                                   |
| 采用六阶段 SysDA 模型并区分三类仿真                        | 已接受            | [ADR-0008](docs/adr/0008-six-stage-sysda-model.md)                                                                            |
| 建立设备与拓扑的分层映射规则                               | 已接受            | [ADR-0009](docs/adr/0009-equipment-and-topology-refinement.md)                                                                |
| 划分 build、test、sim 与领域包边界                         | 已接受            | [ADR-0010](docs/adr/0010-build-test-sim-and-package-boundaries.md)                                                            |
| 原理图（及设备表、计算书）是模型投影，不是核心对象         | 已定（0018 修订） | [SDL 核心模型](spec/sdl/01-core-model.md#交付物与视图)、[ADR-0018](docs/adr/0018-deliverable-semantics-and-baseline-gates.md) |
| 以组成／拓扑／行为／空间界定四类交付物；过程自由、基线有门 | 已定              | [ADR-0018](docs/adr/0018-deliverable-semantics-and-baseline-gates.md)                                                         |
| Component 采用递归组合模型，不另设 System／Assembly        | 已定              | [SDL 核心模型](spec/sdl/01-core-model.md)                                                                                     |
| Port 随实例自动派生，不引入 PortInstance／Endpoint         | 已定              | [SDL 核心模型](spec/sdl/01-core-model.md)                                                                                     |
| Placement 统一用 Frame 表达位姿，并区分定位约束与验证约束  | 已定              | [SDL 核心模型](spec/sdl/01-core-model.md)                                                                                     |

决策的完整索引见 [ADR 索引](docs/adr/README.md)。方法论依据见 [《设计自动化原理》](设计自动化原理/00-前言与导读.md)，该书**不构成约束**。

## 未决议题

所有待定项集中登记在 [开放问题](docs/open-questions.md)，分"阻塞"与"非阻塞"两栏，每项带触发条件。正文中不允许出现孤立的"尚待定义"。

## 三条不可让渡的纪律

这三条来自方法论，是当前范围下唯一被硬性要求的东西，其余均为可协商：

1. **未知与失败不得被当作通过**：已知、假设、待定、缺失、不适用、执行失败必须具有不同语义。
2. **方案与证据同时生成**：检查通过不等于现实成功；结论必须带依据、适用条件与误差口径。
3. **自动化不转移责任**：风险接受与价值权衡保留明确的人工决策位置。
