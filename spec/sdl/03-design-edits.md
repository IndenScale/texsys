# SDL 批量编辑契约

语义编辑入口供 Agent 和本地工具使用。它把操作应用到一个 `.sdl.json` 候选文件，通过与 `texsys check` 共用的 SDL 模式和语义校验后才写入。设计仍保存在项目源文件中，任一编辑器都能修改这些文件。领域检查、出图和工程交付条件由 `texsys check` 和 `texsys build` 按各自契约处理。

## 命令与 API

`texsys design inspect -p <项目>` 返回当前 SDL 文件和项目输入 `revision`。用 `--source` 选择源目录内的其他 `.sdl.json` 文件。不存在的文件返回 `document: null`，可由 `create-design` 创建。`source` 相对于项目声明的源目录，编辑入口拒绝符号链接和越界路径。

`texsys design plan <请求文件> -p <项目>` 返回统一差异、诊断和 `plan` 摘要。`texsys design apply <请求文件> --plan <摘要> -p <项目>` 重新验证同一请求和输入修订后应用。两个命令均接受 `-` 作为请求文件，从标准输入读取 JSON。状态 `pass`、`fail`、`error` 和 `conflict` 分别对应退出码 `0`、`1`、`2` 和 `3`。

Python 对应入口为 `texsys.api.read_design(path, source)`、`plan_edit(path, request)` 和 `apply_edit(path, request, plan)`。命令与 Python API 共用编辑实现。

## 请求与操作

以下请求在空项目中创建根部件，声明供水和负载部件，建立实例、连接和网络。调用时用 `inspect` 返回的 `revision` 替换示例值。

```json
{
  "version": 1,
  "source": "system.sdl.json",
  "revision": "sha256:替换为当前项目输入修订",
  "operations": [
    { "op": "create-design", "id": "Plant" },
    {
      "op": "declare-component",
      "id": "Pump",
      "definition": { "ports": { "outlet": { "kind": "chilled-water", "direction": "out" } } }
    },
    {
      "op": "declare-component",
      "id": "Load",
      "definition": { "ports": { "inlet": { "kind": "chilled-water", "direction": "in" } } }
    },
    { "op": "add-instance", "id": "pump", "component": "Pump" },
    { "op": "add-instance", "id": "load", "component": "Load" },
    { "op": "add-connection", "id": "feed", "from": "pump.outlet", "to": "load.inlet" },
    { "op": "declare-net", "id": "CHW", "kind": "chilled-water", "connections": ["feed"] }
  ]
}
```

`name-connection` 接受与 `add-connection` 相同的字段，为唯一匹配的未命名连接补上标识。`set-net-members` 以 `id` 和 `connections` 替换已有网络的成员。连接标识或显式网络会使用 SDL 交换格式 v2。操作按数组顺序执行，最终候选文件统一检查引用及归属。新增对象的名称冲突会报告错误。

## 写入与构建

项目输入修订覆盖清单、设计锁、源文件和资产。预览只读。应用串行执行并复核修订与计划摘要，校验失败或修订过期时不发布候选文件。一次应用只原子替换或创建一个文件。人工或外部工具直接编辑时，应避开同一个文件正在应用的时段。应用完成后可直接从源文件构建，也可以继续通过 Git 审查差异。

设计依赖使用 `texsys add`、`remove`、`lock` 和 `sync`，Python 对应入口是 `add_dependency`、`remove_dependency`、`lock_dependencies` 和 `sync_dependencies`。依赖操作和设计应用共用项目写锁，分别执行。`add` 和 `remove` 先解析依赖图，再写清单、锁和同步结果。若同步抛出错误，会恢复本次写入且仍匹配本次内容的清单与锁。异常进程退出后，确认写入进程已经停止，才可以清理项目根目录下遗留的 `.texsys-write.lock` 空目录，并用 `texsys lock --check` 检查依赖状态。
