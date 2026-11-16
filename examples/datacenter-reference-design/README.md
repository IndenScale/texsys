# AIDC 参考设计

本例以 1920 kW 装配为基础，演示工程源文件如何编译为几何、A3 图册、计算和专业审查材料。宗地、工况和设备使用可替换的合成数据。一次正常构建运行全部声明的交付目标。每份图纸可单独重建。

```
GB 50174-2017（texsys-gb50174-2017 规范包，外部工作区）
        │  src/design-basis.json：分级、规模与 12 条设计要求
        ▼
本参考设计（examples/datacenter-reference-design）
        │  src/system.sdl.json：73 个顶层实例、114 条连接
        ▼
datacenter-reference 器件/单元库 → structure / architecture / hvac-equipment
```

## 规模与组成

1920 kW IT 负荷：128 台 15 kW 液冷机柜，16 个 IT 方舱（每舱 7.40 × 3.68 × 2.90 m，布置 8 柜，液侧 75% 由 2N CDU 承担，风侧 25% 由 2N 精密空调承担）。

- **供电 2N**：两路独立 10 kV 市电，各经过 3150 kVA 变压器和 5000 A 低压进线及转换开关。每路转换母线直供设施负荷，另接 5 台 500 kVA UPS 和 4 段 1000 A 母线槽，带 16 个 IT 方舱的机柜和舱内冷却。每路电池 600 kWh。
- **备用电源**：A/B 各一组发电机方舱（2×3000 kW，N+1），每组储油 9000 L；控制柜统一发出启动命令，独立黑启动仍待深化。
- **冷源 N+1**：3 个冷却方舱（1200 kW 水冷机组 + 2 台冷冻泵，1 用 1 备），对外 FWS 供回水经集管接 16 个方舱的 CDU 设施侧；3 台闭式冷却塔与 3 台冷却水泵一一对应，喷淋补水经断流水箱接宗地北侧市政来水，排污经自由出流接水器接东侧工艺排水交接井。
- **建筑构造**：8 跨钢框架，轴网 8.0 m × 9.5 m、4 道 2.0 h 防火隔墙和 16 区防静电活动地板。轴网编译产生 22 根屋面钢梁、15 根柱与 15 个独立基础。概念耐火校核沿用已声明的耐火等级与楼板耐火时间。

## 文件

