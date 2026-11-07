import { t } from "../../../../../src/texsys/assets/studio/i18n";
import React from "react";
import type { Diagram, DiagramNode, Point, Primitive } from "./types";
import { engineeringLabels } from "./symbols";

export const FONT = 'Arial, "PingFang SC", "Noto Sans CJK SC", sans-serif';
export const parameterTitles: Record<string, string> = {
  cooling_kw: "制冷量",
  capacity_kw: "容量",
  nominal_load_kw: "额定负荷",
  electric_kw: "输入功率",
  flow_m3_h: "流量",
  head_m: "扬程",
  load_kw: "负荷",
  heat_rejection_kw: "排热量",
  diameter_m: "管径",
  length_m: "长度",
  volume_m3: "容积",
  nominal_cop: "额定 COP",
  mass_flow_kg_s: "质量流量",
  cp_kj_kgk: "比热容",
  chilled_nominal_flow_m3_s: "冷冻侧额定流量",
  cooling_nominal_flow_m3_s: "冷却侧额定流量",
  chilled_nominal_pressure_drop_pa: "冷冻侧额定压降",
  cooling_nominal_pressure_drop_pa: "冷却侧额定压降",
  chilled_volume_m3: "冷冻侧持液量",
  cooling_volume_m3: "冷却侧持液量",
  elevation_change_m: "进出口高差",
  minimum_flow_m3_s: "最小允许运行流量",
  response_tau_s: "制冷响应时间常数",
  efficiency: "泵效率",
  water_heat_fraction: "损耗入水比例",
  shutoff_head_m: "关断扬程",
  curve_m_per_m6_s2: "泵曲线系数",
  check_valve: "出口止回阀",
  nominal_flow_m3_s: "额定流量",
  nominal_pressure_drop_pa: "额定压降",
  heat_loss_ua_w_k: "对外传热系数 UA",
  ua_w_k: "换热系数 UA",
  wall_capacity_j_k: "管壁热容量",
  segments: "沿程集总分段数",
  roughness_m: "绝对粗糙度",
  minor_loss_coefficient: "局部阻力系数",
  kv_m3_h: "阀门 Kv",
};
const mediumNames: Record<string, string> = {
  "chilled-water": "冷冻水",
  "cooling-water": "冷却水",
  air: "空气",
};
const equipmentNames: Record<string, string> = {
  Chiller: "冷水机组",
  ChilledWaterPump: "冷冻水泵",
  CoolingWaterPump: "冷却水泵",
  ChilledPipe: "冷冻水管段",
  CoolingPipe: "冷却水管段",
  Pump: "循环泵",
  CoolingTower: "冷却塔",
  CoolingLoad: "用冷负荷",
  HeatExchanger: "换热器",
  Pipe: "管段",
  Valve: "调节阀",
  Tank: "水箱",
  BufferTank: "缓冲水箱",
};

function svgProps(attrs: Primitive["attrs"]) {
  return Object.fromEntries(
    Object.entries(attrs).map(([key, value]) => [
      key.replace(/-([a-z])/g, (_, letter: string) => letter.toUpperCase()),
      value,
    ]),
  );
}

export function EngineeringSymbol({
  node,
  selected = false,
}: {
  node: DiagramNode;
  selected?: boolean;
}) {
  const graphic = node.graphic;
  if (!graphic) throw new Error(`Unresolved engineering geometry: ${node.id}`);
  return (
    <g
      color={selected ? "#087f72" : "#173247"}
      stroke="currentColor"
      fill="none"
      strokeWidth={selected ? 2.4 : 1.6}
      strokeLinejoin="round"
      strokeLinecap="round"
    >
      {graphic.primitives.map((primitive, index) =>
        React.createElement(primitive.tag, { key: index, ...svgProps(primitive.attrs) }),
      )}
    </g>
  );
}

