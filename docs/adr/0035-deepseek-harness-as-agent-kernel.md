# ADR-0035：以 DeepSeek Harness 作为唯一智能体内核

- 状态：已接受
- 日期：2026-11-18
- 取代：部分取代 [ADR-0034](0034-acp-agent-client.md) 的决策 5（可扩展的多智能体清单）与“Studio 作为通用 ACP 客户端”的产品定位；决策 1–4（不内置模型循环、后端持有连接、客户端执行工作区边界、修改走源文件与 Git）继续有效
- 被取代：无

## 背景

[ADR-0034](0034-acp-agent-client.md) 把 Studio 定位为通用 ACP 客户端，内置 DeepSeek Harness、Claude Code、Codex 与 Gemini CLI 四个条目，并允许 `studio-agents.json` 扩展。实际使用中暴露三个问题：

1. **界面被最小公分母绑架。** 为兼容不同智能体，面板需要 Harness 选择器、按来源合并的模型目录、登录流程、`modes`／`models`／`configOptions` 三套模型接口和多种降级呈现。DDR-0031／0032 的大部分复杂度来自这里，而工程师只用 DeepSeek Harness。
2. **会话历史不可用。** 会话只存在于 Studio 后端内存，重启即丢失；ACP `session/list` 只返回 `sessionId` 和 `cwd`，没有标题和时间，DSH 也明确不支持 `session/load` 回放。
3. **能力无法对齐。** 另外三个智能体从未在 Studio 中验收，却持续占用界面、测试和文档。

用户要求取消 ACP 接口，直接以 DSH（DeepSeek Harness）为内核。为此核对了本机与常驻开发容器中的 `@deepseek-ai/dsh` 0.2.2-rc.1。DSH 提供三种可编程接入面：

| 接入面              | 形式                                | 会话列表／恢复         | 取消           | 运行中切换模型与推理强度 | 权限请求 | 历史回放、计划、标题、子智能体 | 稳定性                                                  |
| :------------------ | :---------------------------------- | :--------------------- | :------------- | :----------------------- | :------- | :----------------------------- | :------------------------------------------------------ |
| `dsh --profile acp` | 标准 ACP v1，stdio                  | 有（列表无标题）       | 有             | 有（`configOptions`）    | 有       | 无（官方定位为“仅自动化”）     | 公开标准协议，有握手与能力声明                          |
| `dsh --profile sdk` | DSH 私有 JSON-RPC，stdio            | 无                     | 无，只能杀进程 | 无，初始化时固定         | 无       | 只有原始会话事件和子智能体通知 | 0.0.1，无版本协商与兼容承诺                             |
| `dsh --profile web` | Web 网关，HTTP + WebSocket 多路复用 | 有（含标题、分页历史） | 有             | 有                       | 有       | 有                             | DSH 自有界面的内部 RPC，无公开契约，rc 版本可能随时变化 |

## 决策

1. **产品层绑定 DSH，去掉多智能体抽象。** Studio 只启动 DeepSeek Harness，不再提供智能体清单、Harness 选择器、按智能体新建会话、登录方式、`session/set_mode` 与 `session/set_model` 兼容路径。界面、文案和测试都只针对 DSH 设计。
2. **线协议继续使用 DSH 自带的 ACP profile，并把它降级为内部实现细节。** 后端以 `dsh --profile acp` 子进程和 ACP v1 通信，不再把 ACP 作为对外扩展点。理由：在三种接入面中，只有它同时具备取消、运行中配置、权限请求、会话恢复和版本握手，且是 DSH 官方测试覆盖的公开协议。SDK profile 能力更少；Web 网关能力最全，但属于私有内部接口。
3. **启动配置收窄为单一内核。** 默认命令为 `dsh --profile acp`，在后端 `PATH`、`~/.local/bin` 与 `~/.bun/bin` 中查找。为不破坏现有凭据配置，`~/.config/texsys/studio-agents.json` 只读取 `agents.deepseek-harness` 一项的 `command` 与 `env`，其余条目忽略。模型路由、工具、插件和 persona 等定制统一交给 DSH 自己的 profile 与 patch（`dsh plugin --profile acp …`），Studio 不重复提供。
4. **Studio 持有会话的呈现日志，DSH 持有智能体状态。** 每个会话的有序事件日志与索引（标题、工作区、创建与更新时间、DSH 会话标识）写入 `$XDG_STATE_HOME/texsys/agent-sessions`（默认 `~/.local/state/texsys/agent-sessions`），位于项目与仓库之外。重新打开历史会话时，后端启动新的 DSH 进程并调用 `session/resume`，再把已保存的事件按序重放给浏览器；DSH 无法恢复时仍可只读查看记录。删除历史只删除 Studio 侧记录，不改写 DSH 自身的持久化。
5. **不随内核绑定放宽边界。** 工作区路径边界、版本库内部目录与本机 AI 设置保护、修改走源文件／Git diff／构建检查、权限选择不构成工程接受等约束保持不变。

