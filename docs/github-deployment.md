# 从 GitHub 下载与部署 TexSys

本指南使用 GitHub 上同一版本的源码、示例、工程能力包与锁文件。准备 uv、Python 3.12、Node.js 22 或 24（含 npm）、Git 和 Google Chrome。首次同步需要联网获取锁定的依赖。

## 下载与安装

在 [GitHub Releases](https://github.com/IndenScale/texsys/releases) 选择所需版本，下载该版本的 **Source code (zip)** 或 **Source code (tar.gz)**。解压后进入包含 `pyproject.toml` 和 `package-lock.json` 的目录，执行：

```bash
uv sync --locked
npm ci --ignore-scripts --no-audit --no-fund
uv run --locked texsys --version
```

如需使用 Studio 的 Git 历史和分支功能，可通过 Git 检出同一标签，并在检出的目录执行相同的安装步骤：

```bash
git clone --branch v0.1.1 https://github.com/IndenScale/texsys.git
cd texsys
uv sync --locked
npm ci --ignore-scripts --no-audit --no-fund
uv run --locked pre-commit install --hook-type pre-commit --hook-type pre-push
```

## 验证标准暖通案例

```bash
uv run --locked texsys check -p examples/hvac-dynamic-station
uv run --locked texsys build -p examples/hvac-dynamic-station --target drawing-system --target drawing-notes
uv run --locked tools/verify_hvac_engineering_poc.py
```

图页位于 `examples/hvac-dynamic-station/build/drawings/sheets/`。工程实验脚本创建独立的项目副本，验证基线、减容越限、修复和缺失输入。它打印本次实验的 `summary.json` 路径。判据适用于该概念冷站与声明工况。

## 启动 Tex Studio

先检查 `127.0.0.1:8710`。如果已有 Tex Studio 在运行，复用现有服务和 Chrome 标签页。确认端口空闲后，在安装目录运行：

```bash
uv run --locked texsys studio -p examples/hvac-dynamic-station
```

在 Google Chrome 打开 [Tex Studio](http://127.0.0.1:8710/)，从文件资源管理器查看模型源文件和已生成的图页。终端按 `Ctrl+C` 停止本次启动的服务。Studio 运行在本机回环地址，交互终端支持 macOS 和 Linux。

原始图纸和客户资料统一保存到本地 `refs/`。在 Git 或工程包中使用自有或已获分发授权的模型和派生资料。 [参考资料管理](references.md)
