# 工程检查与结果契约

> 状态：当前实现。工程源文件通过普通编辑器修改，用 Git diff 表达差异；命令行执行检查并保存结果。POC 不提供专用工程变更 GUI、变更事务或本地接受流程，DevOps 门禁、可视化及通知在范围外。分工见 [ADR-0026](../../docs/adr/0026-git-build-workflow-and-devops-boundary.md)。

## 适配器与工程范围

项目锁定的工程包通过 `engineering_adapters()` 提供适配器，声明 `id`、`name`、`version`，实现 `supports(root)`、`inputs(root)` 和 `evaluate(root)`。前两者识别适用项目与输入；`evaluate` 执行范围明确的工程检查，输出实际判定、逐项结果、提取量与限制。不再包含源文件修改、变更计划或人工接受方法。

输入描述保留源文件、JSON Pointer、单位、值状态及对象引用，用于检查结果的来源追踪。`design` 可携带明确的定义键或实例路径；不从自然语言猜测对象身份。物理参数及依据由项目源文件管理，不由检查命令改写。

## 公开入口

| Python API | CLI | 用途 |
| --- | --- | --- |
| `inspect_engineering(path, adapter_id=None)` | `texsys engineering inspect --adapter ID -p PROJECT` | 读取适配器、当前输入、运行历史、结果适用性与诊断 |
| `run_engineering(path, adapter_id)` | `texsys engineering run ID -p PROJECT` | 执行声明范围的检查，保存输入与工具身份及结果 |

上述 API 从 `texsys.api` 导出。CLI 统一通过 `uv run --locked texsys` 调用，输出 JSON；退出码 0 表示检查通过，1 表示工程不满足，2 表示输入／执行错误，3 表示无法判定。具体诊断与工程状态保留在结果中。

工程检查可以作为外部构建命令序列的一部分。普通 `texsys build` 只执行选定目标，目前不会自动执行所有工程适配器；编译成功、求解执行完成或预期反例回归成功，不等于全部工程约束满足。

## 执行与结果

检查使用独立 Python 工作进程和项目锁定依赖。运行前后检查输入、依赖与实际工具身份；发现运行中变化则标记结果不适用于当前输入。该机制不是完整运行环境隔离，也不是远端主线保护。

`run` 超时为 600 秒，`inspect` 为 120 秒；错误和超时返回诊断。专业代码的标准输出进入日志，结果使用 JSON 协议。当前没有通用任务取消与恢复机制。

| 字段 | 含义 |
| --- | --- |
| `execution` | 执行完成或执行错误 |
| `result.assessment` | 工程判定 `pass`、`fail`、`indeterminate` |
| `result.verification` | 可选的算例回归符合性，与工程判定独立 |
| `result.solver_execution` | 求解是否实际执行；不能用诊断完成代替求解完成 |
| `result.checks` | 判据、对象、实际值、限值、单位、来源及状态 |
| `result.extraction` | 提取量及其来源条件 |
| `inputs_consistent` | 运行期间输入、依赖与工具是否一致 |
| `current`、`freshness` | 结果对当前输入和工具是否仍然适用 |

总体 `pass` 不能掩盖失败、无法判定或没有通过项的检查集合。执行异常保留错误与诊断；失败不丢弃可用计算结果。

运行结果保存在 `build/engineering/runs/<run_id>.json`，以新 ID 增写，`evidence_digest` 校验内容。来源包括输入修订、依赖、实际加载工具及其源码身份。损坏结果报告诊断，不作为有效结果。

输入、锁或工具变化后，历史结果按 `inputs-changed`、`dependency-invalid`、`tools-changed`、`adapter-changed` 等原因失效。恢复原内容后可能重新适用，但检查通过仅对应其声明范围。`inspect` 可比较相邻运行的检查值和提取量；这不构成独立的源文件差异或合并机制。

## 开发方式与外部分工

直接编辑源文件，用 `git diff` 审查变化，再运行所需构建和检查；失败后修复源文件并重跑。文本冲突交给 Git，工程冲突由已声明的检查检出。无需创建变更请求、确认计划或本地接受记录。

POC 不提供工程变更专用 HTTP 路由，也不实现 DevOps 合并门禁、失败看板或消息通知。构建日志、退出码与结果文件可供既有平台消费；本地检查失败不证明远端已经启用禁止合并规则。

## FWS 适配器的当前范围

