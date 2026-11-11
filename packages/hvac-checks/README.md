# 暖通设计检查能力包

本包提供容量余量、通风温升、露点余量及冻结资料容量审查，作为可跨项目引用的计算与检查实现。项目设备清单、环境参数、假设、工况和接受记录由消费项目保存。

## 能力与适用范围

- `hvac:design-checks` 从项目 `src/hvac.json` 读取配置，分别输出容量、温升与露点判据。
- `hvac:reference-capacity` 从项目研究和冻结来源快照读取声明配置，进行条件容量算术；缺少真实性能证据时保持未定，不形成真实工程接受。
- 本包不是厂家选型库，不包含现场工况、客户原件或校准数据。

## 发布与验证

`texsys.toml` 声明公共能力、精确依赖、Python 入口及运行要求。`tests/` 随发布物一起分发，包含容量正反例、通风热平衡和饱和空气露点参考。包测试通过不等于消费项目的设计通过。

从仓库根目录发布到受控目录：

```bash
uv run --locked texsys package publish packages/hvac-checks --registry build/package-registry
```

消费项目采用明确版本，通过 `texsys add`、`texsys sync` 还原包。包内测试可通过 `texsys package test hvac-checks -p 项目目录` 执行；测试工具 pytest 需要在执行环境可用。
