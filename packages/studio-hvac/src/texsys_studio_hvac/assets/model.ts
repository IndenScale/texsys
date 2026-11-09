import { t, getLocale } from "../../../../../src/texsys/assets/studio/i18n";
import type {
  Binding,
  Diagram,
  Simulation,
} from "../../../../hvac-graphics/src/texsys_hvac_graphics/assets/types";
import type { WorkbenchObject } from "../../../../../src/texsys/assets/studio/sdk";
import { labels } from "../../../../../src/texsys/assets/studio/sdk";
import {
  backendName,
  scenarioNames,
  variableName,
} from "../../../../hvac-graphics/src/texsys_hvac_graphics/assets/simulation";

export type Definition = {
  ports?: Record<string, { kind: string; direction: string }>;
  instances?: Record<string, Instance>;
  connections?: { from: string; to: string }[];
  behavior?: {
    reference: string;
    state: string;
    source: string;
    parameters: Record<string, Binding>;
  };
};
export type Instance = { component: string; parameters?: Record<string, Binding> };
export type Source = { root: string; components: Record<string, Definition> };
export type Run = { result: Simulation; current: boolean; path: string };
export type Report = {
  kind: string;
  current: boolean;
  path: string;
  data: {
    status: string;
    files?: number;
    diagnostics?: Record<string, unknown>[];
    targets?: {
      target: string;
      status: string;
      diagnostics?: Record<string, unknown>[];
      open_items?: Record<string, unknown>[];
    }[];
  };
};
export type Data = {
  error?: string;
  source: Source | null;
  source_path: string;
  diagrams: Record<string, { document: Diagram; current: boolean }>;
  runs: Run[];
  scenarios: { id: string; path: string; duration_s: number; events: number }[];
  reports: Report[];
  exports: { name: string; path: string; current: boolean }[];
};
export type Selection = { kind: "instance" | "type"; id: string };
export type Issue = {
  id: string;
  status: string;
  current: boolean;
  title: string;
  detail: string;
  origin: string;
  object?: string;
  runPath?: string;
  assertion?: number;
};
export const names: Record<string, string> = {
  Chiller: "冷水机组",
  ChilledWaterPump: "冷冻水泵",
  CoolingWaterPump: "冷却水泵",
  CoolingTower: "冷却塔",
  CoolingLoad: "负荷换热器",
  ChilledPipe: "冷冻水管段",
  CoolingPipe: "冷却水管段",
  Pipe: "管段",
  Valve: "阀门",
  Tank: "水箱",
  Pump: "循环泵",
};
export const mediumNames: Record<string, string> = {
  "chilled-water": "冷冻水",
  "cooling-water": "冷却水",
  air: "空气",
};
export const bindingLabels: Record<string, string> = { ...labels, failed: "失败" };
export function unitLabel(unit: string): string {
  return (
    ({ "1": "", degC: "°C", "m3/s": "m³/s", m3: "m³", "s2/m5": "s²/m⁵" } as Record<string, string>)[
      unit
    ] ?? unit
  );
}
export function bindingValue(binding?: Binding, key = ""): string {
  if (!binding || !["known", "assumed"].includes(binding.state) || binding.value === undefined)
    return t(bindingLabels[binding?.state ?? "missing"]) ?? binding?.state ?? t("缺失");
  if (key === "check_valve" && binding.unit === "1" && [0, 1].includes(binding.value))
    return binding.value ? t("有") : t("无");
  const numeric = binding.value.toLocaleString(getLocale(), { maximumSignificantDigits: 6 });
  if (key === "efficiency" && binding.unit === "1")
    return `${(binding.value * 100).toLocaleString(getLocale(), { maximumSignificantDigits: 6 })}%`;
  return `${numeric}${unitLabel(binding.unit) ? " " + unitLabel(binding.unit) : ""}`;
}

