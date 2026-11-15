# AIDC 参考设计

本例以 1920 kW 装配为基础，演示工程源文件如何编译为几何、A3 图册、计算和专业审查材料。宗地、工况和设备使用可替换的合成数据。一次正常构建运行全部声明的交付目标。每份图纸可单独重建。

```
GB 50174-2017（texsys-gb50174-2017 规范包，外部工作区）
        │  src/design-basis.json：分级、规模与 12 条设计要求
        ▼
本参考设计（examples/datacenter-reference-design）
        │  src/system.sdl.json：72 个顶层实例、108 条连接
        ▼
datacenter-reference 器件/单元库 → structure / architecture / hvac-equipment
```

## 规模与组成

1920 kW IT 负荷：128 台 15 kW 液冷机柜，16 个 IT 方舱（每舱 8 柜，液侧 75% 由 2N CDU 承担，风侧 25% 由 2N 精密空调承担）。

- **供电 2N**：两路独立 10 kV 市电，各经配电方舱（3150 kVA 干式变压器→低压开关柜→ATS→5×500 kVA 模块化 UPS，每模块 120 kWh 电池柜，单路 600 kWh）和 4 段母线槽向方舱 A/B 路配电。
- **备用电源**：A/B 各一组发电机方舱（2×3000 kW，N+1），每组储油 9000 L；控制屏统一发出启动命令。
- **冷源 N+1**：3 个冷却方舱（1200 kW 水冷机组 + 2 台冷冻泵，1 用 1 备），对外 FWS 供回水经集管接 16 个方舱的 CDU 设施侧；3 台闭式冷却塔与 3 台冷却水泵一一对应，补水接市政水源。
- **建筑构造**：8 跨 8 m 钢框架（二级耐火）、4 道 2.0 h 防火隔墙、16 区防静电活动地板；楼板耐火 1.5 h。

## 文件

- `src/site.json`、`grid.json`、`levels.json` 定义宗地、局部坐标、轴网、标高和分区边界。
- `src/coordination.json` 以 SDL 实例 ID 绑定轴线交点、偏移、专业和分区。物理实例使用锁定部件的 USD 几何。市电及补水等逻辑接口记录待落实的空间条件。
- `src/design-basis.json`：A 级分级依据、规模假设与 12 条 GB 50174 要求（3.2.1、4.2.1、5.1.1、6.1.3、7.5.2、8.1.1、8.1.7、8.1.15、13.1.2、13.2.1、13.2.4、附录 A 电气），每条绑定一个校核，并以 `rules` 记录规范包 texsys-gb50174-2017 的规则键、以 `citation` 保存条文短引与定位；阈值与规范包 A 级结构化限值一致。
- `src/system.sdl.json`：系统装配（`tools/assemble.py` 生成并提交为源文件）。
- `src/placement.study.json`：逻辑分区及设施负荷分解（2729 kW）。空间定位由 `coordination.json` 和轴线、标高提供。
- `src/wbs.json`：WBS 节点与实例归属。
- `src/fws-routing.json` 绑定一条冷却方舱至 IT 方舱的 SDL 往返路径，声明集管节点、走向、标高和概算长度。仿真工况和性能条件保存在 `src/fws-hydraulics.json`。
- `src/review.json` 声明各专业所需的文件与待审项。`assets/sheets/` 定义图号、比例、图框引用、视图和说明。

## 设计活动与编译交付

