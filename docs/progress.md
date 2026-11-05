# 项目进度审查（2026-11-05）

> 本地仓库实现与实际执行证据，不代表已发布产品。当前行为与图纸决策见 [ADR-0020](adr/0020-python-physics-and-typescript-engineering-graphics.md)。

## 当前结论

TexSys 已具备一条同源冷站链路：SDL 中的实例、端口、连接、参数和版本化行为选择，可生成 BOM、系统图、原理图、物理快照，并在时变负荷、调速和停泵工况下求解。Python 维护工程与物理行为，TypeScript/React 维护工程图元和交互呈现，SVG 作为静态及交互矢量载体。

设备呈现已由信息卡片改为可识别的冷机双筒体、泵、塔、换热器、管段与附件轮廓；完整参数与依据在侧栏。图元几何、边界、接管和标注锚点同源用于 ELK、React/SVG 与 draw.io。默认系统图仅突出组成／拓扑，原理图增加关键参数，缺行为与缺空间分别降级。

清除的实现包括 Python SVG 绘制器、旧概念卡片目录、重复 ELK worker、旧手工布局数值／行为段落标签、重复 Node 子集安装器，以及“有序 Modelica 子集”解释器。原容量／通风／露点公式迁移为名称明确的 `hvac-checks` Python 设计校核，不保留旧名称兼容入口。

## 能力状态

| 能力                       | 状态         | 证据与边界                                                                                       |
| -------------------------- | ------------ | ------------------------------------------------------------------------------------------------ |
| SDL 与项目／依赖           | 子集已实现   | JSON 交换模型、形式检查、本地路径依赖、锁定／同步；最终文本语法未完成                            |
| 同源 BOM、物理快照与双视图 | 已实现       | hvac-dynamic-station 的 8 个实例、9 个物理支路；身份和快照摘要贯通                               |
| 前端工程图元与出图         | 已实现       | TypeScript 纯几何、React 静态／离线交互、SVG、draw.io、显式端口与回退；非厂商型号图元            |
| 集总动态行为               | 已实现       | 联立准稳态水力、泵曲线、Darcy 管阻、Kv 阀、持液／管壁热容量、混合、负荷、冷机一阶响应与塔 UA     |
| 工况和数值证据             | 已实现       | 一致初态、显式输入曲线／事件、守恒、性能断言、失败反例、解析解与收敛比较                         |
| 真实 OpenModelica 路线     | 三工况已验证 | 实际导出方程、编译、DASSL 求解；双后端残差与轨迹比较；不代表任意拓扑与版本都已验收               |
| 浏览器工作台               | 已验证       | 设备选择／详情、图元比例／标注层次、工况切换、时间拖动、事件跳转、播放复位／结束、曲线与失败诊断 |
| 人工排版与审核             | 有限子集     | overlay 与摘要绑定审核；完整交互编辑、回读、身份认证尚未实现                                     |
| 空间与安装事件             | 简化闭环     | USD BRep 包络与轴对齐盒碰撞；不等于真实几何内核或运行期水力事件                                  |
| 工程正式交付               | 未验收       | 概念参数、无厂家标定和现场核验；跨页、递归网络、字体锁定与施工基线仍待推进                       |

## 可重跑入口

```bash
uv sync --locked
uv run tools/sync_node_tools.py
uv run texsys lock --check -p examples/hvac-dynamic-station
uv run examples/build-deliverables/run.py all
uv run pytest
node --test tools/test_graphics.mjs tools/markdownlint-rules/no-python-pip-commands.test.cjs
node node_modules/markdownlint-cli2/markdownlint-cli2.mjs "**/*.md"
```

同源 example 的 `build/hvac/system/system.html` 和 `build/hvac/schematic/system.html` 可以直接打开。图元页只修改图形比例，不写回工程性能；动态页回放 Python／外部求解结果，不在 TS 中实现第二套物理求解。

本地 Python API 为 `texsys.api.build/evaluate`，CLI 和参数集中于 [调用示例](../examples/build-deliverables/README.md)。物理与数值口径以 [动态行为契约](../spec/build/04-hvac-dynamics.md) 为权威；图纸与审核以 [出图契约](../spec/build/03-system-schematic.md) 为权威。

