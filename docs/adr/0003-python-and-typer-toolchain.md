# ADR-0003：采用 Python 与 Typer 作为参考实现和 CLI 技术栈

- 状态：已接受
- 日期：2026-11-04
- 取代：无
- 被取代：无

## 背景

texsys 需要从只有规范与文档的仓库转为可执行、可测试、可分发的参考实现。主实现语言会影响 SDL 语义层、校验器、评价器宿主、插件和 CLI，也会影响 OpenUSD／USD BRep 的调用方式与二进制分发。

本决策采用**算法无关**的比较口径：假设各候选语言中的算法均已充分优化，只比较语言与运行时特征、类型和错误模型、原生互操作、打包分发，以及对核心依赖的兼容性。缓存、增量重算、候选剔除和任务调度不作为选型依据。

ADR-0001 当前提议采用 OpenUSD 与 USD BRep，两者提供 C++／Python API。本 ADR 不改变其提议状态，也不替代 USD BRep 可用性 spike。

## 决策

1. **Python 是 texsys 参考实现的主语言。** SDL 语义层、校验器、评价器宿主、领域插件和命令行默认使用 Python；性能敏感或原生依赖要求的位置可以使用 C++ 薄适配层，但不因此建立第二套平行语义实现。
2. **Typer 是默认 CLI 框架。** CLI 使用 Python 类型标注声明参数，以 Typer 应用作为统一入口；不再并行维护 argparse 或直接 Click 的另一套命令定义。
3. **Python 包采用 `pyproject.toml` 与 `src/` 布局。** 产品版本只在 `pyproject.toml` 维护，`texsys --version` 通过包元数据读取。命令行同时提供 `texsys` 和 `python -m texsys` 入口。
4. **首个 CLI 切片只提供 `--help` 和 `--version`。** 关闭 shell completion 选项，不引入子命令、插件发现、配置系统或 SDL 解析。该工程骨架不处理 SDL 身份或 USD 映射，不视为关闭 OQ-3、OQ-5 或 OQ-6。
5. **Node.js 工具链仅用于仓库文档格式化。** `package.json` 不是 texsys 产品版本或 Python 包元数据的来源。
6. **Python 解释器与原生依赖的支持矩阵另行验证。** `pyproject.toml` 声明的是 Python 包的基础解释器要求；OpenUSD、USD BRep 与目标平台的二进制兼容性必须通过 ADR-0001 的 spike 和版本锁定确认。

## 备选方案

### C++ 作为主语言

不采用。C++ 与 OpenUSD／USD BRep 的 C++ API 直接兼容，并能对运行时和内存进行细粒度控制；但其构建、类型和内存管理成本会扩展到 SDL、插件和 CLI 层。texsys 的主实现不需要把这些层全部置于同一原生抽象和工具链中。原生能力通过薄适配层保留。

### TypeScript／Node.js 作为主语言

不采用。TypeScript 适合文本工具和用户界面，但 OpenUSD／USD BRep 的主要 API 位于 C++／Python 技术栈，Node.js 主链路需要额外原生绑定、ABI 与生命周期适配。仓库现有 Prettier 依赖不构成应用主栈约束。

### Rust 作为主语言

不采用。Rust 的类型、所有权和错误模型适合可靠工具，但调用上述核心依赖需要额外的 FFI 与所有权适配层。当前没有仅由语言运行时产生的需求足以证明这层独立绑定和维护成本合理。

### Python 标准库 argparse

不采用。它能够生成最小 CLI 且没有额外框架依赖，但本项目已明确选择以类型标注驱动命令定义的 Typer，框架管理成本不是当前主导约束。

### 直接使用 Click

不采用。Typer 的命令模型源于 Click，并在其之上提供类型标注驱动的声明接口；Typer 0.26.0 起已内嵌 Click。直接使用 Click 会形成另一套接口风格，因此 Click 不作为 texsys 的命令声明 API。

## 后果

### 收益

- 主实现可直接使用核心依赖的 Python API，原生互操作边界较少。
- 动态类型与类型标注可以按模块成熟度逐步强化，CLI 参数与处理函数共享同一类型声明。
- Python 包、模块入口和 Typer 命令入口共用同一实现与版本来源。
- C++ 仍可用于必须直接调用原生 API 的薄适配层，不需要提前替换主实现。

### 代价与风险

- Python 动态语义可能把部分类型错误推迟到运行时；核心模块需要类型标注、测试和必要的静态检查。
- CPython 执行 Python 字节码存在解释器开销，线程并行也受运行时模型约束；不得据此假定原生内核内部并行能力受到同样限制。
- OpenUSD、USD BRep 及其他原生扩展会增加解释器版本、ABI、平台和二进制分发复杂度。
- Typer、Click 与 Rich 的版本组合会影响帮助文本和错误输出；测试应断言稳定契约，不应无必要地固定完整装饰性格式。
- 引入 C++ 适配层后，需要明确所有权、异常转换和构建边界，避免原生错误被转换为虚假成功。

## 验证与复审

- `texsys --help` 与 `texsys --version` 退出码为 0；版本输出与 `pyproject.toml` 一致。
- `python -m texsys --version` 与控制台入口结果一致；未知选项返回非零退出码。
- 最小测试覆盖帮助、版本、未知选项以及未暴露 completion 选项。
- ADR-0001 的 spike 必须记录实际可用的 Python 版本、原生依赖版本、目标平台与分发限制；本 ADR 不预先宣称这些兼容性已经成立。
- 若核心依赖不再提供可维护的 Python API，目标平台无法分发所需运行时，或实测证明 Python 运行时本身成为无法满足约束的瓶颈，应新增 ADR 复审主语言。
- 若 Typer 无法维持稳定命令契约或其依赖链造成不可接受的兼容问题，可新增 ADR 只替换 CLI 框架，不必同时改变主语言。

## 参考资料

- [ADR-0001：采用 OpenUSD 与 USD BRep 作为原生场景和几何技术栈](0001-openusd-native-scene-geometry.md)
- [Typer 文档](https://typer.tiangolo.com/)
- [Python 包元数据](https://packaging.python.org/en/latest/specifications/pyproject-toml/)
