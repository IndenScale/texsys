# datacenter-reference

数据中心参考设计器件/单元库。作为下游应用库，消费 `structure`、`architecture`、`hvac-equipment` 等专业库，提供能装配出一个合理数据中心参考设计的设备、方舱与构造体族，并附带设计依据校核与 WBS/BOM 投影。

## 定位

```
GB 50174-2017（texsys-gb50174-2017 规范包）
        │  设计依据（分级、冗余、防火、环境）
        ▼
数据中心参考设计（examples/datacenter-reference-design）
        │  装配需求
        ▼
datacenter-reference 器件/单元库（本包）
        │  组合
        ▼
专业库：structure / architecture / hvac-equipment / …
```

不建模特定功能的所有型号或构型；每族提供一个概念代表，参数为公开产品水平脱敏的 `assumed` 假设，几何为包络与接口位姿。检查通过不构成真实工程接受。

## 模块

| 类别 | 模块 | 说明 |
| --- | --- | --- |
| 设备 | dry-type-transformer | 3150 kVA 干式变压器 |
| 设备 | lv-switchboard | 4000 A 低压开关柜 |
| 设备 | ats | 自动转换开关 |
| 设备 | ups-module | 500 kVA 模块化 UPS |
| 设备 | battery-cabinet | 电池柜（15 min 后备） |
| 设备 | busway | 密集型母线槽 |
| 设备 | rack-pdu | 机柜 PDU |
| 设备 | diesel-generator | 3000 kW 柴油发电机组 |
| 设备 | water-cooled-chiller | 1200 kW 水冷冷水机组 |
| 设备 | cooling-tower | 1500 kW 闭式冷却塔 |
| 设备 | circulation-pump | 端吸冷冻循环泵 |
| 设备 | condenser-water-pump | 冷却水循环泵 |
| 设备 | raised-access-floor | 防静电活动地板 |
| 单元 | it-container | IT 方舱：8 柜 + 2N CDU + 2N 精密空调 + 气体灭火 |
| 单元 | power-container | 配电方舱：变压器→开关柜→ATS→5×500 kVA UPS→输出，每模块 120 kWh 电池 |
| 单元 | cooling-container | 冷却方舱：冷机 + 2 冷冻泵（1 用 1 备），对外 FWS 接口 |
| 单元 | generator-container | 发电机方舱：2×3000 kW N+1 + 9000 L 储油 |
| 构造 | steel-frame-bay | 8 m 跨钢框架（组合 structure 库） |
| 构造 | fire-partition-wall | 2.0 h 防火隔墙（组合 architecture 库） |

每个模块含 SDL 接口（端口、行为参数）、`module.toml` 资产绑定与四类资产：`geometry.usda`（包络）、`symbol.svg`（符号）、`specification.json`（接口、防火/耐候属性）、`behavior.json`（模型与适用边界）。方舱另含 `boundary` 边界映射，声明外部端口对应的内部实例端口。

## 构建目标

- `dcr:design-checks`：按项目 `src/design-basis.json` 的 GB 50174 条款校核装配（供电 2N、冷却 N+1、储油、防火分隔、耐火等级、净高、灭火、进风温度、功能分区、变压器容量）。必需检查不满足返回失败。
- `dcr:bom`：把装配实例投影为中性 BOM 条目（`texsys_bom` 契约），按 `src/wbs.json` 分组。选型状态 `candidate`，参数状态 `assumed`。

## 重跑

```bash
uv run --locked texsys check -p packages/datacenter-reference
uv run --locked texsys package test datacenter-reference -p examples/datacenter-reference-design
uv run --locked texsys build -p examples/datacenter-reference-design
```

模块资产由 `tools/generate_devices.py` 与 `tools/generate_units.py` 生成并提交为源文件；修改规格表后重跑生成器刷新。

## 编译 AIDC 项目交付

