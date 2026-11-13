# texsys

[![GitHub Actions CI](https://github.com/IndenScale/texsys/actions/workflows/ci.yml/badge.svg?branch=codex%2Fengineering-change-poc)](https://github.com/IndenScale/texsys/actions/workflows/ci.yml) · [源码发布](https://github.com/IndenScale/texsys/releases) · [可执行示例](examples/README.md) · [工程 POC 台账](docs/engineering-poc.md)

让复杂系统像软件一样被持续开发、验证和演化。texsys 从数据中心开始，把系统模型、工程检查和交付物放进同一个可重跑的工作流：修改源文件 → 查看 Git 差异 → 构建与检查 → 审查结果和证据。

| 组成                 | 用途                                                                             |
| -------------------- | -------------------------------------------------------------------------------- |
| **Tex Studio**       | 在本地浏览器工作区编辑模型、查看 Git 差异，打开生成的图纸、物料清单和场景。      |
| **texsys CLI**       | 在终端或 CI 中运行相同的模型检查、依赖锁定、按目标构建和工程校核。               |
| **SDL 与工程能力包** | 用声明式模型描述对象、端口和连接；通过锁定的能力包引入专业规则、计算和出图能力。 |

## 快速开始

准备 Git、uv 和 Node.js 22 或 24（含 npm）。仓库使用 Python 3.12，uv 会按仓库配置选择版本。首次安装需要联网。从默认分支开始：

```bash
git clone https://github.com/IndenScale/texsys.git
cd texsys
uv sync --locked
npm ci --ignore-scripts --no-audit --no-fund
uv run --locked texsys check -p examples/minimal
```

看到 `Status: pass`，表示两泵串联示例通过了形式检查。需要固定版本时，从 [GitHub 下载与部署](docs/github-deployment.md) 选择发布标签或源码归档。

接着在同一个工作目录构建冷站原理图和总说明：

```bash
uv run --locked texsys build -p examples/hvac-dynamic-station \
  --target drawing-system --target drawing-notes
```

图页保存在 [冷站示例](examples/hvac-dynamic-station/README.md) 的 `build/drawings/sheets/`，构建报告为该项目的 `build/build.json`。在 Studio 的文件资源管理器中可以直接打开生成的 SVG。示例图页是工程草稿。

## 打开 Tex Studio

在 Google Chrome 中使用 [本地工作区](http://127.0.0.1:8710/)。已有实例时复用该服务和标签页；确认端口空闲后，才能从源码目录启动：

```bash
uv run --locked texsys studio -p examples/hvac-dynamic-station
```

如果在 macOS 上长期开发本仓库，按 [常驻开发容器](docs/development-container.md) 使用 Podman 登录服务和 Vite 热更新。开发、启动和单实例约定见 [AGENTS.md](AGENTS.md#tex-studio-开发与验证)。

## 重跑一项工程判断

运行冷站工程实验，比较基线、减容越限、修复和缺失输入四种结果：

```bash
uv run --locked tools/verify_hvac_engineering_poc.py
```

脚本在独立的项目副本中执行，并打印 `summary.json` 的路径。日常设计时，可修改项目源文件、用 `git diff` 审查输入变化，再运行：

```bash
uv run --locked texsys engineering run hvac:capacity-change -p examples/hvac-dynamic-station
```

根据退出码和项目构建目录中的证据定位问题。工程不满足、执行错误或无法判定均返回非零；`texsys build` 只运行选定目标。更多案例见 [混合冷却数据中心](examples/datacenter-hybrid-unit/README.md) 和 [交付物构建](examples/build-deliverables/README.md)。

## 开发与验证

在仓库根目录运行：

```bash
uv run --locked pytest
node node_modules/prettier/bin/prettier.cjs --check .
node node_modules/markdownlint-cli2/markdownlint-cli2.mjs "**/*.md"
```

[GitHub Actions](.github/workflows/ci.yml) 还检查公开示例、Studio 回归、TypeScript 和可达历史中的私有文件。提交钩子及文档语言规则见 [开发约定](AGENTS.md) 和 [语言检查配置](tools/doc-language.toml)。原始图纸与客户资料保留在本地 `refs/`；公开源码与工程包使用允许分发的模型和派生资料，见 [参考资料管理](docs/references.md)。

## 当前范围与文档

项目处于可执行参考实现与工程 POC 阶段。SDL 已有 JSON 交换子集和形式检查，最终文本语法仍待实现。公开案例可执行暖通出图、简化物理计算和数据中心有限的跨专业概念校核；判据只覆盖声明的输入与工况。真实工程验证和责任签核另行开展。

- **使用工具：** [项目目录](docs/project-layout.md) · [工程能力包](docs/package-management.md) · [Studio 工作台](spec/studio/01-workbench.md)
- **阅读契约：** [SDL](spec/sdl/00-overview.md) · [构建](spec/build/00-overview.md) · [工程变更与证据](spec/engineering/01-change-evidence.md)
- **了解项目：** [范围](docs/scope.md) · [进度](docs/progress.md) · [设计自动化原理](设计自动化原理/00-前言与导读.md) · [决策与开放问题](MEMO.md)
