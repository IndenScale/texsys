# 设计决策记录

设计决策记录（DDR）用于记录对产品人机交互、界面信息架构、视觉系统、体验准则或设计系统有重要影响的决策，以及作出决策时的背景、取舍和后果。

DDR 与 [ADR](../adr/README.md)（架构决策记录）对标并平行管理：

- **ADR** 负责系统架构、契约协议与技术实现路线；
- **DDR** 负责界面与用户体验设计决策、工作台交互心智模型与设计系统规范。

## 原则

- 一项重要设计/交互决策对应一份 DDR，避免把多个无关决策捆在一起。
- DDR 记录“为什么这样设计与取舍”；规范文档/代码实现描述“当前具体尺寸与行为”。
- 已接受的设计决策需要改变时，新增 DDR 取代旧决策，不悄悄改写历史。
- DDR 应简洁、具体，并记录交互负面影响、心智负担、边界情况和走查验收标准。

## 状态

- `提议`：仍在讨论或评审，尚未形成正式约束。
- `已接受`：当前有效，后续界面与交互实现应严格遵守。
- `已弃用`：不再适用，但没有被特定 DDR 直接取代。
- `已取代`：由另一份 DDR 取代，必须链接到后继决策。

## 编号与命名

```text
ddr/NNNN-short-title.md
```

编号递增且不复用（如 `0001-studio-workbench-spatial-and-interaction-principles.md`）。新文档可从 [模板](TEMPLATE.md) 开始。

## 决策索引

| 编号                                                                    | 决策                                                                   | 状态   |
| :---------------------------------------------------------------------- | :--------------------------------------------------------------------- | :----- |
| [0001](0001-hvac-workbench-interface-round1.md)                         | 落实暖通 EDA 类工作台的分栏布局、信息架构与连续交互                    | 已接受 |
| [0002](0002-studio-uiux-guidelines.md)                                  | 操作按钮仅显示图标且与标签同排，建立快捷键键帽展示规范                 | 已接受 |
| [0003](0003-studio-interaction-design.md)                               | 常驻活动栏、连续尺寸过渡、面板拖动折叠与图纸相机隔离                   | 已接受 |
| [0004](0004-studio-titlebar-and-menu.md)                                | 统一 40 像素标题栏、工具栏按钮规范与下挂式命令中心                     | 已接受 |
| [0005](0005-studio-core-editor.md)                                      | 集成 Monaco 源码编辑、资源管理器树对齐与根节点操作、外部修改比对       | 已接受 |
| [0006](0006-studio-code-search.md)                                      | 搜索替换双行组、整词／正则／大小写保持、Unicode 字符定位与行合并折叠   | 已接受 |
| [0007](0007-studio-source-control.md)                                   | 本地 Git 差异浏览、项目／仓库范围隔离、批量暂存与提交历史图            | 已接受 |
| [0008](0008-studio-integrated-terminal-and-panel-system.md)             | 对标 VS Code 的底部终端模块、面板控制与下拉菜单交互系统                | 已接受 |
| [0009](0009-studio-visual-identity-and-logo-design.md)                  | Tex Studio 视觉识别（VI）系统、标志语义与正交节点设计演进              | 提议   |
| [0010](0010-studio-disabled-action-feedback.md)                         | 在主要操作旁说明禁用原因和可执行的下一步                               | 已取代 |
| [0011](0011-studio-disabled-action-tooltip.md)                          | 通过浮层说明禁用原因，保持面板布局稳定                                 | 已接受 |
| [0012](0012-studio-file-preview-and-source.md)                          | 统一文件组件与「预览 / 源代码」，专业能力只替换预览实现                | 已接受 |
| [0013](0013-studio-editor-actions-and-open-with.md)                     | 统一打开方式与文件类型默认值，提供 Markdown 编辑、预览、分栏和共享文档 | 已接受 |
| [0014](0014-studio-editor-group-actions-and-disk-comparison-removal.md) | 编辑器更多菜单管理当前组，删除磁盘比较流程和单一打开方式选择器         | 已接受 |