// Resolve only an explicit stable reference; diagnostic prose is never an object identity.
export function instanceFromRef(source: Source, reference?: string): string | undefined {
  if (!reference) return;
  const local = reference.startsWith(source.root + ".")
    ? reference.slice(source.root.length + 1)
    : reference;
  return Object.keys(source.components[source.root]?.instances ?? {}).find(
    (id) => local === id || local.startsWith(id + ".") || local.startsWith(id + "#"),
  );
}
export type ModelObject = WorkbenchObject & {
  selection: Selection & { owner?: string };
};
export function modelObjects(source: Source): { objects: ModelObject[]; truncated: boolean } {
  const objects: ModelObject[] = Object.keys(source.components).map((definition) => ({
    id: JSON.stringify(["definition", definition]),
    label: definition,
    icon: "cube",
    kind: "definition",
    definition,
    selection: { kind: "type", id: definition },
  }));
  const limit = 5000;
  let count = 0;
  let truncated = false;
  const ancestors = new Set<string>();
  const visit = (owner: string, path: string[]) => {
    if (ancestors.has(owner) || !Object.hasOwn(source.components, owner)) return;
    ancestors.add(owner);
    for (const [id, entry] of Object.entries(source.components[owner].instances ?? {})) {
      if (count === limit) {
        truncated = true;
        break;
      }
      const occurrence = [...path, id];
      count++;
      objects.push({
        id: JSON.stringify(["instance", ...occurrence]),
        label: [source.root, ...occurrence].join("."),
        keywords: entry.component,
        icon: "system",
        kind: "instance",
        definition: entry.component,
        selection: { kind: "instance", id, owner },
      });
      visit(entry.component, occurrence);
      if (truncated) break;
    }
    ancestors.delete(owner);
  };
  visit(source.root, []);
  return { objects, truncated };
}
export function sourceObjectLine(source: Source, selection?: ModelObject["selection"]): number {
  if (!selection) return -1;
  const lines = JSON.stringify(source, null, 2).split("\n");
  const path =
    selection.kind === "type"
      ? ["components", selection.id]
      : ["components", selection.owner ?? source.root, "instances", selection.id];
  let start = 0,
    end = lines.length;
  for (const [depth, key] of path.entries()) {
    const indent = "  ".repeat(depth + 1),
      marker = `${indent}${JSON.stringify(key)}: {`;
    let found = -1;
    for (let i = start; i < end; i++)
      if (lines[i] === marker) {
        found = i;
        break;
      }
    if (found < 0) return -1;
    start = found + 1;
    for (let i = start; i < end; i++)
      if (lines[i] === indent + "}" || lines[i] === indent + "},") {
        end = i;
        break;
      }
  }
  return start - 1;
}
export function issuesFor(data: Data): Issue[] {
  const issues: Issue[] = [];
  for (const report of data.reports) {
    const entries = [
      ...(report.data.diagnostics ?? []),
      ...(report.data.targets ?? []).flatMap((target) => [
        ...(target.diagnostics ?? []),
        ...(target.open_items ?? []),
      ]),
    ];
    for (const [index, entry] of entries.entries()) {
      const status = String(entry.status ?? entry.severity ?? "indeterminate");
      if (status === "pass" || status === "info") continue;
      const reference = entry.object ?? entry.semantic_ref;
      issues.push({
        id: `${report.path}:${index}`,
        status,
        current: report.current,
        title:
          entry.id === "visual-review"
            ? t("图纸待人工复核")
            : String(entry.rule ?? entry.id ?? t("模型检查项")),
        detail:
          entry.id === "visual-review"
            ? t("尚未记录图纸人工复核，工程审核仍待完成。")
            : String(entry.message ?? entry.impact ?? t("待复核")),
        origin: report.kind === "studio-check.json" ? t("模型检查") : t("图纸检查"),
        object:
          data.source && typeof reference === "string"
            ? instanceFromRef(data.source, reference)
            : undefined,
      });
    }
    if (!entries.length && report.data.status !== "pass")
      issues.push({
        id: report.path,
        status: report.data.status,
        current: report.current,
        title: t("检查结论待处理"),
        detail: t("此报告没有对象级诊断，请查看报告或后台任务日志。"),
        origin: t("模型检查"),
      });
  }
  for (const run of data.runs)
    for (const [index, assertion] of run.result.assertions.entries()) {
      if (assertion.status === "pass") continue;
      const variable = assertion.variable ? run.result.variables[assertion.variable] : undefined;
      issues.push({
        id: `${run.path}:${index}`,
        status: assertion.status,
        current: run.current,
        title:
          variable && assertion.variable
            ? variableName(assertion.variable, variable)
            : (assertion.message ?? t("工况约束待处理")),
        detail:
          [
            assertion.actual === undefined
              ? ""
              : t("极值 {0} {1}", {
                  "0": assertion.actual.toFixed(3),
                  "1": unitLabel(assertion.unit ?? ""),
                }),
            assertion.value === undefined
              ? ""
              : t("要求 {0} {1} {2}", {
                  "0": assertion.operator,
                  "1": assertion.value,
                  "2": unitLabel(assertion.unit ?? ""),
                }),
          ]
            .filter(Boolean)
            .join("；") ||
          assertion.message ||
          t("查看工况结果"),
        origin: `${t(scenarioNames[run.result.scenario]) ?? run.result.scenario} / ${backendName(run.result)}`,
        object:
          data.source && variable ? instanceFromRef(data.source, variable.semantic_ref) : undefined,
        runPath: run.path,
        assertion: index,
      });
    }
  return issues.sort(
    (a, b) =>
      Number(b.current) - Number(a.current) ||
      Number(["fail", "error"].includes(b.status)) - Number(["fail", "error"].includes(a.status)),
  );
}

export function download(content: BlobPart, name: string, type = "application/json") {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = name;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
