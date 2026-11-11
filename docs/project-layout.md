# texsys 项目布局

> 状态：有效。本文定义 `texsys init` 与 `texsys info` 当前支持的唯一项目布局。

## 命令与路径

```bash
uv run texsys init
uv run texsys info
```

两个命令都直接作用于当前工作目录。需要操作其他目录时使用 `-p` 或 `--path`：

```bash
uv run texsys init -p /path/to/project
uv run texsys info --path /path/to/project
```

路径始终被视为目标项目根目录，不搜索父目录，也不递归发现其他项目。`init` 只创建缺失内容，不覆盖已有文件；`info` 只读。

## 最小布局

```text
texsys.toml
src/
assets/
build/
.gitignore
```

| 路径          | 角色                             | 权威性与生命周期                     |
| ------------- | -------------------------------- | ------------------------------------ |
| `texsys.toml` | 项目元数据与布局声明             | 人工维护的权威项目清单               |
| `src/`        | SDL 描述与项目源文件             | 权威输入，不存放生成物               |
| `assets/`     | 源场景、几何与其他源资产         | 由人工或外部系统维护，不存放派生资产 |
| `build/`      | 场景、派生资产、报告与其他生成物 | 可重新生成，不反向冒充源输入         |
| `.gitignore`  | 默认忽略 `/build/`               | 仅在文件不存在时创建，已有文件不修改 |

空目录只表示布局角色，不代表其中已有有效 SDL 模型或资产。

## 项目清单

`schema_version` 当前为 `1`。清单必须包含非空项目名称和三个项目内相对路径：

```toml
schema_version = 1

[project]
name = "example"

[layout]
source = "src"
assets = "assets"
build = "build"
```

布局路径不得为绝对路径，不得包含 `..`，解析符号链接后也不得指向项目根之外。`source`、`assets` 与 `build` 必须是三个互不包含的不同路径，避免源输入与生成物角色重叠。`build` 的 target 选择、profile、scope 与 hook 声明由构建器解释，见 [构建 target 契约](../spec/build/00-overview.md) 和 [Hook Runner 协议](../spec/build/02-hooks.md)。

## 依赖与锁文件

`info` 识别以下现有文件：

| 类别           | 文件                             |
| -------------- | -------------------------------- |
| 实现环境元数据 | `pyproject.toml`、`package.json` |
| 实现环境锁文件 | `uv.lock`、`package-lock.json`   |
| 设计依赖锁     | `texsys.lock`                    |

这些文件不是 `texsys.toml` 的替代品。Python／Node 文件描述实现环境；`texsys.toml` 的 `[dependencies]` 声明设计依赖，`texsys.lock` 固定完整解析结果。支持本地开发路径包和本机／共享目录 registry 中的不可变版本包；前者规则见 [ADR-0006](adr/0006-design-dependency-management.md)，可迁移能力包由 [ADR-0024](adr/0024-portable-capability-packages.md) 扩展，CLI 导入、部件分类及 IDE 隔离遵循 [ADR-0025](adr/0025-package-component-ide-boundaries.md)。

```bash
uv run texsys add ../package
uv run texsys add hvac-checks --version 0.1.0 --registry /path/to/registry
uv run texsys lock --check
uv run texsys sync
uv run texsys remove package
```

`add` 与 `remove` 会更新声明、锁文件并同步；`lock` 只解析和锁定；`sync` 严格按锁文件还原到声明的 build 目录下的 `dependencies/`，不静默更新锁文件。registry 依赖必须指定精确版本，锁文件记录来源、版本、内容摘要、归档摘要及传递依赖。路径包继续指向开发目录；registry 包始终保留发布来源身份，不将解包缓存路径写成新的开发依赖。

当前 `init` **不安装依赖、不运行解析器、不生成锁文件**。只有实际完成依赖解析后才写入 `texsys.lock`，不得用空文件伪装已锁定状态。

## 能力包、共享仓库与缓存

能力包拥有自己的 `texsys.toml`，以 `[package] kind = "capability"` 显式声明可分发身份，沿用 source、assets 和 build 的布局角色。包的源码、运行声明、适用范围及明确包含的测试和说明可发布为 `.texpkg`；消费项目不因存在清单而自动成为可发布能力包。发布、预览变更、同步、测试和跨项目使用的完整流程见 [工程能力包](package-management.md)。

| 位置 | 内容与边界 |
| --- | --- |
| 作者工作区 | 维护能力包源码与测试；改动已有发布内容时发布新版本 |
| `.texpkg` | 包源码与声明资产的可分发归档，携带可校验的包身份；不是整个项目目录的备份 |
| 共享 registry | 按 `packages/<name>/<version>/` 保存 `artifact.texpkg` 和 `package.json`；同名同版内容不可覆盖 |
| 项目 `texsys.toml`、`texsys.lock` | 分别保存依赖意图和完整锁定结果，随项目版本管理 |
| 本机内容缓存 | 默认 `~/.cache/texsys/packages/<内容摘要>`，可由 `TEXSYS_CACHE_DIR` 修改缓存根目录；用于校验后复用，不是工程输入的编辑位置 |
| 项目 `<build>/dependencies/` | 按锁同步的安装副本，可从仍可用的校验缓存或发布来源恢复；不作为权威源码维护 |

共享 registry 是明确指定的文件目录，可由多个项目使用；当前不包含公网账号、托管下载或发布者签名身份。内容摘要用于一致性检查，不证明模型已满足具体工程条件。`sync` 还原工程包内容，不安装 Python 第三方环境、外部求解器或 Studio 前端扩展；这些运行要求与工程依赖锁分别管理。

可跨项目复用的设备类型、接口、默认规格、物理行为、可配置规则和公共 Unit Test 进入能力包。设备实例、型号选定后的覆盖值、连接拓扑、布置、实验边界条件和控制工况保留在消费项目的源输入中。需求映射、项目验收条件、计算证据及人工接受记录继续归属具体项目；复制包测试结果不能替代项目验收。

客户原件、微信附件、现场样本和项目专有资料按各自来源及使用边界保留，不随打包自动变成公共资产。包的 `include` 只声明明确需要分发的附加文件；发布前仍需检查能力范围与资料边界。派生报告与同步副本留在 build，不能回写为未经来源说明的权威输入。

## 扫描结果与失败

`info` 报告项目根、清单版本、目录状态以及依赖和锁文件是否存在：

- 未初始化目录是正常扫描结果，不返回失败；
- 已初始化但目录缺失或不是目录，返回失败；
- 清单无法读取、版本不受支持或布局路径无效，返回失败；
- 路径不存在或不是目录，返回失败；
- 可选依赖或锁文件缺失只表示未识别，不表示项目无效。

`init` 在已初始化项目上重跑时补齐缺失目录；没有需要创建的内容时报告 `No changes needed`。已有清单必须可读取且使用受支持的布局，发生冲突时停止，不覆盖原文件。

项目依赖声明直接在通用文本编辑器中查看和修改，CLI 负责导入、锁定与同步。Studio 不提供专用依赖 activity 或页面，部件引用只消费已导入的明确部件导出；IDE 插件的安装与实现依赖不进入项目锁。`module.toml` 是部件内部资产清单，发布身份以外层包名称、版本和导出入口为准。
