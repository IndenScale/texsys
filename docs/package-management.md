# 工程能力包：发布、同步与跨项目复用

包管理将可复用的工程能力交给多个项目使用。项目依赖通过命令行导入和同步，仓库支持本地／共享目录；Tex Studio 复用通用文本编辑器和终端，不提供专用依赖页面。部件包是包的一种，IDE 插件拥有独立生命周期。当前契约见 [ADR-0025](adr/0025-package-component-ide-boundaries.md)，归档和锁机制沿用 ADR-0024。

## 包与项目的边界

| 放入能力包                               | 保留在消费项目                             |
| ---------------------------------------- | ------------------------------------------ |
| 设备类型、模块、接口、默认规格和适用范围 | 设备实例、选型覆盖、安装数量与连接拓扑     |
| 物理行为、求解器适配器、几何提取方法     | 布置坐标、实验负载、边界条件及控制工况     |
| 可配置规则、输入 schema、例外处理机制    | 本项目选择的规则、阈值、适用依据及例外决定 |
| 包作者的解析参考、正反例和 Unit Test     | 需求到测试的映射、项目验收工况及结果       |
| 公共图元与交付物生成方法                 | 人工标注、交付配置与项目审查资料           |

客户原件、微信附件和现场样本不会因创建能力包而自动成为公共资产。已有 `datacenter` 包仍是特定输入结构的概念研究实现；打包不会扩大其通用性或工程接受范围。

## 一次发布与使用

从作者工作区发布可复用的校核能力：

```bash
uv run --locked texsys package publish packages/hvac-checks --registry build/package-registry
uv run --locked texsys package list --registry build/package-registry
uv run --locked texsys package show hvac-checks --version 0.1.0 --registry build/package-registry
```

发布会准备依赖闭包。同名同版内容不允许覆盖；改动能力后需发布新版本。也可以先 `package pack` 得到 `.texpkg` 归档，再交给共享仓库。

消费项目保留自己的源输入，引用明确版本：

```bash
uv run --locked texsys init -p build/package-consumer
uv run --locked texsys add hvac-checks --version 0.1.0 --registry build/package-registry -p build/package-consumer
uv run --locked texsys sync -p build/package-consumer
uv run --locked texsys package status -p build/package-consumer
uv run --locked texsys package test hvac-checks -p build/package-consumer
```

`add` 声明、锁定并同步。`sync` 恢复已锁内容，不更新版本。包测试可在没有项目冷站输入时验证能力本身；执行 `hvac:design-checks` 则需要项目提供合法的 `src/hvac.json`。缺少项目输入不能通过复制另一项目的检查结果解决。

部件包导出的 `.sdl.json` 通过 `@包名/...` 导入。只有显式声明部件导出的包进入部件库；规则、求解器等包仍可成为项目依赖，但不作为部件库来源。安装包、记录部件选用和创建项目实例是不同操作。

## 更新与回退

把请求保存为 JSON，显式指定动作、包名、来源和精确版本，例如：

```json
{
  "action": "update",
  "name": "hvac-checks",
  "version": "0.1.1",
  "registry": "/共享目录/工程包仓库"
}
```

```bash
uv run --locked texsys package plan /tmp/package-change.json -p build/package-consumer
uv run --locked texsys package apply /tmp/package-change.json --plan '替换为预览返回的plan' -p build/package-consumer
```

预览展示版本、来源和内容变化，应用校验同一计划。回退使用明确的旧版本，不能通过修改缓存文件或覆盖同版发布物完成。依赖变化后重新检查受影响结果；包更新成功不代表设计自动通过。

## 能力清单

发布者在 `texsys.toml` 明确声明：

```toml
[package]
kind = "capability"
description = "暖通设计校核"
capabilities = ["rules", "solver"]
scope = "声明输入下的概念容量与环境检查"
include = ["README.md", "tests"]
tests = ["tests"]

[package.runtime]
api_version = 1
modules = ["texsys_hvac_checks"]
entry_point = "texsys_hvac_checks:package"
requires = ["texsys>=0.1,<0.2"]
```

依赖的其他能力包进入 `[dependencies]`；第三方 Python 运行要求进入 `requires`。仅提供公共导入代码的库可以省略 `entry_point`，但仍声明模块和环境要求。`modules` 是入口所属的顶层 Python 模块白名单。

`include` 只添加明确需要分发的说明或验证资产。归档中的文件身份进入摘要；路径越界、符号链接或不合法的发布物不能作为成功同步。

## 部件包

部件包复用一般包的名称、版本、依赖和锁机制，通过明确导出使部件库能够识别：

```toml
[package]
kind = "component"
description = "暖通设备部件"
capabilities = ["models", "geometry"]

[package.exports]
components = ["src/library"]
```

导出为包根相对路径，限定在源目录的 `library/` 约定内，可声明部件目录、公开 SDL 入口或 `library/models.json`。内部 `module.toml` 继续描述模块入口、资产和端口绑定；它的资源修订不能替代外层包的分发版本。通用包的能力标签不自动成为部件声明。

## Studio 的使用方式

从文件资源管理器打开 `texsys.toml`，使用现有文本编辑器直接查看和修改依赖声明；`texsys.lock` 同样作为原始文件查看，由 CLI 维护。JSON、YAML、TOML 等配置沿用通用编辑能力。依赖导入、更新、移除与同步在集成终端执行 CLI，状态通过 `texsys package status` 查看。没有项目依赖 activity、专用页面或配套 HTTP 包接口。

「部件库」浏览项目自身部件和已导入部件包的明确导出。外部目录仍可只读浏览；尚未导入时显示 CLI 提示，不能通过“引用”安装包。已锁定且同步的同一部件可记录项目引用，引用操作不改依赖图或锁文件。CLI 升级后的旧引用另行复核，不自动成为当前版本的工程接受。

「扩展」属于 IDE，只列 IDE 已加载的插件。安装或启用插件不添加项目包；项目安装规则、求解器或部件包也不安装 IDE 插件。IDE 的检查、构建、求解和编辑前工程校验通过独立进程使用项目锁定能力。

## 重跑端到端演示

在已准备好仓库开发环境的情况下执行：

```bash
uv run --locked python tools/verify_package_platform.py
```

脚本创建隔离的作者副本、文件仓库和两个消费项目，验证发布、移除作者目录后的恢复、不同项目输入下的实际计算、包测试、升级后的旧运行结果失效以及回退。输入与结果写入新的 `build/package-platform-poc/` 子目录；其中 `summary.json` 和逐命令记录供复核。可使用 CLI 将该目录中的 `registry` 引入其他项目，再在 Studio 的文本编辑器查看清单，在部件库浏览部件。示范新版只用于检验升级机制，未改变物理方程。

## 首期运行与分发边界

- 支持本地及共享文件目录仓库，不提供公网账户、托管下载服务或签名发布者身份。
- 发布包能够固定并提供纯 Python 能力源码；第三方 Python 库仍需要在执行环境中满足明确要求。
- 项目能力注册和已声明模块的版本按项目锁隔离；执行进程仍复用宿主 Python 环境。本期不保证包中未声明的 Python 导入不受宿主安装影响，完整环境隔离另行验收。
- `sync` 不安装 OpenModelica、Node.js、React、ELK 或 Studio 专业 UI。它们的独立运行要求需要另行配置，缺少时应显示不可用或由实际执行报错。
- 已取得内容可以通过校验缓存还原；这不等于所有外部运行环境已经能够离线重建。
- 公共能力测试、项目计算与工程接受分别验证，不能互相替代。
