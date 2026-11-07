import type { DiagramNode, Graphic, Primitive, Port } from "./types";

const p = (tag: string, attrs: Primitive["attrs"]): Primitive => ({ tag, attrs });
const path = (d: string, extra: Primitive["attrs"] = {}) => p("path", { d, ...extra });
const line = (x1: number, y1: number, x2: number, y2: number) => p("line", { x1, y1, x2, y2 });
const rect = (
  x: number,
  y: number,
  width: number,
  height: number,
  extra: Primitive["attrs"] = {},
) => p("rect", { x, y, width, height, ...extra });
const circle = (cx: number, cy: number, r: number, extra: Primitive["attrs"] = {}) =>
  p("circle", { cx, cy, r, ...extra });

const families: Record<string, string> = {
  Chiller: "chiller",
  ChilledWaterPump: "pump",
  CoolingWaterPump: "pump",
  Pump: "pump",
  CoolingTower: "tower",
  CoolingLoad: "exchanger",
  HeatExchanger: "exchanger",
  Pipe: "pipe",
  Valve: "valve",
  CheckValve: "check-valve",
  Tank: "tank",
  BufferTank: "tank",
  Junction: "junction",
};

/** Pure geometry: the exact same anchors are used by ELK, React and exports. */
export function resolveGraphic(
  node: DiagramNode,
  detail: string = "schematic",
  stretch = 1,
): Graphic {
  if (!(Number.isFinite(stretch) && stretch >= 0.75 && stretch <= 2))
    throw new Error("Graphic stretch must be within [0.75, 2].");
  const declaredKind = node.symbol.requested.graphic_kind;
  const descriptorTemplate =
    typeof declaredKind === "string"
      ? (
          {
            load: "exchanger",
            chiller: "chiller",
            pipe: "pipe",
            pump: "pump",
            valve: "valve",
            tower: "tower",
            tank: "tank",
            junction: "junction",
          } as Record<string, string>
        )[declaredKind]
      : undefined;
  const requested = descriptorTemplate ?? families[node.component] ?? "neutral";
  const expectedNames =
    requested === "chiller"
      ? ["chilled-in", "chilled-out", "cooling-in", "cooling-out"]
      : ["inlet", "outlet"];
  const knownPorts =
    expectedNames.length === node.ports.length &&
    expectedNames.every((name) => node.ports.some((port) => port.name === name));
  const liquidPorts = node.ports.every(
    (port) =>
      ["chilled-water", "cooling-water"].includes(port.kind) &&
      (port.direction === "inout" ||
        port.direction === (port.name === "outlet" || port.name.endsWith("-out") ? "out" : "in")),
  );
  const compatible =
    liquidPorts &&
    (requested === "chiller"
      ? node.ports.every(
          (port) =>
            port.kind === (port.name.startsWith("chilled-") ? "chilled-water" : "cooling-water"),
        )
      : new Set(node.ports.map((port) => port.kind)).size === 1);
  const template = (knownPorts || requested === "junction") && compatible ? requested : "neutral";
  let width = 150,
    height = 130;
  let primitives: Primitive[];
  const anchors: Record<string, [number, number]> = {};
  if (template === "chiller") {
    width = 310;
    height = 190;
    primitives = [
      rect(40, 48, 230, 37, { rx: 16 }),
      rect(40, 103, 230, 42, { rx: 18 }),
      p("ellipse", { cx: 48, cy: 66.5, rx: 8, ry: 18.5 }),
      p("ellipse", { cx: 262, cy: 66.5, rx: 8, ry: 18.5 }),
      p("ellipse", { cx: 48, cy: 124, rx: 8, ry: 21 }),
      p("ellipse", { cx: 262, cy: 124, rx: 8, ry: 21 }),
      rect(77, 28, 82, 20, { rx: 3 }),
      rect(85, 33, 22, 10),
      line(114, 34, 151, 34),
      line(114, 39, 151, 39),
      path("M 179 48 C 179 24 217 24 217 46 L 217 96 C 217 104 206 111 194 111"),
      path("M 186 48 C 186 33 210 33 210 46 L 210 94 C 210 98 203 104 194 104"),
      rect(88, 107, 72, 24, { rx: 2 }),
      line(40, 66, 0, 66),
      line(270, 66, 310, 66),
      line(40, 124, 0, 124),
      line(270, 124, 310, 124),
      line(35, 59, 35, 73),
      line(275, 59, 275, 73),
      line(35, 117, 35, 131),
      line(275, 117, 275, 131),
      path("M 74 145 L 74 153 L 98 153 L 98 145 M 221 145 L 221 153 L 245 153 L 245 145"),
    ];
    anchors["chilled-in"] = [0, 124];
    anchors["chilled-out"] = [310, 124];
    anchors["cooling-in"] = [0, 66];
    anchors["cooling-out"] = [310, 66];
  } else if (template === "pump") {
    width = 142;
    height = 125;
    primitives = [
      line(0, 47, 45, 47),
      line(97, 47, 142, 47),
      circle(71, 47, 26),
      path("M 58 29 L 88 47 L 58 65 Z"),
      path("M 58 72 L 53 84 L 89 84 L 84 72"),
      rect(92, 68, 25, 16, { rx: 3 }),
      line(97, 72, 112, 72),
      line(97, 77, 112, 77),
      line(45, 84, 122, 84),
    ];
  } else if (template === "tower") {
    width = 180;
    height = 210;
    primitives = [
      path("M 49 40 L 35 145 L 145 145 L 131 40 Z"),
      rect(61, 25, 58, 15),
      p("ellipse", { cx: 90, cy: 25, rx: 29, ry: 9 }),
      circle(90, 25, 4),
      path("M 86 24 Q 69 13 67 25 Q 77 32 86 27 M 94 26 Q 112 37 113 24 Q 103 18 94 23"),
      rect(35, 145, 110, 13),
      line(45, 104, 135, 104),
      line(47, 112, 133, 112),
      line(49, 120, 131, 120),
      line(51, 128, 129, 128),
      path("M 60 58 L 60 89 M 80 58 L 80 89 M 100 58 L 100 89 M 120 58 L 120 89"),
      line(0, 75, 44, 75),
      line(145, 145, 180, 145),
      path("M 46 158 L 46 168 L 57 168 L 57 158 M 123 158 L 123 168 L 134 168 L 134 158"),
    ];
    anchors.inlet = [0, 75];
    anchors.outlet = [180, 145];
  } else if (template === "exchanger") {
    width = 170;
    height = 148;
    primitives = [
      line(0, 64, 49, 64),
      line(121, 64, 170, 64),
      rect(49, 29, 72, 73),
      path("M 57 94 L 113 37 M 57 78 L 97 37 M 73 94 L 113 53 M 89 94 L 113 69"),
      line(44, 22, 44, 110),
      line(126, 22, 126, 110),
      line(37, 110, 133, 110),
    ];
  } else if (template === "pipe") {
    width = 170;
    height = 78;
    primitives = [line(0, 26, 170, 26), line(18, 19, 18, 33), line(152, 19, 152, 33)];
  } else if (template === "valve" || template === "check-valve") {
    width = 112;
    height = 95;
    primitives = [
      line(0, 40, 32, 40),
      line(80, 40, 112, 40),
      path("M 32 27 L 80 53 L 80 27 L 32 53 Z"),
    ];
    if (template === "valve") primitives.push(line(56, 40, 56, 17), line(43, 17, 69, 17));
    else primitives.push(path("M 53 27 L 65 40 L 53 53"));
  } else if (template === "tank") {
    width = 148;
    height = 192;
    primitives = [
      path("M 35 38 L 35 135 C 35 156 113 156 113 135 L 113 38"),
      p("ellipse", { cx: 74, cy: 38, rx: 39, ry: 12 }),
      line(35, 67, 113, 67),
      line(0, 61, 35, 61),
      line(113, 128, 148, 128),
      line(45, 149, 45, 161),
      line(103, 149, 103, 161),
    ];
    anchors.inlet = [0, 61];
    anchors.outlet = [148, 128];
  } else if (template === "junction") {
    width = 90;
    height = 90;
    primitives = [
      line(0, 30, 90, 30),
      line(45, 0, 45, 60),
      circle(45, 30, 3, { fill: "currentColor" }),
    ];
  } else {
    width = 170;
    height = Math.max(110, node.ports.length * 22 + 40);
    primitives = [rect(14, 12, width - 28, height - 48, { "stroke-dasharray": "5 4", rx: 0 })];
  }
  const ports: Port[] = [];
  for (const side of ["WEST", "EAST"] as const) {
    const selected = node.ports.filter(
      (port) =>
        (port.name === "outlet" || port.name.endsWith("-out") || port.direction === "out"
          ? "EAST"
          : "WEST") === side,
    );
    selected.forEach((port, index) => {
      const fixed = anchors[port.name];
      const y =
        template === "pipe"
          ? 26
          : template === "pump"
            ? 47
            : template === "exchanger"
              ? 64
              : template.includes("valve")
                ? 40
                : 24 + ((index + 1) * (height - 70)) / (selected.length + 1);
      ports.push({
        ...port,
        x: fixed?.[0] ?? (side === "WEST" ? 0 : width),
        y: fixed?.[1] ?? y,
        side,
      });
    });
  }
  // Any unfamiliar extra ports remain visible and get explicit neutral stubs.
  for (const port of ports) {
    if (
      anchors[port.name] ||
      ["pump", "exchanger", "pipe", "valve", "check-valve", "junction"].includes(template)
    )
      continue;
    if (template === "neutral")
      primitives.push(line(port.x, port.y, port.side === "WEST" ? 14 : width - 14, port.y));
  }
  if (stretch !== 1) {
    primitives = primitives.map((primitive) => ({
      ...primitive,
      attrs: { ...primitive.attrs, transform: `scale(${stretch} 1)` },
    }));
  }
  return {
    template,
    version: "hvac-engineering/1",
    width: width * stretch,
    height,
    primitives,
    ports: ports.map((port) => ({ ...port, x: port.x * stretch })),
    label_anchor: { x: (width * stretch) / 2, y: height - (detail === "system" ? 13 : 25) },
    annotation_anchor: { x: (width * stretch) / 2, y: height - 7 },
  };
}

export function engineeringLabels(
  node: DiagramNode,
  translate: (message: string) => string = (message) => message,
): string[] {
  const names: Record<string, string> = {
    cooling_kw: "制冷量",
    capacity_kw: "容量",
    electric_kw: "输入功率",
    flow_m3_h: "流量",
    head_m: "扬程",
    load_kw: "负荷",
    nominal_load_kw: "额定负荷",
    shutoff_head_m: "关断扬程",
    nominal_flow_m3_s: "额定流量",
    heat_rejection_kw: "排热量",
    diameter_m: "管径",
    length_m: "长度",
    volume_m3: "容积",
    nominal_cop: "COP",
  };
  const labels: string[] = [];
  for (const [key, title] of Object.entries(names)) {
    const binding = node.parameters[key];
    if (binding && binding.value !== undefined)
      labels.push(
        `${translate(title)} ${binding.value} ${binding.unit === "1" ? "" : binding.unit}`.trim(),
      );
  }
  return labels.slice(0, 2);
}
