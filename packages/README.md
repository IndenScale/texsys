# texsys 领域包

本目录是 uv 工作区，也是可版本化设计包的源代码目录。当前通用 BOM 与暖通领域包通过 `texsys.packages` 入口发现；通用绘图契约、ELK 后端与专业前端图元由专业投影包组合使用。各包分别提供 `texsys.toml`，供设计项目使用 `texsys lock` 与 `texsys sync` 固定内容和依赖图。

Tex Studio 界面插件另通过 `texsys.studio` 入口发现。`studio-hvac/`（`texsys-studio-hvac`）是首个专业工作台，依赖既有暖通领域包和共享工程图元；它属于应用插件，不作为项目的设计依赖包，也不伪造 `texsys.packages` 入口。宿主与专业契约见 [工作台契约](../spec/studio/01-workbench.md)。

| 目录               | Python 分发包            | 设计包            | 主要职责                                       |
| ------------------ | ------------------------ | ----------------- | ---------------------------------------------- |
| `bom/`             | `texsys-bom`             | `bom`             | 通用 BOM 投影、外部快照、映射、比较与检查      |
| `architecture/`    | `texsys-architecture`    | `architecture`    | 房间空间、墙、门洞、竖井和折返楼梯             |
| `controls/`        | `texsys-controls`        | `controls`        | 带时间戳与质量的二值点、执行器、反馈确认切换   |
| `electrical/`      | `texsys-electrical`      | `electrical`      | 供电路径、备用切换、短路保护与等电位连接       |
| `fire-protection/` | `texsys-fire-protection` | `fire-protection` | 防火边界、贯穿封堵、疏散路径与共享通行容量     |
| `plumbing/`        | `texsys-plumbing`        | `plumbing`        | 排液、集液、补水处理、储水与共享排放校核       |
| `datacenter/`      | `texsys-datacenter`      | `datacenter`      | 数据中心单元的配电、风液冷及结构跨联合方案分析 |
| `spatial/`         | `texsys-spatial`         | `spatial`         | 通用轴网、命名水平基准面和轴交点定位           |
| `structure/`       | `texsys-structure`       | `structure`       | 结构专业的钢框架、钢筋混凝土典型构造与模块目录 |
| `hvac-equipment/`  | `texsys-hvac-equipment`  | `hvac-equipment`  | 设备族、USD BRep 投影、几何事件                |
| `hvac-linter/`     | `texsys-hvac-linter`     | `hvac-linter`     | 领域静态规则                                   |
| `hvac-formatter/`  | `texsys-hvac-formatter`  | `hvac-formatter`  | 输入格式化与投影                               |
| `hvac-checks/`     | `texsys-hvac-checks`     | `hvac-checks`     | 容量／通风／露点的 Python 设计校核             |
| `hvac-dynamics/`   | `texsys-hvac-dynamics`   | `hvac-dynamics`   | 热水力、径向供回水配水、泵组调度与热瞬态       |
| `hvac-bom/`        | `texsys-hvac-bom`        | `hvac-bom`        | 暖通来源适配、专业规则与视图                   |
| `diagram/`         | `texsys-diagram`         | `diagram`         | 通用绘图契约、图元解析、人工排版层、检查与导出 |
| `diagram-elk/`     | `texsys-diagram-elk`     | `diagram-elk`     | 锁定 ELK 解析、完整性校验与排版配置            |
| `hvac-diagram/`    | `texsys-hvac-diagram`    | `hvac-diagram`    | 暖通投影、概念符号与系统图／原理图目标         |

依赖方向保持单向：通用 BOM 只依赖核心协议，专业适配器依赖通用内核和相应专业包；核心与通用 BOM 均不反向导入专业规则。新增领域包时，应同时提供独立版本、适用边界、测试和设计包清单，而不是将领域概念加入 SDL 核心。

## 空间基础库与结构专业库

`texsys-architecture` 提供五个可从锁定依赖导入的方案级模块包。房间和竖井使用楼层基准与空间范围，墙构造生成带洞口的实体，门洞记录宿主与净尺寸，折返楼梯从梯段参数生成踏步、平台和行走线。数据中心算例将同一批建筑对象用于三维开洞、消防贯穿和疏散。 [墙体模块](architecture/src/library/modules/wall-panel/module.toml) · [楼梯模块](architecture/src/library/modules/switchback-stair/module.toml) · [建筑协调算例](../examples/datacenter-hybrid-unit/README.md)

