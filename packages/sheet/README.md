# 工程图页组件包

`sheet:compose` 将已构建视图、图框设计组件和项目文字编译为独立 SVG 图页。适用于原理图、空间示意图和总说明草稿；不证明工程内容满足、施工深度、责任审核或签章效力。

## 分开维护

- 本包 `src/texsys_sheet/assets/` 中的 JSON 是可复用设计组件：`frame` 图框、`title-block` 标题栏、`notes` 说明文字、`footer` 页脚文字和 `view` 视图。
- `a3-landscape` 模板将上述组件放到 A3 横向纸面；`a3-notes` 模板用于独立总说明页。模板以 `components[].component` 引用组件，`box` 为纸面毫米坐标 `[x, y, width, height]`。组件与模板可以分别替换为项目 `source/assets` 下的 JSON 文件。
- 项目的 `assets/sheets/` 保存图页装配 JSON、标题栏元数据 JSON 和 UTF-8 纯文本说明。内容与组件几何独立，编译不会回写手写文字。

```toml
[build.targets.drawing-system]
using = "sheet:compose"
dependencies = ["system-schematic"]
scope = { source = "assets/sheets/system.sheet.json" }
output = "build/drawings/sheets/system.svg"

[dependencies]
sheet = { path = "../../packages/sheet", version = "0.1.0" }
```

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
    "main": { "path": "build/drawings/schematics/system.svg", "scale": "fit" }
  }
}
```

元数据是非空文本键值。内置标题栏使用 `project`、`title`、`drawing_number`、`revision`、`date`、`stage`、`scale`、`sheet`、`designed_by`、`checked_by`、`approved_by`。没有责任审核时显式填写“待复核”；这些文本不产生签名。日期与修订由源文件声明，编译不自动生成日期。

文字可用 `{{project}}`、`{{revision}}` 等引用元数据。显式换行和空行保留，长行自动换行；不支持 Markdown 富文本、自动分页或任意表达式求值。组件定义纸面字高和行距，排版超出区域会失败，不截断文字。没有视图的总说明页可用 `sheet:a3-notes`，省略 `views` 和上游依赖。

## 编译与检查

```bash
uv run --locked texsys lock -p examples/hvac-dynamic-station
uv run --locked texsys build -p examples/hvac-dynamic-station --target drawing-system --target drawing-notes
```

输出为自包含 SVG 和同名 `-sheet.json` 来源报告。报告记录图框组件、模板、元数据、文字及上游视图的路径／摘要、纸张尺寸、内容区域与字高。上游视图只从本次构建的依赖快照读取；缺少依赖声明时，即使公开目录已有旧图也会失败。缺少文字、未解析字段、重叠内容区域、越界、文字溢出和无效 SVG 均不能覆盖上次图页。

项目文字或项目内模板变更只影响声明这些输入的图页目标。包模板属于锁定包内容；修改包后必须更新依赖锁，当前工具链身份采用保守失效，可能使其他目标重新构建。上游视图变化会使依赖图页重新组合。

## 适用边界

纸面为毫米，视图使用 `fit`，不推导工程比例尺。含视图的 `metadata.scale` 必须为 `示意，不按比例` 或 `NTS`；纯文字页还可使用 `不适用`。视图内部已有字高会随视图缩放，独立图页文字保持声明字高。嵌入 SVG 的脚本、外部资源和动态内容不在支持范围。

文字使用固定字符进宽排版并以 SVG `textLength` 约束宽度；字体族为系统中文无衬线回退链，没有嵌入字体。字符外观和打印效果需人工核对，排版结果不证明跨主机字体一致。图框采用本包示例版式，可替换组件；不宣称符合某一制图标准。图号唯一性、跨页引用、工程尺寸／引线标注、正式电子签章及成套交付检查留待后续能力实现。

完整合同见仓库 `spec/build/05-sheet-composition.md`，两个消费案例为 `examples/hvac-dynamic-station` 和 `examples/hvac-air-cooled-room`。
