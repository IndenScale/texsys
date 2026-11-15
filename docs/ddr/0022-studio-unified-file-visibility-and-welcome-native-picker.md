# DDR-0022：统一文件可见性契约，欢迎页对标 vscode.dev 原生选择器

- 状态：已接受
- 日期：2026-11-15
- 取代：无（补充 DDR-0020／DDR-0021 的空白工作区文件行为）
- 被取代：无

## 用户体验与交互背景

用户以 vscode.dev 为参照逐块核对欢迎工作区：参照的资源管理器不显示 `.DS_Store` 等文件，「打开文件夹」使用操作系统原生目录选择器。Tex Studio 欢迎页此前用浏览器 File System Access handle 原始列举目录，不做任何过滤，导致 `.DS_Store`、`.editorconfig`、`node_modules` 等全部可见，点击二进制 `.DS_Store` 还触发与服务器侧不同的 2 MB 错误措辞；两个入口（欢迎本地工作区与服务器项目工作区）行为分叉。

## 设计决策与规范约束

- **单一可见性契约，两个传输入口。** 服务器项目列举（`texsys/studio/files.py`）与欢迎页本地列举（`file-excludes.ts` + `welcome-files.ts`）应用同一排除契约：
  - 默认值 = VS Code 默认 `files.exclude`（`**/.git`、`**/.svn`、`**/.hg`、`**/CVS`、`**/.DS_Store`、`**/Thumbs.db`）加产品性能跳过（`**/node_modules`、`**/.venv`、`**/venv`、`**/__pycache__`、`**/.pytest_cache`、`**/.mypy_cache`、`**/.ruff_cache`）。
  - 工作区 `.vscode/settings.json` 的 `files.exclude` 逐模式覆盖：`true` 隐藏、`false` 显示；JSONC 注释与尾逗号容错；非法设置保持默认。
  - glob 语义一致：`**/` 匹配任意深度目录前缀，`*` 段内通配，`?` 单字符；不含 `/` 的模式规范化为 `**/<模式>`；目录命中即剪枝整棵子树。
  - 两侧实现（Python／TypeScript）以同一组正反例测试保持 parity（`tests/test_studio.py`、`tools/test_file_excludes.mjs`）。
- **欢迎页交互对标 vscode.dev。** 「打开文件夹／打开文件」使用原生 `showDirectoryPicker`／`showOpenFilePicker`，最近列表保存浏览器授权 handle（IndexedDB）。原生选择器只暴露 handle、不暴露绝对路径，服务器传输无法消费 handle；因此欢迎页保留浏览器本地传输，项目入口保留服务器传输，统一的是契约与组件（同一 ExplorerTree、SourceEditor、大纲），不是传输本身。
- **限额与措辞统一。** 本地文本读取与服务器 `project_file` 相同：1 MiB 上限、「文件超过 1 MiB，暂不提供文本编辑。」「该文件不能以 UTF-8 文本编辑。」，不再使用 2 MB 专用措辞。
- **访问边界不随可见性配置放宽。** 服务器侧 `.git`／`.hg`／`.svn` 内部文件始终拒绝读写（`PROTECTED_DIRECTORIES`），即使用户在 `files.exclude` 中将其设为显示；可见性配置只影响列举，不改变安全边界。
- 搜索复用 `project_files`，默认搜索范围随契约同步；搜索自身的 include／exclude 输入不变。

## 备选方案

- **服务器路径输入（VS Code Remote 式）＋ `POST /api/workspace` 重绑根**：本轮曾实现，用户对照 vscode.dev 后否决——原生选择器交互与参照不一致，且容器场景只能打开挂载目录。已回退该端点与运行时普通文件夹分支。
- **服务器目录浏览对话框**：可保留单一服务器传输，但交互仍非参照的选择器，未采用。
- **项目入口也改浏览器 handle 传输**：工程构建、预览、Git 与任务在服务器侧执行，容器文件对浏览器不可见，不可行。

## 体验影响与权衡

- 收益：两个入口与参照的可见文件集合一致；`.DS_Store` 等噪声消失；错误措辞、限额、树与编辑器组件统一；用户可用熟悉的 `files.exclude` 配置调整隐藏项。
- 代价：欢迎页与项目入口仍是两种传输（浏览器授权目录 vs 服务器绑定工程），契约 parity 需要双侧测试维护；欢迎页可见范围限于浏览器授权目录，项目入口限于服务器可见目录，二者不互通。
- 性能跳过作为产品默认保留（既有加载性能优化），与 VS Code 纯默认（`node_modules` 可见）的差异可经设置覆盖。

## 可用性走查与验收标准

- 后端：默认隐藏 `.DS_Store`／`node_modules`、显示 `.editorconfig`；设置覆盖正反例（显示 `node_modules`、隐藏 `.editorconfig`、`**/*.log`）；JSONC 容错；受保护目录不随配置放宽。
- 前端：`tools/test_file_excludes.mjs` 与后端同组用例 parity；本地列举过滤与排序；1 MiB／二进制措辞。
- 既有 Chrome 复用 8710 实例：欢迎页「打开文件夹」弹出原生选择器；授权仓库根后树不显示 `.DS_Store`、`node_modules`，显示 `.editorconfig`；项目入口 `/api/files` 同集合。原生 OS 选择器无法由自动化完成授权，该步由用户实际操作核对。

## 参考资料

- vscode.dev 实测（用户截图：原生目录选择器、资源管理器可见集合）。
- [DDR-0020](0020-studio-empty-workspace-full-shell-and-bare-entry.md)、[DDR-0021](0021-studio-statusbar-flush-and-empty-workbench-parity.md)：空白工作区完整外壳。
- [docs/studio-explorer-context-analysis.md](../studio-explorer-context-analysis.md)：参照能力受宿主文件系统影响的既有结论。