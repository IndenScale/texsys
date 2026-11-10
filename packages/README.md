# texsys 领域包

本目录是 uv 工作区，也是可版本化设计包的源代码目录。当前通用 BOM 与暖通领域包通过 `texsys.packages` 入口发现；通用绘图契约、ELK 后端与专业前端图元由专业投影包组合使用。各包分别提供 `texsys.toml`，供设计项目使用 `texsys lock` 与 `texsys sync` 固定内容和依赖图。

Tex Studio 界面插件另通过 `texsys.studio` 入口发现。`studio-hvac/`（`texsys-studio-hvac`）是首个专业工作台，依赖既有暖通领域包和共享工程图元；它属于应用插件，不作为项目的设计依赖包，也不伪造 `texsys.packages` 入口。宿主与专业契约见 [工作台契约](../spec/studio/01-workbench.md)。

| 目录              | Python 分发包           | 设计包           | 主要职责                                    |
| ----------------- | ----------------------- | ---------------- | ------------------------------------------- |
| `bom/`            | `texsys-bom`            | `bom`            | 通用 BOM 投影、外部快照、映射、比较与检查   |
| `hvac-equipment/` | `texsys-hvac-equipment` | `hvac-equipment` | 设备族、USD BRep 投影、几何事件             |
| `hvac-linter/`    | `texsys-hvac-linter`    | `hvac-linter`    | 领域静态规则                                |
| `hvac-formatter/` | `texsys-hvac-formatter` | `hvac-formatter` | 输入格式化与投影                            |
| `hvac-checks/`    | `texsys-hvac-checks`    | `hvac-checks`    | 容量／通风／露点的 Python 设计校核          |
| `hvac-bom/`        | `texsys-hvac-bom`        | `hvac-bom`        | 暖通来源适配、专业规则与视图                  |
| `diagram/`         | `texsys-diagram`         | `diagram`         | 通用绘图契约、图元解析、人工排版层、检查与导出    |
| `diagram-elk/`    | `texsys-diagram-elk`    | `diagram-elk`    | 锁定 ELK 解析、完整性校验与排版配置         |
| `hvac-diagram/`    | `texsys-hvac-diagram`    | `hvac-diagram`    | 暖通投影、概念符号与系统图／原理图目标     |

依赖方向保持单向：通用 BOM 只依赖核心协议，专业适配器依赖通用内核和相应专业包；核心与通用 BOM 均不反向导入专业规则。新增领域包时，应同时提供独立版本、适用边界、测试和设计包清单，而不是将领域概念加入 SDL 核心。

绘图亦遵守相同方向：暖通包定义专业投影、TypeScript 基础图元与介质样式，通用绘图包独立于暖通与 ELK。ELK 适配器负责图面坐标与走线。协议见 [系统原理图构建契约](../spec/build/03-system-schematic.md)。

`hvac-dynamics`（`texsys-hvac-dynamics`）维护不可变物理快照、工况、SciPy 动态求解，以及 Modelica 模型的导出与执行。`hvac-graphics`（`texsys-hvac-graphics`）维护 TypeScript 暖通图元、React 矢量图、离线网页与布局执行。

旧有序 Modelica 解释器与 `hvac-modelica` 包已删除，原公式迁移至 `hvac-checks`。绘图的旧 Python SVG 渲染器、卡片目录与重复的 ELK 后台任务已删除；不提供兼容入口。
