# ADR-0004：统一使用 uv 管理 Python 环境与命令入口

- 状态：已接受
- 日期：2026-11-04
- 取代：ADR-0003 中关于直接调用解释器、包安装器及模块入口的约定
- 被取代：无

## 背景

ADR-0003 已确定 Python 与 Typer 技术栈，但允许多种命令入口，且尚未规定依赖解析、环境创建和开发工具执行方式。直接调用解释器或包安装器会产生不受锁文件约束的环境差异，也使教程出现多套等价命令。

本项目需要单一、可重复、可在提交前自动检查的工作流。此决策只改变工具入口与仓库纪律，不改变 Python 主语言、Typer CLI 框架或核心依赖选型。

## 决策

1. **uv 是唯一文档化的 Python 项目环境与命令入口。** 环境同步、依赖锁定、CLI、测试和 pre-commit 分别通过 `uv sync`、`uv lock`、`uv run texsys`、`uv run pytest` 与 `uv run pre-commit` 使用。
2. **教程不得提供直接解释器或包安装器命令。** 不通过 uv 的解释器执行或兼容安装接口绕过这一约束；语言名称、API 名称和非 shell 代码示例不在禁令范围内。
3. **`pyproject.toml` 与 `uv.lock` 是项目依赖和版本的权威来源。** 测试与 pre-commit 位于 uv 开发依赖组，贡献者不手工创建环境或单独安装项目依赖。
4. **Node.js 工具链继续负责 Prettier 与 markdownlint。** uv 约束不替代 npm 对 JavaScript 开发依赖的管理。
5. **命令政策由自定义 markdownlint 规则执行。** 规则检查 bash、sh、shell、zsh、console 与 shell-session 代码块，拒绝其中的直接解释器、包安装器及相应兼容接口命令。
6. **pre-commit 必须运行同一 markdownlint 配置。** 提交检查与手工 lint 使用同一自定义规则，避免本地教程与提交门禁标准不一致。

## 备选方案

### 仅约定使用 uv，不增加自动检查

不采用。文字约定无法阻止旧命令继续进入教程，也不能保证贡献者在提交前执行检查。

### 使用通用文本搜索代替 markdownlint 规则

不采用。简单全文件搜索会误伤 Python 语言名称、API 说明和非 shell 示例。基于 Markdown token 的检查可以把约束限定在命令教程中。

### 只在 CI 中检查

不采用。CI 仍可作为后续门禁，但提交前检查能更早给出文件、行号和修正方向，且不需要等待远端结果。

## 后果

### 收益

- 环境同步、依赖锁定和命令执行具有统一入口。
- 教程只维护一套命令，减少环境差异与文档漂移。
- 自定义规则同时作用于手工 lint 和 pre-commit，违规示例会在提交前被定位。
- JavaScript 与 Python 开发依赖分别由 npm 与 uv 管理，边界明确。

### 代价与风险

- 贡献者必须安装 uv；文档检查还需要 Node.js 与 npm。
- 自定义规则限定于显式标记的 shell 代码块，未标记语言的代码块不会被当作 shell 教程检查。
- pre-commit 复用项目本地 Node.js 依赖，运行文档 hook 前必须执行 `npm install`，不另建一套工具版本。
- 规则只约束命令形式，不证明 uv 环境本身与 OpenUSD／USD BRep 的二进制兼容性；后者仍由 ADR-0001 的 spike 验证。

## 验证与复审

- `uv sync` 能依据 `pyproject.toml` 与 `uv.lock` 创建开发环境。
- `uv run texsys --help`、`uv run texsys --version` 与 `uv run pytest` 成功。
- 自定义规则接受 uv 命令，并拒绝直接解释器、包安装器及相应兼容接口命令。
- `npm run lint:md` 与 `uv run pre-commit run --all-files` 使用同一规则并通过。
- 若 uv 不再支持目标平台或核心原生依赖的分发要求，应新增 ADR 复审工作流，不得先在教程中恢复多套命令。

## 参考资料

- [ADR-0003：采用 Python 与 Typer 作为参考实现和 CLI 技术栈](0003-python-and-typer-toolchain.md)
- [uv 文档](https://docs.astral.sh/uv/)
- [markdownlint 自定义规则](https://github.com/DavidAnson/markdownlint/blob/v0.42.0/doc/CustomRules.md)