- `src/site.json`、`grid.json`、`levels.json` 定义宗地、局部坐标、轴网、标高和分区边界。
- `src/site-grading.json` 声明合成测点、硬化平台、竖向控制和发电机区密闭收集。三角地形生成等高线、室外汇水分区及挖填方。
- `src/site-storm.json` 从锁定屋面落水口编译雨水接管、重力管道、限流调蓄和东界交接。三种降雨工况按同一屋面雨强计算，覆盖覆土、管道能力、调蓄和场外交接限值。
- `src/lateral-bracing.json` 定位主机房交叉拉杆。安装定位读取轴网、标高和柱梁节点。`src/lateral-actions.json` 声明候选风、静力替代地震、有效重量及验收门限。`src/structure.json` 按柱编号记录受侧力的加大独立基础。
- `src/roof-transfer.json` 从屋面板跨、主梁、柱头和安装标高编译双层拉杆及沿轴集力路径，声明候选预张拉、节点和杆件限值。
- `src/equipment-foundations.json` 从场区设备、锁定模块额定质量和地形生成基础轮廓、垫层、锚栓及两面双向钢筋。缓冲罐、补水箱的液量从已编译容积提取。
- `src/exit-access.json` 声明门扇开启角、坡道扶手、场区铺装线路、集合范围和人数。门槛与门洞仍由 `architecture.json` 定位，坡脚标高引用竖向模型。
- `src/emergency-lighting.json` 声明灯具候选光度和电池、安装高度、交错分组、持续时间和照度条件。照度采样面继承建筑出口、坡道、步道和集合面。
- `src/lighting-distribution.json` 声明应急照明充电盘、正常充电支路与消防监督接点。上游从已安装的辅助转换柜端口定位，末端接入已编译灯具和两区消防控制盘。
- `src/earthing.json` 定义室外接地环网、接地极、柜体与柱脚连接、候选 TN-S 电源阻抗及保护导体。桥架及排管的 PE 分配随实体路径校核，照明使用线缆内 PE。
- `src/lightning-roof.json` 定义屋面接闪带、桅杆、支座与材料。重量随实体尺寸反注结构。`src/lightning.json` 定义引下线、滚球包络、场外地形边界假设和低压 SPD 工况。
- `src/coordination.json` 以 SDL 实例 ID 绑定轴线交点、偏移、专业和分区。物理实例使用锁定部件的 USD 几何。市电及补水等逻辑接口记录待落实的空间条件。
- `src/architecture.json` 将既有北侧墙件接入完整主机房围护，用 B 轴定位分隔墙。东西门位由南北走道导出，并按门洞、平台和坡道坐标校核到室外场坪的通行。
- `src/air-routing.json` 声明送风短管、空调回风口、机柜背部排风后腔及空气流通尺寸。`src/air-study.json` 声明候选风机、风量、露点、回流和额外显热。
- `src/tcs-routing.json` 声明舱内 A/B 干管层位、跨舱接管及管材，编译 CDU 到机柜的二次侧回路。`src/tcs-study.json` 声明泵曲线、二次侧温升、末端控制和停用设备。
- `src/pod-layout.json` 声明舱内检修净宽、净高和设备深度要求，并可按 SDL 方舱与设备标识覆盖安装位姿。设备轮廓及 TCS 端口来自锁定设备库。
- `src/telecom.json` 声明 A、B 两路汇聚柜、桥架中心标高、方舱端接位置和光纤构成。`telecom-study.json` 声明光模块和接续损耗预算、交换容量与可用状态。舱内跳线路由待核，当前按预算长度计。
- `src/fire-systems.json` 从主机房防火分区和方舱 SDL 控制器生成烟感、手报、声光、两条环形回路及舱内灭火布置。`fire-systems-study.json` 声明报警回路限值、断线和方舱联动模拟条件。
- `src/design-basis.json`：A 级分级依据、规模假设与 12 条 GB 50174 要求（3.2.1、4.2.1、5.1.1、6.1.3、7.5.2、8.1.1、8.1.7、8.1.15、13.1.2、13.2.1、13.2.4、附录 A 电气），每条绑定一个校核，并以 `rules` 记录规范包 texsys-gb50174-2017 的规则键、以 `citation` 保存条文短引与定位；阈值与规范包 A 级结构化限值一致。
- `src/system.sdl.json`：系统装配（`tools/assemble.py` 生成并提交为源文件）。
- `src/placement.study.json`：逻辑分区及单路带全场的设施负荷预算（2771 kW）。空间定位由 `coordination.json` 和轴线、标高提供。
- `src/structure.json`、`structure-loads.json`：钢构件截面、独立基础、作用组合及承载限值。构件坐标由主机房轴网和标高派生。
- `src/roof.json` 声明主机房屋脊与坡度、钢承板及保温防水层、檩条、预留洞口构造和雨水工况。屋盖直接读取已编译的结构轴网、设施水立管及 A/B 下引，生成板区、檩条、墙头闭合、物理洞口、檐沟和落水立管。
- `src/electrical.json`、`electrical-study.json`：A/B 电网边界、故障源、保护时间带及用电工况。单线图展开 SDL 方舱边界，保留每条连接的源标识。
- `src/power-routing.json` 声明 A/B 室外转接与屋面桥架位置、导体配置、阻抗、候选载流修正及洞口尺寸。布置直接引用 SDL 连线和锁定设备物理端口，编译分路平面、入舱剖面、馈线与洞口定位表及三维协调场景。
- `src/facility-power.json` 定义动力区 A/B 地埋排管、各冷源的泵塔动力支路、辅助柜与控制柜配电、导体及保护。三列冷源各有开路转换，泵塔从同列柜出线。路线、最小覆土、上下交叉和阻抗均从已编译端口与地形提取。
- `src/wbs.json`：WBS 节点与实例归属。
- `src/fws-network.json` 声明总管和外绕环路坐标、管径、保温、管件及屋面预留，编译到 3 个冷源和 16 个 IT 方舱的全部 38 条 SDL 连接。`src/fws-network-study.json` 声明温差、设备阻力、变频运行范围和必需隔离校核。
- `src/fws-thermal-parameters.json` 保留管段水量预算。`src/fws-transient.json` 声明切换初温、备用延时、升载和候选缓冲容积。读取编译所得管段净内径、实际长度和有符号流量以复算热输运。缓冲罐水量从冷源出口布置的净内径和工作液位提取。
- `src/fws-routing.json`、`src/fws-hydraulics.json` 保留单回路校验夹具。正式审查包消费全网目标。
- `src/review.json` 声明各专业所需的文件与待审项。`assets/sheets/` 定义图号、比例、图框引用、视图和说明。

## 设计活动与编译交付

