# texsys

texsys 得名于「文本化系统」，目标是让复杂系统像软件一样被持续开发、验证和演化。从数据中心开始，Tex Studio、texsys CLI 与官方库共同提供工程开发环境；系统描述语言（SDL）提供声明式系统语义。

## 当前状态（2026-11）

| 维度             | 状态                                                                                                         |
| ---------------- | ------------------------------------------------------------------------------------------------------------ |
| 方法论           | 有：13 章《设计自动化原理》                                                                                  |
| SDL 规范         | 草案：核心语义模型与 JSON 交换子集                                                                           |
| 语法 / 校验器    | JSON 交换子集的基础静态检查；最终文本语法未开始                                                              |
| 参考实现／代码   | 命令行支持项目管理、依赖管理、形式检查、按目标构建、测试与事件仿真，也支持目标物料清单和构建钩子 |
| 一致性测试       | 单元测试、命令行集成测试与示例端到端测试，覆盖反例和结果确定性 |
| 暖通领域插件     | 设备族、静态规则、设计校核、目标物料清单、工程图元、系统原理图与集总参数动态求解 |
| 数据中心概念研究 | 供电、FWS/TCS、空气环境、控制、热维持、结构、建筑与消防等有限跨专业计算；参数与判据仍有算例假设 |
| 模块库与资产     | 私有定义、递归实例、锁定引入、模块资产和端口绑定；完整能力边界与厂家证据尚待补齐 |
| 通用构建组件     | 专业无关的 BOM 投影、快照、映射、比较与检查内核，通过显式契约组合专业规则                                    |
| 工程反馈基础     | 同源冷站及数据中心支管几何—水力—泵组配流；源文件变更、构建检查与结果产物正在贯通 |
| 系统图／原理图   | TypeScript 共享工程图元、React/SVG、draw.io、离线交互与工况回放                                              |

项目当前处于**可执行参考实现与工程 POC 建设阶段**。JSON 交换子集、形式检查、路径与发布包依赖、暖通链路和数据中心概念计算已可执行；最终 SDL 文本语法、通用工程事实／结论模型及真实工程接受尚未完成。Python 支持准稳态水力与集总热动态，既有 OpenModelica 路线对三个暖通工况有独立比较记录；几何包括 USD 包络、路由和有限空间校核，通用精确几何与高保真求解仍未完成。见 [项目进度审查](docs/progress.md) 和 [完整目标台账](docs/engineering-poc.md)。

Tex Studio 已提供本地网页插件宿主、通用项目编辑、模块库／实例导航、场景预览与暖通工作台，支持实例参数编辑、后台检查、出图、仿真和结果回放。跨专业对象视图仍在贯通；工程变更复用 Git 与命令行检查，领域计算存在不等于相应专业界面已可用。Electron 桌面端与远程协作尚未实现。

## 文档地图

