# HVAC 系统图与原理图构建契约

> 状态：首个可执行子集。实现边界见 [ADR-0019](../../docs/adr/0019-first-executable-hvac-schematic-slice.md)，术语以 [SDL 核心模型](../sdl/01-core-model.md#交付物与视图) 为准。

## 输入与入口

目标为 `hvac:system-schematic`，由 `texsys-hvac-diagram` 注册。输入为项目 source 目录中的 `system.sdl.json`，使用根 Component 的全部叶子 Instance、Port 和 Connection，不依赖 `hvac.json` 或物理 Placement。行为绑定见 [JSON 交换子集](../sdl/02-json-exchange.md#行为定义与参数绑定)。

首次准备本地 ELK 后端：

```bash
uv sync --locked
uv run tools/sync_node_tools.py
```

需要本机 Node.js。安装脚本按 package-lock.json 下载锁定的 Node 工具与 React／WASM／ELK 依赖，验证每份归档 SHA-512；构建时验证版本和 bundle SHA-256，执行本地 worker，**构建不联网**。安装锁位于 `packages/diagram-elk/src/texsys_diagram_elk/backend-lock.json`。外部安装可用 `TEXSYS_ELK_ROOT` 指向同版本 ELK 包目录。后端缺失、版本不符或摘要不符时为 `indeterminate`，不得悄悄切换排版器。

```bash
uv run texsys lock --check -p examples/hvac-system-schematic
uv run texsys build -p examples/hvac-system-schematic
uv run texsys build -p examples/hvac-system-schematic --target hvac:system-schematic --scope '{"view":"system"}'
uv run texsys build -p examples/hvac-system-schematic --target hvac:system-schematic --profile engineering
```

最后一条在缺少人工审核记录时返回 `indeterminate`，同时保留标记 partial 的诊断产物。`view` 只接受 `schematic`（默认）或 `system`，不允许以任意筛选悄悄隐藏设备或连接。当前仅支持单页、根层叶子实例；嵌套子系统、根边界端口和重复连接显式拒绝，不猜测折叠／展开语义。

## 语义与降级

| 条件                                           | 产物语义                 | 检查结果                                                        |
| ---------------------------------------------- | ------------------------ | --------------------------------------------------------------- |
| 全部实例有 known／assumed 行为、资源和参数绑定 | 原理图，可显示假设       | `behavior_state=defined`，不表示行为已求解或工程正确            |
| 主动请求 `view=system`                         | 系统图，拓扑与原理图相同 | `deliverable=system-diagram`                                    |
| 任一行为、引用文件或必需参数缺失／待定         | 明确降级为系统图         | `behavior_state=missing`；review 可产出草稿，engineering 不闭环 |
| 行为或参数显式 failed                          | 失败产物                 | `fail`，不能以降级掩盖失败                                      |
| 没有 SDL Placement                             | 仅连接视图               | `spatial_state=connectivity-only`，不输出位置、距离或标高事实   |

行为定义引用必须指向项目 source 或 assets 内的固定文件，禁止目录逃逸；资源摘要进入绘图文档和构建证据。Component 参数是默认绑定，Instance 参数覆盖同名绑定；参数名必须已声明。系统图突出设备轮廓与管线；原理图增加少量关键参数。完整引用、状态、依据和带单位参数保留在 JSON 与交互侧栏。`not-applicable` 可省略数值，其他未解决状态不得用默认值替代。

该 target 校验并投影行为定义引用和参数绑定；动态求解由独立的 hvac-dynamics 执行，见 [行为契约](04-hvac-dynamics.md)。现有 `hvac:design-checks`／`texsys test` 的简化环境断言与此 target 分别闭环，不能将其中一个的 pass 当作另一个的结论。

## 产物与证据

直接选择包注册目标时，系统图和原理图分别输出到构建目录的 `hvac/system/` 和 `hvac/schematic/`。项目命名目标使用清单声明的主文件路径，例如 `build/drawings/schematics/system.json`，配套文件与主文件位于同一目录：

| 文件                | artifact ID     | 内容                                                         |
| ------------------- | --------------- | ------------------------------------------------------------ |
| `system.json`       | `system-data`   | DiagramDocument v1、图元解析、排版、来源、行为引用摘要       |
| `system.svg`        | `system-svg`    | 矢量预览，显示介质、可确定的流向、端口和行为标注             |
| `system.drawio`     | `system-drawio` | 非压缩 XML，保留图元、显式端口、路由与 UserObject 语义属性   |
| `system.html`       | `system-viewer` | React 离线工作台：图元参数预览、设备详情、运行曲线与时间回放 |
| `system-check.json` | `system-check`  | diagnostic；语义／版面检查、回退、降级、人工审核与未决项     |

DiagramDocument 含 target、profile、view、模型摘要、单页、节点、端口、连接、分组／注记空集合、source map、expected projection、图元和后端证据。图面 ID 由根身份、实例身份、端口名、连接端点组成，与坐标及数组顺序分离。连接以端点对寻址。若 SDL 声明显式网络，`nets` 记录 `net:<根部件>.<网络名>`、介质类型和成员图面连接 ID，`source_map` 同时记录网络及连接的源位置。

通用 diagram 包维护投影契约、overlay、几何／语义检查和 draw.io 序列化；hvac-diagram 投影专业事实，hvac-graphics 的 TypeScript 函数解析专业几何，React 复用同一数据渲染 SVG／HTML。diagram-elk 只解析和验证锁定的 ELK 后端；坐标与路由执行与共享几何在同一 Node 进程中完成，避免重复 worker 协议。

当前支持冷机、泵、冷却塔、换热器、管段、阀门／止回阀、水箱与接点的概念工程轮廓。版本化物理类型或已知设备族选择图元，先校验端口名称、介质与方向；不兼容时使用保留全部语义端口的中性轮廓。参数化轮廓、包围盒、接管位置、标签锚点由同一纯函数计算，draw.io 不再绘制另一套设备卡片。未接厂商型号目录，presentation_exact=false；概念图元不声称符合某套完整工程制图标准。

scope.results 可指定 build 目录内的固定 results.json 数组。模型与工况源摘要必须匹配，时序和变量必须有限且长度一致；结果文件摘要进入图纸审核摘要。HTML 只回放结果，支持设备选择、工况切换、时间游标、播放／暂停、事件跳转与约束展示；实际流向取自有符号流量，未附结果时只显示静态可确定方向。失败工况可以查看，但不能据图纸 review 的 pass 声称工况合格。

自动检查覆盖设备／连接完整性、ID 唯一性、端口介质、有限坐标、节点重叠、路由正交、连线端点对齐及穿越设备内部。自动检查没有覆盖所有标签碰撞、线线交叉语义和工程审美，必须保留视觉审核。

`build/build.json` 记录 target、profile、scope、包版本、输入与产物摘要及状态；`system.json` 另记录 ELK／Node 版本、worker／bundle 摘要、布局选项和渲染策略。数字固定到三位小数序列化，同一工具链和输入下产物字节一致。当前使用 Arial／系统中文无衬线字体；没有锁定字体文件和主机字体替换，因此不承诺跨主机像素一致，见 OQ-15。

## 人工排版 overlay

在 assets 中保存 JSON，通过 `--scope '{"overlay":"assets/layout-overlay.json"}'` 指定。示例结构：

```json
{
  "schema_version": 1,
  "view": "schematic",
  "source": "排版审核记录",
  "revision": "1",
  "nodes": {},
  "edges": {}
}
```

`nodes` 以稳定图面 ID 为键，仅允许 `{ "x": 40, "y": 60 }`；`edges` 以稳定图面 ID 为键，仅允许 `{ "points": [{"x": 390,"y": 175},{"x": 500,"y": 175}] }`。坐标必须非负且有限。移动节点必须同时明确提供所有关联连接的路由，避免自动排版覆盖人工意图或任意猜测新管路。overlay 路由标注放在最长线段附近，仍须人工检查。

失效引用、未知字段、视图不符、缺关联路由或语义修改均 `fail`；有效 overlay 在每次 ELK 排版后覆盖白名单字段，再运行完整检查。overlay 版本、摘要与字段来源写入文档。第一版不支持注记编辑、尺寸／样式编辑或 draw.io 回读；专业事实需修改 SDL，不能只改导出图。

## Profile 与人工审核

`review` 为默认 profile：自动检查通过即可完成**草稿生成**，但明确记录 `visual_review=unreviewed` 和开放项，不能据此声称正式交付。`engineering` 还要求行为完整和绑定当前图纸摘要的审核资产。

审核者读取本次 `system-check.json` 的 `visual_review.diagram_digest`，审核图纸后自行创建 assets 内的记录：

```json
{
  "schema_version": 1,
  "diagram_digest": "sha256:替换为本次图纸摘要",
  "decision": "approved",
  "reviewer": "实际审核人",
  "source": "审核记录编号或依据"
}
```

```bash
uv run texsys build -p examples/hvac-system-schematic --target hvac:system-schematic --profile engineering --scope '{"approval":"assets/visual-approval.json"}'
```

审核摘要覆盖模型、图元、行为资源、布局、overlay 和渲染策略；profile 不参与摘要，方便同一草稿进入工程检查。任一相关输入变化导致旧审核失效，返回 `fail`。该资产是显式审核记录协议，不是身份认证、数字签名或施工配置基线管理。程序不会代签人工审核。
