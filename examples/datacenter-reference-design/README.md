# 数据中心参考设计（GB 50174 A 级概念）

本例按 **规范 → 参考设计 → 器件/单元库** 的链条装配一个合理的数据中心概念设计，验证 `datacenter-reference` 下游应用库能支撑完整装配、设计依据校核与 WBS/BOM 投影。

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

- `src/design-basis.json`：A 级分级依据、规模假设与 12 条 GB 50174 要求（3.2.1、4.2.1、5.1.1、6.1.3、7.5.2、8.1.1、8.1.7、8.1.15、13.1.2、13.2.1、13.2.4、附录 A 电气），每条绑定一个校核，并以 `rules` 记录规范包 texsys-gb50174-2017 的规则键、以 `citation` 保存条文短引与定位；阈值与规范包 A 级结构化限值一致。
- `src/system.sdl.json`：系统装配（`tools/assemble.py` 生成并提交为源文件）。
- `src/placement.study.json`：功能分区、设施负荷分解（2729 kW）与概念坐标。
- `src/wbs.json`：WBS 节点与实例归属。

## 重跑

```bash
uv run --locked texsys check -p examples/datacenter-reference-design
uv run --locked texsys lock --check -p examples/datacenter-reference-design
uv run --locked texsys build -p examples/datacenter-reference-design
```

`build/datacenter-reference/design-checks.json` 逐条给出判定与证据。预期 12 条全部通过：单路 UPS 0.9 功率因数可用 2250 kW ≥ 1920 kW，电池后备约 18.8 min ≥ 15 min；冷机 N+1 可用 2400 kW ≥ 2304 kW；储油约 18.9 h ≥ 12 h；变压器单台可用 2835 kW ≥ 设施负荷 2729 kW；钢框架耐火等级二级（级数 ≤ 2）。上游独立性沿 SDL 连接与方舱边界映射追溯，A/B 路分别回到各自的市电与发电机方舱。

`build/datacenter-reference/bom.json` 交付 512 条 BOM 条目、34 个族分组，按 WBS-01 主机房、WBS-02 供配电、WBS-03 冷源、WBS-04 建筑构造汇总。选型状态 `candidate`，参数状态 `assumed`。

修改库内容后需重新锁定（`texsys lock -p examples/datacenter-reference-design`），否则检查按锁失效处理。

## 边界

全部参数为公开产品水平脱敏的概念假设，几何为包络与接口位姿。校核通过仅表示装配与给定设计依据一致，不构成真实工程接受：短路保护选择性、水力平衡、气流组织、储油消防、抗震与地基、现场调试等留待专业深化。规范条文摘录的正式适用性由项目专业复核。包测试 `test_standard_basis.py` 在相邻规范工作区存在时逐条校验规则键、条文短引与 A 级阈值；工作区不在场（如独立 CI 检出）时跳过，不阻断构建。