| 位置                                               | 内容                                                   | 权威性                                 |
| -------------------------------------------------- | ------------------------------------------------------ | -------------------------------------- |
| [MEMO.md](MEMO.md)                                 | 定位、范围、决策与开放问题索引                         | 索引，不重复正文                       |
| [docs/scope.md](docs/scope.md)                     | 从数据中心出发的工程 POC 范围与验收边界              | **权威**：判断某件事是否在范围内       |
| [docs/engineering-poc.md](docs/engineering-poc.md) | 完整目标、贯通案例与逐项证据台账                      | 追踪完成状态，不替代规范或真实工程接受 |
| [docs/progress.md](docs/progress.md)               | 本地实现进度、验证入口与未完成能力                     | 实现状态审查，不代替规范或发布验收     |
| [docs/project-layout.md](docs/project-layout.md)   | 项目元数据、依赖／锁文件识别与目录布局                 | **权威**：`init`／`info` 的布局契约    |
| [examples/](examples/README.md)                    | 用户参考项目与端到端桩数据                             | **权威**：可执行示例契约               |
| [docs/adr/](docs/adr/README.md)                    | 架构决策记录                                           | **权威**：为什么这样决定               |
| [docs/ul/](docs/ul/README.md)                      | texsys 统一语言：首选用语与定义索引                     | 用语入口；规则以对应规范为准           |
| [docs/rfc/](docs/rfc/README.md)                    | 待评审提案与取舍                                       | 提案；生效决策与规则见 ADR、DDR 和规范 |
| [docs/open-questions.md](docs/open-questions.md)   | 未决议题、阻塞影响与触发条件                           | **权威**：什么还没定、何时必须定       |
| [docs/references.md](docs/references.md)           | 外部资料边界、来源与校验和                             | **权威**：某份资料能否被引用、是否入库 |
| [plugins/hvac/](plugins/hvac/README.md)            | 暖通领域插件：设备模型、静态规则、输出格式、简易求解器 | 领域扩展，不含核心语义                 |
| [spec/sdl/](spec/sdl/00-overview.md)               | SDL 规范                                               | **权威**：语义怎么定义                 |
| [构建规范](spec/build/00-overview.md)              | 构建目标、目标物料清单与构建钩子执行契约               | **权威**：构建怎么选择与闭环           |
| [工程变更与证据](spec/engineering/01-change-evidence.md) | Git／构建工作流与检查产物契约       | **权威**：当前工程变更子集的行为和边界 |
| [设计自动化原理/](设计自动化原理/00-前言与导读.md) | 方法论书                                               | 背景论证，**不构成约束**               |

同一件事只在一处定义。若发现重复，以本表标注的权威来源为准，并删除复述。

## 四条阅读路径

1. **想理解为什么**：`设计自动化原理/00` → `01` → `02`。
2. **想判断范围**：本文件 → `docs/scope.md` → `docs/adr/0001`。
3. **想动手实现**：`spec/sdl/00` → `spec/sdl/01` → `docs/open-questions.md`（先解决阻塞项）。
4. **想推进工程闭环**：`docs/engineering-poc.md` → `examples/datacenter-hybrid-unit/` 或暖通示例 → `docs/references.md`（原始资料只在本地）。

## 开发环境

Python 项目环境、依赖与命令执行统一使用 uv。首次同步时，运行以下命令验证命令行工具。

```bash
uv sync
uv run texsys --help
uv run texsys --version
uv run pytest
```

## Tex Studio 与暖通工作台

