# 工程变更与证据契约 v1

> 状态：本地实现子集。共享 Python API、CLI 和 Studio 接口使用本契约。当前事务限单一 JSON 源文件的数值字段，证据采用项目级保守失效；不承诺完整语义合并、身份认证或正式工程批准。

## 目标与边界

同一工程操作贯通读取输入、预览影响、应用修改、重新计算、比较结果和记录接受。领域适配器解释专业输入、影响与判据；核心负责修订、事务、运行记录、内容完整性和适用性。

首个适配器是 `datacenter:fws-change`，复用现有设施水几何、水力和泵组配流；第二个 `hvac:capacity-change` 复用冷站集总动态求解链。界面和 CLI 不实现第二套物理算法。整体目标及验收证据另见 [工程 POC 台账](../../docs/engineering-poc.md)。

## 适配器与输入

已安装的 `TexsysPackage` 通过 `engineering_adapters()` 返回适配器。适配器必须有带命名空间的 `id`、`name`、`version`，可用字符串 `description` 解释适用范围，并实现以下方法：

| 方法                    | 职责                                                   |
| ----------------------- | ------------------------------------------------------ |
| `supports(root)`        | 判断当前项目是否具有受支持输入                         |
| `inputs(root)`          | 声明可以编辑的数值字段及来源、单位、边界和对象引用     |
| `impact(root, changes)` | 返回已登记的影响路径、计算目标和覆盖限制               |
| `evaluate(root)`        | 返回独立工程判定、逐项检查、提取量、限制和实际计算目标 |

输入描述至少包含 `id`、`label`、`file`、`pointer`、`unit`、`object_ref`。`file` 是项目 source 目录内的普通 JSON 文件，`pointer` 是定位已有值的 JSON Pointer。核心重新读取真实值，不信任适配器提供的缓存数值。可选 `min`、`max` 表达数值边界；值和边界必须有限，布尔值不能冒充数值。

输入 ID 和文件内定位不得重复。路径不得越界或经过符号链接，不能写入新字段或任意文件。`object_ref` 提供定位关系，首个适配器中的参数位置引用尚不等于任意 SDL 实例与几何对象的完整映射。

可选 `state` 沿用 SDL 值状态。`assumed` 可通过本次数值事务修改；`known` 可读取、求解或提交同值请求，但禁止只换数值而保留原来源背书，须先修正来源和状态或使用同时更新来源的工作流。省略 `state` 保持旧适配器兼容，不推定为已知值。`unknown`、`missing`、`not-applicable`、`failed` 不得携带数值；适配器应省略不可编辑输入，并在求值时给出无法判定的原因。

输入、检查和提取量均可携带 `design`：精确的 `{ "definition": "src/system.sdl.json#Chiller" }` 或 `{ "occurrence": ["CH-01"] }`。定义键和实例路径成员必须是非空字符串，两种形状不能混合或带其他键。核心只验证形状；Studio 使用设计索引解析身份，不从 `object_ref` 的自然语言或分隔符猜测对象。定义级参数可能影响多个继承实例，不能显示成仅修改一个实例。

## 共享 API 与命令

以下函数通过 `texsys.api` 导出，也可从 `texsys.engineering` 调用。返回值都是可序列化的字典。

| Python 函数                                                      | CLI                                                                            | 返回内容                                                           |
| ---------------------------------------------------------------- | ------------------------------------------------------------------------------ | ------------------------------------------------------------------ |
| `inspect_engineering(path, adapter_id=None)`                     | `texsys engineering inspect --adapter ID -p PROJECT`                           | 可用适配器、当前修订、输入、运行历史、接受记录、最近两次比较和诊断 |
| `plan_engineering_change(path, request)`                         | `texsys engineering plan REQUEST -p PROJECT`                                   | 修改前后值、来源、影响、文本差异及 `plan` 摘要；不写设计           |
| `apply_engineering_change(path, request, plan)`                  | `texsys engineering apply REQUEST --plan DIGEST -p PROJECT`                    | 应用结果及新修订                                                   |
| `run_engineering(path, adapter_id)`                              | `texsys engineering run ID -p PROJECT`                                         | 带输入与工具标识的完整运行记录                                     |
| `accept_engineering(path, adapter_id, run_id, actor, rationale)` | `texsys engineering accept ID RUN_ID --actor NAME --rationale TEXT -p PROJECT` | 关联准确证据的具名接受记录                                         |

CLI 示例统一通过 `uv run --locked texsys` 调用。`REQUEST` 是 JSON 文件路径，使用 `-` 时从标准输入读取。所有命令输出 JSON；`-p` 与 `--path` 同义。无受支持适配器时，未指定适配器的 inspect 返回 `available:false`；显式指定不可用适配器则报告错误。

CLI 退出码为：0 表示操作完成或工程判定通过，1 表示工程不满足或操作结果为 fail，2 表示输入／执行错误，3 表示修订冲突或工程无法判定。读取结果时仍需分别检查具体状态字段，不能仅使用退出码代替工程判断。

## 计划与单源事务

