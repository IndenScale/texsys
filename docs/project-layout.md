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

这些文件不是 `texsys.toml` 的替代品。Python／Node 文件描述实现环境；`texsys.toml` 的 `[dependencies]` 声明设计依赖，`texsys.lock` 固定完整解析结果。当前支持本地路径包，规则见 [ADR-0006](adr/0006-design-dependency-management.md)。

```bash
uv run texsys add ../package
uv run texsys lock --check
uv run texsys sync
uv run texsys remove package
```

`add` 与 `remove` 会更新声明、锁文件并同步；`lock` 只解析和锁定；`sync` 严格按锁文件还原到声明的 build 目录下的 `dependencies/`，不静默更新锁文件。

当前 `init` **不安装依赖、不运行解析器、不生成锁文件**。只有实际完成依赖解析后才写入 `texsys.lock`，不得用空文件伪装已锁定状态。

## 扫描结果与失败

`info` 报告项目根、清单版本、目录状态以及依赖和锁文件是否存在：

- 未初始化目录是正常扫描结果，不返回失败；
- 已初始化但目录缺失或不是目录，返回失败；
- 清单无法读取、版本不受支持或布局路径无效，返回失败；
- 路径不存在或不是目录，返回失败；
- 可选依赖或锁文件缺失只表示未识别，不表示项目无效。

`init` 在已初始化项目上重跑时补齐缺失目录；没有需要创建的内容时报告 `No changes needed`。已有清单必须可读取且使用受支持的布局，发生冲突时停止，不覆盖原文件。