| 活动 | 当前编译交付 | 继续深化所需资料或验证 |
| --- | --- | --- |
| 宗地建模 | `dcr:site` 计算宗地与可建设范围面积，`sheet-parcel` 输出红线与退界图。竖向编译现状及形成面、挖填方、设备平台和四处疏散坡道落地，续接东西场区步行线路及集合点。洁净场区由 TIN 追踪至四处雨水受水井 | 实测界址和地形、道路详图、压实、岩土及报批 |
| 轴网划分 | `dcr:grid` 核对轴线并定位，`sheet-grid` 输出轴号和轴间尺寸 | 协调结构跨度、伸缩缝和建筑定位 |
| 楼层标高、层内分区 | `dcr:levels` 从轴线和标高生成空间分区。`dcr:architecture` 编译建筑围护和 B 轴防火分隔、四处外门及相应平台和坡道，输出建筑平面、坡道剖面、门表和 USD。屋盖由钢梁顶以上的檩条、钢承板、找坡保温层及防水层闭合，出屋面平面、横剖、洞口详图和墙头构造协调 | 墙头变形与耐火封堵、屋面防水、坡道节点及现场条件 |
| 结构建模 | `dcr:structure-model` 输出钢梁、柱、基础及剖面。`dcr:roof-actions` 提取屋盖自重和檩条反力。沿轴集力梁、双层屋面拉杆、纵横向支撑和独立基础共同编译定位平面、纵横立面、节点投影、基础及杆力控制表、专业与综合 USD。候选侧向荷载从围护及屋面质量分配到节点，联算屋面杆力、柱脚反力、基础接触与抗滑。场区三组基础定位、发电机方舱锚栓横剖及控制值表共享 19 座设备基础模型 | 实测地勘、风参数和反应谱、抗风揭、设备基础、集力梁局部稳定、节点板、端板、锚栓、张拉验收与配筋 |
| 核心机电与方舱布局 | 分区图核对 67 个物理实例，BOM 保留 515 条 SDL 明细。16 舱装配锁定的机柜、CDU 和精密空调尺寸，编译设备布置、通道横剖面、设备表和可查看的典型舱剖切场景。设施水与弱电协调包络和贯穿预留。弱电编译两张桥架平面、端接系统图和 128 个机柜的双路纤芯表。消防编译主机房报警平面和方舱灭火设备布置 | 厂商制造尺寸、舱内二次侧接管、机柜跳线、支吊架和灭火专项选型 |
| 性能计算与冗余保护 | 电气编译主机房和室外应急布灯平面、坡道安装剖面、正常充电负荷与独立电池续航。校核任一灯组失效后的维持照度。接地编译环网、接地极、等电位和 PE，覆盖 332 个相壳故障工况及落地、停用工况。防雷编译屋面接闪网、桅杆、引下线、滚球面片和两列低压 SPD，并将桅杆和接闪带质量反注结构。充电配电编译辅助柜出线、配电箱、8 条支路和 92 处末端以及双消防监督线路，并重算压降、磁脱扣与消防指令。关键供电 A/B 出单线图及屋面布置、单路带全部 16 舱的电压降和载流校核、88 个三相故障工况及保护表。馈线长度、母线段和下引电缆阻抗进入三相故障复算。动力区 A/B 到三列冷源、泵塔及辅助柜共 15 回路，交付排管平面、泵塔平面、入线剖面和保护表。逐列校核单路、冷源柜或泵塔退出，发电机启动控制的共因依赖保留待核。弱电校核链路光预算、单路承载 5.12 Tb/s 的配置容量及各登记构件单点断开后连通性 | 厂家曲线、接地、单相故障、多源并联、弯曲半径、谐波、厂家应急光度、正常照明与水处理等其余动力、发电机黑启动和实际切换、运营商和网络协议测试 |
| 管道布置 | `dcr:fws-network` 从设备接口生成供回水环路、支管和立管。五张图输出总平面、系统图、屋面预留、局部接驳正视投影和分段隔离图。三个冷凝器侧闭式回路从实际法兰取点，编译冷却水平面、横剖面、系统图、管段工程量和跨专业碰撞检查。场区给排水编译补水排污平面、排污纵断面、系统图和管段表。屋面雨水经双侧檐沟和四处落水接入场区雨水管网。室外雨水编译总图、纵断面、调蓄池及限流孔剖面，给水及工艺排污沿各自路径交接。发电机区汇入独立密闭池。TCS 从机柜和 CDU 的实际端口生成独立 A/B 管网、接管平面、机柜顶横剖面和系统图。风侧生成空调顶部短管、回风口和机柜排风后腔，编译送回风图及协调模型 | 支吊架、排空、阀件检修及防火封堵 |
| 仿真与寄生反注 | `dcr:fws-network-study` 复算 32 个稳态工况。`dcr:fws-thermal-parameters` 从同一套管段提取水量、热容和滞留时间。`dcr:fws-transient` 校核在役冷源分别退出后的 16 舱水温，并编译温升曲线、峰值平面、缓冲设备表和接管图。冷却水侧使用同批 FWS 冷源负荷，核算排热、流量、压损和温度，另验证六个冷却水泵或塔退出工况。喷淋水量及水质由上述排热量驱动，校核市政动压、各塔余压、重力排水、停水储备和恢复时间。TCS 读取 FWS 正常、主干隔离和冷源退出算例，以及冷源切换一次侧峰温；编译逐机柜可供流量、冷量、压差、实际管段储液量和驻留时间。舱内风侧按配对备用空调、风道与后腔压损及公共回风混合，生成机柜进风温湿度及空调静压，并与 TCS 联算。屋面和场坪雨水按降雨汇流，使用物理池容和限流孔复算逐时出流和峰值水位 | 真实机组控制曲线、承压缓冲罐选型与锚固、管壁与设备蓄热、流量切换与水锤、CDU 厂家实测曲线及二次侧泵阀切换瞬态 |
| 多专业签核 | `review-package` 从本次构建快照生成总图、建筑、消防、结构、机房工艺、弱电、电气、暖通和给排水审查清单与文件摘要。建筑、总图和消防共同复核实际门扇、坡道扶手、室外路线与集合容量。消防纳入报警、灭火设备、联动、断线证据、封堵和发电机区密闭收集。电气和消防校审照明、蓄电、实体布线、监督故障和复位。电气纳入 A/B 平剖、桥架与洞口协调、动力排管与局部支路、馈线表及三相保护证据 | 责任人在现有 Git 审查中针对指定修订处置，审查清单保留待核状态 |

