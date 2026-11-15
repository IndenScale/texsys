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

1920 kW IT 负荷：128 台 15 kW 液冷机柜，16 个 IT 方舱（每舱 7.40 × 3.68 × 2.90 m，布置 8 柜，液侧 75% 由 2N CDU 承担，风侧 25% 由 2N 精密空调承担）。

- **供电 2N**：两路独立 10 kV 市电，各经过 3150 kVA 变压器和 5000 A 低压进线及转换开关。每路转换母线直供设施负荷，另接 5 台 500 kVA UPS 和 4 段 1000 A 母线槽，带 16 个 IT 方舱的机柜和舱内冷却。每路电池 600 kWh。
- **备用电源**：A/B 各一组发电机方舱（2×3000 kW，N+1），每组储油 9000 L；控制屏统一发出启动命令。
- **冷源 N+1**：3 个冷却方舱（1200 kW 水冷机组 + 2 台冷冻泵，1 用 1 备），对外 FWS 供回水经集管接 16 个方舱的 CDU 设施侧；3 台闭式冷却塔与 3 台冷却水泵一一对应，喷淋补水经断流水箱接宗地北侧市政来水，排污经自由出流接水器接东侧工艺排水交接井。
- **建筑构造**：8 跨钢框架，轴网 8.0 m × 9.5 m、4 道 2.0 h 防火隔墙和 16 区防静电活动地板。轴网编译产生 22 根屋面钢梁、15 根柱与 15 个独立基础。概念耐火校核沿用已声明的耐火等级与楼板耐火时间。

## 文件

- `src/site.json`、`grid.json`、`levels.json` 定义宗地、局部坐标、轴网、标高和分区边界。
- `src/site-grading.json` 声明合成测点、硬化平台、竖向控制和发电机区密闭收集。三角地形生成等高线、室外汇水分区及挖填方。
- `src/site-storm.json` 从锁定屋面落水口编译雨水接管、重力管道、限流调蓄和东界交接。三种降雨工况按同一屋面雨强计算，覆盖覆土、管道能力、调蓄和场外交接限值。
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
- `src/wbs.json`：WBS 节点与实例归属。
- `src/fws-network.json` 声明总管和外绕环路坐标、管径、保温、管件及屋面预留，编译到 3 个冷源和 16 个 IT 方舱的全部 38 条 SDL 连接。`src/fws-network-study.json` 声明温差、设备阻力、变频运行范围和必需隔离校核。
- `src/fws-thermal-parameters.json` 保留管段水量预算。`src/fws-transient.json` 声明切换初温、备用延时、升载和候选缓冲容积。读取编译所得管段净内径、实际长度和有符号流量以复算热输运。缓冲罐水量从冷源出口布置的净内径和工作液位提取。
- `src/fws-routing.json`、`src/fws-hydraulics.json` 保留单回路校验夹具。正式审查包消费全网目标。
- `src/review.json` 声明各专业所需的文件与待审项。`assets/sheets/` 定义图号、比例、图框引用、视图和说明。

## 设计活动与编译交付

