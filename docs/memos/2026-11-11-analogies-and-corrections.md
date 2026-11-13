# 技术类比与事实修正

日期：2026-11-11。分类：可入库。类型：技术资料与架构启发。

原讨论借助 VS Code、Figma、EDA、Git 和包管理解释工程设计方向。本备忘录整理相关事实及对 texsys 的具体启发。

## VS Code 的演进时间线

| 时间         | 产品进展                                               | 一手来源                                                                                                   |
| ------------ | ------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------- |
| 2015 年      | 首次公开预览；同年后续加入完整扩展模型并开源           | [VS Code 1.0 回顾](https://code.visualstudio.com/blogs/2016/04/14/vscode-1.0)                              |
| 2016-04-14   | 发布正式 1.0                                           | [VS Code 1.0](https://code.visualstudio.com/blogs/2016/04/14/vscode-1.0)                                   |
| 2016-06-27   | 与 Red Hat、Codenvy 推动公共语言服务器协议及跨工具采用 | [A Common Protocol for Languages](https://code.visualstudio.com/blogs/2016/06/27/common-language-protocol) |
| 2018 年 1.27 | 将 GUI 设置编辑器设为默认，同时保留 JSON 编辑          | [August 2018](https://code.visualstudio.com/updates/v1_27)                                                 |
| 2021-10-20   | 发布 vscode.dev，提供浏览器运行的编辑体验              | [vscode.dev](https://code.visualstudio.com/blogs/2021/10/20/vscode-dev)                                    |

原聊天把这些选择集中放进了立项叙事。按时间展开后，更值得借鉴的是渐进演化：产品先提供具体编辑体验，再逐步丰富扩展接口、协议、设置方式和运行环境。

## 插件隔离与语言服务

Sublime Text 3 在 2013-01-29 的官方版本说明中已经采用进程外 Python 插件。插件隔离在编辑器中有多个实践来源。[Sublime Text 3 Beta](https://www.sublimetext.com/blog/articles/sublime-text-3-beta)

VS Code 的扩展宿主使用 Node.js 本地／远程运行时或浏览器 WebWorker；LSP 通过协议连接使用不同语言实现的语言服务器。二者分别组织编辑器扩展和语言智能服务。[Extension Host](https://code.visualstudio.com/api/advanced-topics/extension-host)、[公共语言协议](https://code.visualstudio.com/blogs/2016/06/27/common-language-protocol)

对 texsys 的启发是将交互宿主、专业扩展和求解进程分工设计，通过清晰接口交换输入、进度、结果和诊断。

## Atom 的停止维护

GitHub 宣布 Atom 于 2022-12-15 停止维护。官方说明涉及长期功能开发减少、社区参与下降，以及资源转向云端开发工具。[Sunsetting Atom](https://github.blog/news-insights/product-news/sunsetting-atom/)

原聊天将其概括为性能导致失败。更完整的解释包含产品投入、社区活跃度和资源配置。这提醒产品规划同时关注技术体验、维护投入与生态参与。

## Figma 的浏览器渲染

Figma 的 2015 年技术文章介绍了 C++ 经 Emscripten 编译，以及在浏览器中通过 WebGL 实现渲染引擎。其高性能图形体验依靠浏览器端的底层图形能力。[Building a professional design tool on the web](https://www.figma.com/blog/building-a-professional-design-tool-on-the-web/)

Tex Studio 可以分别组织编辑、可视化、重计算与协作：交互在浏览器中完成，求解任务根据资源安排在本地或远程执行，协作围绕项目版本和变更展开。

## IFC 的结构化信息

buildingSMART 的 2024 tunnel 项目库文档列出 IFC-XML 与 IFC-SPF 交换形式，并包含项目库版本、发布日期、发布者等结构化信息。[Project Library Information](https://technical.buildingsmart.org/projects/2024tunnel/schema/templates/project-library-information.htm)

原聊天将 IFC 归为二进制黑箱、把内容概括为几何与少量属性。格式与语义应具体分析：schema 定义可表达的信息，工具负责导入导出，工程计算消费对象、关系、参数和行为。

对 texsys 的建议是按工程用途建立适配：保留已有信息，为求解补齐工况、行为和依据链，并记录输入映射。这样可以连接既有工程资料和新的设计工具链。

## 可直接借鉴的结构

| 来源     | 设计结构                             | 在 texsys 中的应用                             |
| -------- | ------------------------------------ | ---------------------------------------------- |
| EDA      | 分层抽象、预算、物理提取、反注和签核 | 从指标推进到空间实现，并将实现影响送回行为计算 |
| Git      | 版本、变更、分支、合并和恢复         | 组织设计修改、比较方案和保留决策过程           |
| monorepo | 共享语义和联合检查                   | 在综合设计组织中连接各专业接口                 |
| 包管理   | 声明依赖、锁定版本、同步与升级       | 分发工程对象、规则、设备与求解能力             |
| CI       | 自动运行检查与回归                   | 持续发现变更带来的工程冲突                     |
| 开源生态 | 公开接口、共享实现、社区扩展         | 扩大工具链接入和工程能力供给                   |

## 建议的表达方式

每条产品主张写清痛点、机制、使用体验和实现步骤。例如，设计者调整路由，系统提取参数、更新行为计算并解释变化；这一体验直接串起物理反注、依赖与持续验证。

具体推进方案见 [工程验证与推进次序](2026-11-11-assumptions-and-validation.md)。
