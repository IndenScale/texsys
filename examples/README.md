# 可执行示例

本目录供用户参考，也用于命令行与持续集成的端到端测试，契约见 [ADR-0005](../docs/adr/0005-executable-examples.md)。

## 目录

| 路径                      | 用途                                                     |
| ------------------------- | -------------------------------------------------------- |
| `minimal/`                | 两个泵串联的最小有效项目                                 |
| `hvac-cold-station/`      | 设备布置、粗几何安装事件与 Python 设计校核切片           |
| `hvac-system-schematic/`  | 冷机、两类泵、负荷和冷却塔的双环路出图，含行为定义与参数 |
| `fixtures/invalid/`       | 未定义引用、介质和方向冲突等失败反例                     |
| `fixtures/large/`         | 120 个泵实例的确定性规模桩数据                           |
| `hvac-dynamic-station/`   | 同源 SDL 的 BOM、双图纸、集总物理模型与三种动态工况      |
| `hvac-air-cooled-room/`   | 风冷机房轴网、钢构、架空地板、机柜布置与风量及显冷量预算 |
| `datacenter-hybrid-unit/` | A/B 配电、风液冷工况与 W 型楼承板概念单元                |
| `build-deliverables/`     | BOM、系统图、原理图的命令行与 Python 调用示例及可执行脚本   |

需要查命令、参数和函数返回值时，直接阅读 [统一交付物与工况调用示例](build-deliverables/README.md)。该目录是调用示例，不是新的 SDL 项目。

公开示例都可以通过：

```bash
uv run texsys check -p examples/minimal
uv run texsys lint -p examples/minimal
uv run texsys format --check -p examples/hvac-cold-station
```

冷站示例还固定五个暖通设计包及其通用 BOM 内核，并可执行完整最小闭环：

```bash
uv run texsys lock --check -p examples/hvac-cold-station
uv run texsys sync -p examples/hvac-cold-station
uv run texsys build -p examples/hvac-cold-station
uv run texsys test -p examples/hvac-cold-station
uv run texsys sim --kind event -p examples/hvac-cold-station
```

`texsys build` 默认按清单执行 `hvac:equipment-bom`、设备布置、文本投影和设计校核四个目标，可用重复的 `--target` 以及 `--profile`、`--scope` 选择其他闭环。物料清单目标的构建后钩子 `hvac.check-bom` 生成独立检查报告。

构建生成 `build/build.json`、USD BRep 场景、设计校核 JSON、设备与拓扑投影，以及 `build/hvac/bom.json`、`bom.md` 和 `bom-check.json`；测试和事件仿真分别写入 `build/tests/` 与 `build/simulations/`。采购、竣工和维护 BOM 只读取 `assets/` 中显式指定的固定快照。源输入、依赖锁和后端版本不变时，权威生成物和证据必须保持字节一致，且不得修改 `src/`。

失败反例用于验证非零状态和诊断，不应作为建模模板。`expected/` 保存人工审核的检查基线；`build/` 不保存生成物。测试会先把用例复制到临时目录，再运行写入命令。

## 系统图与原理图示例

`hvac-system-schematic` 独立于物理布置示例：由专业投影包、共享前端工程图元与通用绘图包组合，没有虚构设备原点，也不把回水支路伪装成额外的泵。五个叶子设备和十二个端口形成两条闭合水环路。

```bash
uv run tools/sync_node_tools.py
uv run texsys lock --check -p examples/hvac-system-schematic
uv run texsys sync -p examples/hvac-system-schematic
uv run texsys build -p examples/hvac-system-schematic
uv run texsys build -p examples/hvac-system-schematic --scope '{"view":"system"}'
```

默认输出 SVG、非压缩 draw.io、绘图文档（`DiagramDocument`）和检查报告。`assets/*.mo` 是局部稳态行为定义引用，SDL 中参数均为标记为假设的概念值；新出图目标不执行这些方程，不能据图中参数声称整个冷站已完成仿真。原有 `hvac-cold-station` 可显式选择同一目标，其缺少行为绑定会得到明确降级的系统图。

运行方式、失败状态和人工审核口径见 [出图构建契约](../spec/build/03-system-schematic.md)。示例没有附带伪造的人工批准，工程配置默认不能闭环。

## 同源物理冷站

默认推荐从 `hvac-dynamic-station` 开始：相同实例、连接和参数贯通 BOM、系统图、原理图、物理快照与动态运行；`build-deliverables/run.py all` 可以一次重跑。Python 权威行为与 TypeScript 图元的决策见 [ADR-0020](../docs/adr/0020-python-physics-and-typescript-engineering-graphics.md)。
