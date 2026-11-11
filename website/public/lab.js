/* Inspect saved records. Selecting a record does not run a solver or alter acceptance. */
const labels = {
  baseline: {
    explanation: "基线：选定机房供冷达到 300 kW。几何与供冷检查满足声明工况，接受另行记录。",
    applicability: "对应当时输入",
    acceptance: "另行测试接受",
  },
  changed: {
    explanation:
      "缩径与移位：96 条管段长度改变，4 个管段达到 2.5 m/s 流速上限。选定机房供冷降至 81.83 kW。",
    applicability: "旧证据待复核",
    acceptance: "接受请求被拒绝",
  },
  repaired: {
    explanation:
      "恢复输入：供冷回到 300 kW。新运行先待审查，再单独记录测试接受；原决定不自动继承。",
    applicability: "新证据独立生成",
    acceptance: "重审后测试接受",
  },
};

for (const panel of document.querySelectorAll("[data-lab-console]")) {
  const source = panel.querySelector("[data-lab-records]");
  if (!source) continue;
  let records;
  try {
    records = JSON.parse(source.textContent);
  } catch {
    continue;
  }
  const buttons = [...panel.querySelectorAll("[data-lab-select]")];
  const update = (state) => {
    const record = records[state];
    const label = labels[state];
    if (!record || !label) return;
    const values = {
      explanation: label.explanation,
      execution: record.execution === "completed" ? "已完成" : record.execution,
      assessment: record.failed_checks
        ? `${record.failed_checks} / ${record.total_checks} 不满足`
        : `${record.total_checks} 项满足`,
      applicability: label.applicability,
      acceptance: label.acceptance,
    };
    for (const element of panel.querySelectorAll("[data-lab-field]")) {
      const value = values[element.dataset.labField];
      if (value !== undefined) element.textContent = value;
    }
    panel.dataset.labState = state;
    for (const row of panel.querySelectorAll("[data-lab-row]")) {
      row.classList.toggle("is-selected", row.dataset.labRow === state);
    }
    for (const button of buttons) {
      button.setAttribute("aria-pressed", String(button.dataset.labSelect === state));
    }
  };
  for (const [index, button] of buttons.entries()) {
    button.addEventListener("click", () => update(button.dataset.labSelect));
    button.addEventListener("keydown", (event) => {
      let next;
      if (event.key === "ArrowRight") next = (index + 1) % buttons.length;
      if (event.key === "ArrowLeft") next = (index + buttons.length - 1) % buttons.length;
      if (event.key === "Home") next = 0;
      if (event.key === "End") next = buttons.length - 1;
      if (next === undefined) return;
      event.preventDefault();
      buttons[next].focus();
      update(buttons[next].dataset.labSelect);
    });
  }
}