## 重跑

```bash
uv run --locked texsys check -p examples/datacenter-reference-design
uv run --locked texsys lock --check -p examples/datacenter-reference-design
uv run --locked texsys build -p examples/datacenter-reference-design
uv run --locked texsys build -p examples/datacenter-reference-design --target sheet-fws-network
uv run --locked texsys build -p examples/datacenter-reference-design --target dcr:structure-gravity --target dcr:power-study
uv run --locked texsys build -p examples/datacenter-reference-design --target dcr:lateral-check --target dcr:lateral-scene --target sheet-lateral-plan --target sheet-lateral-elevations --target sheet-lateral-foundations
uv run --locked texsys build -p examples/datacenter-reference-design --target dcr:roof-transfer-scene --target dcr:roof-transfer-results --target sheet-roof-transfer-plan --target sheet-roof-transfer-detail --target sheet-roof-transfer-demands
uv run --locked texsys build -p examples/datacenter-reference-design --target dcr:equipment-foundation-check --target dcr:equipment-foundation-scene --target sheet-equipment-foundation-power --target sheet-equipment-foundation-cooling --target sheet-equipment-foundation-generators --target sheet-equipment-foundation-section --target sheet-equipment-foundation-checks
uv run --locked texsys build -p examples/datacenter-reference-design --target dcr:roof-scene --target dcr:roof-gravity-check --target dcr:roof-drainage-check --target sheet-roof-envelope --target sheet-roof-opening-detail
uv run --locked texsys build -p examples/datacenter-reference-design --target dcr:site-grading-check --target dcr:site-storm-check --target dcr:site-storm-scene --target sheet-site-storm-profile
uv run --locked texsys build -p examples/datacenter-reference-design --target dcr:power-installation --target dcr:power-scene --target sheet-power-roof-a --target sheet-power-roof-b --target sheet-power-pod-section
uv run --locked texsys build -p examples/datacenter-reference-design --target dcr:facility-power-check --target dcr:facility-power-scene --target sheet-facility-power-entry --target sheet-facility-power-continuity
uv run --locked texsys build -p examples/datacenter-reference-design --target dcr:fire-egress --target sheet-fire-egress
uv run --locked texsys build -p examples/datacenter-reference-design --target dcr:telecom-study --target sheet-telecom-a
uv run --locked texsys build -p examples/datacenter-reference-design --target dcr:fire-systems-study --target sheet-fire-alarm
uv run --locked texsys build -p examples/datacenter-reference-design --target dcr:fws-transient-check --target sheet-fws-transient-plan
uv run --locked texsys build -p examples/datacenter-reference-design --target dcr:cw-check --target dcr:cw-coordination --target sheet-cw-plan
uv run --locked texsys build -p examples/datacenter-reference-design --target dcr:wet-utility-check --target dcr:wet-utility-coordination --target sheet-wet-profile
uv run --locked texsys build -p examples/datacenter-reference-design --target dcr:pod-coordination --target dcr:pod-scene --target sheet-pod-plan --target sheet-pod-section
uv run --locked texsys build -p examples/datacenter-reference-design --target dcr:tcs-coordination --target dcr:tcs-check --target sheet-tcs-section --target sheet-tcs-system
uv run --locked texsys build -p examples/datacenter-reference-design --target dcr:air-coordination --target dcr:air-check --target sheet-air-environment
```

