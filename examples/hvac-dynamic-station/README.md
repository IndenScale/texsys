# 同源冷站：设备表、图纸与动态行为

这是一份集总参数冷站概念示例，包含冷水机组、两台循环泵、负荷换热器、冷却塔、两条管段和一只调节阀。冷机形成两个水力独立、热力耦合的回路。所有设备身份、端口、管路和参数位于 `src/system.sdl.json`，不复制到独立设备台账。

- `assets/physics/`：版本化本构选择与假设。
- `assets/scenarios/load-step.json`：300 s 时负荷从 40 kW 增至 80 kW。
- `assets/scenarios/pump-speed.json`：300 s 时冷冻水泵转速比例从 1 降至 0.7。
- `assets/scenarios/pump-trip.json`：300 s 时冷冻水泵退出；供冷温度约束故意失效。

每个工况求解 900 s，输出间隔 5 s，并保留事件时刻。参数为显式 assumed 概念值；不代表具体厂家设备或实际工程验收。

```bash
uv run examples/build-deliverables/run.py all
```

运行前先在仓库根目录同步依赖并检查项目锁，完整命令和 Python API 见 [调用示例](../build-deliverables/README.md)。

输出包括 `build/hvac/bom.json`、`build/hvac/behavior/model.json`、`build/hvac/system/system.html`、`build/hvac/schematic/system.html` 和各工况目录。浏览器中可选择设备、查看参数依据、切换工况、播放时序、跳至下一事件、查看曲线与守恒／性能检查。工程图元页支持横向比例与标注层次变化；图形比例不修改物理性能参数。

复核用例见 `tests/test_hvac_dynamics.py`、`tests/test_hvac_physical_pipeline.py` 和 `tools/verify_hvac_backends.py`。安装几何事件示例单独保留在 hvac-cold-station，不与运行工况混用。
