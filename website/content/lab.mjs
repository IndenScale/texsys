import { readFileSync } from "node:fs";

const evidence = JSON.parse(
  readFileSync(new URL("../public/evidence/pipe-change.json", import.meta.url), "utf8"),
);
const escape = (value) =>
  String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
const baseline = evidence.states.baseline;
const failed = evidence.states.changed;
const stateNames = { baseline: "基线", changed: "变更", repaired: "修复" };
const states = Object.entries(evidence.states);
const input = (record, id) => record.input_values.find((item) => item.id === id).value;

function consoleMarkup({ article = false } = {}) {
  const heading = article ? "h2" : "h3";
  return `<section id="experiment" class="experiment-panel" aria-labelledby="experiment-title" data-lab-console data-lab-state="baseline" data-md-exclude>
    <header class="experiment-header">
      <${heading} id="experiment-title">管径改变之后，供冷还够吗？</${heading}>
      <span class="experiment-kind-label">FWS / 已保存结果回放</span>
    </header>
    <div class="experiment-toolbar">
      <div class="experiment-controls" role="group" aria-label="选择运行记录，查看对应诊断">
        ${states.map(([key], index) => `<button type="button" data-lab-select="${key}" aria-pressed="${key === "baseline"}" aria-controls="experiment-readout"><span>${String(index + 1).padStart(2, "0")}</span>${stateNames[key]}</button>`).join("")}
      </div>
      <span class="experiment-date">2026.11.11 · 概念模型</span>
    </div>
    <div class="experiment-data">
      <div class="experiment-inputs">
        <h4>输入对照</h4>
        <table class="experiment-input-table">
          <caption class="experiment-sr-only">三次记录的列配水管内径和主配水管 X 坐标</caption>
          <thead><tr><th scope="col">记录</th><th scope="col">内径 <span>mm</span></th><th scope="col">X 坐标 <span>m</span></th></tr></thead>
          <tbody>${states.map(([key, record]) => `<tr data-lab-row="${key}" class="${key === "baseline" ? "is-selected" : ""}"><th scope="row">${stateNames[key]}</th><td>${Math.round(input(record, "fws.row-diameter") * 1000)}</td><td>${input(record, "fws.spine-x").toFixed(1)}</td></tr>`).join("")}</tbody>
        </table>
      </div>
      <figure class="experiment-chart" aria-labelledby="experiment-chart-title">
        <figcaption><h4 id="experiment-chart-title">机房供冷</h4><span>需求 ${baseline.selected_check.limit} kW</span></figcaption>
        <div class="experiment-axis" aria-hidden="true"><span>0</span><span>150</span><span>300 kW</span></div>
        <div class="experiment-bars">${states.map(([key, record]) => `<div class="experiment-bar-row ${key === "baseline" ? "is-selected" : ""}" data-lab-row="${key}" data-lab-series="${key}"><span class="experiment-bar-label">${stateNames[key]}</span><div class="experiment-bar-track" aria-hidden="true"><span class="experiment-bar-fill" style="--experiment-ratio:${(record.selected_check.actual / record.selected_check.limit) * 100}%"></span></div><span class="experiment-bar-value">${record.selected_check.actual.toFixed(2)}</span></div>`).join("")}</div>
        <p class="experiment-chart-note">L01 / Unit-01-01 · 初期正常工况</p>
      </figure>
    </div>
    <div id="experiment-readout" class="experiment-readout">
      <p class="experiment-diagnosis" role="status" aria-live="polite" aria-atomic="true" data-lab-field="explanation">基线：选定机房供冷达到 300 kW。几何与供冷检查满足声明工况，接受另行记录。</p>
      <dl class="experiment-outcomes">
        <div><dt>执行</dt><dd data-lab-field="execution">已完成</dd></div>
        <div><dt>工程判定</dt><dd data-lab-field="assessment">254 项满足</dd></div>
        <div><dt>证据适用性</dt><dd data-lab-field="applicability">对应当时输入</dd></div>
        <div><dt>接受记录</dt><dd data-lab-field="acceptance">另行测试接受</dd></div>
      </dl>
    </div>
    <footer class="experiment-footer"><p>三条量条采用同一尺度。概念计算值，非实时求解。</p><a href="/lab/pipe-change/#evidence">查看证据与条件</a></footer>
    <noscript><p class="experiment-noscript">当前诊断为基线。三组输入与供冷对照可直接读取，启用 JavaScript 可切换诊断。</p></noscript>
    <script type="application/json" data-lab-records>${JSON.stringify(evidence.states).replaceAll("<", "\\u003c")}</script>
  </section>`;
}