成品图在 `build/drawings/sheets/`，包含 98 张 A3 图及各图的来源报告。可校准图纸按标题栏比例原尺寸打印。门、疏散、预留、构件、保护、馈线、洞口定位、水力、弱电端接、热瞬态及消防工况表在 `build/schedules/`。综合交付包含 `build/scenes/aidc-lightning-coordinated.usda` 全场防雷协调、`build/scenes/aidc-lightning.usda` 防雷专业场景、`build/scenes/aidc-earthing-coordinated.usda` 接地全场协调、`build/scenes/aidc-earthing.usda` 接地专业场景、`build/scenes/aidc-lighting-distribution-coordinated.usda` 灯控配电全场协调、`build/scenes/aidc-lighting-distribution.usda` 灯控安装专业场景、`build/scenes/aidc-emergency-lighting-coordinated.usda` 应急照明全场协调、`build/scenes/aidc-emergency-lighting.usda` 应急照明专业场景、`build/scenes/aidc-exit-access-coordinated.usda` 场区疏散协调、`build/scenes/aidc-exit-access.usda` 建筑出口专业场景、`build/scenes/aidc-equipment-foundations-coordinated.usda` 全场设备基础协调、`build/scenes/aidc-equipment-foundations.usda` 场区基础锚栓专业场景、`build/scenes/aidc-roof-transfer-coordinated.usda` 全场屋面传力协调、`build/scenes/aidc-roof-transfer.usda` 主体屋面传力专业场景、`build/scenes/aidc-lateral-coordinated.usda` 全场结构支撑协调、`build/scenes/aidc-braced-frame.usda` 主体结构支撑、`build/scenes/aidc-facility-power-coordinated.usda` 全场动力协调、`build/scenes/aidc-facility-power.usda` 冷源动力专业场景、`build/scenes/aidc-site-storm-underground.usda` 雨水地下工程剖切场景、`build/scenes/aidc-roof-assembly.usda` 屋盖专项场景、`build/scenes/aidc-power-roof.usda` 电气专业屋面场景及 `build/scenes/aidc-it-pod-air.usda` 典型舱内送回风、TCS 与设备剖切场景。专业审查材料在 `build/review/aidc-discipline-review.json`。用 Tex Studio 的文件资源管理器或快速打开读取上述文件。

主机房屋盖在 +4.900 m 主体钢梁顶以上形成 +5.200 m 钢承板底及最高 +5.476 m 完成面。64 个设施水及关键电气预留逐层切空，并由同一套坐标更新设施水和电气剖面。本例编译屋面与檩条候选自重约 480.82 kN，逐檩条支座力用于计算主体钢梁和基础。四处屋面落水在设计雨强下单点约 6.79 L/s，从场坪以上 0.40 m 落水口转接至室外受水井。闭合屋面综合视图隐藏此视角下位于舱内的细密 TCS 管线；管线与舱内设备可从已有 TCS 与典型舱场景查看。

主机房电气屋面使用两路物理分离的桥架、8 段横向母线和 32 条方舱下引。候选条件下，任一路独自承担 IT 和舱内冷却，全路径最大电压降约 1.61%，最满桥架截面约占 25.3%。上游故障源仍使用声明的候选阻抗；末端的 R、X 从编译路线提取。洞口需深化防水、防火封堵和围护节点，桥架需深化耐候、热环境、弯曲半径和支架。

原 6.00 × 2.40 m 方舱用小方块表示内部设备。用设备库的 0.80 × 1.20 × 2.20 m 机柜重排两排四柜，方舱改为 7.40 × 3.68 m。工作通道净宽 1.12 m，南北各有 1.10 m 清宽侧门，主机房疏散从朝向公共通道的一侧实际门位计算。保持 16 舱与原负荷，将 A、C 轴分别外移 1.50 m。设施水套管、消防下引、弱电、结构和全部门位随轴网复核。舱内厂家制造尺寸、支架、密封与检修操作程序留待专项验证。