| 活动 | 当前编译交付 | 继续深化所需资料或验证 |
| --- | --- | --- |
| 宗地建模 | `dcr:site` 计算宗地与可建设范围面积，`sheet-parcel` 输出红线与退界图 | 实测界址、竖向基准、道路和市政接驳 |
| 轴网划分 | `dcr:grid` 核对轴线并定位，`sheet-grid` 输出轴号和轴间尺寸 | 协调结构跨度、伸缩缝和建筑定位 |
| 楼层标高、层内分区 | `dcr:levels` 从轴线和标高生成三维分区并检验重叠，`sheet-zoning`、`sheet-architecture-l01` 出图 | 完整围护、围护节点、疏散路径及防火分区 |
| 结构建模 | `dcr:layout` 编译八个钢框架跨的部件几何，`sheet-structure-l01` 输出平面和跨号 | 勘察、作用组合、截面、节点、基础和结构计算 |
| 核心机电与方舱布局 | 同一场景和分区图核对 65 个物理实例。综合、机房工艺、电气、冷源分别出图，BOM 保留 512 条明细 | 实际设备尺寸、检修净距、安装与跨专业碰撞 |
| 性能计算与冗余保护 | `dcr:design-checks` 核对 A/B 供电独立性、UPS 容量、续航与冷源 N+1 等既有概念判据 | 电气单线、短路、选择性、接地及独立故障工况 |
| 管道布置 | `dcr:fws-routing` 由已放置端口布置一条 FWS 供回水路径，编译带标高的平面、3D 和中心线提取报告 | 全网、管段规格、分支、穿越、支吊架和管道协调 |
| 仿真与寄生反注 | `dcr:fws-hydraulics` 消费路由提取的长度和弯头，按 IT 设计负荷重算流量、流速和压降。保存概算值、提取值、单位和路由摘要 | 厂家泵曲线、设备阻力、全网配流及瞬态 |
| 多专业签核 | `review-package` 从本次构建快照生成总图、建筑、结构、机房工艺、电气、暖通审查清单和文件摘要 | 责任人在现有 Git 审查中针对指定修订处置，审查清单保留待核状态 |

## 重跑

```bash
uv run --locked texsys check -p examples/datacenter-reference-design
uv run --locked texsys lock --check -p examples/datacenter-reference-design
uv run --locked texsys build -p examples/datacenter-reference-design
uv run --locked texsys build -p examples/datacenter-reference-design --target sheet-fws
```

成品图在 `build/drawings/sheets/`，包含 10 张 A3 图及各图的来源报告。图纸使用显式校准比例 1:200 或 1:400，纸面验读使用原尺寸打印。综合场景和 FWS 场景在 `build/scenes/`。专业审查材料在 `build/review/aidc-discipline-review.json`。用 Tex Studio 的文件资源管理器或快速打开读取上述文件。

改动 `src/fws-routing.json` 的供水标高后重跑，供水布置和几何提取随之更新，水力计算使用新提取量。若压降预算不足，构建非零退出且审查材料停止更新。查阅当前 `build/build.json` 的目标状态、输入摘要和产物发布记录来识别历史文件。

`build/datacenter-reference/design-checks.json` 逐条给出判定与证据。预期 12 条全部通过：单路 UPS 0.9 功率因数可用 2250 kW ≥ 1920 kW，电池后备约 18.8 min ≥ 15 min；冷机 N+1 可用 2400 kW ≥ 2304 kW；储油约 18.9 h ≥ 12 h；变压器单台可用 2835 kW ≥ 设施负荷 2729 kW；钢框架耐火等级二级（级数 ≤ 2）。上游独立性沿 SDL 连接与方舱边界映射追溯，A/B 路分别回到各自的市电与发电机方舱。

`build/datacenter-reference/bom.json` 交付 512 条 BOM 条目、34 个族分组，按 WBS-01 主机房、WBS-02 供配电、WBS-03 冷源、WBS-04 建筑构造汇总。选型状态 `candidate`，参数状态 `assumed`。

修改库内容后需重新锁定（`texsys lock -p examples/datacenter-reference-design`），否则检查按锁失效处理。

## 边界

全部参数为公开产品水平脱敏的概念假设，几何为包络与接口位姿。校核通过仅表示装配与给定设计依据一致，不构成真实工程接受：短路保护选择性、水力平衡、气流组织、储油消防、抗震与地基、现场调试等留待专业深化。规范条文摘录的正式适用性由项目专业复核。包测试 `test_standard_basis.py` 在相邻规范工作区存在时逐条校验规则键、条文短引与 A 级阈值；工作区不在场（如独立 CI 检出）时跳过，不阻断构建。