export function DiagramSvg({
  document,
  selected,
  onSelect,
  temperatures,
  flows,
  debug = false,
}: {
  document: Diagram;
  selected?: string;
  onSelect?: (id: string) => void;
  temperatures?: Record<string, number>;
  flows?: Record<string, number>;
  debug?: boolean;
}) {
  const page = document.pages[0];
  const header = 92;
  const title = document.title.replace(/暖通(?:系统图|原理图)$/, (suffix) => t(suffix));
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox={`0 0 ${page.width} ${page.height + header}`}
      width={page.width}
      height={page.height + header}
      role={onSelect ? "group" : "img"}
      aria-label={title}
      style={{ fontFamily: FONT, background: "white" }}
    >
      <title>{title}</title>
      <metadata>
        {JSON.stringify({
          model_digest: document.model_digest,
          view: document.view,
          behavior_state: document.behavior_state,
          spatial_state: document.spatial_state,
          renderer: "react-svg/1",
        })}
      </metadata>
      <rect width="100%" height="100%" fill="white" />
      <defs>
        {Object.entries(document.media).map(([medium, color]) => (
          <marker
            key={medium}
            id={`arrow-${medium}`}
            viewBox="0 0 10 10"
            refX="9"
            refY="5"
            markerWidth="6"
            markerHeight="6"
            orient="auto-start-reverse"
          >
            <path d="M 1 1 L 9 5 L 1 9" fill="none" stroke={color} strokeWidth="1.8" />
          </marker>
        ))}
      </defs>
      <text x="24" y="28" fontSize="20" fill="#173247" fontWeight="600">
        {title}
      </text>
      <text x="24" y="50" fontSize="11" fill="#586b79">
        {document.deliverable === "schematic" ? t("组成 · 拓扑 · 行为参数") : t("组成 · 连接拓扑")}
        {t("｜ 概念数据 / 待工程审核 ｜ 图面位置不代表物理布置")}
      </text>
      <g transform="translate(24 69)">
        {Object.entries(document.media).map(([medium, color], index) => (
          <g key={medium} transform={`translate(${index * 145} 0)`}>
            <line x1="0" y1="0" x2="27" y2="0" stroke={color} strokeWidth="2" />
            <text x="34" y="4" fontSize="11" fill="#455a6b">
              {t(mediumNames[medium]) ?? medium}
            </text>
          </g>
        ))}
        {document.behavior_state !== "defined" && (
          <text x="360" y="4" fontSize="11" fill="#a33c21">
            {t("行为缺失：当前按系统图表达")}
          </text>
        )}
      </g>
      <g transform={`translate(0 ${header})`}>
        {document.edges.map((edge) => {
          const color = document.media[edge.medium] ?? "#586b79";
          const points = edge.points.map((point) => `${point.x},${point.y}`).join(" ");
          const flow = flows?.[edge.semantic_ref];
          const forward = flow === undefined ? edge.directed : flow > 1e-7;
          const reverse = flow !== undefined && flow < -1e-7;
          return (
            <g key={edge.id} id={edge.id} data-semantic-ref={edge.semantic_ref}>
              <polyline
                points={points}
                fill="none"
                stroke={color}
                strokeWidth="1.8"
                markerEnd={forward ? `url(#arrow-${edge.medium})` : undefined}
                markerStart={reverse ? `url(#arrow-${edge.medium})` : undefined}
              />
              {debug && (
                <text
                  x={edge.label_position.x}
                  y={edge.label_position.y + 11}
                  fontSize="10"
                  fill={color}
                >
                  {edge.medium}
                </text>
              )}
            </g>
          );
        })}
        {document.nodes.map((node) => {
          const graphic = node.graphic!;
          const temperature = temperatures?.[node.semantic_ref];
          const localizedLabels = engineeringLabels(node, t);
          return (
            <g
              key={node.id}
              id={node.id}
              data-semantic-ref={node.semantic_ref}
              transform={`translate(${node.x} ${node.y})`}
              className={onSelect ? "equipment-selectable" : undefined}
              onClick={onSelect ? () => onSelect(node.id) : undefined}
              role={onSelect ? "button" : undefined}
              tabIndex={onSelect ? 0 : undefined}
              aria-label={`${t(equipmentNames[node.component]) ?? node.component} ${node.label}`}
              onKeyDown={
                onSelect
                  ? (event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        onSelect(node.id);
                      }
                    }
                  : undefined
              }
            >
              <rect
                x="-6"
                y="-6"
                width={node.width + 12}
                height={node.height + 12}
                rx="3"
                fill={selected === node.id ? "#eaf6f3" : "transparent"}
                stroke={selected === node.id ? "#a2cfc4" : "none"}
                strokeDasharray="4 3"
              />
              <EngineeringSymbol node={node} selected={selected === node.id} />
              <text
                x={graphic.label_anchor.x}
                y={graphic.label_anchor.y}
                textAnchor="middle"
                fontSize="13"
                fontWeight="600"
                fill="#173247"
              >
                {node.label}
              </text>
              {document.deliverable === "schematic" && (
                <text
                  x={graphic.annotation_anchor.x}
                  y={graphic.annotation_anchor.y}
                  textAnchor="middle"
                  fontSize="10"
                  fill="#5b6f7e"
                >
                  {(localizedLabels.length ? localizedLabels : node.engineering_labels)?.join("　")}
                </text>
              )}
              {node.ports.map((port) => (
                <circle
                  key={port.id}
                  id={port.id}
                  data-semantic-ref={port.semantic_ref}
                  cx={port.x}
                  cy={port.y}
                  r={debug ? 3 : 1.6}
                  fill={document.media[port.kind] ?? "#586b79"}
                />
              ))}
              {temperature !== undefined && (
                <g transform={`translate(${node.width - 55} 4)`}>
                  <rect width="61" height="23" rx="3" fill="#e6f2f9" />
                  <text x="30" y="16" textAnchor="middle" fontSize="12" fill="#1769aa">
                    {temperature.toFixed(1)} °C
                  </text>
                </g>
              )}
              {debug && (
                <text x="0" y="-10" fontSize="9" fill="#a33c21">
                  {node.component} / {node.symbol.level}
                </text>
              )}
            </g>
          );
        })}
      </g>
    </svg>
  );
}

export function pointAtPolyline(points: Point[], fraction: number): Point {
  const lengths = points
    .slice(1)
    .map((point, index) => Math.hypot(point.x - points[index].x, point.y - points[index].y));
  let distance =
    lengths.reduce((sum, length) => sum + length, 0) * Math.max(0, Math.min(1, fraction));
  for (let index = 0; index < lengths.length; index++) {
    if (distance <= lengths[index]) {
      const ratio = lengths[index] ? distance / lengths[index] : 0;
      return {
        x: points[index].x + (points[index + 1].x - points[index].x) * ratio,
        y: points[index].y + (points[index + 1].y - points[index].y) * ratio,
      };
    }
    distance -= lengths[index];
  }
  return points.at(-1) ?? { x: 0, y: 0 };
}
