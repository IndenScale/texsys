# 模块包目录与资产清单

模块包在 SDL 部件库中交付一个模块入口及其可选资产。项目内的 SDL `imports` 引用入口，模块资产清单用于发现交付的资源和接口。包内模型、图纸和三维资产可各自维护。现有仅提供 SDL 的模块仍可按原方式引用。 [部件库引用](02-json-exchange.md#部件库引用)

## 目录

```text
<source>/library/modules/<name>/
├── <name>.sdl.json        # 模块入口，root 指向对外定义
├── module.toml            # 模块资产清单
├── assets/                # 由清单显式列出的本地文件
│   ├── geometry.usda
│   └── symbol.svg
└── library/               # 可选，模块内部部件与子模块
```

同一约定适用于模块内部部件库中的子模块。模块清单版本与所属项目清单的 `project.version` 分别标识模块和库来源。跨项目锁定通过既有的库引用和设计依赖机制覆盖整个库来源的 `source` 和 `assets` 内容。选择、实例化及适用性评价分别按项目的设计流程记录。 [依赖与锁文件](../../docs/project-layout.md#依赖与锁文件)

## 清单 v1

```toml
schema_version = 1
name = "42U 机柜"
version = "0.1.0"
entry = "rack-42u.sdl.json"

[assets.geometry]
kind = "geometry"
path = "assets/geometry.usda"
source = "风冷机房参考库方案假设"
fidelity = "方案级实体与接口定位"

[assets.symbol]
kind = "symbol"
path = "assets/symbol.svg"
source = "风冷机房参考库方案假设"
fidelity = "带端口锚点的功能示意"

[port_bindings.front]
geometry = "/Module/Ports/front"
symbol = "port-front"
```

- `entry` 与旁边的 SDL 模块入口文件名一致。`name` 用于展示，`version` 随交付内容管理。
- `assets` 的键是包内资产 ID。支持 `behavior`、`evidence`、`geometry`、`material`、`mesh`、`plan`、`render-scene`、`specification`、`symbol` 和 `texture`。每条资产声明类型、文件路径、来源和保真度。清单列出已提供的资产，可按用途选择资产类型。
- 资产路径以模块目录为基准，使用 `assets/` 下的普通文件。路径使用 `/`，禁止空段、`.`、`..` 和符号链接。依赖的 SDL 模块继续通过 `imports` 声明。
- `port_bindings` 可省略。外层键是 SDL 根定义上的端口，内层键是资产 ID，值是在对应格式中可定位的标识。机房例使用 USD Prim 路径和 SVG 元素 ID。
- `texsys check` 报告清单结构、路径、端口或资产 ID 错误（`M001`）。资产内容和绑定目标由消费该格式的构建目标检查。Studio 库索引随模块根定义提供 `module_package` 元数据。

机房示例在构建时核对工程几何包络、USD 端口位姿和 SVG 锚点，产出模块库场景及引用这些模块的项目场景。 [风冷机房参考库](../../examples/hvac-air-cooled-room/README.md)

## 与发布包的关系

跨项目分发使用一般 Package 契约：部件包声明 `kind="component"` 和 `exports.components`，复用 `texsys.toml` 的名称、发布版本和依赖锁。`module.toml` 属于导出部件的内部资产描述，其版本表示内部资源修订，不构成另一套安装、依赖解析或升级机制。包身份与包内入口共同确定导出身份。内部子部件和项目实例不要求独立发布。详见 [ADR-0025](../../docs/adr/0025-package-component-ide-boundaries.md)。
