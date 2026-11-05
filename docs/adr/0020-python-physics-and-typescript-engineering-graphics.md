# ADR-0020：Python 物理行为与 TypeScript 工程图元

- 状态：已接受
- 日期：2026-11-05
- 补充：ADR-0010、0017、0018、0019；替代 ADR-0019 的卡片式首稿呈现、独立 Python 图元与重复渲染实现；补充 ADR-0004 的本仓库 Node 工具安装方式。

## 背景

首稿将设备的行为引用、参数和依据铺在矩形卡片内，图元退化成卡片中的小图标。图纸难以识读，也不能展示实际负荷下的管网演化。项目没有客户兼容负担，允许直接删除被替代实现。

## 决策

1. Python 是工程事实与物理行为的权威。SDL 定义实例身份、端口、连接与显式参数绑定；版本化 `.physics.json` 选择本构类型和假设。快照、工况、运行结果分别存储。前端只读取结果，不维护第二套物理方程。
2. `hvac-dynamics` 组装整个网络，联立求解准稳态水力与集总热状态。默认 SciPy；独立导出 Modelica 方程，由真实 OpenModelica 求解。缺外部后端返回 indeterminate，不回退为 Python 并冒充外部执行。
3. `hvac-graphics` 使用 TypeScript 纯函数生成设备轮廓、边界、端口与标注锚点，React 生成 SVG 与离线 HTML。SVG 是输出载体；组件化与参数化由 TypeScript/React 实现。draw.io 直接消费同一已解析几何。
4. ELK 仅执行布局与正交路由；几何由图元函数提供。保留 JS/WASM 依赖，因为共享 React 图元已需要 Node 工具链，不再为排版引入第二套几何定义。构建时不联网。
5. 系统图突出设备与连接，原理图增加少量关键参数；完整参数和依据放入 JSON 与交互侧栏。物理比例和行为完整性分别判断。
6. 删除 Python SVG 绘制器、旧卡片目录、重复 ELK worker。删除“有序 Modelica 子集”解释器；原容量、通风和露点公式迁移为 `hvac-checks` Python 设计校核，输出 JSON，不再把它称作 Modelica 求解。
7. 示例以同一物理 SDL 输入生成 BOM、系统图、原理图与三种动态工况；公开本地 `texsys.api.build/evaluate` 与 CLI，不提前创建 HTTP 服务。
8. Node 开发工具由单一 `package-lock.json` 锁定，`uv run tools/sync_node_tools.py` 下载并校验归档。直接执行 node CLI 文件，不执行包生命周期脚本；删除两份只安装绘图子集的安装器与重复依赖锁。

## 影响与验证

需要 Python 与 Node 两个运行环境；外部 Modelica 还需要可运行的 omc 及其 C 工具链。以同源身份／摘要、共享端口几何、独立解析解、守恒与收敛、双后端实际轨迹比较、浏览器操作作为验证证据。概念数据的计算一致性不能替代厂商性能校准或真实工程审核。

当前约束和适用假设见 [动态行为契约](../../spec/build/04-hvac-dynamics.md)，调用见 [统一示例](../../examples/build-deliverables/README.md)。
