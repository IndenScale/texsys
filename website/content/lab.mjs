import { readFileSync } from 'node:fs';

const evidence = JSON.parse(readFileSync(new URL('../public/evidence/pipe-change.json', import.meta.url), 'utf8'));
const escape = (value) => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;');
const baseline = evidence.states.baseline;
const failed = evidence.states.changed;

const diagram = `<svg viewBox="0 0 720 190" role="img" aria-label="概念依赖示意：管路输入经过几何提取和水力求解，形成证据并交给审查。不是实际管路布置图。" data-lab-diagram>
  <g fill="none" stroke="currentColor" stroke-opacity=".3" stroke-width="1.5">
    <path d="M111 80 H197 M291 80 H377 M471 80 H557"/>
    <path d="M604 112 V161 H64 V112" stroke-dasharray="4 5"/>
  </g>
  <g fill="currentColor" opacity=".45"><path d="m189 76 8 4-8 4zm180 0 8 4-8 4zm180 0 8 4-8 4z"/></g>
  <g fill="none" stroke="currentColor" stroke-opacity=".55" stroke-width="1.5">
    <rect x="17" y="48" width="94" height="64" rx="2"/>
    <rect x="197" y="48" width="94" height="64" rx="2"/>
    <rect x="377" y="48" width="94" height="64" rx="2" data-lab-node="assessment"/>
    <rect x="557" y="48" width="94" height="64" rx="2"/>
    <path d="M42 69 H84 M42 80 H84 M42 91 H68 M217 92 V69 H269 V92 M227 69 V59 M259 69 V59 M400 91 L410 71 L421 83 L434 64 L448 91 M581 65 H627 V95 H581 Z M589 73 H617 M589 81 H617 M589 89 H609"/>
  </g>
  <g fill="currentColor" font-family="inherit" text-anchor="middle" font-size="13">
    <text x="64" y="30">设计输入</text><text x="244" y="30">几何提取</text><text x="424" y="30">水力求解</text><text x="604" y="30">证据 · 审查</text>
    <text x="64" y="135" data-lab-field="diagram-input">Ø 160 mm</text>
    <text x="244" y="135" data-lab-field="diagram-geometry">基线几何</text>
    <text x="424" y="135" data-lab-field="diagram-result">254 项满足</text>
    <text x="604" y="135" data-lab-field="diagram-review">测试接受</text>
    <text x="334" y="183" opacity=".55" font-size="11">修改设计，重新检查证据的适用性</text>
  </g>
</svg>`;

function consoleMarkup(compact = false) {
  return `<div class="lab-console" data-lab-console data-lab-state="baseline">
    <div class="lab-scenario"><span>设施水 / FWS</span><span class="lab-record-label">已保存的真实原型运行</span></div>
    <div class="lab-controls" role="group" aria-label="选择要回放的运行记录">
      <button type="button" data-lab-select="baseline" aria-pressed="true">01 基线</button>
      <button type="button" data-lab-select="changed" aria-pressed="false">02 查看变更结果</button>
      <button type="button" data-lab-select="repaired" aria-pressed="false">03 回放修复</button>
    </div>
    <div class="lab-diagram">${diagram}</div>
    <div class="lab-metrics">
      <div class="lab-metric"><span>列配水管内径</span><strong><span data-lab-field="diameter">160</span><small> mm</small></strong></div>
      <div class="lab-metric"><span>主配水管 X 坐标</span><strong><span data-lab-field="position">46.4</span><small> m</small></strong></div>
      <div class="lab-metric"><span>选定机房供冷</span><strong><span data-lab-field="cooling">300.00</span><small> kW</small></strong><span class="lab-metric-note">需求 300 kW</span></div>
      <div class="lab-metric"><span>未满足的检查</span><strong data-lab-field="failures">0 / 254</strong></div>
    </div>
    <p class="lab-status" role="status" aria-live="polite" aria-atomic="true" data-lab-field="status">基线记录：计算完成，254 项检查满足；已另行记录自动化测试接受。</p>
    <p class="lab-detail" data-lab-field="explanation">供冷与已建模几何在声明工况内满足。这个结论绑定当前输入与工具版本。</p>
    ${compact ? '' : `<dl class="lab-record-meta">
      <div><dt>输入修订</dt><dd><code data-lab-field="revision">${escape(baseline.revision)}</code></dd></div>
      <div><dt>原运行 ID</dt><dd><code data-lab-field="run-id">${escape(baseline.run_id)}</code></dd></div>
      <div><dt>原证据摘要</dt><dd><code data-lab-field="digest">${escape(baseline.evidence_digest)}</code></dd></div>
      <div><dt>接受记录</dt><dd data-lab-field="acceptance">${escape(baseline.recorded_follow_up)}</dd></div>
    </dl>`}
    <p class="lab-caption">概念水力算例 · 回放已保存结果，非浏览器实时求解。图示表达依赖关系，不代表实际管路布置。</p>
    <noscript><p>当前展示基线。开启 JavaScript 可切换回放；三组记录均可在下方 JSON 中读取。</p></noscript>
    <script type="application/json" data-lab-records>${JSON.stringify(evidence.states).replaceAll('<', '\\u003c')}</script>
  </div>`;
}

