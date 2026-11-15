# AIDC 参考设计

本例以 1920 kW 装配为基础，演示工程源文件如何编译为几何、A3 图册、计算和专业审查材料。宗地、工况和设备使用可替换的合成数据。一次正常构建运行全部声明的交付目标。每份图纸可单独重建。

```
GB 50174-2017（texsys-gb50174-2017 规范包，外部工作区）
        │  src/design-basis.json：分级、规模与 12 条设计要求
        ▼
本参考设计（examples/datacenter-reference-design）
        │  src/system.sdl.json：72 个顶层实例、111 条连接
        ▼
datacenter-reference 器件/单元库 → structure / architecture / hvac-equipment
```

## 规模与组成

1920 kW IT 负荷：128 台 15 kW 液冷机柜，16 个 IT 方舱（每舱 8 柜，液侧 75% 由 2N CDU 承担，风侧 25% 由 2N 精密空调承担）。

- **供电 2N**：两路独立 10 kV 市电，各经过 3150 kVA 变压器和 5000 A 低压进线及转换开关。每路转换母线直供设施负荷，另接 5 台 500 kVA UPS 和 4 段 1000 A 母线槽，带 16 个 IT 方舱的机柜和舱内冷却。每路电池 600 kWh。
- **备用电源**：A/B 各一组发电机方舱（2×3000 kW，N+1），每组储油 9000 L；控制屏统一发出启动命令。
- **冷源 N+1**：3 个冷却方舱（1200 kW 水冷机组 + 2 台冷冻泵，1 用 1 备），对外 FWS 供回水经集管接 16 个方舱的 CDU 设施侧；3 台闭式冷却塔与 3 台冷却水泵一一对应，补水接市政水源。
- **建筑构造**：8 跨 8 m 钢框架、4 道 2.0 h 防火隔墙和 16 区防静电活动地板。轴网编译产生 22 根屋面钢梁、15 根柱与 15 个独立基础。概念耐火校核沿用已声明的耐火等级与楼板耐火时间。

## 文件

- `src/site.json`、`grid.json`、`levels.json` 定义宗地、局部坐标、轴网、标高和分区边界。
- `src/coordination.json` 以 SDL 实例 ID 绑定轴线交点、偏移、专业和分区。物理实例使用锁定部件的 USD 几何。市电及补水等逻辑接口记录待落实的空间条件。
- `src/architecture.json` 将既有北侧墙件接入完整主机房围护，用 B 轴定位分隔墙。东西门位由南北走道导出，并按门洞、平台和坡道坐标校核到室外场坪的通行。
- `src/telecom.json` 声明 A、B 两路汇聚柜、桥架中心标高、方舱端接位置和光纤构成。`telecom-study.json` 声明光模块和接续损耗预算、交换容量与可用状态。舱内跳线路由待核，当前按预算长度计。
- `src/design-basis.json`：A 级分级依据、规模假设与 12 条 GB 50174 要求（3.2.1、4.2.1、5.1.1、6.1.3、7.5.2、8.1.1、8.1.7、8.1.15、13.1.2、13.2.1、13.2.4、附录 A 电气），每条绑定一个校核，并以 `rules` 记录规范包 texsys-gb50174-2017 的规则键、以 `citation` 保存条文短引与定位；阈值与规范包 A 级结构化限值一致。
- `src/system.sdl.json`：系统装配（`tools/assemble.py` 生成并提交为源文件）。
- `src/placement.study.json`：逻辑分区及单路带全场的设施负荷预算（2771 kW）。空间定位由 `coordination.json` 和轴线、标高提供。
- `src/structure.json`、`structure-loads.json`：钢构件截面、独立基础、作用组合及承载限值。构件坐标由主机房轴网和标高派生。
- `src/electrical.json`、`electrical-study.json`：A/B 电网边界、故障源、保护时间带及用电工况。单线图展开 SDL 方舱边界，保留每条连接的源标识。
- `src/wbs.json`：WBS 节点与实例归属。
- `src/fws-network.json` 声明总管和外绕环路坐标、管径、保温、管件及屋面预留，编译到 3 个冷源和 16 个 IT 方舱的全部 38 条 SDL 连接。`src/fws-network-study.json` 声明温差、设备阻力、变频运行范围和必需隔离校核。
- `src/fws-routing.json`、`src/fws-hydraulics.json` 保留单回路校验夹具。正式审查包消费全网目标。
- `src/review.json` 声明各专业所需的文件与待审项。`assets/sheets/` 定义图号、比例、图框引用、视图和说明。

## 设计活动与编译交付