TCS 按每舱 90 kW、每台 11.25 kW 机柜液侧负荷编译。两套独立冷却回路各自以约 3.60 L/s 带全部八台机柜。IT01 的 A/B 路分别需要约 39.0 / 41.4 kPa，实际中心线和内径提取出约 59.9 / 69.4 L 储液量。一级冷源切换后使用设施侧峰值温度验证单 CDU 可用状态。额定工况及压力检查采用候选泵曲线、换热接近温差和冷板快接阻力，局部切换时序、热惯量、泵耗热反馈、软管弯曲半径、支架和实际换热性能待设备选型后核证。

风侧按东西各四柜分组，每组配置两台由 A/B 供电的配对空调，平时 A 列运行。机柜排风口与侧板内表面之间留出 64 mm 候选后腔，收集热排风进入舱内共用回风空间。IT01 正常工况管口限制合计送风 2.88 m³/s，余热 33 kW，候选模型最高机柜进风约 22.9°C。出图裁剪和碰撞使用物理包络，风侧使用带压损的等效路径与空气混合，需要现场风量、短路回流、单元真实外静压与湿度动态校核。薄侧板及排风腔防火构造需专业验收。

温度预算按 CDU 设施水入口 28°C。直管提取 7.390 m³ 水量，三个串联缓冲罐各提取 603.2 L。按 24 s 启动及 32 s 升载，最不利入口约 27.22°C。泵表列出罐体压损，冷源区平面、接管剖面和设备表同源。修改罐体内径时，容积和热瞬态重算，旧的标量缓冲输入已移至实体模型。温升结果假定失效后的规定流量在切换时立即成立。罐体、底座和检修包络已协调，承压专项和现场流量仍待核。

冷却水依据 `src/cw-network.json` 与 `src/cw-study.json`。工况计算使用设备名义定流量和候选夏季湿球，压力保持、泵吸入气蚀、设备真实曲线、现场控制联锁和支架深化仍待专项校核。设备退出时，复用 FWS 相应停冷源的重新分配结果，求解其余两列。

场区给排水以 `src/wet-utilities.json` 声明断流水箱的净湿面积和有效高程、各塔补水法兰、排污接水器、管道布置及两个宗地边界。纵断面按水平 1:250、垂直 1:25 生成，排污管底、坡度及交叉净距来自同一份三维路由。`src/wet-utility-study.json` 声明来水压力、水质、排放限值和停水时间。按 38 个冷却水工况计算喷淋水量，正常工况补水 2.90 m³/h、排污 0.69 m³/h。约 4.99 m³ 的有效水量可覆盖 90 min 停水，余量约 0.64 m³。边界采用候选工况，市政实测、污水许可、清洗放空、卫生给排水及雨水另需工程校核。

省略 `SD-FC-S-11` 后报警平面标记未覆盖单元，审查包停止更新。断开它相邻的两个回路段可在工况表中定位失联设备。删除风阀关闭反馈会使联动反例的模拟许可与验收期望冲突，构建非零退出。消防覆盖依据项目声明的几何单元，联动模拟与任何现场设备隔离。介质量、喷放能力、实际烟气及声光覆盖、失电恢复和现场联动试验待专项校核。

将 `telecom.json` 的西侧柜外移会改变对应的光缆长度和光损耗。用 `telecom-study.json` 声明 A 路入口失效后，机柜仍有 B 路可达，正常双路校核与随后再失去 B 路的工况失败，触发审查门禁。布置检查还覆盖桥架撞柱和穿墙孔能否容纳。

将 `architecture.json` 中西南出口标记为不可用后，消防图改绘封闭门，南分区各方舱仅余东门可达，审查包停止更新。移动方舱占用声明通道时，疏散路径报告记录设备编号。路线从朝向公共通道的舱门访问点出发，经建筑出口和室外坡道延伸到东、西两个候选集合点。16 舱共 32 条路线，到建筑出口、坡道分流点及集合点的里程分别记录。两个集合点各按本例的 40 人满员假设核对，最长路线为 75.67 m。建筑出 A-008 门扇与坡道平面、A-009 坡道与扶手剖面。消防出 F-005 室外疏散与集合点平面、F-006 路线及集合核对表。门扇角度、扶手、铺装与模型的标高和净空计算使用同一源，门及场区铺装另出明细表。实际占用、门锁释放、启闭力及五金、扶手锚固、夜间现场照度、消防车道和围栏仍需核对。

```bash
uv run --locked texsys build -p examples/datacenter-reference-design \
  --target dcr:exit-access-check --target dcr:exit-access-scene \
  --target sheet-exit-access-plan --target sheet-exit-access-section \
  --target sheet-evacuation-site-plan --target sheet-evacuation-site-verification
```

