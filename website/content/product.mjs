export const productPages = [
  {
    path: "/texsys/",
    section: "TexSys",
    title: "把方法，放进一次真实操作。",
    english: "TexSys · Engineering development tools",
    summary:
      "TexSys 是当前检验 Movement 方法的实践工具链：用共享模型连接工作区、命令行与领域能力。",
    body: `<h2 id="relationship">从 Movement 到 TexSys</h2>
<p>Movement 提供关于复杂系统开发的方法、术语与公开讨论。TexSys 是当前用来检验这些想法的一套工具链，首先从数据中心与暖通案例开始。方法的价值仍需由实践检验，其他工具和团队也可以据此探索自己的实现。</p>
<p>你可以先阅读<a href="/ideas/feedback/">实现信息如何更新设计模型</a>，再查看<a href="/lab/pipe-change/">管路变更实验</a>，最后通过<a href="/docs/quick-start/">快速开始</a>在本地重跑同一类操作。</p>
<h2 id="tools">三个入口，共享工程对象与操作</h2>
<table><thead><tr><th>组成</th><th>用于什么</th></tr></thead><tbody>
<tr><td>Tex Studio</td><td>本地网页工作区。查看和编辑项目，运行专业操作，理解诊断与证据。</td></tr>
<tr><td><code>texsys</code> CLI</td><td>可脚本化工具链。检查项目、管理依赖，执行构建、工程检查与重跑。</td></tr>
<tr><td>领域包</td><td>提供专业对象、接口、模型、规则和工具适配；共享核心组织修订、执行与证据。</td></tr>
</tbody></table>
<h2 id="try">现在可以尝试什么</h2>
<p>设施水案例从普通源文件编辑和 Git diff 开始，提取几何信息，运行水力与供冷检查，再比较结果。重跑脚本在独立项目副本中保留基线、失败和修复后的运行。</p>
<p>这条路径让一次变更可以被定位、解释和复算。输入仍包含概念假设，判定范围限于案例声明的工况与检查；真实设备资料、完整跨专业依赖、工程语义合并和第二项目独立复用仍需进一步验证。</p>
<h2 id="start">从源码预览开始</h2>
<p>当前文档面向已经取得源代码副本的使用者。先完成一次 CLI 概念实验，再按需要进入 Tex Studio。使用过程中保留输入、运行记录和失败信息，便于判断工具行为以及模型的适用范围。</p>
<p><a class="button" href="/docs/quick-start/">重跑第一个实验</a> <a href="/docs/">浏览 TexSys 文档</a></p>`,
    related: [
      { href: "/docs/quick-start/", label: "开始：运行概念实验" },
      { href: "/system/compiler/", label: "方法：工程编译器" },
      { href: "/lab/pipe-change/", label: "证据：管路变更实验" },
    ],
  },
  {
    path: "/docs/",
    section: "Docs",
    title: "TexSys 文档",
    english: "Run. Inspect. Understand.",
    summary: "从独立副本里的第一次运行开始，了解工程变更流程，以及怎样判断结果和证据。",
    body: `<h2 id="start-here">第一次使用</h2>
<p>这些文档介绍当前 TexSys 源码预览中可以执行的操作。所有命令从源代码仓库根目录运行；示例使用概念数据，其结果与正式工程接受分别判断。</p>
<ol><li><a href="/docs/quick-start/"><strong>快速开始</strong></a>：准备环境，运行完整概念实验，找到输出与运行记录。</li><li><a href="/docs/engineering-workflow/"><strong>工程变更流程</strong></a>：在实验副本中编辑源文件、审查 Git 差异、重算和修复。</li><li><a href="/docs/evidence/"><strong>读懂结果与证据</strong></a>：区分执行状态、工程判定和证据适用性，理解失败后的下一步。</li></ol>
<h2 id="reading-path">从概念找到操作</h2>
<table><thead><tr><th>想理解的问题</th><th>阅读与操作入口</th></tr></thead><tbody>
<tr><td>一次实现变化为什么会影响已有结论？</td><td><a href="/ideas/feedback/">设计模型反馈</a> → <a href="/docs/engineering-workflow/">变更流程</a></td></tr>
<tr><td>工具完成了计算，方案为什么仍然失败？</td><td><a href="/ideas/engineering-evidence/">工程证据</a> → <a href="/docs/evidence/">结果字段与判断</a></td></tr>
<tr><td>Tex Studio、CLI 和领域包分别做什么？</td><td><a href="/texsys/">认识 TexSys</a> → <a href="/docs/quick-start/#studio">本地工作区</a></td></tr>
</tbody></table>
<h2 id="source-reference">需要更完整的契约时</h2>
<p>源码中的 <code>README.md</code> 提供整体入口，<code>spec/engineering/01-change-evidence.md</code> 定义当前工程变更子集，<code>docs/engineering-poc.md</code> 记录逐项验证与未完成范围。以这些随源码版本维护的文件核对具体行为。</p>
<p>文档中的命令使用 <code>texsys</code>；产品名为 TexSys，本地网页工作区名为 Tex Studio。Movement 的思想与术语页面讨论更广的方法，当前工具的完成状态以具体实现和实验依据为准。</p>`,
    related: [
      { href: "/docs/quick-start/", label: "下一步：快速开始" },
      { href: "/texsys/", label: "了解工具：TexSys" },
      { href: "/lexicon/", label: "查阅概念：Lexicon" },
    ],
  },
  {
    path: "/docs/quick-start/",
    section: "Docs",
    title: "重跑第一个工程实验",
    english: "Quick start",
    summary: "在独立项目副本中运行基线、变更、失败与修复，定位本次实验的输入和证据。",
    body: `<h2 id="prepare">准备源代码环境</h2>
<p>以下步骤面向已取得 TexSys 源代码的使用者。需要可用的 uv 与 Python 3.11 或更新版本。从仓库根目录同步锁定依赖，再确认命令行入口：</p>
<pre><code class="language-bash">uv sync --locked
uv run --locked texsys --version</code></pre>
<p>如果同步或版本命令报错，先处理输出中指出的环境问题。首次同步可能需要下载依赖；当前文档使用仓库内的参考实现。</p>
<h2 id="run">运行完整概念实验</h2>
<pre><code class="language-bash">uv run --locked tools/verify_engineering_poc.py</code></pre>
<p>脚本在 <code>build/engineering-poc/</code> 下自动创建新的独立目录和项目副本，依次运行基线、记录管路修改的 Git 差异、检查旧证据失效和缺冷反例，再恢复输入并重算。它保留标准示例的源输入。每次执行都会生成新目录，可用于比较不同版本的行为。</p>
<p>脚本执行成功时打印 <code>status: pass</code>、本次 <code>output</code> 目录和 <code>summary</code> 文件路径。这表示预期的闭环行为得到检查，其中包括有意产生的工程失败；它不表示中间每个设计都满足要求。</p>
<h2 id="outputs">找到本次运行的材料</h2>
<table><thead><tr><th>输出目录中的位置</th><th>内容</th></tr></thead><tbody>
<tr><td><code>summary.json</code></td><td>基线、失败、修复的运行 ID，修订、证据摘要与案例限制。</td></tr>
<tr><td><code>commands.json</code> 与 <code>commands/</code></td><td>实际命令、退出码与逐次输出，适合核对操作顺序。</td></tr>
<tr><td><code>project/</code></td><td>本次使用的独立项目副本，可继续手动操作。</td></tr>
<tr><td><code>source-edits/</code></td><td>失败方案和恢复后的 Git 差异。</td></tr>
<tr><td><code>project/build/engineering/runs/</code></td><td>基线、失败与恢复对应的检查结果。</td></tr>
</tbody></table>
<p>对照<a href="/lab/pipe-change/">Lab 实验页</a>理解变化，再阅读<a href="/docs/evidence/">结果与证据</a>。如果脚本提前停止，按错误提示检查已保存的命令输出；无需删除失败记录或通过修改期望值消除失败。</p>
<h2 id="studio">按需要进入 Tex Studio</h2>
<p>macOS 上的标准暖通工作台由常驻 Podman 容器运行。系统在用户登录后启动虚拟机和容器。用现有 Google Chrome 标签页访问 <code>http://127.0.0.1:8710</code>。</p>
<details><summary>首次配置常驻开发容器</summary>
<p>准备 macOS 的 Python 3、Podman CLI 和已初始化的 <code>podman-machine-default</code>，在仓库根目录执行：</p>
<pre><code class="language-bash">/usr/bin/python3 tools/dev_container.py install
/usr/bin/python3 tools/dev_container.py status</code></pre>
<p>首次启动会从锁文件同步依赖。脚本管理实例和独占锁。重新启动、重建和日志位置见仓库的 <code>docs/development-container.md</code>。</p>
</details>
<p>前面的 CLI 实验可独立运行。标准暖通工作台加载的是仓库中的暖通示例。</p>
<h2 id="next-step">继续一次手动变更</h2>
<p>保留本次输出目录，使用其中的 <code>project/</code> 跟随<a href="/docs/engineering-workflow/">工程变更流程</a>操作。依次核对源文件、Git 差异和各次运行的检查结果。</p>`,
    related: [
      { href: "/docs/engineering-workflow/", label: "下一步：手动完成工程变更" },
      { href: "/docs/evidence/", label: "参考：读懂结果与证据" },
      { href: "/lab/pipe-change/", label: "案例：对照实验记录" },
    ],
  },
  {
    path: "/docs/engineering-workflow/",
    section: "Docs",
    title: "完成一次可审查的变更",
    english: "Engineering workflow",
    summary: "直接编辑模型源文件，用 Git 审查差异，重算并保留检查结果。",
    body: `<h2 id="project">使用独立实验副本</h2>
<p>先完成<a href="/docs/quick-start/">快速开始</a>，将下面变量替换为该次输出目录中的 <code>project</code> 路径。后续操作使用这个副本。每条命令从 TexSys 仓库根目录执行。</p>
<pre><code class="language-bash">TEXSYS_DEMO_PROJECT='替换为本次输出目录中的 project 路径'
uv run --locked texsys engineering inspect --adapter datacenter:fws-change -p "$TEXSYS_DEMO_PROJECT"</code></pre>
<p><code>inspect</code> 返回当前输入及其来源、运行历史和结果适用性。先记录两项输入的原值。</p>
<h2 id="preview">编辑源文件并核对 Git 差异</h2>
<p>在副本的 <code>src/fws-branches.study.json</code> 中，把 <code>spine_x_m</code> 改为 <code>46.5</code>，把 <code>pipe_types.row.diameter_m</code> 改为 <code>0.05</code>。这些值用于重现概念案例中的缺冷反例。查看该副本的 Git 差异：</p>
<pre><code class="language-bash">git -C "$TEXSYS_DEMO_PROJECT" diff -- src/fws-branches.study.json
uv run --locked texsys engineering inspect --adapter datacenter:fws-change -p "$TEXSYS_DEMO_PROJECT"</code></pre>
<p>旧运行的 <code>latest.freshness</code> 应为 <code>inputs-changed</code>。Git diff 显示源文件变化，工程检查给出实际判定。</p>
<h2 id="apply-run">运行声明范围内的检查</h2>
<pre><code class="language-bash">uv run --locked texsys engineering run datacenter:fws-change -p "$TEXSYS_DEMO_PROJECT"</code></pre>
<p>工程不满足时，<code>run</code> 返回退出码 1，并保存实际值、限值、对象和提取量。结合供冷检查、流速限制和泵扬程余量判断缺冷原因。</p>
<h2 id="repair">恢复输入并比较结果</h2>
<p>把两项输入改回最初记录的值，再运行检查和读取对比：</p>
<pre><code class="language-bash">git -C "$TEXSYS_DEMO_PROJECT" diff -- src/fws-branches.study.json
uv run --locked texsys engineering run datacenter:fws-change -p "$TEXSYS_DEMO_PROJECT"
uv run --locked texsys engineering inspect --adapter datacenter:fws-change -p "$TEXSYS_DEMO_PROJECT"</code></pre>
<p>源文件恢复原样时 Git 差异为空。通过 <code>comparison</code> 查看失败和恢复两次运行的检查值与提取量，核对 <code>latest.current</code> 与最新判定。</p>
<h2 id="review">保留源版本和检查结果</h2>
<p>用 Git 保存需要保留的源版本，并记录对应的运行 ID。结果文件位于副本的 <code>build/engineering/runs/</code>。正式工程审查由既有责任流程完成。</p>`,
    related: [
      { href: "/docs/evidence/", label: "下一步：检查结果的条件与有效范围" },
      { href: "/ideas/feedback/", label: "理解机制：实现信息更新设计模型" },
      { href: "/lexicon/design-commitment/", label: "理解概念：设计承诺与回退" },
    ],
  },
  {
    path: "/docs/evidence/",
    section: "Docs",
    title: "读懂结果与证据",
    english: "Results and evidence",
    summary: "分别判断计算是否完成、声明的工程判据是否满足，以及证据是否适用。",
    body: `<h2 id="states">检查结果的三种状态</h2>
<table><thead><tr><th>结果字段</th><th>回答的问题</th><th>阅读方式</th></tr></thead><tbody>
<tr><td><code>execution</code></td><td>操作有没有执行完成？</td><td><code>completed</code> 表示执行完成；<code>error</code> 需要检查错误与诊断。</td></tr>
<tr><td><code>result.assessment</code></td><td>声明的工程判据是否满足？</td><td><code>pass</code>、<code>fail</code> 或 <code>indeterminate</code> 分别表示满足、不满足或无法判定。</td></tr>
<tr><td><code>freshness</code> / <code>current</code></td><td>结果还适用于当前输入与工具吗？</td><td>输入、工具、适配器或依赖变化都可能触发复核。</td></tr>
</tbody></table>
<p><code>result.verification</code> 是适配器可能提供的算例回归结果，与工程判定独立。一个测试若正确检出失败方案，测试通过与工程不满足可以同时成立。</p>
<h2 id="checks">从一条失败检查回到对象</h2>
<p>查看 <code>result.checks</code> 中的检查 ID、对象引用、工况、实际值、限值、单位和依据。设施水实验同时检查声明工况下的供冷和已建模几何净距；诊断依据实际配流结果解释流速限制与泵扬程余量。</p>
<p>若判定为 <code>indeterminate</code>，先处理缺失输入、工况不符或执行错误。没有有效判断时，不能把空结果理解为满足要求。CLI 退出码有助于脚本分支，但仍需阅读这些状态和具体诊断。</p>
<h2 id="provenance">核对结果依据什么</h2>
<p>运行记录关联设计修订、适配器与工具摘要，并保留工程检查、提取量、限制和证据摘要。提取值要连同来源、单位、精度与模型条件阅读。概念路由中的确定长度，仍然属于概念模型信息。</p>
<p>原始运行记录位于项目构建目录的 <code>engineering/runs/</code>。完整重跑脚本另在输出目录保存 <code>summary.json</code> 和逐次命令输出。</p>
<h2 id="change">设计改变以后</h2>
<p>当前版本采用项目级保守失效。影响路径帮助解释已登记的关系，但不能证明未列出的结论完全不受影响。输入、工具或锁定依赖变化后，应检查证据适用性并按需重算。</p>
<p>历史记录保留当时的依据。输入与工具恢复为原内容时，旧证据可能重新适用。新运行拥有自己的 ID 和证据摘要，可据此核对该次结果。</p>
<h2 id="scope">确认检查覆盖范围</h2>
<p>当前设施水案例使用概念几何和设备数据，覆盖指定工况与检查。TCS、热瞬态、控制、电气、结构、消防与施工审查属于其他检查范围。正式工程采用需要真实资料、独立校核和责任人审查。</p>
<p>阅读证据的最终目的，是明确“这个结论在什么条件下成立，以及还有什么需要判断”。保留这个问题，才能让下一次修改有可靠的起点。</p>`,
    related: [
      { href: "/docs/engineering-workflow/", label: "返回操作：工程变更流程" },
      { href: "/ideas/engineering-evidence/", label: "继续阅读：一个结论需要怎样的证据" },
      { href: "/lexicon/evidence-applicability/", label: "定义：证据适用性" },
    ],
  },
];