## 备选方案

### 改用 DSH SDK profile

不采用。它没有取消，模型与推理强度在初始化时固定，不发权限请求，也没有会话列表与恢复；用它驱动交互式面板需要的能力比 ACP 更少，协议仍处于无兼容承诺的 0.0.1。

### 直接接入 DSH Web 网关

暂不采用。它是唯一能提供会话标题、历史回放、计划、待办和子智能体（类似“专家团”）的接入面，但它是 DSH 自有浏览器界面的内部 RPC：需要启动独立 HTTP 服务、处理令牌交换与 Cookie 认证，并在 Python 中复刻 WebSocket 多路复用协议，任何 rc 升级都可能破坏 Studio。等到需要计划、待办或多智能体协作，并且 DSH 为该网关给出稳定契约时再复审。

### 保留通用 ACP 客户端

不采用。见背景第 1、3 点：为从未验收的智能体保留抽象，使界面无法针对真正使用的内核做取舍。

## 后果

- 面板去掉 Harness 选择器、登录卡片与多来源模型合并逻辑，前端与测试显著缩减；新的交互设计见 [DDR-0033](../ddr/0033-studio-agent-panel-qoder-redesign.md)。
- 会话历史在后端重启后仍可浏览并继续；呈现日志可能与 DSH 内部记录不完全一致（例如 DSH 压缩上下文后），以 Studio 日志为准呈现，以 DSH 状态为准继续对话。
- DSH ACP 不提供模式、计划、待办与历史回放，面板暂不提供“智能体／问答／专家团”模式切换；这些能力随 DSH 接入面演进再引入。
- DSH 版本升级仍可能改变 ACP 行为。`tests/test_studio_agent.py` 用脚本化 ACP 进程覆盖 Studio 侧契约，真实 DSH 由容器内端到端验证覆盖。
- 运行其他智能体的需求不再由 Studio 内置支持，可在集成终端中直接使用其 CLI。

## 验证与复审

- `tests/test_studio_agent.py` 覆盖单一内核启动、会话持久化与重放、`session/resume`、删除历史、工作区边界、权限、终端、取消与进程退出。
- 常驻开发容器中以真实 DSH 完成一次提问、重启 Studio 后从历史恢复并继续对话。

出现以下情况时复审：DSH 为 Web 网关或 SDK 提供带兼容承诺的会话、计划与子智能体接口；DSH ACP 开始提供 `session/load`、模式或计划；需要远程或多用户执行。

## 参考资料

- `@deepseek-ai/dsh` 0.2.2-rc.1 随附文档：`dsh-acp`、`dsh-acp-app`、`dsh-sdk-protocol`、`dsh-sdk-app`、`dsh-api-session-controller`、`dsh-client-connection`
- 2026-11-18 在常驻开发容器中对 `dsh --profile acp` 的握手探测：`sessionCapabilities` 为 `close`／`list`／`resume`，`configOptions` 提供 `model` 与 `reasoning_effort`（`off`／`low`／`high`／`max`），`session/list` 仅返回 `sessionId` 与 `cwd`
- [ADR-0034：Studio 作为 ACP 客户端接入外部智能体](0034-acp-agent-client.md)
