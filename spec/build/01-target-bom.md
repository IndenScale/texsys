# Target BOM 交换契约

> 状态：有效。当前 schema_version 为 `1`，通用内核为 `texsys-bom` 0.1.0，HVAC 适配包为 `texsys-hvac-bom` 0.1.0。架构依据见 ADR-0013、ADR-0014、ADR-0016。

## 通用内核与专业契约组合

`texsys-bom` 提供条目结构、scope、投影、聚合、外部快照、身份映射、维护历史、受控比较和通用检查。它只依赖 texsys 核心，不解释专业型号或参数，也不导入任何 HVAC 包。

专业包通过不可变 `BomContract` 组合以下规则，而不是继承或覆盖通用推导器：

- `required_engineering_specifications`：工程 profile 额外必需的专业参数；
- `validate_external_item`：外部项的专业分类和型号校验；
- `decorate_group`：专业聚合指标；
- `view_name` 和 `backend`：视图及证据所有者标识。

通用规则与专业规则只叠加。字段、规则、视图和诊断必须有明确所有者，不允许通过继承顺序、注册顺序或后注册覆盖解决冲突。当前契约只覆盖首个 HVAC 切片所需组合点，不构成跨专业 `interface_bindings` 的实现。

## HVAC 权威设备输入

`hvac:equipment-bom` 从唯一的 `src/hvac.json` 设备台账投影，不读取或生成第二份权威设备清单。schema v1 保持原有概念型号输入；schema v2 使用：

```json
{
  "schema_version": 2,
  "equipment": {
    "chiller-40-group": {
      "family": "chiller",
      "quantity": 2,
      "unit": "ea",
      "selection": { "status": "unselected" },
      "specifications": {
        "capacity_kw": {
          "value": 40,
          "status": "assumed",
          "basis": "planning-load-split"
        }
      }
    }
  }
}
```

v2 设备身份必须与 SDL 根实例一致。`quantity` 为正有限数，`unit` 为非空字符串。选型状态为 `unselected`、`candidate` 或 `approved`；后两者必须引用设备族包中的概念型号。参数状态为 `known`、`assumed` 或 `pending`，三者都必须给出依据；pending 不得携带 value，其他状态必须携带有限数值。可选 `space` 保存字符串形式的空间归属。

## Profile

- `feasibility`：允许设备组和未选型对象，但必须有功能规格及状态；开放项必须显式保留。
- `engineering`：要求实例级 quantity 1，以及确定的容量和热排放参数；未批准选型可以作为开放项，但不构成采购证据。
- `as-purchased`：消费采购快照，使用 `procurement_item_id`。
- `as-built`：消费竣工快照，使用 `physical_asset_id` 并要求安装位置。
- `as-maintained`：消费维护快照，保留当前资产、退役资产和替换历史；配置变化的替换必须具有批准的设计变更记录。

这些 profile 不构成线性成熟度。任何 profile 的通过只适用于本次 target 和 scope。

## 外部事实快照

三类外部 profile 必须在 scope 中显式给出 `snapshot`，路径位于 `assets/`：

```json
{
  "schema_version": 1,
  "profile": "as-purchased",
  "source": {
    "system": "procurement-system",
    "record_version": "po-record-42",
    "effective_at": "2026-11-05T00:00:00Z"
  },
  "items": {
    "po-40": {
      "family": "chiller",
      "quantity": 2,
      "unit": "ea",
      "selection": { "status": "approved", "model": "generic-100kw" },
      "specifications": {
        "capacity_kw": { "value": 40, "status": "known", "basis": "purchase-order-42" }
      }
    }
  },
  "mappings": [
    {
      "design_instance_id": "chiller-40-group",
      "external_item_id": "po-40",
      "relation": "implemented-by",
      "status": "confirmed",
      "basis": "purchase-order-42"
    }
  ]
}
```

外部项的实际配置必须为 known，选型必须 approved。映射引用未知对象、重复映射和非法替换是确定性违例；缺少映射、映射 pending 或必需资料不足为 indeterminate。工具检查结构与一致性，不证明订单真实、现场验收或记录获得法律授权。

多对多映射按外部项 `entry_id` 去重计数。没有显式分配契约时，不把设计数量直接相加，也不把成套采购数量摊派到设计角色。维护快照用 `status = "retired"`、`replaces` 和 `effective_at` 保留历史，不能覆盖旧资产身份。

## Target BOM 与视图

`build/hvac/bom.json` 包含 target、profile、scope、source_snapshots、entries、mappings、differences、history、open_items、views 和 evidence。每行具有 `item_basis`，记录来源快照、对象摘要、参数状态、选型状态、记录版本、适用边界及未解决项。

默认 `by-family-spec` 视图按设备族、选型、完整规格和单位聚合，不静默合并不同配置。当前只消费字段名约定的规范单位，不执行隐式单位换算。`bom.md` 是同一数据的派生视图。

## 受控比较

`bom:diff` 通过 scope 中的 `baseline` 和 `candidate` 读取两份固定 Target BOM，生成 `build/bom/bom-diff.json`。`hvac:equipment-bom-diff` 保留兼容入口并写入 `build/hvac/bom-diff.json`，两者共用通用比较内核。差异类型为新增、移除、替代、数量变化、配置变化、位置变化和映射变化。

比较成功只表示差异已经确定生成，不表示没有差异，也不自动判错、批准或回写。多对多对象没有分配契约时只比较映射，不虚构数量分配。