开发与验证统一复用 `http://127.0.0.1:8710` 上的单个实例。启动前检查进程、监听端口和锁；若已有实例，联系所属会话加载所需改动。独占锁及会话归属的完整规则见 [AGENTS.md](AGENTS.md#tex-studio-开发与验证)。

确认没有运行实例并取得独占锁后，从仓库根目录以 Vite 热更新模式启动标准暖通示例：

```bash
uv sync --locked
bun install --frozen-lockfile
uv run tools/sync_node_tools.py
uv run texsys studio -p examples/hvac-dynamic-station --hmr
```

该模式需要 Node.js 与 Bun，由 Vite 提供 React 组件快速刷新，本地接口仍由 Python 服务提供。使用现有 Google Chrome 和工作台标签页访问该地址；不使用自动打开浏览器选项，不另起端口或隔离浏览器实例。

第一次进入暖通工作台后点击“生成图纸”，或到“仿真分析”启动工况求解。工作台提供系统图／原理图、设备表、器件图元、参数与依据、检查报告及模型源视图。参数保存形成实例覆盖，可恢复继承或撤销；后台计算在输入副本中执行，保留日志、诊断与固定版本结果。

专业插件通过 `texsys.studio` 入口发现，宿主启动时加载已安装专业并提供切换入口。目前接入了暖通专业。架构搜索、物理布局布线和正式提资显示为未接入。 [插件架构](docs/adr/0021-tex-studio-plugin-based-disciplinary-workbenches.md) · [工作台契约](spec/studio/01-workbench.md)

## 工程变更与证据

可复用的模型、规则、求解器适配和包自身测试通过工程能力包发布；项目实例、工况和计算结果保留在项目。`texsys package` 管理共享文件仓库中的明确版本，`add / sync` 将能力还原到项目。在 Tex Studio 的通用文本编辑器中查看和编辑 `texsys.toml`，通过终端执行包命令；部件库只消费部件包的明确导出，IDE 插件独立管理；完整使用方式与首期边界见 [工程能力包](docs/package-management.md)。

工程变更直接修改版本库中的源文件，以 `git diff` 表达差异，再运行构建和检查。Studio 不提供专用工程变更 GUI。POC 负责退出码、诊断与结果文件；合并门禁、失败可视化和协作通知由 DevOps 平台承接，范围见 [ADR-0026](docs/adr/0026-git-build-workflow-and-devops-boundary.md)。

现有工程检查命令在声明范围内执行实际判据，从仓库根目录运行：

```bash
git diff -- examples/hvac-dynamic-station/src
uv run --locked texsys engineering run hvac:capacity-change -p examples/hvac-dynamic-station
uv run --locked texsys engineering run datacenter:fws-change -p examples/datacenter-hybrid-unit
```

工程不满足、执行错误和无法判定分别返回非零退出码，结果保存在项目构建目录。普通 `texsys build` 仅执行选定目标，目前不会自动运行全部工程适配器；模型编译成功或预期反例回归成功均不能替代实际工程判据。

## 交付物构建与检查

生成暖通系统图与原理图需要本机安装 Node.js。

```bash
uv run tools/sync_node_tools.py
uv run examples/build-deliverables/run.py all
```

统一示例分别在 `build/hvac/system/` 与 `build/hvac/schematic/` 输出 SVG、draw.io、JSON、离线 HTML 和检查报告，动态结果写入 `build/tests/scenarios/`。默认按审阅配置生成草稿，采用工程配置时还需要人工审核记录。缺少行为定义时生成系统图，并单独标记空间状态。 [出图构建契约](spec/build/03-system-schematic.md)

BOM、系统图、原理图的命令、参数和 Python 函数调用集中展示在 [构建调用示例](examples/build-deliverables/README.md)，并提供可执行脚本。

Markdown 与提交前检查：

```bash
uv run tools/sync_node_tools.py
node node_modules/markdownlint-cli2/markdownlint-cli2.mjs "**/*.md"
uv run pre-commit install
uv run pre-commit run --all-files
```

命令行教程不得直接调用解释器或包安装器，也不得通过兼容接口绕过 uv 项目工作流。提交钩子运行自定义 Markdown 检查规则来执行这项约束。

Markdown 正文默认使用中文。各级目录中的 `README.md`、带语言后缀的 README 和 `AGENTS.md` 一律使用中文正文，语言目录和文件名后缀均不能改变这项要求。整篇或单个段落识别为其他语言时，语言检查会报错。其他文档可用语言目录或变更日志的文件名后缀指定语言，个别段落的疑似错配只提示。提交前检查暂存版本，合并请求检查从合并基点到源分支提交新增或修改的 Markdown。 [文档语言配置](tools/doc-language.toml)

```bash
uv run --locked --only-group docs-lint tools/check_doc_language.py --staged
uv run --locked --only-group docs-lint tools/check_doc_language.py --base main --head HEAD
```

## 下一步（按优先级）

1. 通过源文件修改、Git diff 和命令行检查贯通 FWS 几何反馈、重算、失败解释与修复。
2. 补齐公共对象／结论模型、证据失效、语义比较与组合变更检查，明确未覆盖的影响与工程条件。
3. 以可核实设备资料验证小场景，由第二位使用者复算；在第二个项目检验复用并记录新增工作。
4. 根据真实需求发展能力包、执行环境复现与外部工具接入。逐项状态和证明要求见 [工程 POC 台账](docs/engineering-poc.md)。

## 约定

- ADR 只增不改；改变已接受决策时新增后继 ADR。
- 待定项必须进入 `docs/open-questions.md`，带触发条件，不允许散落在正文里。
- 文档中"已知/假设/待定/缺失/不适用/失败"六态必须区分，不得用空值或默认值代替。
- 原始图纸与客户资料**不入库**：本地 `refs/`，已被忽略；入库的只有提取后的模型与结论。
