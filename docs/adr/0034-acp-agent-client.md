# ADR-0034：Studio 作为 ACP 客户端接入外部智能体

- 状态：已接受
- 日期：2026-11-17
- 取代：无
- 被取代：部分由 [ADR-0035](0035-deepseek-harness-as-agent-kernel.md) 取代（决策 5 与通用 ACP 客户端定位）
- 补充：[ADR-0021](0021-tex-studio-plugin-based-disciplinary-workbenches.md)、[ADR-0026](0026-git-build-workflow-and-devops-boundary.md)

## 背景

工程师希望在 Studio 右侧像 VS Code 一样直接与智能体协作，而不是另建独立的可视化应用。[AGENTS.md](../../AGENTS.md) 要求 AI 与工程师使用同一套源文件、Git diff、构建和检查，不为工程变更新增专用确认或接受页面；[ADR-0021](0021-tex-studio-plugin-based-disciplinary-workbenches.md) 已否决每个专业独立建设完整应用。

自建模型循环需要维护上下文、工具、重试、权限和登录，并与 Studio 的文件和终端形成第二条修改通道。Agent Client Protocol（ACP）定义了编辑器与编码智能体之间基于 stdio 的 JSON-RPC 协议，DeepSeek Harness、Claude Code、Codex 和 Gemini CLI 均已提供原生实现或官方适配器。

## 决策

1. **Studio 只实现 ACP 客户端，不内置智能体循环。** 模型访问、工具编排、上下文管理和登录由所选智能体负责。Studio 负责启动子进程、协议握手、项目文件与终端代办、权限请求转交和会话呈现。
2. **后端持有协议连接。** `texsys.studio.agent` 以换行分隔的 JSON-RPC 2.0 与智能体通信，把 `session/update` 等事件追加到每个会话的有序事件日志。浏览器通过 `/api/agent/events` 长轮询读取，页面重载后按序号重建对话，不依赖浏览器保存会话。
3. **工作区边界由客户端执行。** 项目工作区会话以项目根目录为边界，空白工作区会话在 `~/.cache/texsys/agent-scratch` 独立目录中启动，不接触已绑定但未打开的项目。`fs/read_text_file`、`fs/write_text_file` 和 `terminal/*` 只接受当前会话工作区内的绝对路径，拒绝版本库内部目录和本机 AI 设置。写入使用原子替换，项目会话在修改后告知工作台刷新资源管理器与源代码管理。
4. **修改仍走源文件与 Git。** 智能体直接修改源文件；工程师在源代码管理中审查差异，用构建与检查判定。ACP 权限请求只回答“是否允许这次工具操作”，不构成工程接受。
5. **智能体清单可本机扩展。** 内置 DeepSeek Harness、Claude Code、Codex 与 Gemini CLI 的启动命令；`~/.config/texsys/studio-agents.json` 可覆盖命令、补充环境变量或停用条目。该文件位于项目和仓库之外，API 只返回环境变量名，不回传值，文件 API 与产物 API 拒绝读取。
6. **协议实现不新增依赖。** 首期只用到 ACP v1 的初始化、会话、提示、取消、配置项、模式、文件、终端和权限方法，以标准库实现；协议面扩大到需要模式校验或传输扩展时，再评估官方 SDK。

## 备选方案

### 继续自建 DeepSeek 工具调用循环

不采用。需要重复实现智能体基础能力，修改绕开 Studio 编辑器与 Git，且只能使用一个模型服务。

### 在 Studio 中嵌入某个智能体自带的 Web 界面或私有接口

不采用。界面与 Studio 的文件、终端和源代码管理脱节，也会把 Studio 绑定到单一产品的私有接口。

### 浏览器内直接使用 ACP TypeScript SDK

不采用。浏览器无法启动子进程，文件与终端代办仍需后端；会话跨页面重载保留也要求连接由后端持有。

## 后果

- 工程师在 Studio 右侧选择已安装的智能体完成任务，修改与人工修改走同一条审查路径。
- 不同智能体的协议覆盖不一致。DeepSeek Harness ACP 0.0.1 自行读写文件和执行命令，不向客户端请求权限，也不发送 diff；Studio 根据工具原始输入呈现命令、路径与片段差异，并在工具完成后刷新工作区。客户端代办和权限确认只对使用这些方法的智能体生效。
- 智能体运行在 Studio 后端所在环境。常驻开发容器中需要在容器内安装智能体并配置其凭据。
- 会话在 Studio 后端进程内存中保存，后端重启后需要新建会话；`session/load` 恢复留待后续评估。

## 验证与复审

- `tests/test_studio_agent.py` 用脚本化 ACP 智能体覆盖握手、配置项、提示上下文、文件读写边界、权限往返、终端、取消和进程退出。
- `tools/test_agent_panel.mjs` 与 `tools/test_agent_panel_ui.mjs` 覆盖事件折叠、工具呈现、差异计算和面板交互。
- 在常驻开发容器中以真实 DeepSeek Harness 完成一次只读任务，事件日志包含工具调用、思考、回答与轮次结束。

出现以下情况时复审：需要跨后端重启恢复会话；需要向智能体提供 MCP 工程工具；需要远程或多用户执行；官方 SDK 成为支持新协议版本的必要条件。

## 参考资料

- [Agent Client Protocol](https://agentclientprotocol.com)
- [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness)
- [DDR-0030：右侧智能体面板](../ddr/0030-studio-agent-panel.md)
