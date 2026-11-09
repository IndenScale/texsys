# texsys 共享词表

这里收录 texsys 在讨论、规范、界面和诊断中使用的首选名称。每条给出中文、英文或代码标识、容易混淆的概念，以及定义来源。条目中的释义用于帮助选词；语义和数据规则以链接的 `spec/` 为准。

## 项目与产品

| 首选用语            | 使用边界                                                                    | 定义来源                                                                                                                     |
| ------------------- | --------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| 设计自动化（SysDA） | 方法论。英文全称按 [OQ-10](../open-questions.md#oq-10) 保留未决。           | [SDL 概述](../../spec/sdl/00-overview.md#定位)                                                                               |
| 系统描述语言（SDL） | 描述工程系统的声明式语言与语义标准。当前实现使用 `.sdl.json` 交换子集。     | [SDL 概述](../../spec/sdl/00-overview.md)、[JSON 交换子集](../../spec/sdl/02-json-exchange.md)                               |
| texsys              | SDL 的参考实现、工具链与生态。                                              | [项目 README](../../README.md)                                                                                               |
| texsys CLI          | texsys 的命令行接口。命令名为 `texsys`，仓库文档通过 `uv run texsys` 调用。 | [ADR-0003](../adr/0003-python-and-typer-toolchain.md)、[ADR-0004](../adr/0004-uv-only-python-workflow.md)                    |
| Tex Studio          | texsys 的插件式图形工作台。当前提供本地 Web 宿主和 HVAC 工作台。            | [工作台契约](../../spec/studio/01-workbench.md)、[ADR-0021](../adr/0021-tex-studio-plugin-based-disciplinary-workbenches.md) |

## SDL 模型

| 首选用语                          | 使用边界                                                                                                                                           | 定义来源                                                                                                                                                           |
| --------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 部件（`Component`）               | 可复用定义。为避免与具体实例混淆，讨论类型和引用时写「部件定义」。设备是暖通领域用语，并非所有部件都是设备。                                       | [Component](../../spec/sdl/01-core-model.md#component)                                                                                                             |
| 实例（`Instance`）                | 部件在具体层级中的实例化结果，有自身身份和参数绑定。                                                                                               | [Instance](../../spec/sdl/01-core-model.md#instance)                                                                                                               |
| 系统／子系统（system／subsystem） | 部件在顶层或下级的工程角色。`System` 不是独立 SDL 核心类型；`root` 在当前 JSON 中指向顶层部件定义。                                                | [Component](../../spec/sdl/01-core-model.md#component)、[文件与文档](../../spec/sdl/02-json-exchange.md#文件与文档)                                                |
| 端口（`Port`）                    | 部件定义的交互边界。实例上的端口用 `instance.port` 引用，称「端口引用」。                                                                          | [Port](../../spec/sdl/01-core-model.md#port)                                                                                                                       |
| 连接（`Connection`）              | 两个端口之间的局部连接事实。图上的线是呈现；有压降的管段或阀门可以另建部件与实例。                                                                 | [Connection](../../spec/sdl/01-core-model.md#connection)                                                                                                           |
| 网表（`Netlist`）                 | 部件内部逻辑连接拓扑的描述。当前 JSON 用 `connections` 表达有限子集。                                                                              | [Netlist、Connection 与 Net](../../spec/sdl/01-core-model.md#netlistconnection-与-net)、[JSON 交换子集](../../spec/sdl/02-json-exchange.md#instance-与-connection) |
| 网络（`Net`）                     | 具有类型、可拥有独立身份和整体语义的网络。「流网」「回路」可用于具体专业语境。显式声明、匿名推导与成员规则仍见 [OQ-3](../open-questions.md#oq-3)。 | [Net](../../spec/sdl/01-core-model.md#net)                                                                                                                         |
| 空间布置（`Layout`）              | 实例、坐标参考和约束所在的物理空间上下文。「图面排版」专指绘图坐标或走线。                                                                         | [Layout](../../spec/sdl/01-core-model.md#layout)、[人工排版 overlay](../../spec/build/03-system-schematic.md#人工排版-overlay)                                     |
| 放置（`Placement`）               | 一个实例在某个空间布置中的空间绑定；位姿是绑定中的表达。                                                                                           | [Placement 与 Frame](../../spec/sdl/01-core-model.md#placement-与-frame)                                                                                           |
| 坐标系（`Frame`）                 | 解释位姿的坐标参考。                                                                                                                               | [Placement 与 Frame](../../spec/sdl/01-core-model.md#placement-与-frame)                                                                                           |

## 检查与格式化

| 首选用语                                          | 使用边界                                                                                   | 定义来源                                                                     |
| ------------------------------------------------- | ------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------- |
| 项目检查（check）                                 | 汇总项目结构、SDL 解析与模型校验以及已安装插件提供的检查，输出诊断。                       | [诊断契约](../../spec/sdl/02-json-exchange.md#诊断与格式化)                  |
| 解析（parsing）                                   | 将源文本读成结构化表示，报告语法错误，例如 JSON 缺少逗号。                                 | [诊断契约](../../spec/sdl/02-json-exchange.md#诊断与格式化)                  |
| 模型校验（validation）                            | 检查模型是否满足模式、引用完整性与静态语义约束，例如端口引用是否存在。                     | [SDL 交换子集](../../spec/sdl/02-json-exchange.md)                           |
| 静态分析（static analysis）                       | 直接从设计表示推理其性质，例如从网络拓扑判断可达性，无需执行或仿真被分析的系统。           | [构建、测试与仿真边界](../adr/0010-build-test-sim-and-package-boundaries.md) |
| 风格检查（linting）／风格检查器（Linter）         | 检查可读取的源文件是否符合书写或表达约定。语法错误归入解析诊断，悬空引用归入模型校验诊断。 | [诊断与格式化](../../spec/sdl/02-json-exchange.md#诊断与格式化)              |
| 源文件格式化（formatting）／格式化器（Formatter） | 统一缩进、键顺序等文本表示，保留数据值与设计语义。由模型生成图纸或设备表属于投影。         | [诊断与格式化](../../spec/sdl/02-json-exchange.md#诊断与格式化)              |

设计规则检查（DRC）用于专业语境，按该专业的检查对象和规则集理解，例如 PCB 的线宽和间距规则。 [参考稿术语对照](../rfc/0001-sdl-topology-slice.md#sdl-下载稿与现行约定)

## 交付与验证

| 首选用语                                             | 使用边界                                                                                                                          | 定义来源                                                                                                                                                 |
| ---------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 投影（projection）                                   | 从模型派生视图；SVG、draw.io、HTML 是输出形式。                                                                                   | [交付物与视图](../../spec/sdl/01-core-model.md#交付物与视图)                                                                                             |
| 物料清单（BOM）                                      | 在明确范围内列出组成项、数量、规格及状态，可按用途和视图规则聚合。                                                                | [BOM](../../spec/sdl/01-core-model.md#bom)、[Target BOM](../../spec/build/01-target-bom.md)                                                              |
| 拓扑图（system diagram）                             | 表达系统的组成与连接拓扑，包括实例、端口和连接关系。可按视图范围折叠或展开子系统。                                                | [系统图](../../spec/sdl/01-core-model.md#系统图)                                                                                                         |
| 原理图（schematic）                                  | 在组成和连接拓扑上表达系统如何工作。行为定义引用和关键参数按专业交付契约表达，求解与验证结果另行记录。                            | [原理图](../../spec/sdl/01-core-model.md#原理图)、[语义与降级](../../spec/build/03-system-schematic.md#语义与降级)                                       |
| 布置图（layout drawing）                             | 空间布置（`Layout`）的交付视图，表达共同坐标下的实例位姿、空间约束与路由。空间布置可独立于行为建模推进。                          | [布置图](../../spec/sdl/01-core-model.md#布置图)、[ADR-0018](../adr/0018-deliverable-semantics-and-baseline-gates.md)                                    |
| 几何预算／回注（geometry budget／back-annotation）   | 预算记录性能允许的几何参数范围；回注把提取值送回模型与验证证据。首版出图契约未提供完整回注流水线。                                | [几何预算与回注验证](../../spec/sdl/01-core-model.md#几何预算与回注验证)                                                                                 |
| 构建目标（target）／配置档（profile）／范围（scope） | 分别标识执行契约、目标所支持的用途配置，以及本次调用传入的具体范围。拓扑图与原理图是共享 `hvac:system-schematic` 目标的两个视图。 | [构建 target 契约](../../spec/build/00-overview.md)、[出图契约](../../spec/build/03-system-schematic.md)                                                 |
| 工况（scenario）／断言（assertion）                  | 工况描述一次行为求解的输入和条件；断言判定结果是否满足指定条件。构建、求解、断言和人工审核分别记录结果。                          | [HVAC 集总参数行为契约](../../spec/build/04-hvac-dynamics.md#工况参数)、[Profile 与人工审核](../../spec/build/03-system-schematic.md#profile-与人工审核) |

现有规范中的「系统图」对应「拓扑图」。SDL 参考稿中的「版图」在共享词表中对应「布置图」。[参考稿术语对照](../rfc/0001-sdl-topology-slice.md#sdl-下载稿与现行约定)

## 维护方式

1. **按语境维护。** 术语正文放在 `docs/ul/` 的词表中，由[目录索引](README.md)统一导航。专业或工作台的术语可在同一目录下单列词表，并标明与共享术语的关系。
2. **命名澄清与语义变更分开。** 只澄清命名时，直接更新词表及所触及的现行文档。改变对象身份、数据规则或工程承诺时，先用 [RFC](../rfc/README.md) 讨论；决策记录进 [ADR](../adr/README.md) 或 [DDR](../ddr/README.md)，规则进入 `spec/`，同次更新词表。未决项链接到 [开放问题](../open-questions.md)。
3. **代码和历史按原样引用。** 代码标识、CLI 选项、交换字段与历史资料保留原拼写。新增面向用户的文字使用首选词；兼容字段迁移由对应契约定义。首版由评审检查用语与语义是否一致。
