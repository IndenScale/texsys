# ADR-0032：独立部署本机 TPM Registry

- 状态：已接受，本机部署与 AIDC 端到端验收通过
- 日期：2026-11-15
- 取代：无（修订 ADR-0031 的服务归属与本机部署）
- 被取代：无

## 决策

- `texsys` 保留 CLI、包格式、依赖解析、锁、摘要校验、官方包源码与文件发布工具。
- `texsys-package-manager` 独立维护 Registry 服务和部署。账户、认证、鉴权和发布者管理如有需要，在该仓定义服务端实现。客户端需要的协议能力单独定义。
- 本机部署由 macOS `launchd` 管理，监听 `127.0.0.1:8711`，静态提供官方包元数据与归档。包数据存放于用户的 `Library/Application Support/TexSys/TPM/registry`，可独立于两边的工作树保存。Studio 继续用 `8710`。
- 切换时复制原有版本与归档，再重建 Studio 容器释放 `8711`，最后加载 TPM 登录服务。保持原地址、路径、版本和摘要可继续使用已有锁文件。

## 验收

2026-11-15，Studio 与 TPM 分别监听回环端口 `8710` 和 `8711`。从 AIDC Reference Design 的 `f27c749` 干净副本使用原锁和临时空包缓存重跑 `sync → lock --check → build → check → package status`，五步退出码均为 0。TPM 返回 6 份元数据和 6 个归档，12 次 HTTP 请求均为 200。证据见[工程 POC 台账](../engineering-poc.md)。公网部署和账号管理另行决策。
