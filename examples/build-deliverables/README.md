# 同源冷站的 API、命令与参数

默认输入为 [hvac-dynamic-station](../hvac-dynamic-station/README.md)：同一份 SDL 的 8 个实例生成 BOM、系统图、原理图、物理快照与动态结果。公开入口为 CLI 与本地 Python API `texsys.api`，当前不提供 HTTP／REST 服务。

## 一次运行全部示例

在仓库根目录，需要 uv 和 Node.js：

```bash
uv sync --locked
uv run tools/sync_node_tools.py
uv run texsys lock --check -p examples/hvac-dynamic-station
uv run examples/build-deliverables/run.py all
```

`all` 执行负荷阶跃、调速、停泵三种工况，再构建 BOM、物理模型与两个图纸视图，将运行结果嵌入离线 HTML。`build/demo.json` 分别记录 `verification` 和 `engineering_status`：预期状态为 pass、pass、fail 时，示例验证通过，而工程工况集合仍包含停泵失效。只有 `all` 的进程退出码表示示例回归结果；单个构建／工况入口始终按其自身状态退出。

## CLI

```bash
# BOM：全部设备；或按完整设计身份选取
uv run texsys build -p examples/hvac-dynamic-station --target hvac:equipment-bom --profile feasibility
uv run texsys build -p examples/hvac-dynamic-station --target hvac:equipment-bom --scope '{"identities":["ColdStation.CH-01"]}'

# 物理模型快照
uv run texsys build -p examples/hvac-dynamic-station --target hvac:physical-model

# 系统图：设备组成与连接；原理图：增加关键行为参数
uv run texsys build -p examples/hvac-dynamic-station --target hvac:system-schematic --scope '{"view":"system"}'
uv run texsys build -p examples/hvac-dynamic-station --target hvac:system-schematic --scope '{"view":"schematic"}'

# 时变载荷与求解后端
uv run texsys test -p examples/hvac-dynamic-station --scenario assets/scenarios/load-step.json --backend scipy
uv run texsys test -p examples/hvac-dynamic-station --scenario assets/scenarios/pump-speed.json --backend scipy

# 把固定运行结果关联到图纸回放；模型和工况摘要必须匹配
uv run texsys build -p examples/hvac-dynamic-station --target hvac:system-schematic --scope '{"view":"schematic","results":["build/tests/scenarios/load-step/scipy/results.json"]}'
```

系统图和原理图分别保存在 `build/hvac/system/`、`build/hvac/schematic/`，各含 `system.json`、`.svg`、`.drawio`、`.html`、`system-check.json`。可以直接在浏览器打开 HTML；无需服务端、联网或浏览器内求解器。

## Python API

```python
from pathlib import Path
from texsys.api import build, evaluate, ProjectError

project = Path("examples/hvac-dynamic-station")
bom = build(project, targets=("hvac:equipment-bom",), profile="feasibility")
run = evaluate(project, scenario="assets/scenarios/load-step.json", backend="scipy")

schematic = build(
    project,
    targets=("hvac:system-schematic",),
    profile="review",
    scope={"view": "schematic", "results": [run.report.relative_to(project.resolve()).as_posix()]},
)
print(schematic.status)  # pass / fail / indeterminate / error
print(schematic.report)  # build.json 的绝对路径
print(schematic.items)   # 构建产物 Path 集合
print(run.items)         # 运行产物 Path 集合
```

API 进行静态检查和依赖锁检查；输入／项目契约错误可能抛出 ProjectError，成功返回后仍需判断 status。详细诊断、开放项、partial 和摘要位于报告。每次 build 重写当前 build.json，固定交付记录时需要归档报告及产物。

## 参数速查

| 操作         | 入口／参数                                  | 默认值与约束                                       |
| ------------ | ------------------------------------------- | -------------------------------------------------- |
| 项目根目录   | CLI `-p`／`--path`，Python 第一个 Path 参数 | 必须已初始化且依赖锁有效                           |
| 构建目标     | 可重复 `--target`／`targets=(...)`          | 省略时读取项目 build.default_targets               |
| 构建 profile | `--profile`／`profile`                      | BOM feasibility；图纸 review；物理模型 engineering |
| 构建范围     | `--scope` JSON／`scope` dict                | 不同 target 字段不同，建议分别调用                 |
| BOM scope    | include、identities、snapshot               | 采购／竣工／维护 profile 需要固定外部快照          |
| 图纸 scope   | view、overlay、approval、results            | 默认 schematic；engineering 还要求有效人工审核     |
| 工况文件     | test `--scenario`／evaluate 的 scenario     | 必须是项目 assets 内的固定 JSON                    |
| 求解器       | `--backend`／backend                        | scipy；可选真实 openmodelica，需另装 omc           |
| 输出目录     | 项目 `[layout].build`                       | 默认 build；没有单独 `--output` 或 `--format`      |

单个操作退出码：pass 0、fail 1、error 2、indeterminate 3。BOM、图纸与动态求解的通过结论分别成立；图纸 review 通过只表示草稿自动检查通过。

## 本目录脚本

```bash
uv run examples/build-deliverables/run.py bom
uv run examples/build-deliverables/run.py physical
uv run examples/build-deliverables/run.py system
uv run examples/build-deliverables/run.py schematic
uv run examples/build-deliverables/run.py transient --scenario assets/scenarios/pump-speed.json
uv run examples/build-deliverables/run.py schematic --results build/tests/scenarios/load-step/scipy/results.json
```

支持 `--project`、`--profile`、`--scope`、可重复 `--results`、`--scenario`、`--backend`。kind 固定图纸 view，不能再传冲突的 scope.view；all 使用各目标声明的默认 profile 并自行附上运行结果。外部核验使用 `uv run tools/verify_hvac_backends.py`。

物理假设、求解参数和结果结构见 [动态行为契约](../../spec/build/04-hvac-dynamics.md)，布局与审核见 [图纸契约](../../spec/build/03-system-schematic.md)。