应急照明从两个防火分区的通道和 16 个方舱门、四处坡道、步道、转角以及两个集合面提取采样点。灯具交错编组，设独立蓄电。按候选 90 min 的维持直射照度，校核全部灯具及任一组退出，逐点扣除墙、门扇、设备和机电设施的视线遮挡。出口各设两个独立标志灯，检查三个距离的中央视线并记录两侧探针。交付 E-011 至 E-014 和 F-007，包括平面、剖面、充电分组、电池续航和低照度点，另出逐点计算和 CSV。光度为候选曲线，指示系统、正常充电布线及监督控制的实际选型、动态烟气、现场照度和产品认证需责任专业核证。

```bash
uv run --locked texsys build -p examples/datacenter-reference-design \
  --target dcr:emergency-lighting-check --target dcr:emergency-lighting-scene \
  --target sheet-emergency-lighting-hall --target sheet-emergency-lighting-site \
  --target sheet-emergency-lighting-section --target sheet-emergency-lighting-circuits \
  --target sheet-emergency-lighting-checks
```

辅助转换柜的 `AuxFeed.out_lighting` 经独立埋管接至 `EL-MDB`。该箱向 8 条单相充电支路与 92 个灯具端口供电，正常灯具充电负荷 896 W。两路监督信号进入配电箱的分组接触器。模型校核火警、监督线路断路或短路、正常馈线失电、任一灯组接触器粘连和复位。光照复核纳入新增载体的遮挡。主馈线长度 9.91 m，全网埋管最小覆土 0.535 m，与既有动力管群竖向净距 288 mm，候选支路最大压降 0.370%。交付 E-015 至 E-019、F-008、导管及电缆明细、工况表、两份 OpenUSD。路由和孔位受已编译设施、通行及主机房地坪下检修边界限制，实际支护、封堵、接地、电源阻抗、级配及联动验收仍需专业核证。

```bash
uv run --locked texsys build -p examples/datacenter-reference-design \
  --target dcr:lighting-distribution-check --target dcr:lighting-distribution-scene \
  --target sheet-lighting-distribution-site --target sheet-lighting-distribution-hall \
  --target sheet-lighting-distribution-section --target sheet-lighting-distribution-single-line \
  --target sheet-lighting-distribution-sleeves --target sheet-lighting-distribution-sequence
```

改动 `src/fws-network.json` 的供水环路坐标后重跑，安装图、管线长度和各段隔离压力随之更新。删除供水绕行段后，原 `S-010` 隔离工况中全体方舱失供，触发审查门禁。必需工况不满足或保温包络相交时构建非零退出，审查材料停止更新。查阅当前 `build/build.json` 的目标状态、输入摘要和产物发布记录。单回路夹具仍可用 `--target dcr:fws-hydraulics` 单独计算。

`design-checks.json` 逐条给出 12 项设计基线的判定和证据。`spatial/power-study.json` 按 A/B 可用路径复算供电基线。UPS 关键负荷 2144 kW，包括存活路的 CDU 和精密空调。计入原设施负荷预算与转换损耗后，单路进线负担约 2770.33 kW，等效电池供电时间 16.79 min。`spatial/facility-power-study.json` 进一步按当前水系统工况计算三列冷源用电，纳入 40 kW 辅助预算后，单路工况含 IT 最大合计 2624.68 kW。短路结果保留故障点、实际 SDL 路径、电流范围、分断校核及保护配合。

`build/datacenter-reference/bom.json` 交付 515 条 BOM 条目，按主机房、供配电、冷源及建筑构造汇总。结构专业的构件表按深化后的柱、梁、基础分别计量。

修改库内容后需重新锁定（`texsys lock -p examples/datacenter-reference-design`），否则检查按锁失效处理。

按主机房轴网，X 向竖向支撑沿 A、C 轴，Y 向沿 2、4 轴。八跨屋面各设双层交叉拉杆，线性轴力梁将水平荷载送到柱顶，各竖向斜拉杆按自身上节点位移受拉。屋面拉杆初始预张拉 31 kN，集力梁内计其平衡压缩分量。32 个候选工况检查拉力裕度、集力梁轴力与重力弯曲、节点偏心力矩及基础压力。新增拉杆和端部构造 13.99 kN，从放样长度和节点用量进入柱顶重力和地震重量。下层平面侧移 75 mm 避开八处电气立段，上下两层钢杆最小表面净距 58 mm。S-008 为 1:150 集力平面，S-009 为 1:20 端部高度投影与交叉剖面，S-010 为拉力包络表。S-002 更新四座受控独立基础为 2800×2800×600 mm，逐工况边缘压力最大约 88.11 kPa。实际节点板、柱脚锚栓、长期张拉损失、钢承板局部承载及地勘仍待核。

