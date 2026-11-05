# SDL JSON 交换子集

> 状态：草案。本文定义当前可执行的最小交换表示，不定义最终面向人的 SDL 文本语法。决策边界见 [ADR-0007](../../docs/adr/0007-formal-checking-and-formatting.md)。

## 文件与文档

输入文件位于项目源目录，扩展名为 `.sdl.json`。每个文件是独立的 UTF-8 JSON 文档，包含：

| 字段             | 类型             | 含义                        |
| ---------------- | ---------------- | --------------------------- |
| `schema_version` | 整数，当前为 `1` | 交换模式版本                |
| `root`           | 字符串           | 顶层 Component 名称         |
| `components`     | 对象             | 以名称索引的 Component 定义 |

标识符以 ASCII 字母开头，后续可以包含 ASCII 字母、数字、下划线和连字符。JSON 对象顺序不承载语义，重复键和非标准数值无效。

## Component 与 Port

Component 可以包含 `ports`、`instances`、`connections` 和 `behavior`，均可省略。没有内部实例的是叶子定义，包含内部实例的是复合定义。

Port 声明：

| 字段              | 类型                 | 含义                 |
| ----------------- | -------------------- | -------------------- |
| `kind`            | 非空字符串           | 介质或交互类型       |
| `direction`       | `in`／`out`／`inout` | 交互方向             |
| `max_connections` | 正整数，可选         | 连接数上限，默认 `1` |

## Instance 与 Connection

Instance 是 `instances` 对象中的成员，必须通过 `component` 引用已声明的 Component：

```json
{
  "instances": {
    "pump-01": {
      "component": "Pump"
    }
  }
}
```

Connection 是 `connections` 数组中的对象，只包含 `from` 和 `to`。端点使用 `instance.port` 形式，引用当前 Component 内部实例的 Port：

```json
{
  "from": "pump-01.outlet",
  "to": "pump-02.inlet"
}
```

连接必须满足：

- 两端 Instance 和 Port 均存在；
- 两端 `kind` 相同；
- 方向为 `out → in`，或至少一端为允许双向交互的 `inout`；
- 同一 Port 的连接数不得超过 `max_connections`；
- 不允许把一个 Port 连接到自身。

当前不支持跨文件 Component 引用、显式 Net／Net 匿名推导、通用参数表达式、Placement 和约束求解；缺失这些能力不得被解释为对应语义无效。下节定义的是有限标量行为参数子集。

## 行为定义与参数绑定

Component 可声明 `behavior`，严格包含 `reference`、`state`、`source`、`parameters`。`reference` 和 `source` 均为非空字符串，`state` 使用下表六态；`parameters` 为参数名到绑定对象的映射。`reference` 的基础静态校验只检查字符串，具体 target 校验可解析性；原理图 target 要求指向项目 source 或 assets 内的固定本地文件，不支持 URI 或片段猜测。

```json
{
  "behavior": {
    "reference": "assets/Chiller.mo",
    "state": "assumed",
    "source": "概念稳态模型，尚未按厂商曲线验证",
    "parameters": {
      "cooling_kw": {
        "value": 100,
        "unit": "kW",
        "state": "assumed",
        "source": "概念设计假设 A"
      }
    }
  }
}
```

| 状态             | 含义   | value                      |
| ---------------- | ------ | -------------------------- |
| `known`          | 已知   | 必须为有限数值，布尔值无效 |
| `assumed`        | 假设   | 同上，并保留 source        |
| `unknown`        | 待定   | 不得出现                   |
| `missing`        | 缺失   | 不得出现                   |
| `not-applicable` | 不适用 | 不得出现                   |
| `failed`         | 失败   | 不得出现                   |

参数绑定只允许 `value`、`unit`、`state`、`source`；单位和来源为必需非空字符串，无量纲量使用 `1`。首版不做单位换算、量纲推导、区间预算或参数表达式求值。行为整体为 not-applicable 不满足完整原理图的行为承诺，不能由此跳过行为检查。

Instance 可附带 `parameters`，结构相同，仅能覆盖所引用 Component 的行为参数中已有的同名绑定。缺省使用 Component 绑定；覆盖采用完整绑定替换，不继承遗漏的单位、状态或来源。未声明参数产生 `L009`。这只绑定行为定义，不把图纸或参考文件中的默认值当成实例事实。

图纸中的行为引用和参数标注不代表求解通过。如何降级、验证文件摘要并生成系统图／原理图，见 [出图构建契约](../build/03-system-schematic.md)。

## 诊断与格式化

`texsys lint` 检查项目布局、JSON 语法、上述模式和静态语义。诊断规则编号以 `P`、`S`、`L`、`E` 分别表示项目、语法／模式、语义和执行问题。

`texsys format` 使用两个空格缩进、键排序和末尾换行。它只改变 JSON 呈现，不改变值。`fmt` 是等价别名，`--check` 和 `--diff` 不写文件。
