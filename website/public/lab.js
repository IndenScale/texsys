/* Saved-result replay only. This page does not execute an engineering solver. */
const labels = {
  baseline: {
    status: '基线记录：计算完成，254 项检查满足；已另行记录自动化测试接受。',
    explanation: '供冷与已建模几何在声明工况内满足。这个结论绑定当前输入与工具版本。',
    geometry: '基线几何',
    review: '测试接受',
  },
  changed: {
    status: '变更记录：计算完成，250 项检查不满足。旧接受转为待复核，新结果的接受请求被拒绝。',
    explanation: '96 条管段提取长度改变。选定供冷路径的 4 个管段达到 2.5 m/s 流速上限，机房仅获 81.83 kW；泵组仍有扬程余量。',
    geometry: '96 个长度改变',
    review: '拒绝接受',
  },
  repaired: {
    status: '修复记录：恢复原输入，254 项检查重新满足。新运行先待审查，再单独记录自动化测试接受。',
    explanation: '恢复到 160 mm 与 46.4 m 后，检查结果与基线一致。新运行拥有自己的证据摘要，原接受记录不会自动继承。',
    geometry: '恢复原几何',
    review: '重新审查',
  },
};

for (const panel of document.querySelectorAll('[data-lab-console]')) {
  const source = panel.querySelector('[data-lab-records]');
  if (!source) continue;
  let records;
  try {
    records = JSON.parse(source.textContent);
  } catch {
    continue;
  }
  const buttons = [...panel.querySelectorAll('[data-lab-select]')];
  const update = (state) => {
    const record = records[state];
    const label = labels[state];
    if (!record || !label) return;
    const inputs = Object.fromEntries(record.input_values.map((input) => [input.id, input.value]));
    const values = {
      diameter: String(Math.round(inputs['fws.row-diameter'] * 1000)),
      position: inputs['fws.spine-x'].toFixed(1),
      cooling: record.selected_check.actual.toFixed(2),
      failures: `${record.failed_checks} / ${record.total_checks}`,
      status: label.status,
      explanation: label.explanation,
      revision: record.revision,
      'run-id': record.run_id,
      digest: record.evidence_digest,
      acceptance: record.recorded_follow_up,
      'diagram-input': `Ø ${Math.round(inputs['fws.row-diameter'] * 1000)} mm`,
      'diagram-geometry': label.geometry,
      'diagram-result': record.failed_checks ? `${record.failed_checks} 项不满足` : `${record.total_checks} 项满足`,
      'diagram-review': label.review,
    };
    for (const element of panel.querySelectorAll('[data-lab-field]')) {
      const value = values[element.dataset.labField];
      if (value !== undefined) element.textContent = value;
    }
    panel.dataset.labState = state;
    for (const button of buttons) {
      button.setAttribute('aria-pressed', String(button.dataset.labSelect === state));
    }
  };
  for (const button of buttons) {
    button.addEventListener('click', () => update(button.dataset.labSelect));
  }
}