`datacenter:fws-change` 目前暴露主配水管 X 坐标，以及主配水管、机柜列配水管、CDU 接管的内径。内径须小于既有外包络直径；改变内径不自动代表已完成管材选型。

计算调用 `datacenter:fws-branches` 的 `evaluation` profile。该 profile 允许在正确检出的负面设计结论之后继续取得依赖报告，输入错误和求解错误仍阻止有效计算。工程检查显式选择 `initial-normal`、`full-normal`、`full-supply-a-out` 三个设计工况，核对逐机房供冷和已建模几何净距；不从 `expected` 字段推断工程适用工况。

工况名称本身不证明条件成立。适配器核对初期／满配阶段、正常／失电定义，并通过供电接口映射核对 A 路故障是否真正施加到单元模型。结果保存实际工况定义及来源；不符合声明时返回无法判定并明确范围未成立。缺冷诊断引用实际配流报告中的管段流速／质量流量限制和泵扬程余量，不能仅凭缺冷推断泵不足。

提取量保留管段身份、中心线长度、内径、弯头与局部损失，以及最不利配水路径压损。当前几何和设备数据仍属概念假设。TCS、热瞬态、控制、电气、结构承载、消防与施工审查在此检查范围之外；上游相关计算可供复核，不能被设施水通过掩盖。

## 冷站容量适配器的当前范围

`hvac:capacity-change` 支持 `examples/hvac-dynamic-station` 的 `load-step` 概念工况，读取 `Chiller` 定义的 `capacity_kw`。实际链为现有 `evaluate_project`、`compile_model`、`load_scenario` 和 `ThermalNetwork`，不新增求解器。修改源文件中的容量后，通过检查结果及相邻运行比较判断变化。

范围检查核对实际 40→80 kW 负荷、300 s 阶跃、900 s 时段、正常控制、7°C 设定、25°C 环境、温度约束和数值精度。结果承接所有断言、模型范围及质量／压力／瞬时能量／积分能量检查；偏离声明工况不能被同名场景或预期值背书。采样极值和首次超限均保留实际采样时刻，不声称连续轨迹的精确越限时间。

120→60→120 kW 是可重跑的通过、温度越限、修复案例。未知容量不补默认值：适配器完成诊断后返回 `assessment:indeterminate`、`solver_execution:not-run`。无法初始化的方案保存执行错误。两者都返回非零退出码。模型仍为集总概念模型，参数为假设；微信图纸中的 18/24°C 设计工况与此例不同，真实资料审查见 [冷站资料核读](../../docs/cold-station-reference-review.md)，不能直接迁入此例并宣称现场校准。

公开重跑入口为 `uv run --locked tools/verify_hvac_engineering_poc.py`。脚本使用独立副本，记录源文件修改、真实 Git diff、反例和恢复。

## 真实资料容量审查的当前范围

`hvac:reference-capacity` 在既有 `hvac-cold-station` 项目中读取 `assets/reference/cold-station-acceptance.json`，用 `src/reference-capacity.study.json` 的全文件摘要绑定源快照。原始报告不参与运行时外部文件读取；冻结的脱敏事实参与项目输入修订。该项目原有通用设备布局模型保持独立，不虚构从报告设备到通用 SDL 实例的映射。

快照参数保留源 ID、原件 SHA-256、位置、含义、单位和状态；源 ID 必须唯一且引用可解析。缺失、无数值状态、量纲不符、错误整数数量、越界路径或摘要失配不能获得默认计算值。摘要匹配只证明消费了绑定的快照，不证明原件真实性或设备实测能力。

项目输入为风冷／液冷负荷率和可用冷机台数，均属审查假设；检查可用台数的整数语义及不超过所列冷机数量。账面负荷是各类数量乘单台声明量再乘该类负荷率；可用账面容量只按冷机计算，不叠加板换、末端或冷却塔数据。零以上的最低余量要求可以更严格，不能用负值豁免缺额。

`arithmetic_execution` 分别记录 `completed`／`not-run`；`solver_execution:not-run` 明示未进行真实性能求解。任一数值检查不满足则总体 `fail`，否则因真实工况、性能曲线与独立现场数据未确认而为 `indeterminate`。当前不能判定该范围满足工程条件。报告中原有建设质量终验记录不自动转为本工具的工程判定，不由这项条件性审查修改。

公开重跑入口为 `uv run --locked tools/verify_reference_engineering_poc.py`；它验证源文件变更、反例、恢复、退出码及来源变更，不将流程验证通过解释为工程通过。