export const labPreview = consoleMarkup(true);

export const labPage = {
  path: '/lab/pipe-change/',
  section: 'Lab',
  title: '一根管路改变，哪些结论需要重来？',
  english: 'A change. A chain of consequences.',
  summary: '从管径与路由修改，到几何提取、水力重算和旧证据失效。回放 Texsys 原型的一次完整概念实验。',
  body: `<p class="lead">一个“完成”的计算，可以得出一个“不满足”的工程结果。一次通过的设计，也会在输入改变后失去原来的依据。我们用设施水管路原型，把这两件事放到同一个可追踪的循环里。</p>
    <div class="callout"><strong>本页展示什么</strong><p>这是 2026 年 11 月 11 日通过公开 CLI 实际运行的概念算例。页面回放三个已保存状态；数据、摘要与局限可以下载。这里没有调用实时求解器，也不代表真实工程签核。</p></div>
    <h2 id="replay">观察一次变更</h2>
    <p>先查看基线，再把列配水管内径由 160 mm 改为 50 mm，同时将主配水管 X 坐标由 46.4 m 移到 46.5 m。系统重算后保留失败记录；恢复原输入后，新的运行仍需单独接受。</p>
    ${consoleMarkup()}
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
    <p>需要该版本的 Texsys 仓库、锁定依赖和概念算例。在仓库根目录执行下面的命令；输出目录必须尚不存在。下载脚本应放置于仓库的 <code>tools/</code> 目录，或直接使用该提交中已有的同名脚本。</p>
    <pre class="code-block"><code>uv run --locked tools/verify_engineering_poc.py \\
  --output build/engineering-poc/website-replay</code></pre>
    <p>下载 JSON 是精简摘录，不是完整原始运行文件；其中的证据摘要对应原记录，不能用于校验这份删节文件。重跑会产生新的运行 ID 与证据摘要，应比较输入、判据、结果与状态变化，而不是要求 ID 相同。</p>
    <h2 id="limits">这个实验的边界</h2>
    <ul>${evidence.limitations.map((item) => `<li>${escape(item)}</li>`).join('')}</ul>
    <p>这次实验说明一个有限工程闭环可以运行、解释和复核。真实厂家曲线、现场标定、独立工程审查与第二项目复用仍需要单独证据。网站回放也不替代 Tex Studio 的完整操作验收。</p>
    <h2 id="next-question">下一步，检验哪些问题？</h2>
    <p>在换用真实设备曲线后，诊断是否仍能定位决定性的约束？另一位工程师能否独立还原这个实验？当电气、控制或施工条件同时改变时，如何清楚地表达已覆盖的影响与尚未登记的依赖？这些问题决定原型能否从一个概念案例走向可靠的工程环境。</p>`,
  related: [
    { href: '/ideas/feedback/', label: '为什么实现必须反过来修正原理' },
    { href: '/ideas/engineering-evidence/', label: '证据为什么必须绑定版本与条件' },
    { href: '/system/compiler/', label: 'Engineering Compiler 如何组织这个循环' },
    { href: '/participate/', label: '参与验证与提出反例' },
  ],
};
