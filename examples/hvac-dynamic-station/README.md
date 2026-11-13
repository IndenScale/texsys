# 同源冷站：设备表、图纸与动态行为

这是一份集总参数冷站概念示例，包含冷水机组、两台循环泵、负荷换热器、冷却塔、两条管段和一只调节阀。冷机形成两个水力独立、热力耦合的回路。所有设备身份、端口、管路和参数位于 `src/system.sdl.json`，不复制到独立设备台账。

- `assets/physics/`：版本化本构选择与假设。
- `assets/layout.json`：与八个 SDL 实例对应的显式概念位置和包络；不代表实际设备尺寸、管线走向或施工布置。
- `assets/scenarios/load-step.json`：300 s 时负荷从 40 kW 增至 80 kW。
- `assets/scenarios/pump-speed.json`：300 s 时冷冻水泵转速比例从 1 降至 0.7。
- `assets/scenarios/pump-trip.json`：300 s 时冷冻水泵退出；供冷温度约束故意失效。

每个工况求解 900 s，输出间隔 5 s，并保留事件时刻。参数为明确标记为假设的概念值；不代表具体厂家设备或实际工程验收。

```bash
uv run examples/build-deliverables/run.py all
```

运行前先在仓库根目录同步依赖并检查项目锁，完整命令和 Python 接口见 [调用示例](../build-deliverables/README.md)。

项目预览使用默认构建目标，生成设备 BOM、物理模型、拓扑图、原理图、概念场景，以及带图框的原理图页和总说明页：

```bash
uv run --locked texsys build -p examples/hvac-dynamic-station
```

在 Tex Studio 的文件资源管理器中直接打开下列真实文件，每个文件拥有独立文档标签；无需进入项目实例 Activity：

- `build/drawings/schematics/system.json`：原理图及同目录的 SVG、HTML、Draw.io。
- `build/drawings/topology/system.json`：拓扑图及同目录的 SVG、HTML、Draw.io。
- `build/drawings/sheets/system.svg`：含独立图框、标题栏与说明的原理图页。
- `build/drawings/sheets/general-notes.svg`：使用同一组件库的总说明页；各页旁的 `*-sheet.json` 保存组合来源。
- `build/hvac/bom.json`：设备 BOM 数据，使用普通 JSON 编辑器。
- `build/hvac/bom.md`：设备 BOM 文档，可切换 Markdown 预览。
- `build/hvac/layout/scene.usda`：概念场景。
- `build/build.json`：本次构建清单与诊断。

文件使用统一的“预览 / 源代码”布局，Markdown、图纸和场景在预览区域渲染；JSON 保持代码编辑体验。生成文件可以直接编辑和保存，预览使用当前文件内容。重新构建可能覆盖生成文件的手工修改；要持续保留设计变化，应修改声明式源文件。图形预览暂不反向写回声明式设计。重复构建复用有效目标缓存；只修改 `assets/layout.json` 中的位置或包络时，图纸、BOM 和物理模型不需要重建。构建失败保留独立分支的产物，执行信息保存在 `build/build.json`，各次清单与输入／输出快照位于 `build/runs/`；这些信息不占据文件预览布局。

从仓库根目录运行 `uv run --locked python tools/verify_project_preview.py`，可在隔离的两个项目副本中回放首次构建、缓存命中、布局变化、失败回退、修复及产物恢复，证据写入 `build/project-preview-poc/`。这是开发机制与概念模型的验证，不替代真实工程校准。

运行 `examples/build-deliverables/run.py all` 的独立工程演示时，包注册目标生成 `build/hvac/system/system.html`、`build/hvac/schematic/system.html` 和各工况目录。浏览器中可选择设备、查看参数依据、切换工况、播放时序、跳至下一事件、查看曲线与守恒／性能检查。工程图元页支持横向比例与标注层次变化；图形比例不修改物理性能参数。

复核用例见 `tests/test_hvac_dynamics.py`、`tests/test_hvac_physical_pipeline.py` 和 `tools/verify_hvac_backends.py`。安装几何事件示例单独保留在 `hvac-cold-station`，不与运行工况混用。

## 图框、总说明与文字

图页由独立的设计组件、项目内容和已有视图编译组成：

| 维护内容         | 源文件                                                                                                   | 修改作用                                                     |
| ---------------- | -------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------ |
| 图框与排版规则   | `sheet` 包中的 `sheet:a3-landscape`、`sheet:a3-notes`                                                    | 定义纸幅、线宽、文字样式及内容区域；可改为项目内的模板路径   |
| 项目、图名与修订 | [原理图标题栏](assets/sheets/title.json)、[总说明标题栏](assets/sheets/notes-title.json)                 | 每张图独立的图号、图名和修订字段                             |
| 工程说明         | [general-notes.txt](assets/sheets/general-notes.txt)                                                     | 由工程师直接维护的总说明，保留段落与显式编号                 |
| 页脚文字         | [footer.txt](assets/sheets/footer.txt)                                                                   | 维护草稿标识和适用范围，使用 `{{drawing_number}}` 等字段绑定 |
| 图页装配         | [system.sheet.json](assets/sheets/system.sheet.json)、[notes.sheet.json](assets/sheets/notes.sheet.json) | 选择模板、元数据、文字和已构建的视图                         |

普通文字和 JSON 在 Studio 通用编辑器中维护；输出 SVG 使用现有图纸预览。默认构建已经包含 `drawing-system` 和 `drawing-notes`，也可分别执行：

```bash
uv run --locked texsys build -p examples/hvac-dynamic-station --target drawing-system
uv run --locked texsys build -p examples/hvac-dynamic-station --target drawing-notes
```

`drawing-system` 显式依赖 `system-schematic`，先生成原理图，再编排图页；`drawing-notes` 只依赖自己的声明式内容。修改说明、标题栏或模板后重新构建，原始工程源不因此改变。原理图按图页区域适配，标注为“示意，不按比例”。标题栏的设计、校核和批准字段是普通文字，当前均为示例或待复核状态，不构成签章或工程接受。完整输入、失败处理与缓存边界见 [图页组合契约](../../spec/build/05-sheet-composition.md)。
