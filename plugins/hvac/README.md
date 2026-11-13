# 暖通领域插件

> 状态：最小闭环已实现；设备库和模型适用范围仍有意保持很小。

## 定位

这是 SDL 的第一个领域扩展。设备族、规则、输出投影和行为模型都在核心之外，通过 `TexsysPackage` 协议和 Python 入口接入；核心不包含冷机、水泵、露点或暖通容量判据。架构边界见 [ADR-0010](../../docs/adr/0010-build-test-sim-and-package-boundaries.md)。

## 独立包

| 包                         | 设计包名         | 职责                                                          |
| -------------------------- | ---------------- | ------------------------------------------------------------- |
| `packages/hvac-equipment/` | `hvac-equipment` | 设备族与概念型号、身份解析、USD BRep 场景、安装事件与碰撞结果 |
| `packages/hvac-linter/`    | `hvac-linter`    | 领域字段、设备型号、安装顺序及 SDL 实例身份一致性检查         |
| `packages/hvac-formatter/` | `hvac-formatter` | `hvac.json` 规范化、设备表与连接拓扑投影                      |
| `packages/hvac-checks/`    | `hvac-checks`    | 容量、通风温升和 Magnus 露点近似的 Python 设计校核            |
| `packages/hvac-bom/`       | `hvac-bom`       | 暖通来源适配、专业规则与视图，组合通用 `texsys-bom` 内核      |
| `packages/hvac-diagram/`   | `hvac-diagram`   | 系统图／原理图投影、概念符号与行为标注，组合通用绘图／ELK 包  |

六个包都有独立 Python 包配置和 `texsys.toml`，可被示例通过 `lock`／`sync` 固定。原冷站设备性能数据属于设备族包；独立出图示例的行为值是 SDL 中显式、带来源的概念绑定，不能视为厂商性能库。

## 闭环

参考项目位于 `examples/hvac-cold-station/`。执行方式见 [示例说明](../../examples/README.md)，数据流为：

```text
系统描述与暖通输入
  → 核心与领域静态检查
  → build：指定目标→USD BRep＋Modelica＋文本投影＋目标物料清单＋构建证据
  → hvac.check-bom 构建后钩子：BOM 结构、来源与状态检查
  → test：激励→有序方程求解→容量／室温／结露断言
  → sim(event)：安装事件→解析盒几何检查→独立碰撞断言
```

USD 场景引用真实 `BrepArray` 单位资产，以平移和缩放表达设备包络，并保留 SDL 身份属性。当前几何求值由明确命名的 `AnalyticBoxKernel` 完成，不把 USD 写出与几何运算混为一谈。

## 目标物料清单与构建证据

`hvac:equipment-bom` 从 `src/hvac.json` 投影，通过 `BomContract` 组合通用 BOM 内核与暖通工程参数、设备型号和容量视图规则，不使用继承覆盖。它支持可行性、工程、采购、竣工和维护五类构建配置。后三类从 `assets/` 中显式指定的固定快照读取外部事实，通过设计实例与采购项／物理资产的映射保留身份；不直接连接或认证真实外部系统。通用 `bom:diff` 比较两份固定 BOM，`hvac:equipment-bom-diff` 保留兼容入口；差异不自动批准或回写。

`build/build.json` 记录解析后的目标、构建配置、范围、输入与产物摘要及钩子证据。构建钩子使用参数数组、结构化结果和受限修改边界，但不是恶意代码安全沙箱。详见 [目标物料清单契约](../../spec/build/01-target-bom.md) 和 [构建钩子执行协议](../../spec/build/02-hooks.md)。

## 适用边界

系统图／原理图已有单页根网络切片：`hvac:system-schematic` 输出 SVG、draw.io 与检查证据。行为缺失降级为系统图，缺少放置定义时标记 `connectivity-only`。审阅草稿通过不表示人工工程审核完成。入口、人工排版层与审核要求见 [出图构建契约](../../spec/build/03-system-schematic.md)，示例为 `examples/hvac-system-schematic/`。

- 内置 Modelica 后端只执行无循环、按声明顺序可求值的方程子集，同时生成对应 `.mo` 文本；它不是 OpenModelica 调用。
- 设备包络是轴对齐盒；尚未验证 SMLib 创建、布尔、修复、测量或三角化全链路。
- `sim(event)` 当前检查安装顺序中的静态包络与净距碰撞，不包含机械臂轨迹、资源调度或施工工期。
- `sim(field)` 未实现；没有有限元、多物理场或韧性结论。
- 当前示例参数是用于软件闭环的概念桩数据，不是实际项目选型或施工依据。
- 不做运行期调度，见 [ADR-0002](../../docs/adr/0002-design-time-vs-operation-time.md)；客户原始资料仍不得入库。

新增行为与呈现：`hvac-dynamics` 负责 Python 管网／集总动态，`hvac-graphics` 负责 TypeScript 参数化工程图元与 React/SVG。统一输入、命令行与 Python 接口见 [同源调用示例](../../examples/build-deliverables/README.md)。
