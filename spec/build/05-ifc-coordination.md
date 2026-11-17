# IFC 协调模型构建

> 状态：有效。首个切片由 `datacenter-reference@0.32.0` 提供，使用 IfcOpenShell `0.8.4.post1` 写出和回读 IFC4。输出为派生交付，不取代工程源文件，不承诺完整视图认证或 Revit 原生编辑。

## 运行入口

开发环境完成 `uv sync --locked` 后执行：

```bash
uv run --locked texsys lock -p examples/datacenter-reference-design
uv run --locked texsys build -p examples/datacenter-reference-design --target ifc-coordination
```

命名目标指定生成器及输出：

```toml
[build.targets.ifc-coordination]
using = "dcr:ifc-coordination"
profile = "coordination"
output = "build/ifc/aidc-coordination.ifc"
```

生成器依赖 `dcr:layout`、`dcr:pod-layout` 与 `dcr:fws-network`，消费同一批设备落位、子设备和管网数据，不从 USD 渲染网格猜测分类和连接。当前只接受 `coordination` profile，不接受 scope 筛选或其他 IFC schema。

## 项目身份

项目维护 `src/ifc.json`：

```json
{
  "schema_version": 1,
  "schema": "IFC4",
  "project_id": "urn:texsys:aidc-reference-design:coordination",
  "name": "AIDC reference coordination"
}
```

`project_id` 是项目的稳定身份，不随目录搬迁或显示名称改变。独立项目使用不同身份，相同项目的克隆与重跑保留原身份。GlobalId 由项目身份与源对象身份确定，参数及几何调整不会随机重编号。设施水沿用已编译管段编号；任意拓扑重构后的身份连续性仍需单独验证。

## 输出与范围

每个目标输出三个文件：

| 产物               | 内容                                                                   |
| ------------------ | ---------------------------------------------------------------------- |
| `.ifc`             | 米制、局部坐标的空间、设备、装配及设施水网络                           |
| `.sources.json`    | GlobalId、源对象身份、已编译模型路径／摘要／JSON Pointer，以及端口连接 |
| `.validation.json` | 回读检查结果、输入与产物摘要、限制和开放边界                           |

顶层对象按明确的族映射选择 IFC 类别，未知族失败。IT 方舱展开已有子设备包络，父装配不叠加完整实心包络；其他方舱保留顶层包络。机柜表达为技术柜，CDU 表达为具有明确对象类型的机组，避免暗示模型包含尚未定义的服务器或 CDU 内部构件。空间来自同一标高／分区模型，并标明功能区域假设包络，不推断为已实测或已确认室内外性质的房间。设施水保留供回水系统、管段、设备端口、缓冲罐和连接，多路节点通过拓扑管件连接，避免一个端口连接多次。

缓冲罐使用包络，管道使用源模型的外径、内径与长度。保温厚度作为属性保留。阀件、保温实体、支吊架、罐基础、完整建筑围护、结构详图及其他专业网络不在此切片内。参数保持概念假设状态；设备原始单位、状态和来源保存在 `ParametersJSON`，不能解读为实测或选型确认。

## 校验与复现

写出后重新解析，执行 schema／EXPRESS 检查，核对唯一身份、必需属性、装配与空间关系、系统成员、端口所属对象、位置及连接图。几何由 IfcOpenShell 几何引擎实际求值，包络位置容差 `1e-5 m`。管道端点、内外径及长度按解析实体独立核对，再以圆环截面体积公式比较求值体积，允许 `0.5%` 网格体积误差。管道网格包围盒允许 `1 mm` 离散误差，显示离散精度与工程参数精度分别检查。

同一输入、项目身份、能力包及运行环境下，完整重建应产生字节一致的 IFC。文件头固定生成器信息和基准时间，运行时间由通用 `build.json` 保存。输出路径和运行 ID 不参与对象身份。依赖、实现或源内容变化后沿用原有构建缓存与证据适用性机制。

缺少配置或后端为 `indeterminate`；不支持的 schema、筛选或未映射对象失败；schema、几何或关系不一致失败。上游失败时阻断 IFC 目标。诊断可以保留，不得据此声明交付成功。目标通过只说明本契约满足，`engineering_acceptance` 始终为 `not-assessed`。

## 独立验收边界

接收方 BIM 工具中的语义保留、Revit 原生类别／族／参数编辑、格式往返、外部修改合并及真实项目责任审查尚未验收。RVT 另选授权桌面、APS 或兼容 SDK 后端，不由 IFC 构建自动获得。

回归入口及实际证据：

```bash
uv run --locked pytest tests/test_ifc_delivery.py
```

验证记录见[工程 POC 台账](../../docs/engineering-poc.md)。
