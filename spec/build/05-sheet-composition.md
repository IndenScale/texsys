# 图页组件、内容与编译组合

> 状态：首个可执行子集。架构决策见 [ADR-0030](../../docs/adr/0030-independent-sheet-components-and-content.md)。图页编排消费专业视图和项目文字，构建通过表示组合完成，不表示工程设计已通过责任审查。

## 分别维护的源

| 层次       | 内容                                               | 维护位置                                  |
| ---------- | -------------------------------------------------- | ----------------------------------------- |
| 设计组件   | 图框、标题栏、纸幅、线宽、文字样式、视图与文字区域 | `sheet` 包模板，或项目显式引用的模板 JSON |
| 项目元数据 | 项目、图名、图号、修订、阶段与标题栏文字           | 项目 `assets/sheets/*.json`               |
| 工程文字   | 总说明、适用条件、显式编号条款与页脚               | 项目 UTF-8 `.txt` 文件                    |
| 图页装配   | 模板、元数据、文字与 SVG 视图的引用                | 项目 `*.sheet.json`                       |
| 派生图页   | 自包含 SVG 与来源报告                              | 项目 `build/drawings/sheets/`             |

原理图的连接、设备与行为仍由其专业生成器维护；平面的定位、尺寸与轴网仍来自空间源。图页模板不复制这些事实，文字维护也不要求修改专业生成器。生成文件的手工修改不反向写回装配源。

## 构建入口

项目显式依赖 `sheet` 能力包，并配置一个或多个命名目标：

```toml
[dependencies]
sheet = { path = "../../packages/sheet", version = "0.1.0" }

[build.targets.drawing-system]
using = "sheet:compose"
scope = { source = "assets/sheets/system.sheet.json" }
dependencies = ["system-schematic"]
output = "build/drawings/sheets/system.svg"
```

`dependencies` 引用包注册目标或项目命名目标。此例中的 `system-schematic` 是已有原理图目标；其 SVG 通过本次构建依赖传给组合目标。只含总说明、不含视图的图页不需要该依赖。每个命名目标拥有自己的输出路径和缓存身份，默认构建可直接包含这些目标。

```bash
uv run --locked texsys build -p examples/hvac-dynamic-station --target drawing-system
uv run --locked texsys build -p examples/hvac-dynamic-station --target drawing-notes
uv run --locked texsys build -p examples/hvac-air-cooled-room --target drawing-room
```

包源与项目锁定按现有依赖管理处理。修改依赖声明后先同步项目锁；仅修改已声明文件的内容无须更改依赖声明。

## 图页装配

```json
{
  "schema_version": 1,
  "template": "sheet:a3-landscape",
  "metadata": "assets/sheets/title.json",
  "texts": {
    "notes": "assets/sheets/general-notes.txt",
    "footer": "assets/sheets/footer.txt"
  },
  "views": {
    "main": {
      "path": "build/drawings/schematics/system.svg",
      "scale": "fit"
    }
  }
}
```

第一版内置 `sheet:a3-landscape` 与 `sheet:a3-notes` 两个模板，分别提供视图与说明组合页、总说明页。总说明页没有 `main` 视图，`views` 可以为空对象。`template` 也可指向项目模板文件；组件由引用选用，不自动改写到项目内容中。

`metadata` 与 `texts` 引用项目源文件。`views` 引用声明依赖所产出的 SVG 文件，路径、可用视图名称与模板必须匹配。缺少文件、未知组件、未知位置或错误类型属于输入错误，不能以空白占位代替。

## 设计模板与独立组件

内置模板组合 `sheet:frame`、`sheet:title-block`、`sheet:notes`、`sheet:footer` 和 `sheet:view`。模板决定组件位置，组件文件决定自己的线条与文字样式；项目内容仍由装配文件绑定。例如，项目可维护一个自己的纯说明模板：

```json
{
  "schema_version": 1,
  "paper_mm": [420, 297],
  "components": [
    { "id": "frame", "component": "sheet:frame", "box": [10, 10, 400, 277] },
    {
      "id": "notes",
      "component": "assets/sheets/components/notes.json",
      "box": [15, 15, 390, 225],
      "binding": "notes"
    },
    { "id": "footer", "component": "sheet:footer", "box": [15, 244, 390, 15], "binding": "footer" }
  ]
}
```

`box` 是纸面毫米 `[x, y, width, height]`。组件身份必须唯一；除图框外的区域不能互相重叠或超出纸幅。模板中的所有 `binding` 必须与装配的 `texts`、`views` 精确对应。此示例没有标题栏；需要正式图号区时复用或独立定义 `title-block` 组件。

项目 `assets/sheets/components/notes.json` 可以只维护文字设计：

```json
{
  "schema_version": 1,
  "kind": "text",
  "font_size_mm": 3.5,
  "line_height_mm": 5,
  "padding_mm": 2
}
```

组件类型为 `frame`、`title-block`、`text` 或 `view`。图框通过 `line_width_mm` 设置线宽；标题栏通过 `cells` 设置单元格的相对 `box`、元数据 `field` 和文字 `label`；文字组件设置字高、行高与内边距；视图组件设置内边距。行高至少为字高的 1.15 倍，区域必须留有正的内容空间。内容和组件类型不匹配时失败。

