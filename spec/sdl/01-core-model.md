# SDL 核心模型

> 状态：草案。本文记录当前已经对齐的核心概念，不确定具体文本语法。

## 核心概念

SDL 以六个概念构成组成、连接和空间语义的核心：

1. **Component**
2. **Instance**
3. **Port**
4. **Netlist**
5. **Layout**
6. **Placement**

Connection、Net 和 Frame 是支撑这些核心概念的语义对象。

## Component

Component 是可复用的类型定义或设计资产，可以声明参数、端口、行为、几何、内部实例、Netlist、候选 Layout、约束和设计依据。

Component 采用递归组合模型：

- 没有内部实例的 Component 是叶子部件；
- 包含内部实例、Netlist 或 Layout 的 Component 是复合部件；
- 顶层设计同样是 Component，其实例化结果是整个设计的根实例；
- “系统”和“装配”只是 Component 在特定层级与语境中的角色，不作为独立核心概念；
- 叶子与复合是结构状态，不要求使用不同的声明语法。

每个 Component 都可以定义自己的外部端口契约和内部实现。

## Instance

Instance 是 Component 在具体上下文中的实例化结果，具有稳定身份、参数绑定和所属层级。实例化是产生 Instance 的操作，不作为另一个独立核心对象。

实例可以先存在，之后再连接或放置。已实例化但未连接、已实例化但未放置、已连接但未完成布局，都是合法的设计中间状态。

Component 与 Instance 必须严格区分。修改共享 Component 定义不允许的实例内部结构时，应创建参数化变体或新的 Component 定义，而不是静默修改类型。

## Port

Port 是 Component 与外界交互的类型化边界，可以声明交互类型、方向、单位、容量、协议、兼容条件、连接数量限制和空间参考信息。

Port 在 Component 上定义，并随 Component Instance 自动派生。SDL 不设置独立的 PortInstance 生命周期，也不要求用户显式实例化、创建或删除实例端口。

不同实例上的同名端口通过实例路径和端口路径共同定位，例如 `pump-01.inlet`。该路径直接引用 Port，不引入独立的 Endpoint 对象或端口副本。

## Netlist、Connection 与 Net

Netlist 描述 Component 内部的逻辑连接拓扑，不描述空间位置。

### Connection

Connection 是两个 Port 之间的连接边，可以具有稳定身份、连接类型、方向、容量、参数、所属 Net、设计依据和验证状态。

### Net

Net 是由一组 Port 和 Connection 构成的类型化网络，表示连接关系的整体功能与语义。Net 可以拥有稳定身份、网络类型、网络级参数、目标、约束、关联评估器以及空间路由和交付物引用。

Net 可以表示电源、传热、水循环、控制信号、巡检动线、疏散动线等不同网络。核心模型不预设所有 Net 都具有相同方向、连通或状态语义，具体规则由网络类型及其评估器定义。

Connection 决定局部连接事实，Net 保存网络身份和整体语义。Net 的显式声明、匿名推导及成员一致性规则尚未定义，登记为 [OQ-3](../../docs/open-questions.md)。

在复合 Component 内，Netlist 可以连接内部实例端口和 Component 自身端口。Netlist 与 Layout 必须引用同一组内部实例，不得各自创建部件副本。

## Layout

Layout 描述一个 Component 的内部实例在共同空间上下文中的组织方式。一个 Component 可以拥有多个候选 Layout，以支持方案比较而无需复制系统模型。

Layout 可以包含根坐标系及可选地理配准、Placement、空间边界、定位约束、验证约束、安装与维护区域、禁入区域和路由几何引用。

Netlist 管逻辑连接，Layout 管空间组织。

## Placement 与 Frame

Placement 是某个 Component Instance 在某个 Layout 中的空间绑定，至少关联：

- 被放置的实例；
- 所属 Layout；
- 实例局部 Frame 与参考 Frame；
- 位姿表达或待求解状态；
- 自由度与定位状态。

Frame 是解释位姿所需的坐标参考系。Placement 的核心语义是实例局部 Frame 相对于另一 Frame 的变换，而不是孤立的一组坐标。

地理坐标、全局坐标和相对坐标不是不同的 Placement 类型，而是参考 Frame 不同。实例通常使用 Layout 的局部工程坐标系，地理坐标通过 Layout 的地理配准变换获得。

Placement 支持：

1. **未放置**：位置未知，不能与坐标为零混淆；
2. **固定位姿**：位姿已经确定，自动布局器不得修改；
3. **部分定位**：部分自由度固定，其余自由度开放；
4. **约束求解**：通过几何约束确定位姿，可以附带非强制性的初始位姿。

直接相对位姿引用必须可求值且无环；几何约束图可以包含闭环，由求解器处理。Component 的组成层级可以是树，但空间关系不受树状结构限制。

空间约束分为：

- **定位约束**：用于减少自由度，例如重合、同轴、平行、距离和角度；
- **验证约束**：用于判断布局是否成立，例如最小净距、禁入区域和维护空间，不应被求解器擅自转化为唯一位置。

欠约束表示仍有开放自由度，不一定错误；是否允许取决于设计阶段与交付要求。过约束需要区分一致冗余、冲突过约束和数值病态。编译期负责引用、单位、坐标系和直接位姿环等静态检查，布局求解与评价阶段负责自由度、数值残差、冲突和解的稳定性检查。

## 所有权与不变量

- Component 拥有内部实例声明、Netlist 和候选 Layout；
- Instance 引用 Component 定义，不是 Component 定义的副本；
- Port 属于 Component 定义，其实例端口随 Instance 自动派生；
- Connection 和 Net 引用 Port，不保存端口副本；
- Connection 表示两个 Port 之间的边，Net 表示具有整体语义的网络；
- Layout 放置当前 Component 的内部实例；
- Placement 引用 Instance，而不是直接引用 Component 类型；
- 同一 Layout 中的实例默认至多有一个有效 Placement；
- 同一实例可以在不同候选 Layout 中拥有不同 Placement；
- 根实例没有父实例，也不被放置到自身 Layout 中。
