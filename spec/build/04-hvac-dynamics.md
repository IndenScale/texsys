# HVAC 集总参数行为契约

> 版本：`lumped-water-network/1`。决策见 [ADR-0020](../../docs/adr/0020-python-physics-and-typescript-engineering-graphics.md)。

## 工程模型、工况与结果

工程事实位于 `src/system.sdl.json`，包括根系统 water-network 行为、叶子设备行为、参数绑定、端口与连接。根行为给定密度、比热、黏度和重力加速度；每个参数要求有限数值、确切单位、known／assumed 状态和非空来源。缺参或未解决状态为 indeterminate，非法数值／单位／本构类型为 error 或静态 fail，不隐式补设备参数。

行为描述符位于项目 source／assets，严格包含 `schema_version=1`、`kind`、`version` 与 `assumptions`，不含可执行代码。当前支持 pipe、pump、valve、tank、load、tower、chiller、junction。每个族的完整必需参数在 `domain.validate_component` 与分支构造时校验；精确、可运行的绑定样例见 [统一冷站输入](../../examples/hvac-dynamic-station/src/system.sdl.json)。

编译产生不可变 PhysicalModel：完整身份、参数及来源、行为资源摘要、分支、接点、连接与物性。快照摘要覆盖 SDL 和行为描述符。端口参考方向定义正流量，实际流量可以为负；当前支持根层封闭液态水网络，未定义递归子系统边界。

工况放在 `assets/scenarios/*.json`，不写回设计模型。运行产物放在 `build/tests/scenarios/{scenario-id}/{backend}/`，同一个设计可评估多个工况和后端。输入变化会产生新摘要；当前路径是最近一次执行结果，需要归档时固定整个运行目录。

## 水力与热力

- 水力同时求解各支路流量和接点表压。泵采用转速平方关断扬程与二次流量曲线；可声明出口止回阀。管道采用 Darcy–Weisbach，层流极限为 Poiseuille，湍流为 Haaland，Re 2300–4000 间线性混合。阀门采用 Kv 与开度，零开度是精确零流约束。
- 支路包含高差、被动压降；独立连通压力岛分别选取表压基准。输出压力是相对参考压力，不代表充水绝对压力或汽蚀余量。
- 每个持液容积形成热容量；管道可显式分为 1–100 个集总单元，包含管壁热容量和对外 UA。采用有符号流向选择上游，零容积接点采用质量流量加权混合。
- 负荷作为热输入；泵的水力功、声明入水的效率损耗及管阻耗散纳入热平衡。冷机按最小双侧流量、使能、供水设定值、容量上限和一阶响应时间计算制冷量，冷凝侧获得蒸发侧取热与电功率之和。
- 冷机 COP 为固定显式参数；冷却塔采用 UA × 风机比例 × 水温与等效环境温差。它不是完整的制冷剂循环、湿空气或冷却塔湿球模型。
- 使用定物性、不可压液态水与准稳态水力。没有水锤、压力波、两相、结冰、温度相关物性或经过厂家标定的性能曲面；温度离开 (0,100) °C 时显式 fail。

所有热状态在一个状态向量中积分；前端不存在逐设备 `.step()` 物理逻辑。

## 工况参数

顶层字段严格为 `schema_version`、`id`、`duration_s`、`sample_interval_s`、`signals`、`initial`、`solver`、`assertions`。ID 使用可移植字母、数字、连字符和下划线。采样上限为 100000 个间隔，事件时间另行保留。

| 设备 | 必需时变输入 | 单位／约束 |
| --- | --- | --- |
| pump | `speed_ratio` | 1，0–1 |
| valve | `opening` | 1，0–1 |
| load | `load_w` | W，非负 |
| chiller | `enabled`、`setpoint_c` | 1（离散 0/1）、degC |
| tower | `fan_ratio`、`ambient_c` | 1（0–1）、degC |
| pipe、tank | `ambient_c` | degC |

信号键为 `{完整设备身份}.{输入名}`。每个信号包含 `unit`、`interpolation=hold|linear`、`points=[[time,value],...]`、`state`、`source`；时间从 0 开始，严格递增且不超过工况时长。使能信号只接受 hold。没有遗漏输入的隐式默认值。

初始条件可选 `steady-state` 加各介质 `temperature_guess_c`，由全网络联立建立一致初态；或 `specified` 加每一个热状态／冷机功率状态的完整 states 映射。无法实现的稳态初值不能被当作已收敛。

SciPy 默认 solver：BDF，rtol 1e-6，温度 atol 1e-7 K，功率 atol 1e-3 W，max_step_s 10；可选 Radau、RK45。水力压力容差 0.05 Pa、质量容差 1e-7 kg/s、初值方程残差容差 0.1 W。所有实际选项写入结果。积分在输入节点处分段重启，保留左右极限，事件后使用右侧控制值。

断言包含 `variable`、`operator=<=|>=`、有限 `value` 与准确 `unit`。上限检查全部输出采样的最大值，下限检查最小值；不保证发现采样间的全部极值。未知变量或单位不符为 indeterminate。已采样温度范围、质量、压力、瞬时能量与积分能量共同检查；能量门槛为 0.1 W 和 1 J。

## 入口与结果

```bash
uv run texsys build -p examples/hvac-dynamic-station --target hvac:physical-model
uv run texsys test -p examples/hvac-dynamic-station --scenario assets/scenarios/load-step.json --backend scipy
uv run texsys test -p examples/hvac-dynamic-station --scenario assets/scenarios/pump-trip.json --backend scipy
```

物理模型 target 输出 `build/hvac/behavior/model.json`。test 输出完整 model.json 和 results.json：模型／工况／实现摘要、求解器版本与选项、一致初态、time_s、具单位／角色／语义身份的变量数组、输入、事件、性能断言、守恒检查与假设。停泵工况故意违反负荷出口温度上限，退出码为 1；不能隐藏这一失败。

`texsys sim --kind event` 仍是安装运动／碰撞事件，与此处的运行工况不同。没有 scenario 的旧设备布置示例 `texsys test` 执行 Python 设计校核。

## 独立 Modelica 后端

```bash
# omc 在 PATH 中；也可用 TEXSYS_OMC 指向可执行入口
uv run texsys test -p examples/hvac-dynamic-station --scenario assets/scenarios/load-step.json --backend openmodelica
uv run tools/verify_hvac_backends.py
```

导出同一声明模型的压力／流量代数方程、热微分方程、控制信号和初始方程，实际调用 omc 编译并以 DASSL 执行。不会把 Python 计算的初值或轨迹写进 Modelica。结果附编译器版本、入口摘要和导出源码摘要；`.mo`、`.mos`、CSV、日志保存在运行目录。

CSV 的事件迭代先按控制输入辨别左右侧，再保留规范事件时刻的右侧值。Python 对返回的流量和热方程作残差审计，并对外部积分能量作守恒检查。比较脚本实际运行两个后端，按相同身份与摘要对齐温度、质量流量和电功率；容差分别为 0.002 °C、1e-5 kg/s、2 W。

缺 omc 或无法启动为 indeterminate，编译／求解失败、提前结束、非有限轨迹或本构审计失败为 error。外部导出当前用参考连通图选择压力基准，多处同时关闭导致新压力岛时可能由 DAE 求解器显式报奇异，尚未验证任意切换拓扑；不得推广为通用 Modelica 网络编译器。外部后端不是默认安装依赖。
