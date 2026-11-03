# ADR-0001：采用 OpenUSD 与 USD BRep 作为原生场景和几何技术栈

- 状态：已接受
- 日期：2026-11-03
- 取代：无
- 被取代：无

## 背景

texsys 需要连接 SDL 的工程语义、部件实例、空间布局、几何资产、场景生成和几何评价。如果分别自研场景图、几何内核和原生几何格式，将产生巨大的研发与验证成本，也会把资源从 SDL、SysDA 和设计闭环转移到成熟基础设施的重复建设上。

OpenUSD 已提供场景层级、资产引用、Layer、Variant、Payload 和场景合成能力。NVIDIA 于 2026 年 10 月以 Apache 2.0 许可公开 USD BRep，提供 SMLib 几何内核、C++／Python API，以及 OpenUSD 中的 `BrepArray` 精确 B-rep 表示，使场景合成与精确几何有机会进入同一个开放技术栈。

SDL 与场景几何仍有不同职责：SDL 表达设计意图、Port、Connection、Net、自由度、约束和证据语义；OpenUSD 表达解析后的场景与几何资产。前者不能因采用后者而失去独立的语言标准地位。

## 决策

1. **texsys 以 OpenUSD 作为原生场景与几何资产表示。** 场景组织与合成优先使用 USD Stage、Prim、Layer、Reference、Payload 和 Variant 等现有机制。
2. **texsys 以 USD BRep／SMLib 作为默认精确几何内核。** 精确 B-rep 使用 `omniSolid` Schema 定义的 `BrepArray` 表示，几何创建、编辑、验证、修复和三角化优先使用同一主链路。
3. **不再自研平行的场景 IR、几何内核或私有原生几何格式。** 研发资源优先投入 SDL、SysDA、设计评价、自动化闭环和资产治理。
4. **SDL 保持为权威工程语义标准。** OpenUSD 是 SDL 编译、解析和驱动的原生场景与几何表示，不取代 SDL，也不要求把全部工程语义压入 USD 自定义属性。
5. **Mesh、Voxel、SDF 等表示继续受到支持。** 它们作为 USD 场景中的表示、派生资产或类型化外部资产接受统一管理；采用 B-rep 不意味着所有几何都必须转换为 B-rep。
6. **保留薄内核调用边界。** 该边界用于版本隔离、诊断、验证和回归测试，不要求同时维护多个场景或几何后端。

## 备选方案

### 完全自研场景和几何层

不采用。研发和长期验证成本过高，并会形成与现有生态隔离的私有基础设施。

### OpenUSD 仅作为导出后端

不采用。只能获得交换能力，无法统一原生场景、精确几何、派生资产和增量更新。

### 以 OpenUSD 同时取代 SDL

不采用。通用场景图不能替代 SDL 的设计依据、连接网络、自由度、约束和证据语义。

### 维持多个等价场景与几何后端

当前不采用。项目早期不为抽象而维护多套实现；主链路统一为 OpenUSD＋USD BRep，薄适配边界仅承担工程隔离和验证职责。

## 后果

### 收益

- 避免重复建设场景合成和精确几何基础设施。
- 场景层级、实例、精确 B-rep 和派生几何可以进入统一资产体系。
- 利用 Apache 2.0 开源实现及 OpenUSD 生态。
- SDL 与几何场景之间形成明确的权威语义和原生表示分工。

### 代价与风险

- `BrepArray` 是 `omniSolid` Schema 扩展，不能假设所有 vanilla OpenUSD 工具都理解精确 B-rep。
- USD BRep 公开版本仍较早期，官方已知问题涉及几何修复、测量、三角化和格式转换；这些问题会影响 SysDA 的证据可信性。
- 上游目前不接受外部代码贡献，补丁、版本升级和长期治理需要持续评估。
- OpenUSD、Schema 插件和内核增加了二进制分发、平台兼容和版本锁定复杂度。
- SDL 身份与 USD Prim Path、Layout 与 Layer／Variant 之间需要明确且稳定的映射规范。

## 验证与复审

主链路必须建立覆盖以下能力的标准回归测试集：

- B-rep 创建、布尔、修复、测量和三角化；
- USD 导入、导出及往返转换；
- SDL 身份与 USD 场景对象的稳定映射；
- 多候选 Layout 和增量更新；
- 跨平台结果差异、确定性和失败状态；
- 几何结果验证，而不是仅检查操作是否返回成功。

锁文件与证据清单应记录 OpenUSD、Schema、内核、转换器版本以及关键容差和参数。对于可能静默产生无效结果的操作，必须执行独立几何验证。

本决策不要求因单个缺陷立即增加第二后端，但出现以下情况时应重新审议：

- 上游许可、治理或维护模式发生实质变化；
- 关键几何缺陷长期无法隔离或验证；
- 目标平台或分发形态无法得到支持；
- Schema 演化破坏资产长期可读性和迁移能力；
- 主链路无法满足关键工程任务的正确性或可复现性要求。

## 参考资料

- [NVIDIA-Omniverse/usd-brep](https://github.com/NVIDIA-Omniverse/usd-brep)
- [USD BRep 已知问题](https://github.com/NVIDIA-Omniverse/usd-brep/blob/main/KNOWN_ISSUES.md)
- [USD BRep 变更记录](https://github.com/NVIDIA-Omniverse/usd-brep/blob/main/CHANGELOG.md)