## 本次实际验证

提交整理时，从暂存内容导出的干净目录执行锁定环境与 Python 全量测试，**143 passed**（原有 134 项及本次新增的 9 项回归）；Node 图元 9 项、命令规则 4 项通过；TypeScript strict 类型检查与 Markdown 检查通过。统一冷站 lint、format、lock 检查通过。旧设备布置示例迁移后的容量／温升／露点校核仍通过。

本次审查修复了布局目录嵌套及符号链接越界、依赖同步忽略自定义 build 目录、锁文件漏包仍能同步、Hook 输入快照同名覆盖、非法端口方向导致 lint 无结构化结果，以及 transient 示例访问不存在的返回字段。每项行为修复均有回归覆盖。提交按工具链、核心协议、通用内核、专业实现、出图集成与质量文档的依赖顺序组织；各阶段使用匹配的 Python／设计依赖锁文件。

Prettier 负责通用文档和前端资源；`*.sdl.json` 与 `src/hvac.json` 由 `texsys format` 保持规范化格式。完整 USD 外部提案原文保存在忽略的 `refs/openusd/`，提取脚本可显式接收本地原文路径，正常构建与测试只依赖入库的 USD 示例资产。

三个运行工况各 900 s、默认 5 s 输出：负荷阶跃 pass、泵速调整 pass、停泵 fail。停泵后负荷出口约 25.065 °C，超过 12 °C 上限；示例回归验证通过不等于该失效工况合格。SciPy 各工况最大瞬时能量残差约 3e-10 W、积分能量误差小于 1e-6 J；独立测试还覆盖泵阻力闭式解、Poiseuille 极限、反向支路、全闭阀、集总冷却解析解、故意漏热、不可实现初态与容差／步长收敛。

实际运行 `tools/verify_hvac_backends.py` 的三个工况全部符合预期，输出 `build/evidence/backend-comparison.json`。温度最大差小于 1.4e-5 °C、电功率最大差小于 0.05 W、质量流量差小于 1e-5 kg/s；这些是当前声明方程的实现一致性证据，不是物理模型真实性或性能标定结论。

实现阶段的既有浏览器核验保留 `build/evidence/symbol-browser.png` 与 `dynamic-browser.png`：冷机比例变为 2.00 后四个接管随轮廓同步变化；切换工况复位时间，事件跳到 300 s，播放结束为 900 s；停泵冷冻水环路无流向箭头，温度曲线与超限诊断相符。draw.io 已核验 XML、共享图元、端口与身份映射，尚未在 draw.io 应用中作编辑验收。

本机 Python 3.12.13、NumPy 2.5.4、SciPy 1.18.1、Node 24.11.0；外部编译器实际报告 `v1.23.0-dev-152-g5b94ed190c-cmake`，运行在 macOS arm64 的项目隔离目录。官方 ARM64 包来自 [OpenModelica 构建站](https://build.openmodelica.org/omc/builds/mac/arm64/nightly/OpenModelica-6.1.5-Darwin.pkg)，SHA-256 `194484ab559bc9d7179a295e7d8830bd7e18729cdb8be936a8b92e8268eddd0b`；libiconv 来自 [MacPorts 官方包](https://packages.macports.org/libiconv/libiconv-1.19_0.darwin_23.arm64.tbz2)，SHA-256 `f3c66266fddbb39e393a0dc398d0489e18f24b25fbfee4d7efc94a489e578258`。仅解包到项目忽略目录，未进行全局安装；这些运行文件不作为项目分发依赖。CI 使用 Node 22，配置已覆盖新链路，本次没有远端 CI 运行证据。

## 后续边界

继续推进真实设备参数／性能曲面校准、更复杂的切换拓扑、递归子系统与显式 Net、跨页、字体锁定、真实几何内核与施工配置基线。当前采用定物性单相水、准稳态水力、集总热力和等效塔 UA；高保真瞬态、两相、水锤与完整湿空气模型不在本次实现范围内。
