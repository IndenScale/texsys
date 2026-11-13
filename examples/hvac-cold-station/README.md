# 冷站设备布置与条件容量审查

本项目包含设备布置、安装事件、通风与露点概念校核，以及基于合成快照的条件容量审查。

| 输入                                              | 用途                                                 |
| ------------------------------------------------- | ---------------------------------------------------- |
| `src/hvac.json`、`src/system.sdl.json`            | 通用设备布置概念模型，用于 BOM、投影、安装和环境校核 |
| `assets/reference/synthetic-capacity-source.json` | 独立编写的教学输入                                   |
| `assets/reference/cold-station-acceptance.json`   | 绑定教学输入摘要的合成快照                           |
| `src/reference-capacity.study.json`               | 负荷率、可用台数与容量余量要求，按摘要绑定快照       |

## 工程问题

指定两台 90 kW 冷机可用。计算 6 个 20 kW 风冷负载和 3 个 40 kW 液冷负载在同一热边界内的账面需求。示例仅考虑负荷率和可用台数。

| 运行条件           | 假设负荷 | 可用账面冷量 |   余量 | 工程判定        |
| ------------------ | -------: | -----------: | -----: | --------------- |
| 两类负荷率 0.6     |   144 kW |       180 kW |  36 kW | `indeterminate` |
| 两类负荷率 1.0     |   240 kW |       180 kW | −60 kW | `fail`          |
| 恢复两类负荷率 0.6 |   144 kW |       180 kW |  36 kW | `indeterminate` |

## 可重跑入口

从仓库根目录执行：

```bash
uv run --locked texsys lock -p examples/hvac-cold-station
uv run --locked texsys engineering inspect --adapter hvac:reference-capacity -p examples/hvac-cold-station
uv run --locked texsys engineering run hvac:reference-capacity -p examples/hvac-cold-station
```

初始工程判定应为 `indeterminate`，CLI 退出码为 3。`arithmetic_execution: completed` 表示上述算术已执行；`solver_execution: not-run` 表示真实热力／水力性能求解没有运行。运行成功与工程可接受分别判断，当前资料不足以判定真实装置性能满足要求。

完整流程验证使用独立副本，不修改标准示例：

```bash
uv run --locked tools/verify_reference_engineering_poc.py
```

脚本检查基线、源文件修改与 Git diff、旧证据失效、容量反例、恢复以及快照变化后摘要不匹配。脚本本身通过仅说明流程符合预期，不能改写领域结果中的 `indeterminate` 或 `fail`。

## 审查与继续建模

用通用文本编辑器修改 `src/reference-capacity.study.json` 中的负荷率、可用台数或容量余量要求，通过 `git diff` 审查修改，再执行上述 `engineering inspect/run` 命令查看输入、逐项判据和计算结果。Tex Studio 中可直接使用文件资源管理器、文本编辑器、Source Control 与终端完成这些操作。

实际项目使用经授权的资料建立独立快照，并核实设备型号、额定工况、性能曲线、回路和测点身份、校准时序及同时运行条件。 [资料管理](../../docs/references.md)