| 活动 | 当前编译交付 | 继续深化所需资料或验证 |
| --- | --- | --- |
| 宗地建模 | `dcr:site` 计算宗地与可建设范围面积，`sheet-parcel` 输出红线与退界图 | 实测界址、竖向基准、道路和市政接驳 |
| 轴网划分 | `dcr:grid` 核对轴线并定位，`sheet-grid` 输出轴号和轴间尺寸 | 协调结构跨度、伸缩缝和建筑定位 |
| 楼层标高、层内分区 | `dcr:levels` 从轴线和标高生成空间分区。`dcr:architecture` 编译建筑围护和 B 轴防火分隔、四处外门及相应平台和坡道，输出建筑平面、坡道剖面、门表和 USD | 墙体、屋面及坡道的构造节点、厂家门位和现场条件 |
| 结构建模 | `dcr:structure-model` 输出屋面钢梁与柱平面、基础平面、B 轴剖面、构件表及 USD。`dcr:structure-gravity` 编译重力计算书 | 实测地勘、抗风抗震、设备基础、节点与配筋 |
| 核心机电与方舱布局 | 分区图核对 65 个物理实例，BOM 保留 514 条明细。设施水与弱电协调包络和贯穿预留。弱电编译两张桥架平面、端接系统图、四处独立穿墙孔和 128 个机柜的双路纤芯表 | 实际设备尺寸、机柜跳线路由、检修净距、支吊架和贯穿详图 |
| 性能计算与冗余保护 | 电气 A/B 出单线图、容量证据、88 个三相故障工况及保护表。弱电校核链路光预算、单路承载 5.12 Tb/s 的配置容量及各登记构件单点断开后连通性 | 厂家曲线、接地、单相故障、多源并联、实际切换、运营商和网络协议测试 |
| 管道布置 | `dcr:fws-network` 从设备接口生成供回水环路、支管和立管。五张图输出总平面、系统图、屋面预留、局部接驳正视投影和分段隔离图 | 支吊架、排空、阀件检修及防火封堵 |
| 仿真与寄生反注 | `dcr:fws-network-study` 按实际管段、孔径、管件及阀门阻力计算 1440 kW 液侧负荷，覆盖正常、单冷源、单泵和 22 个单干管段隔离工况 | 制造商设备曲线、平衡阀规格、二次侧与动态切换 |
| 多专业签核 | `review-package` 从本次构建快照生成总图、建筑、消防、结构、机房工艺、弱电、电气和暖通审查清单与文件摘要。消防纳入疏散路径、分区图和屋面及弱电封堵预留表 | 责任人在现有 Git 审查中针对指定修订处置，审查清单保留待核状态 |

## 重跑

```bash
uv run --locked texsys check -p examples/datacenter-reference-design
uv run --locked texsys lock --check -p examples/datacenter-reference-design
uv run --locked texsys build -p examples/datacenter-reference-design
uv run --locked texsys build -p examples/datacenter-reference-design --target sheet-fws-network
uv run --locked texsys build -p examples/datacenter-reference-design --target dcr:structure-gravity --target dcr:power-study
uv run --locked texsys build -p examples/datacenter-reference-design --target dcr:fire-egress --target sheet-fire-egress
uv run --locked texsys build -p examples/datacenter-reference-design --target dcr:telecom-study --target sheet-telecom-a
```

成品图在 `build/drawings/sheets/`，包含 23 张 A3 图及各图的来源报告。可校准图纸按标题栏比例原尺寸打印。门、疏散、预留、构件、保护、水力及弱电端接和工况表在 `build/schedules/`。带桥架和穿墙孔的建筑、结构与设施水综合场景为 `build/scenes/aidc-telecom-coordinated.usda`。专业审查材料在 `build/review/aidc-discipline-review.json`。用 Tex Studio 的文件资源管理器或快速打开读取上述文件。

将 `telecom.json` 的西侧柜外移会改变对应的光缆长度和光损耗。用 `telecom-study.json` 声明 A 路入口失效后，机柜仍有 B 路可达，正常双路校核与随后再失去 B 路的工况失败，触发审查门禁。布置检查还覆盖桥架撞柱和穿墙孔能否容纳。

将 `architecture.json` 中西南出口标记为不可用后，消防图改绘封闭门，南分区各方舱仅余东门可达，审查包停止更新。移动方舱占用声明通道时，疏散路径报告记录设备编号。计算从假设的舱外访问点出发，并计入室外坡道。到出口距离与到场坪距离分列，室外退界使用和场坪后的道路条件待复核。

改动 `src/fws-network.json` 的供水环路坐标后重跑，安装图、管线长度和各段隔离压力随之更新。删除供水绕行段后，原 `S-010` 隔离工况中全体方舱失供，触发审查门禁。必需工况不满足或保温包络相交时构建非零退出，审查材料停止更新。查阅当前 `build/build.json` 的目标状态、输入摘要和产物发布记录。单回路夹具仍可用 `--target dcr:fws-hydraulics` 单独计算。

`design-checks.json` 逐条给出 12 项设计基线的判定和证据。`spatial/power-study.json` 按 A/B 可用路径复算全场工况，其中 UPS 关键负荷 2144 kW，包括存活路的 CDU 和精密空调。计入转换损耗与设施负荷后，单路进线负担约 2770.33 kW。按等效电池能量算得 16.79 min。短路结果保留故障点、实际 SDL 路径、电流范围、分断校核及保护配合。

`build/datacenter-reference/bom.json` 交付 514 条 BOM 条目，按主机房、供配电、冷源及建筑构造汇总。结构专业的构件表按深化后的柱、梁、基础分别计量。

修改库内容后需重新锁定（`texsys lock -p examples/datacenter-reference-design`），否则检查按锁失效处理。

## 边界

设备使用包络与接口位姿，模型和求解采用合成工程参数。结构计算覆盖屋盖均布重力、梁挠度、柱轴力与基础轴心接触。电气采用放射链、三相金属性短路和假设的时间带。设施水计算采用闭式准稳态、两台工作冷源均分负荷、末端定流量，使用所声明的泵速与阀压差范围核对可行性。单干管段完成隔离后的稳态供冷通过当前校核。径向末端支管、共因故障、多段故障、排空和切换瞬态仍需深化。全专业工程接受还需要真实场址、设备资料及责任人审查。包测试 `test_standard_basis.py` 在相邻规范工作区存在时逐条校验规则键、条文短引与 A 级阈值。