项目模板和组件路径统一相对于项目根目录，并位于声明的 source 或 assets 内，不相对于引用它的模板目录。第一版拒绝符号链接源和目录逃逸。包内模板实现及组件文件见 [sheet 能力包](../../packages/sheet/README.md)。

## 标题栏元数据

元数据为平坦对象，字段值均为非空文本。内置模板使用以下字段：

```json
{
  "project": "冷站概念工程示例",
  "title": "冷站系统原理图",
  "drawing_number": "HVAC-S-001",
  "revision": "P01",
  "date": "2026-11-13",
  "stage": "方案草稿",
  "scale": "示意，不按比例",
  "sheet": "1 / 2",
  "designed_by": "示例设计",
  "checked_by": "待复核",
  "approved_by": "待复核"
}
```

含视图图页的 `scale` 只接受 `NTS` 或 `示意，不按比例`；纯文字图页还接受 `不适用`。

每张图页可使用不同元数据文件，并共享同一段总说明、页脚或模板。修订、日期和阶段由项目显式给定；构建不会自动写入当前日期，避免无设计变化也改变产物。字段名称与模板绑定相对应，模板需要的绑定缺失时构建失败。

这些字段是图面文字，不证明填写者身份，也不构成电子签名或印章。实际责任审查和签署沿用既有工程流程。

## 总说明与文字

说明使用 UTF-8 纯文本，空行分段、条款编号由作者显式维护。支持 `{{field}}` 绑定当前图页元数据，例如：

```text
{{project}} · 总说明

1. 本套文件为{{stage}}，修订 {{revision}}。

2. 设备与连接以声明式工程源为准，图面距离不代表安装尺寸。
```

绑定值只作为文字插入，不执行代码或解释为 XML。未知字段报错；不隐式读取环境变量，也不把任意源文件当成绑定内容。专业说明与项目条件由工程师维护，不由模板生成器补写。

模板负责文字区域和排版，纸面文字以毫米定义字高。段落按区域换行，超出区域不得静默截断；过长文本应由作者缩写、调整模板或拆分为另一张图页。第一版不解析 Markdown 标题、列表语法或 HTML，不自动分页，不提供图元引线注记。

## 视图、比例与图面完整性

内嵌视图必须是 SVG。第一版 `scale` 使用 `fit`，在模板分配的区域内保持比例适配。其含义是图形显示适配，不是工程比例尺。原理图和已经包含标题、图例或排版偏移的平面必须明确“示意，不按比例”；不得根据 viewBox 自动推断 `1:N`。

外层图页使用明确纸幅和毫米坐标。图框与文字字号不随内嵌视图的 fit 缩放改变。内嵌视图保留自身内容和语义，组合层不会重新生成其中的设备、尺寸或图例。输出需要完整包含所引用的视图内容，不依赖读者的项目目录才能显示。

## 产物、来源与失败

主产物为命名目标声明的 `.svg` 文件。同目录生成 `<文件主名>-sheet.json` 来源报告，例如 `system.svg` 对应 `system-sheet.json`。报告关联装配源、模板、元数据、文字与视图，记录其摘要和图页信息，便于复核此次组合的依据。

构建记录继续区分目标执行、输入适用性和产物发布。依赖失败、缺失引用、无效 SVG、未知绑定、无效纸面配置及文字溢出均应使组合失败；不能把旧公开视图的存在当作当前依赖已通过。失败时按共享构建发布规则保留历史文件及当前诊断，历史文件不因此获得当前有效性。

说明、元数据或模板改变后，需要重建对应图页；原始 SDL 和求解输入不因此改变。专业模型或视图改变后，依赖链先重建相应视图，再重新组合图页。缓存命中与否由输入摘要、目标配置、依赖产物和工具链共同决定，不能仅按文件名判定。项目内的说明、元数据与模板被图页目标单独追踪；修改共享能力包组件属于依赖内容变化，需更新锁定，现有全局工具链摘要会保守使其他目标缓存失效，尚不承诺包级模板修改只重组图页。

## 使用与当前边界

在 Studio 中编辑项目 JSON 和 `.txt` 源文件，通过现有终端或构建入口执行，再打开生成的 SVG。各图页是独立普通文件，使用共同的图纸缩放和平移能力，不新增专用图页管理界面。

两个可重跑示例为 [冷站](../../examples/hvac-dynamic-station/README.md)和[风冷机房](../../examples/hvac-air-cooled-room/README.md)：前者提供原理图页与总说明页，后者使用同一图框和自己的项目文字。它们验证编译链及概念内容复用，不构成正式施工图交付。

第一版尚未覆盖锁定字形和跨主机打印一致性、工程比例尺、签章、自动分页、复杂表格或正式交付格式。系统字体替换及内嵌旧视图中的文字仍需视觉复核。新增制图或交付能力应声明独立验证范围，不能从图页组合成功推断完整工程文件满足要求。