| 活动 | 当前编译交付 | 继续深化所需资料或验证 |
| --- | --- | --- |
| 宗地建模 | `dcr:site` 计算宗地与可建设范围面积，`sheet-parcel` 输出红线与退界图。竖向编译现状及形成面、挖填方、设备平台和四处疏散坡道落地。洁净场区由 TIN 追踪至四处雨水受水井 | 实测界址和地形、道路详图、压实、岩土及报批 |
| 轴网划分 | `dcr:grid` 核对轴线并定位，`sheet-grid` 输出轴号和轴间尺寸 | 协调结构跨度、伸缩缝和建筑定位 |
| 楼层标高、层内分区 | `dcr:levels` 从轴线和标高生成空间分区。`dcr:architecture` 编译建筑围护和 B 轴防火分隔、四处外门及相应平台和坡道，输出建筑平面、坡道剖面、门表和 USD。屋盖由钢梁顶以上的檩条、钢承板、找坡保温层及防水层闭合，出屋面平面、横剖、洞口详图和墙头构造协调 | 墙头变形与耐火封堵、屋面防水、坡道节点及现场条件 |
| 结构建模 | `dcr:structure-model` 输出屋面钢梁与柱平面、基础平面、B 轴剖面、构件表及 USD。`dcr:roof-actions` 从屋盖提取材料自重、逐檩条挠度和支座反力。主体钢梁逐点接收该反力，`dcr:structure-gravity` 编译传力至轴心基础的重力计算书 | 实测地勘、抗风抗震、设备基础、钢承板局部承载、洞口加强、座板、节点与配筋 |
| 核心机电与方舱布局 | 分区图核对 65 个物理实例，BOM 保留 515 条 SDL 明细。16 舱装配锁定的机柜、CDU 和精密空调尺寸，编译设备布置、通道横剖面、设备表和可查看的典型舱剖切场景。设施水与弱电协调包络和贯穿预留。弱电编译两张桥架平面、端接系统图和 128 个机柜的双路纤芯表。消防编译主机房报警平面和方舱灭火设备布置 | 厂商制造尺寸、舱内二次侧接管、机柜跳线、支吊架和灭火专项选型 |
| 性能计算与冗余保护 | 电气 A/B 出单线图及屋面布置、单路带全部 16 舱的电压降和载流校核、88 个三相故障工况及保护表。馈线实长、母线至插接口距离与下引电缆阻抗进入三相故障复算。弱电校核链路光预算、单路承载 5.12 Tb/s 的配置容量及各登记构件单点断开后连通性 | 厂家曲线、接地、单相故障、多源并联、弯曲半径、谐波、全场非 IT 动力和实际切换、运营商和网络协议测试 |
| 管道布置 | `dcr:fws-network` 从设备接口生成供回水环路、支管和立管。五张图输出总平面、系统图、屋面预留、局部接驳正视投影和分段隔离图。三个冷凝器侧闭式回路从实际法兰取点，编译冷却水平面、横剖面、系统图、管段工程量和跨专业碰撞检查。场区给排水编译补水排污平面、排污纵断面、系统图和管段表。屋面雨水经双侧檐沟和四处落水接入场区雨水管网。室外雨水编译总图、纵断面、调蓄池及限流孔剖面，给水及工艺排污沿各自路径交接。发电机区汇入独立密闭池。TCS 从机柜和 CDU 的实际端口生成独立 A/B 管网、接管平面、机柜顶横剖面和系统图。风侧生成空调顶部短管、回风口和机柜排风后腔，编译送回风图及协调模型 | 支吊架、排空、阀件检修及防火封堵 |
| 仿真与寄生反注 | `dcr:fws-network-study` 复算 32 个稳态工况。`dcr:fws-thermal-parameters` 从同一套管段提取水量、热容和滞留时间。`dcr:fws-transient` 校核在役冷源分别退出后的 16 舱水温，并编译温升曲线、峰值平面、缓冲设备表和接管图。冷却水侧使用同批 FWS 冷源负荷，核算排热、流量、压损和温度，另验证六个冷却水泵或塔退出工况。喷淋水量及水质由上述排热量驱动，校核市政动压、各塔余压、重力排水、停水储备和恢复时间。TCS 读取 FWS 正常、主干隔离和冷源退出算例，以及冷源切换一次侧峰温；编译逐机柜可供流量、冷量、压差、实际管段储液量和驻留时间。舱内风侧按配对备用空调、风道与后腔压损及公共回风混合，生成机柜进风温湿度及空调静压，并与 TCS 联算。屋面和场坪雨水按降雨汇流，使用物理池容和限流孔复算逐时出流和峰值水位 | 真实机组控制曲线、承压缓冲罐选型与锚固、管壁与设备蓄热、流量切换与水锤、CDU 厂家实测曲线及二次侧泵阀切换瞬态 |
| 多专业签核 | `review-package` 从本次构建快照生成总图、建筑、消防、结构、机房工艺、弱电、电气、暖通和给排水审查清单与文件摘要。消防纳入疏散、报警、灭火设备、联动、断线证据、封堵和发电机区密闭收集。电气纳入 A/B 平剖、桥架与洞口协调、馈线表及三相保护证据 | 责任人在现有 Git 审查中针对指定修订处置，审查清单保留待核状态 |

## 重跑