function recordDetails() {
  return `<details class="experiment-provenance" data-md-exclude><summary>查看三次运行的完整修订与证据摘要</summary><div class="experiment-provenance-content">${states.map(([key, record]) => `<section><h3>${stateNames[key]}记录</h3><dl><div><dt>运行 ID</dt><dd><code>${escape(record.run_id)}</code></dd></div><div><dt>输入修订</dt><dd><code>${escape(record.revision)}</code></dd></div><div><dt>原证据摘要</dt><dd><code>${escape(record.evidence_digest)}</code></dd></div><div><dt>运行完成时</dt><dd>待审查（${escape(record.acceptance_at_run_completion)}）</dd></div><div><dt>后续记录</dt><dd>${escape(record.recorded_follow_up)}</dd></div></dl></section>`).join("")}<p>摘要对应原始运行记录；下载文件为精简摘录，不能使用原摘要校验这份删节 JSON。</p></div></details>`;
}

export const labPreview = consoleMarkup();

export const labPage = {
  path: "/lab/pipe-change/",
  section: "Lab",
  title: "一根管路改变，哪些结论需要重来？",
  english: "A change. A chain of consequences.",
  summary:
    "从管径与路由修改，到几何提取、水力重算和旧证据失效。回放 TexSys 原型的一次完整概念实验。",
  body: `${consoleMarkup({ article: true })}
    ${recordDetails()}
    <p class="lead">缩小管径并移动路由，几何提取与水力计算给出了新的反馈。设计模型需要据此更新，原来的接受也必须重新审查。</p>
    <p>本页回放 2026 年 11 月 11 日通过 TexSys 公开 CLI 保存的三次运行。数值来自概念模型，不是现场测量；页面不执行实时求解，自动化测试接受也不代表工程签核。</p>
    <h2 id="question">实验要回答的问题</h2>
    <p>如果几何和尺寸改变，原来的水力结论能否继续使用？这不是检查一个表格字段是否更新，而是检查设计输入、派生几何、模型计算、工程判据和接受记录是否仍然对应同一个版本。</p>
    <p>对象是混合冷却数据中心概念单元的设施水系统（FWS）。声明范围包括初期正常、满配正常与满配 A 路失电时的逐机房供冷，以及已建模的几何净距。选定观察点为 <code>L01/Unit-01-01</code>，在 <code>initial-normal</code> 工况下要求供冷不少于 300 kW。</p>
    <h2 id="chain">从一个输入，追到一个结论</h2>
    <ol>
      <li><strong>固定基线。</strong>记录输入修订、工具链摘要与判据。基线计算的 254 项检查满足，脚本另行写入自动化测试接受。</li>
      <li><strong>预览并应用修改。</strong>通过同一套工程变更接口提交位置和内径修改；接受记录在输入改变后立即转为待复核。</li>
      <li><strong>提取并重算。</strong>96 条管段的提取长度改变。水力计算完成，但 250 项供冷检查未满足。</li>
      <li><strong>定位约束。</strong>首个失败观察点只获得 ${failed.selected_check.actual.toFixed(2)} kW。其供冷路径中 4 个管段达到 2.5 m/s 的流速上限；在可用泵与转速上限条件下，最小扬程余量仍有 ${failed.diagnosis.minimum_pump_head_margin_kpa.toFixed(2)} kPa。诊断将限制关联到管段，而不只显示一个红色结果。</li>
      <li><strong>拒绝失效结论。</strong>系统拒绝接受未满足的运行结果。计算成功与工程满足分别记录。</li>
      <li><strong>恢复后重新判断。</strong>恢复原输入，检查结果与基线一致。新运行先保持待审查，随后另行记录测试接受；旧接受不会被直接移植到新证据。</li>
    </ol>
    <h2 id="comparison">三个记录，可以分别检查</h2>
    <div class="table-scroll"><table class="data-table"><thead><tr><th scope="col">记录</th><th scope="col">执行</th><th scope="col">工程判据</th><th scope="col">接受与适用性</th></tr></thead><tbody>
      <tr><th scope="row">基线</th><td>完成</td><td>254 项满足</td><td>测试接受；输入变更后待复核</td></tr>
      <tr><th scope="row">缩径与移位</th><td>完成</td><td>250 项不满足</td><td>拒绝接受；旧证据不再适用</td></tr>
      <tr><th scope="row">恢复</th><td>完成</td><td>与基线一致</td><td>新记录待审查，再单独测试接受</td></tr>
    </tbody></table></div>
    <h2 id="evidence">检查证据，重跑实验</h2>
    <p>本次验证在独立项目副本中执行了 15 条公开命令。源码基线为 <code>${evidence.source_commit.slice(0, 7)}</code>，适配器为 <code>datacenter:fws-change</code>。下载文件保留三个运行 ID、输入修订、原证据摘要、代表性检查、源码文件校验和及适用边界。</p>
    <p><a href="/evidence/pipe-change.json" download>下载三次运行的证据摘录（JSON）</a> · <a href="/evidence/verify_engineering_poc.py" download>下载原版重跑脚本（Python）</a></p>
    <p>需要该版本的 TexSys 仓库、锁定依赖和概念算例。在仓库根目录执行下面的命令；输出目录必须尚不存在。下载脚本应放置于仓库的 <code>tools/</code> 目录，或直接使用该提交中已有的同名脚本。</p>
    <pre class="code-block"><code>uv run --locked tools/verify_engineering_poc.py \\
  --output build/engineering-poc/website-replay</code></pre>
    <p>下载 JSON 是精简摘录，不是完整原始运行文件；其中的证据摘要对应原记录，不能用于校验这份删节文件。重跑会产生新的运行 ID 与证据摘要，应比较输入、判据、结果与状态变化，而不是要求 ID 相同。</p>
    <h2 id="limits">这个实验的边界</h2>
    <ul>${evidence.limitations.map((item) => `<li>${escape(item)}</li>`).join("")}</ul>
    <p>这次实验说明一个有限工程闭环可以运行、解释和复核。真实厂家曲线、现场标定、独立工程审查与第二项目复用仍需要单独证据。网站回放也不替代 TexSys 工作台的完整操作验收。</p>
    <h2 id="next-question">下一步，检验哪些问题？</h2>
    <p>在换用真实设备曲线后，诊断是否仍能定位决定性的约束？另一位工程师能否独立还原这个实验？当电气、控制或施工条件同时改变时，如何清楚地表达已覆盖的影响与尚未登记的依赖？这些问题决定原型能否从一个概念案例走向可靠的工程环境。</p>`,
  related: [
    { href: "/ideas/feedback/", label: "为什么实现反馈需要更新设计模型" },
    { href: "/ideas/engineering-evidence/", label: "证据为什么必须绑定版本与条件" },
    { href: "/system/compiler/", label: "Engineering Compiler 如何组织这个循环" },
    { href: "/participate/", label: "参与验证与提出反例" },
  ],
};