- `texsys-spatial` 用 `AxisGrid` 表达局部坐标系内的正交命名轴线，通过 `point(x_axis, y_axis, offset_m)` 求交点和偏移。用 `DatumPlane` 表达同一坐标系内的命名水平基准面。轴名、轴距、标高和放置绑定由项目的 `Layout` 提供。 [SDL 空间布置](../spec/sdl/01-core-model.md#layout)
- `texsys-structure` 用 `ISection` 生成参数化梁柱几何，`grid_frame` 根据通用轴网、柱脚基准和梁顶基准生成方案级钢框架。结构包也是 Studio 可添加的库来源，构造目录通过 SDL、符号、适用接口和 USD 几何交付样件。 [钢结构支架清单](structure/src/library/modules/steel-support/module.toml)
- `texsys_structure.beam` 和 `texsys_structure.strips` 计算简支梁与单向条带的弯矩、剪力、挠度和支承反力。材料有效刚度、承载限值和荷载组合由项目明确提供。 [数据中心结构跨示例](../examples/datacenter-hybrid-unit/README.md)
- `texsys_structure.deck.DeckSlab` 生成 W 型永久钢模板和现浇板的分体几何及用量，结构库的模块包提供可替换的截面默认值与样件。`texsys_structure.contacts` 筛查设备支脚承压和完整冲切周边。项目通过设备模块的安装质量及支脚规格建立集中荷载，并声明有效深度与判据。 [W 型永久模板模块](structure/src/library/modules/w-form-concrete-slab/module.toml) · [设备传力算例](../examples/datacenter-hybrid-unit/README.md)
- `texsys_structure.lateral` 根据刚性楼盖、连续交叉支撑及各层水平力和扭矩计算层间位移、支撑拉力及支撑跨的柱轴力增量。风震输入、基础筛查和门洞协调由数据中心算例组合。 [数据中心钢框架示例](../examples/datacenter-hybrid-unit/README.md)
- `texsys_structure.diaphragm` 将混凝土面内板、钢梁轴向构件和连接组滑移组合求解，按矩形洞口扣除刚度积分区域，输出板内力、钢梁轴力和节点传力。数据中心算例负责提取实际梁网、机房洞口和支撑节点荷载。 [数据中心楼盖传力示例](../examples/datacenter-hybrid-unit/README.md)
- `texsys_structure.openings` 提供矩形区域分割与套管净距。楼盖积分和 W 型钢模板开洞几何共用该分割。数据中心算例将实际管线贯穿、主楼板洞口和竖井封闭板登记到统一坐标系。 [数据中心洞口协调示例](../examples/datacenter-hybrid-unit/README.md)
- `texsys_structure.foundation` 计算矩形独立基础的双向偏心、受压接触区和基础截面需求。土体参数、柱脚构造、反力和筛查限值由项目提供。 [数据中心基础示例](../examples/datacenter-hybrid-unit/README.md)
- `texsys_structure.foundation_ties` 计算直线拉梁、刚性基础、单边受压和有摩擦上限的底面传力，输出基础接触与拉梁端部内力。数据中心算例用同一组荷载比较独立基础和拉梁方案。 [数据中心基础方案比较](../examples/datacenter-hybrid-unit/README.md)

结构库的钢筋混凝土梁模块发布纵筋轴线参考。电气库提供热浸镀锌扁钢和扁钢与钢筋的不可逆压接节点，分别交付 SDL、符号、几何及规格。数据中心项目将这些模块在基础处配接，并独立校核导通路径。 [压接节点清单](electrical/src/library/modules/flat-strip-rebar-compression/module.toml) · [钢筋混凝土梁清单](structure/src/library/modules/reinforced-concrete-beam/module.toml)

结构库依赖空间库。暖通项目读取两者，设置本项目的定位、截面尺寸和空间校核条件。结构库的首版输出用于占位和协调，结构承载和连接验算需有独立的专业模型和设计依据。 [风冷机房示例](../examples/hvac-air-cooled-room/README.md)

### 钢筋混凝土典型构造

| 构造               | 共用的配筋几何       | 协调接口               |
| ------------------ | -------------------- | ---------------------- |
| 直条钢筋           | 公称直径和长度       | 供构造复核             |
| 钢筋混凝土墙、楼板 | 单层或双层正交钢筋网 | 墙脚、楼板支承面       |
| 钢筋混凝土梁、柱   | 四角纵筋与矩形箍筋   | 梁端支承点、柱脚及柱顶 |
| 独立基础、设备基座 | 正交钢筋网           | 基底、柱座或设备支承面 |

每份构造的 `specification.json` 记录尺寸、保护层、配筋和局部接口位置。`texsys_structure.concrete` 从规格生成分离的混凝土和钢筋 USD 几何，并检查配筋能否放进给定的保护层。钢筋以直线段和矩形箍筋示意，尺寸与配筋均为方案假设。锚固、弯钩、搭接、承载与抗震应由项目详图和专业验算确定。

```bash
uv run --locked python -m texsys_structure.concrete \
  packages/structure/src/library/modules/reinforced-concrete-beam/assets/specification.json --check
```

后续优先补墙板洞口与预留套管、预埋件和设备锚固接口。这些构造能让机电路由和设备安装直接引用结构边界。梁柱节点、基础连接和配筋详图则需要先确定连接和锚固的表达方式。

绘图亦遵守相同方向：暖通包定义专业投影、TypeScript 基础图元与介质样式，通用绘图包独立于暖通与 ELK。ELK 适配器负责图面坐标与走线。协议见 [系统原理图构建契约](../spec/build/03-system-schematic.md)。

`hvac-dynamics`（`texsys-hvac-dynamics`）维护不可变物理快照、工况、SciPy 动态求解，以及 Modelica 模型的导出与执行。`hvac-graphics`（`texsys-hvac-graphics`）维护 TypeScript 暖通图元、React 矢量图、离线网页与布局执行。

`hvac-dynamics` 的设施水模型复用管道压降计算，在成对径向供回水网中按共用设备阻力、并联泵曲线和终端需求校核可交付冷量。 [数据中心设施水示例](../examples/datacenter-hybrid-unit/README.md)

同一包的 `RoomRideThrough` 根据 IT 热输入、风液冷恢复时序和等效热容，计算机柜固体、冷区空气及 TCS 水体的温度轨迹，并校验能量平衡。 [数据中心热响应示例](../examples/datacenter-hybrid-unit/README.md)

`hvac-equipment` 库发布精密空调、冷却液分配单元（CDU）和双回路液冷机柜的方案级模块包。模块 SDL 提供额定点及端口语义，模块清单绑定符号锚点、USD 接口坐标、工程规格和额定点求解约定。数据中心算例从锁定的库引用模块，并以同一端口定位设备和支管。 [精密空调模块](hvac-equipment/src/library/modules/precision-air-unit/module.toml) · [CDU 模块](hvac-equipment/src/library/modules/coolant-distribution-unit/module.toml) · [双回路液冷机柜模块](hvac-equipment/src/library/modules/liquid-cooled-rack/module.toml)

`hvac-dynamics` 的 `TechnologyCooling` 复用成对径向网络和泵曲线，按 CDU 可移除的热量、机柜流量和温度范围、部件额定阻力及实际管长校核各机柜供液。两侧水网的压力与流量约束可以组合到同一组机柜冷量变量，供数据中心算例在局部故障后重算共用冷源到机柜的分配。 [技术冷却回路示例](../examples/datacenter-hybrid-unit/README.md)

`texsys-controls` 提供专业通用的二值传感器、二位执行器与反馈确认切换控制器模块。控制器根据带时间戳和质量的闭合及流量证明决定是否允许备用，超时或证明丢失进入锁定。数据中心项目通过 SDL 连接这些模块，再绑定现场管段和水力证明。 [确认切换模块](controls/src/library/modules/confirmed-changeover/module.toml) · [动环算例](../examples/datacenter-hybrid-unit/README.md)

旧有序 Modelica 解释器与 `hvac-modelica` 包已删除，原公式迁移至 `hvac-checks`。绘图的旧 Python SVG 渲染器、卡片目录与重复的 ELK 后台任务已删除；不提供兼容入口。
