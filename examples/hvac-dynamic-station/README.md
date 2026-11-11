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

项目级只读预览使用默认构建目标，生成设备 BOM、物理模型、拓扑图、原理图与概念场景：

```bash
uv run --locked texsys build -p examples/hvac-dynamic-station
```

在 Tex Studio 的“项目实例”中点击 `ColdStation` 根名称，在中央文档切换对应预览。重复构建复用有效目标缓存；只修改 `assets/layout.json` 中的位置或包络时，图纸、BOM 和物理模型不需要重建。输入变化后旧预览标为过期，构建失败保留独立分支的产物，历史回退明确显示来源。各次清单与输入／输出快照位于 `build/runs/`，`build/build.json` 指向最新运行。预览不写回源文件，修改仍通过通用编辑器和 CLI 完成。

从仓库根目录运行 `uv run --locked python tools/verify_project_preview.py`，可在隔离的两个项目副本中回放首次构建、缓存命中、布局变化、失败回退、修复及产物恢复，证据写入 `build/project-preview-poc/`。这是开发机制与概念模型的验证，不替代真实工程校准。

输出包括 `build/hvac/bom.json`、`build/hvac/behavior/model.json`、`build/hvac/system/system.html`、`build/hvac/schematic/system.html` 和各工况目录。浏览器中可选择设备、查看参数依据、切换工况、播放时序、跳至下一事件、查看曲线与守恒／性能检查。工程图元页支持横向比例与标注层次变化；图形比例不修改物理性能参数。

复核用例见 `tests/test_hvac_dynamics.py`、`tests/test_hvac_physical_pipeline.py` 和 `tools/verify_hvac_backends.py`。安装几何事件示例单独保留在 `hvac-cold-station`，不与运行工况混用。