供配电区四座、冷源与给水区十三座、备用发电区两座，共 19 座设备基础。96 个锚栓定位到对应设备底座，1524 根钢筋按保护层和预埋件净距放样。B 路方舱基础南侧及东侧局部退边，用于协调 B 路入线立段和相邻柜基础。S-011 至 S-013 分区出定位平面，S-014 出发电机方舱横剖，S-015 出锚栓与地基工况包络。一组工况覆盖设备空载、运行、候选风与双向静力加速度，并分别采用竖向荷载下限和上限。1900 个算例按底座脚点分力、地基接触、平移和扭转摩擦及板带设计检查。厂家底座反力、锚固锥体与冲切、施工配筋与动力隔振需另行核证。

## 边界

设备使用包络与接口位姿，模型和求解采用合成工程参数。结构计算覆盖屋盖均布重力、梁挠度、柱轴力与基础轴心接触。电气采用放射链、三相金属性短路及相壳故障的候选电源和保护时间带。设施水计算采用闭式准稳态、两台工作冷源均分负荷、末端定流量，使用所声明的泵速与阀压差范围核对可行性。单干管段完成隔离后的稳态供冷通过当前校核。径向末端支管、共因故障、多段故障、排空和切换瞬态仍需深化。全专业工程接受还需要真实场址、设备资料及责任人审查。包测试 `test_standard_basis.py` 在相邻规范工作区存在时逐条校验规则键、条文短引与 A 级阈值。

## 保护接地与相壳故障

场区环网由 125 m 直埋铜排、10 根接地极、A/B 两处汇流排和 12 处外圈钢柱连接编译而成。北墙三道引线穿越雨水管时按实际场坪和管道位置安排下绕。与既有安装路径关联的 PE 覆盖 IT 关键负荷、泵塔动力、控制和 92 个应急照明端点。校核包含载体占用、接地环网单段断开、接地极单根退出、相壳故障的主保护清除与热稳定。候选均匀土壤计算联合电阻为 2.087 Ω，最不利单极退出为 2.275 Ω。相壳故障按金属保护导体返回路径求解。

```bash
uv run --locked texsys build -p examples/datacenter-reference-design \
  --target dcr:earth-fault-check --target dcr:earthing-scene \
  --target sheet-earthing-site --target sheet-earthing-hall --target sheet-earthing-section \
  --target sheet-earthing-electrodes --target sheet-earthing-fault
```

E-020 至 E-024 提供场区、主机房、跨管剖面、接地极放样及保护校核。导体清单、逐工况计算及接地极退出记录随图交付。源侧中性点及转换极数、接地故障保护级配、内部三处柱脚接头、现场土壤及跨步／接触电压、场区独立设施防雷及现场测试列入专业待审事项。


## 主机房防雷与浪涌防护

沿屋面轴线生成约 191.01 m 接闪带、12 根桅杆及 12 根引下线。引下线通过柱脚连接套用保护接地环网和接地极编号。以 45 m 候选滚球及所声明的周边地面下界，对屋面和外露桥架、设施水管进行连续面片与接触方向校核。北侧 B-P3 为最不利包络。记录单桅杆失效时需复核的面片，并验证逐条引下线断开后接闪端至接地极的金属连续性。接闪带约 1.26 kN 分配到檩条，桅杆约 7.80 kN 经对应柱头进入重力与候选地震重量。屋盖含新增附件为 0.799 kPa，接近 0.800 kPa 候选自重限值。SPD 定位到柜内实际转换和关键出线母线，以本地引线和浪涌前沿复算合成保护电平。

```bash
uv run --locked texsys build -p examples/datacenter-reference-design \
  --target dcr:lightning-check --target dcr:lightning-scene \
  --target sheet-lightning-roof --target sheet-lightning-coverage --target sheet-lightning-section \
  --target sheet-lightning-surge --target sheet-lightning-checks
```

E-025 至 E-029 交付屋面、覆盖平面、北墙剖面、SPD 系统及校核图。导体、覆盖及浪涌计算表随图交付。全场防雷视图采用闭合屋面，默认隐藏舱内风路。在舱内风侧专业场景可查看风道和设备。真实风险定级和场外地形、桅杆连接和风作用传力、场区独立冷源和发电油区、触及和跨步电压、信号及中压进线、SPD 产品配合和现场测试列入专业审查。
