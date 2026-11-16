# macOS 常驻开发容器

Tex Studio 在 Podman 的 Linux 虚拟机内运行。macOS 登录当前用户后，两个 `launchd` 服务分别启动 Podman 虚拟机和 Studio 容器。系统会在容器退出后重启它，并定期检查 Podman 虚拟机是否停止。退出终端、关闭 Codex 或结束代理会话都不会卸载服务。首次系统登录前不启动这套用户服务。

## 安装和日常使用

安装需要 macOS 的 Python 3、Podman CLI，以及已初始化的 `podman-machine-default`。本机使用现有的 Podman 虚拟机；安装过程不会重建它或修改其他容器。从仓库根目录执行：

```bash
/usr/bin/python3 tools/dev_container.py install
/usr/bin/python3 tools/dev_container.py status
/usr/bin/python3 tools/dev_container.py shell
```

首次启动需要下载镜像并从锁文件安装依赖，`install` 返回后可查容器日志确认 Vite 就绪。复用已有 Google Chrome 标签页访问 [Tex Studio](http://127.0.0.1:8710/)。宿主只在 IPv4 回环地址发布端口；Python API 仍监听容器内回环地址，由 Vite 转发。

容器以 `--empty-workspace` 启动：默认页面是空白工作区欢迎页，不自动打开工程。服务绑定 `examples/datacenter-reference-design`，在欢迎页选择「打开已启动的项目」后才加载该工程。绑定项目由 `tools/dev-container/entrypoint.sh` 的 `-p` 参数声明，修改后运行 `rebuild`。

容器内的 `/workspace/texsys` 是同一份宿主工作树，包含 Git、设计源文件及构建结果；宿主修改前端文件后由 Vite 检测。Python 环境、Node 依赖和容器用户主目录分别保存在 Podman 卷中，容器重建会保留这些卷。本机 Git 全局配置、凭据和 Studio 服务端设置不会自动导入；如需在容器提交，可在容器中配置 Git 作者信息。进入 `shell` 后，可直接执行 `git diff`、`uv run --locked texsys build -p examples/hvac-dynamic-station` 或测试。

本机只读包源 `127.0.0.1:8711` 由独立的 `texsys-package-manager` 登录服务管理。已有容器需要运行 `rebuild` 才能移除旧端口映射，迁移和发布方法见 [工程能力包](package-management.md#只读-http-包源)及 [ADR-0032](adr/0032-independent-local-tpm-registry.md)。

- 前端源码修改通常由 Vite 热更新。修改 Python 服务、插件入口或锁文件后运行 `/usr/bin/python3 tools/dev_container.py restart`，启动时同步依赖。
- 修改镜像或容器启动脚本后运行 `/usr/bin/python3 tools/dev_container.py rebuild`。脚本先构建镜像，再替换本工作树的容器。
- 用 `podman logs --tail 100 texsys-studio-dev` 查看依赖同步及 Studio 输出。启动与恢复日志保存在 `~/Library/Logs/TexSys/`。

## 暂停与恢复

只暂停 Studio 时，卸载它的登录服务，Podman 和其它容器不受影响：

```bash
launchctl bootout "gui/$(id -u)/local.texsys.studio"
```

等待服务退出后，运行 `install` 可立即恢复；下次登录也会自动恢复。如果要跨登录保持暂停，先运行 `launchctl disable "gui/$(id -u)/local.texsys.studio"`，再卸载。`install` 会重新启用。

需要手动停止 Podman 虚拟机时，先暂停 Studio 并卸载 `gui/$(id -u)/local.texsys.podman-machine`，否则系统会再次启动虚拟机。确认其中其他工作负载可以停机后，再运行 `podman machine stop podman-machine-default`。Podman 登录服务同样可以用 `launchctl disable` 跨登录暂停，并用 `install` 恢复。

若 `8710` 已被占用或存在旧锁，服务会在日志中说明原因。不要另开端口或删除来源不明的锁。先确认旧实例归属和实际进程状态，再完成交接。

## 只读 USD 检查

从资源管理器打开 USD 文件并选择「预览」。在构件面板搜索 Prim 或按已声明的专业、楼层、分区、材料、构件类型与 USD 集合筛选。点击或悬停查看构件，属性中提供当前部件、所属装配、包络标高、USD 属性和可用的源码定位。选中后可聚焦、隐藏、半透明或隔离。在筛选栏使用「隐藏筛选结果」可以移走某种材料并立即查看余下装配。

拖动鼠标环绕，右键拖动平移，滚轮缩放。飞行模式使用 WASD 移动、Q/E 下降或上升及右键拖动转向。视口获得焦点后按 F 聚焦选中项，按 Home 适应当前可见范围。可切换俯视、正视、侧视和等轴测，沿 X/Y/Z 滑动剖切面，并在可见表面选两点量取三维直线距离。包络几何的测量对应显示的包络尺寸。视图操作只改变预览状态。

OpenUSD 负责解析组合层、世界变换、集合与材质绑定。Three.js 驱动浏览器视口。工程导出器可在 Prim 或其祖先写入字符串属性 `texsys:discipline`、`texsys:level`、`texsys:zone`、`texsys:material` 和 `texsys:role`。当部件各有独立标识时，可在装配根写入 `texsys:assembly`。自定义标量属性可通过属性的 `unit` 扩展数据提供单位。普通 USD 的 Prim 树、组合材质和集合同样可用。材料隔离依赖可独立选择的几何部件。