版本 0.2 增加宗地、轴网、标高及落位目标。命名图纸目标支持楼层、区块和专业范围。场景重用部件资产并展平 USD 引用，平面与场景核对同一实例包络和编号。设施水目标绑定 SDL 端口并使用 `hvac-dynamics` 计算声明条件下的局部回路。输入和图页装配示例见 [AIDC 参考设计](../../examples/datacenter-reference-design/README.md)。

版本 0.3 由轴网和标高编译屋面钢构件、独立基础、剖面及构件表。使用 `structure` 核对声明的重力传力与地基接触，并检查示例 FWS 管段包络是否与钢构件相交。电气目标展开 A/B 电网的 SDL 边界、生成单线并使用 `electrical` 核对合成故障源下的保护。项目审查包保留范围和未决条件。

版本 0.4 编译配对的设施水共有总管和放射形分配支路。SDL 边界定位冷源接管、备用泵及末端 CDU。编译结果提供正交管段、管件、保温包络、预留洞和取量表。计算使用同一份几何，比较正常及单设备退出工况，保留各节点连续性和共用段隔离失供范围。

版本 0.5 支持供回水各一条实际绕行环路。共用干管分段、两端阀位、开阀局部阻力与阀体包络来自同一几何。按声明的末端定流量和在线冷源均分，使用管路两端压差和连续性求有符号循环流量，逐段复算隔离后的设备裕量。阀门响应、瞬态和多重故障需要单独建模。

版本 0.6 编译主机房围护与防火分区、门洞和平台坡道，保留已落位的 SDL 北侧墙件。同一门和路径用于平面、剖面、门表及两出口校核。路径检查按声明净宽净高与设备及结构包络相交判定，连接到样例室外场坪。三专业合成场景保留设施水屋面预留及对应分区。舱内、实际人员、灭火及封堵选型仍待专业深化。

版本 0.7 从机柜归属、方舱包络、轴网与主机房围护生成 A/B 两路弱电架、方舱端接、纤芯分配和孔洞。光预算按桥架路径和舱内预留长度累计，另检查弯曲空间、桥架填充、交换容量及逐单元断开后的连通性。综合场景复用建筑、结构和设施水，并在同一墙件中保留两路独立的预留孔。

版本 0.8 编译消防分区的闭合报警回路和已声明方舱控制器的监视接口。主机房烟感按几何分配单元核验，回路按实际安装段的连通图逐段验证断线。方舱灭火柜、探测器、喷头与管线采用包络和局部坐标，联动按离散时序核验反馈、停止抑制及监视断线。喷放能力、烟气响应和现场动作另行验证。

版本 0.9 提取设施水管段容积、蓄热和正常滞留时间，保留预算和源摘要。单冷源退出用独立水力工况的有符号流量和几何滞留时间输运热量，校核候选出口缓冲容积、备用升载和逐舱入口温度。稳态初始化、热量守恒和时间步长复核一同编译。液压切换和候选设备的安装与选型待深化。

版本 0.10 将三台冷源的供水缓冲编入水路拓扑和综合场景，每台有串联接管、罐体、垫座和检修包络。热瞬态使用罐体净内径及工作液深提取的水容积，水力以额定压降曲线预算独立计算。编译冷源区布置、单罐剖面和设备表。压力容器设计、实际阻力和内部混合效果待产品专项验证。

项目可由 `dcr:cw-network`、`dcr:cw-coordination`、`dcr:cw-study` 与 `dcr:cw-check` 编译冷凝器侧闭式回路、综合场景、热／水力工况和独立工程判定。项目用同一套 SDL 法兰与路由输出暖通图纸，额定设备接口和计算假设的适用性由消费项目声明。

版本 0.12 从宗地边界与冷却塔 SDL 接口编译市政补水、断流水箱、喷淋支管、间接排污及重力总管。平面、纵断面、系统图、管段表和空间校核从同一模型发布。消费项目提供压力、水质、排水接收条件与停水时长，按冷却水排热量计算喷淋及停水储备。
