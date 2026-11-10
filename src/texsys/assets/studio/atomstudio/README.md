# AtomStudio 布局组件适配

本目录按 MIT 许可引入 AtomLab `products/atom-studio` 中的编辑器界面组件。原始版本、工作树文件摘要和适配范围见 [来源清单](SOURCE.json)，原许可见 [许可原文](LICENSE)。

## 引入范围

- `EditorArea`、`EditorHeader`、`EditorCanvas`：统一文档区、上下文行和内容视口。
- `WorkspaceTabs`：文档标签、关闭、草稿标记与方向键焦点切换。
- `ResizeHandle`、`layoutSizing`：拖拽分栏、尺寸约束、方向键、双击复位与收起。
- `editor-shell.css`：编辑器几何结构。
- `Layout`、`layout.css`：根据原工作台分栏结构适配的 React 插槽布局和互斥活动区。

`primitives.tsx` 是 React 桥接层，替换原 Preact 基础元素和图标依赖。此处没有引入 AtomDocs 文档模型、路由、后台服务、品牌资源或应用启动代码。

## 所有权

宿主管理底部的项目 → 专业 → 分支上下文、顶部全局布局按钮与居中的命令入口、各专业面板状态与后台任务内容。专业提供左侧资源、中央编辑器、右侧属性及底部问题内容。活动区选择和打开文档独立；面板隐藏保留内部草稿。新增专业直接组合这些插槽，不复制工作台外壳。

更新上游组件时对照文件摘要和适配说明，保留 React 边界与布局按钮的宿主所有权。