```bash
uv run --locked texsys check -p examples/datacenter-reference-design
uv run --locked texsys lock --check -p examples/datacenter-reference-design
uv run --locked texsys build -p examples/datacenter-reference-design
uv run --locked texsys build -p examples/datacenter-reference-design --target sheet-fws-network
uv run --locked texsys build -p examples/datacenter-reference-design --target dcr:structure-gravity --target dcr:power-study
uv run --locked texsys build -p examples/datacenter-reference-design --target dcr:roof-scene --target dcr:roof-gravity-check --target dcr:roof-drainage-check --target sheet-roof-envelope --target sheet-roof-opening-detail
uv run --locked texsys build -p examples/datacenter-reference-design --target dcr:site-grading-check --target dcr:site-storm-check --target dcr:site-storm-scene --target sheet-site-storm-profile
uv run --locked texsys build -p examples/datacenter-reference-design --target dcr:power-installation --target dcr:power-scene --target sheet-power-roof-a --target sheet-power-roof-b --target sheet-power-pod-section
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

成品图在 `build/drawings/sheets/`，包含 58 张 A3 图及各图的来源报告。可校准图纸按标题栏比例原尺寸打印。门、疏散、预留、构件、保护、馈线、洞口定位、水力、弱电端接、热瞬态及消防工况表在 `build/schedules/`。综合交付包含 `build/scenes/aidc-site-coordinated.usda` 全场形成面、`build/scenes/aidc-site-storm-underground.usda` 雨水地下工程剖切场景、`build/scenes/aidc-roof-assembly.usda` 屋盖专项场景、`build/scenes/aidc-power-roof.usda` 电气专业屋面场景及 `build/scenes/aidc-it-pod-air.usda` 典型舱内送回风、TCS 与设备剖切场景。专业审查材料在 `build/review/aidc-discipline-review.json`。用 Tex Studio 的文件资源管理器或快速打开读取上述文件。

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

将 `architecture.json` 中西南出口标记为不可用后，消防图改绘封闭门，南分区各方舱仅余东门可达，审查包停止更新。移动方舱占用声明通道时，疏散路径报告记录设备编号。计算从假设的舱外访问点出发，并计入室外坡道。到出口距离与到场坪距离分列，室外退界使用和场坪后的道路条件待复核。

改动 `src/fws-network.json` 的供水环路坐标后重跑，安装图、管线长度和各段隔离压力随之更新。删除供水绕行段后，原 `S-010` 隔离工况中全体方舱失供，触发审查门禁。必需工况不满足或保温包络相交时构建非零退出，审查材料停止更新。查阅当前 `build/build.json` 的目标状态、输入摘要和产物发布记录。单回路夹具仍可用 `--target dcr:fws-hydraulics` 单独计算。

`design-checks.json` 逐条给出 12 项设计基线的判定和证据。`spatial/power-study.json` 按 A/B 可用路径复算全场工况，其中 UPS 关键负荷 2144 kW，包括存活路的 CDU 和精密空调。计入转换损耗与设施负荷后，单路进线负担约 2770.33 kW。按等效电池能量算得 16.79 min。短路结果保留故障点、实际 SDL 路径、电流范围、分断校核及保护配合。

`build/datacenter-reference/bom.json` 交付 514 条 BOM 条目，按主机房、供配电、冷源及建筑构造汇总。结构专业的构件表按深化后的柱、梁、基础分别计量。

修改库内容后需重新锁定（`texsys lock -p examples/datacenter-reference-design`），否则检查按锁失效处理。

## 边界

设备使用包络与接口位姿，模型和求解采用合成工程参数。结构计算覆盖屋盖均布重力、梁挠度、柱轴力与基础轴心接触。电气采用放射链、三相金属性短路和假设的时间带。设施水计算采用闭式准稳态、两台工作冷源均分负荷、末端定流量，使用所声明的泵速与阀压差范围核对可行性。单干管段完成隔离后的稳态供冷通过当前校核。径向末端支管、共因故障、多段故障、排空和切换瞬态仍需深化。全专业工程接受还需要真实场址、设备资料及责任人审查。包测试 `test_standard_basis.py` 在相邻规范工作区存在时逐条校验规则键、条文短引与 A 级阈值。