export const labMarkdown = `# 一根管路改变，哪些结论需要重来？

*A change. A chain of consequences.*

缩小管径并移动路由，几何提取与水力计算给出了新的反馈。设计模型需要据此更新，原来的接受也必须重新审查。

这是 TexSys 于 2026 年 11 月 11 日通过公开 CLI 保存的概念实验。以下为真实运行记录的整理，不是浏览器实时求解，也不是现场测量或工程签核。

## 输入、工况与判据

- 对象：混合冷却数据中心概念单元的设施水系统（FWS）。
- 声明范围：${evidence.scope}
- 观察点：\`L01/Unit-01-01\`；工况：\`initial-normal\`（初期正常）。
- 该观察点的机房液侧供冷需求：至少 300 kW。
- 修改：列配水管内径由 160 mm 缩为 50 mm，主配水管 X 坐标由 46.4 m 移到 46.5 m；修复时恢复两项原输入。

## 三次运行对照

| 记录 | 内径（mm） | X 坐标（m） | 选定机房供冷（kW） | 不满足检查 | 执行 | 工程判定 |
| --- | ---: | ---: | ---: | ---: | --- | --- |
${states.map(([key, record]) => `| ${stateNames[key]} | ${Math.round(input(record, "fws.row-diameter") * 1000)} | ${input(record, "fws.spine-x").toFixed(1)} | ${record.selected_check.actual.toFixed(2)} | ${record.failed_checks} / ${record.total_checks} | 完成 | ${record.assessment === "pass" ? "满足" : "不满足"} |`).join("\n")}

表中供冷值保留两位小数；下载 JSON 保留记录中的完整数值。三次运行均完成，但变更后的工程判定不满足。这个区别不能被一个“测试通过”状态替代。

## 从修改到诊断

1. 固定基线输入、工具链和判据，运行后另行记录自动化测试接受。
2. 预览并应用坐标与管径修改，原接受立即转为待复核。
3. 96 条管段提取长度改变；重算后 250 项供冷检查不满足。
4. 首个失败观察点仅获 ${failed.selected_check.actual.toFixed(8)} kW，低于 300 kW。其供冷路径中 4 个管段达到 2.5 m/s 流速上限；可用泵在转速上限条件下的最小扬程余量为 ${failed.diagnosis.minimum_pump_head_margin_kpa.toFixed(2)} kPa。
5. 系统拒绝接受失败结果。恢复输入后，检查与基线一致，但新的运行仍需独立审查。

## 证据适用性与接受

| 记录 | 完成运行时 | 后续记录 |
| --- | --- | --- |
${states.map(([key, record]) => `| ${stateNames[key]} | 待审查（${record.acceptance_at_run_completion}） | ${record.recorded_follow_up} |`).join("\n")}

这些接受由自动化验证脚本写入，只检验概念接受机制，不代表工程师签核。旧证据的适用性、当前工程判定和接受记录分别表达。

## 来源与重跑

- 源码提交：\`${evidence.source_commit}\`。
- 适配器：\`${evidence.adapter}\`。
- 工具链摘要：\`${evidence.toolchain_digest}\`。
- [下载三次运行证据摘录（JSON）](/evidence/pipe-change.json)
- [下载原版重跑脚本（Python）](/evidence/verify_engineering_poc.py)

| 记录 | 原运行 ID |
| --- | --- |
${states.map(([key, record]) => `| ${stateNames[key]} | \`${record.run_id}\` |`).join("\n")}

在所列版本的 TexSys 仓库中，安装锁定依赖，保留概念算例，从仓库根目录执行：

\`\`\`bash
uv run --locked tools/verify_engineering_poc.py \\
  --output build/engineering-poc/website-replay
\`\`\`

输出目录必须尚不存在。下载脚本应放在仓库 \`tools/\` 下，或直接使用该提交中的同名脚本。本次验证实际执行了 15 条公开命令。重跑会生成新运行 ID 和摘要，应比较输入、条件、判据、结果与状态变化。

JSON 为精简摘录；其中摘要对应原始记录，不能用于校验删节后的 JSON。

## 适用边界

${evidence.limitations.map((item) => `- ${item}`).join("\n")}

真实厂家曲线、现场标定、独立工程审查、完整工作台交互和第二项目复用仍需要分别验证。这个概念实验只说明所声明的有限闭环可以运行、解释与复核。

## 继续探索

- [为什么实现反馈需要更新设计模型](/ideas/feedback/)
- [证据为什么必须绑定版本与条件](/ideas/engineering-evidence/)
- [Engineering Compiler 如何组织这个循环](/system/compiler/)
`;