请求仅接受下列字段。示例中的 revision 必须先从 inspect 取得：

```json
{
  "adapter": "datacenter:fws-change",
  "revision": "sha256:替换为本次inspect返回的revision",
  "changes": [{ "input_id": "fws.row-diameter", "value": 0.05 }]
}
```

`changes` 非空，`input_id` 必须属于当前适配器的可编辑输入且不重复。v1 中同一请求只能修改一个源文件内的一个或多个字段，不支持跨文件原子事务、型号替换、删除或重连。

计划摘要绑定请求、修改前后值、已登记影响、目标文本和当前工具摘要。应用时持有共享项目写锁，重新计算计划并核对摘要及输入修订；原子替换单个源文件。过期请求返回 `status:conflict`，摘要不符或非法输入报告错误。相同值不会重写文件，返回 `applied:false`。

预览通过表示字段和数值边界合法，未证明几何可行或工程满足。专业几何、流量和能力判据由实际运行判断。恢复原值使用当前修订重新计划和应用，不能重放过期计划。

## 运行、状态与比较

运行先检查设计依赖锁，记录项目输入和工具身份，在项目写锁内调用领域适配器。运行后再次检查输入、依赖及工具。该流程读取当前项目文件，未建立通用的隔离输入副本；运行期间发生变化会将结果标为不一致，禁止接受。

五个公共操作都通过新的 Python 工作进程执行，长驻 Studio 只传递请求。工作进程使用白名单 JSON 协议，直接编译并记录实际加载的 Python 源内容，避免内存旧实现或旧字节码被绑定到磁盘新源码摘要。专业包的普通输出进入日志流，不参与 JSON 返回。run 超时为 600 秒，其他操作为 120 秒；超时或进程异常报告错误，重试前需检查项目状态。该进程边界不是不可信插件的安全沙箱，也尚未提供用户取消或任务恢复。

| 字段                      | 含义及当前值                                                                        |
| ------------------------- | ----------------------------------------------------------------------------------- |
| `execution`               | 核心执行状态：`completed` 或 `error`                                                |
| `result.verification`     | 适配器可提供的算例回归符合性；与工程判定独立                                        |
| `result.solver_execution` | 适配器可选的求解状态，如 `completed`、`not-run`；核心执行完成不代表物理求解已经运行 |
| `result.assessment`       | 工程判定：`pass`、`fail`、`indeterminate`                                           |
| `result.checks[]`         | 具有唯一 ID、标签、对象引用和状态的检查项；可附工况、实际值、限值、单位、裕量和依据 |
| `result.extraction[]`     | 适配器输出的几何或其他提取量及来源条件                                              |
| `inputs_consistent`       | 运行前后输入、依赖和工具是否保持一致                                                |
| `freshness`／`current`    | 记录对当前项目及工具是否仍适用                                                      |
| `acceptance`              | 单次运行的 `unreviewed`、`accepted` 或 `needs-review`                               |

核心不允许 `assessment:pass` 隐藏失败或无法判定的检查，也不接受没有通过项的空判定。适配器执行异常会保存 `execution:error`、`assessment:indeterminate` 及诊断；错误记录不能接受。

inspect 的 `comparison` 比较当前适配器最近两次有效读取的运行，按检查项 ID 给出前后内容及总体判定，并保留前后提取量。它不是通用工程对象差异、三方合并或任意方案搜索。Studio 可查看历史运行并与其前一次记录比较。

## 项目级保守失效

`revision` 来自清单、设计锁及 source／assets 文件的路径与内容摘要。生成物和 source／assets 之外的接受记录不参与计算输入修订。运行还记录 Python、平台、已安装发行包版本、已加载工程包和适配器的 Python 源摘要。

当前 `coverage` 为 `declared-adapter-with-project-wide-invalidation`。影响列表用于解释登记的因果路径，不用于证明未列出的结论不受影响，也不支持按对象精确复用缓存。项目输入、工具或锁定依赖发生变化时，历史记录保守失效。

| `freshness`          | 判断依据                             |
| -------------------- | ------------------------------------ |
| `dependency-invalid` | 设计依赖锁检查失败                   |
| `inputs-changed`     | 输入修订不同，或原运行未保持输入一致 |
| `tools-changed`      | 当前工具摘要不同                     |
| `adapter-changed`    | 适配器身份、版本或所属包信息不同     |
| `current`            | 上述检查均一致                       |

适用性按当前内容重新计算，不是永久撤销记录。输入及工具恢复为原内容后，旧记录可能再次适用；新的重跑拥有新的运行 ID，仍需单独接受。源摘要与发行包版本不等于完整运行环境封装；原生库、外部求解器及跨机器复现仍有未完成边界。

## 证据完整性与接受

运行保存到项目构建目录的 `engineering/runs/<run_id>.json`。API 以新 ID 增写，不覆盖既有记录。`evidence_digest` 校验除该字段外的记录内容。读取时损坏记录进入诊断，不作为可接受结果。

接受仅允许输入和工具仍为当前、执行完成、输入一致且工程判定通过的运行。`actor` 和 `rationale` 必须是非空文本。接受记录写入 `reviews/engineering/<decision_id>.json`，包含运行 ID、修订、证据摘要、完整证据记录、限制、姓名和理由，并计算 `decision_digest`。记录目录位于计算输入之外，可随 Git 保存；即使构建目录被清理，已接受的证据副本仍可审查。

读取接受记录时同时检查决定摘要、嵌入证据摘要和运行身份。当前输入发生变化后显示 `needs-review`，保留原接受及理由。证据记录包含输入摘要而非完整源文件归档；重新计算仍需要对应设计源、依赖与工具。

接受前后重新检查输入与工具上下文。遵守项目写锁的操作互斥；外部编辑器可能忽略锁，扫描期间检测到输入变化则拒绝接受。若写入接受记录期间发生变化，已写入的历史记录保留并需要复核，接口报告错误。该机制不声称可以阻止任意外部进程在检查后再次改写文件。

这些是**具名的本地审查记录**，没有身份认证、数字签名或外部审批权限。内容摘要可以发现不一致，不能证明作者身份或防止有写权限的人连同摘要一起重写记录。接受的效力限于证据中声明的范围，不能自动升级为施工、报审或整个数据中心的批准。

## FWS 适配器的当前范围

`datacenter:fws-change` 目前暴露主配水管 X 坐标，以及主配水管、机柜列配水管、CDU 接管的内径。内径须小于既有外包络直径；改变内径不自动代表已完成管材选型。

计算调用 `datacenter:fws-branches` 的 `evaluation` profile。该 profile 允许在正确检出的负面设计结论之后继续取得依赖报告，输入错误和求解错误仍阻止有效计算。工程检查显式选择 `initial-normal`、`full-normal`、`full-supply-a-out` 三个设计工况，核对逐机房供冷和已建模几何净距；不从 `expected` 字段推断工程适用工况。

工况名称本身不证明条件成立。适配器核对初期／满配阶段、正常／失电定义，并通过供电接口映射核对 A 路故障是否真正施加到单元模型。结果保存实际工况定义及来源；不符合声明时返回无法判定并明确范围未成立。缺冷诊断引用实际配流报告中的管段流速／质量流量限制和泵扬程余量，不能仅凭缺冷推断泵不足。

提取量保留管段身份、中心线长度、内径、弯头与局部损失，以及最不利配水路径压损。当前几何和设备数据仍属概念假设。TCS、热瞬态、控制、电气、结构承载、消防与施工审查在此接受范围之外；上游相关计算可供复核，不能被设施水通过掩盖。

## 冷站容量适配器的当前范围

`hvac:capacity-change` 支持 `examples/hvac-dynamic-station` 的 `load-step` 概念工况，暴露 `Chiller` 定义的 `capacity_kw`。实际链为现有 `evaluate_project`、`compile_model`、`load_scenario` 和 `ThermalNetwork`，不新增求解器。影响预览列出继承该定义参数且无容量覆盖的实例。

范围检查核对实际 40→80 kW 负荷、300 s 阶跃、900 s 时段、正常控制、7°C 设定、25°C 环境、温度约束和数值精度。结果承接所有断言、模型范围及质量／压力／瞬时能量／积分能量检查；偏离声明工况不能被同名场景或预期值背书。采样极值和首次超限均保留实际采样时刻，不声称连续轨迹的精确越限时间。

120→60→120 kW 是可重跑的通过、温度越限、修复案例。未知容量不补默认值：适配器完成诊断后返回 `assessment:indeterminate`、`solver_execution:not-run`。无法初始化的方案保存执行错误。两者都不能接受。模型仍为集总概念模型，参数为假设；微信图纸中的 18/24°C 设计工况与此例不同，真实资料审查见 [冷站资料核读](../../docs/cold-station-reference-review.md)，不能直接迁入此例并宣称现场校准。

公开重跑入口为 `uv run --locked tools/verify_hvac_engineering_poc.py`。脚本使用独立副本，记录数字事务及另一个副本中的未知状态源编辑，两类操作不混同。

## Studio 交换接口

接口沿用本地会话授权与同源校验。详见 [工作台契约](../studio/01-workbench.md#工程变更与证据文档)。

| 接口                              | 请求                                                                    |
| --------------------------------- | ----------------------------------------------------------------------- |
| `GET /api/engineering?adapter=ID` | 适配器可省略，返回 inspect 结果                                         |
| `POST /api/engineering/plan`      | 直接提交变更 request                                                    |
| `POST /api/engineering/apply`     | `{ "request": REQUEST, "plan": DIGEST }`                                |
| `POST /api/engineering/run`       | `{ "adapter": ID }`                                                     |
| `POST /api/engineering/accept`    | `{ "adapter": ID, "run_id": RUN_ID, "actor": NAME, "rationale": TEXT }` |

当前 plan／apply 的修订冲突通过 HTTP 200 响应中的 `status:conflict` 表达，客户端必须读取该状态；非法输入等异常返回 HTTP 400。run 在请求内同步等待新的工作进程，尚未接入 `/api/jobs` 的任务进度、取消与恢复契约。
