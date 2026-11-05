import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { DiagramSvg } from "./graphics";
import { engineeringLabels, resolveGraphic } from "./symbols";
import type { Diagram } from "./types";

export function prepareGraphics(document: Diagram): Diagram {
  for (const node of document.nodes) {
    node.graphic = resolveGraphic(node, document.view);
    node.width = node.graphic.width;
    node.height = node.graphic.height;
    node.ports = node.graphic.ports;
    node.engineering_labels = engineeringLabels(node);
    const neutral = node.graphic.template === "neutral";
    node.symbol.level = neutral ? "neutral" : "family";
    node.symbol.presentation_exact = false;
    Object.assign(node.symbol, {
      semantic_ports_complete: true,
      catalog_version: "hvac-engineering/1",
      fallback_reason: neutral
        ? "No compatible family graphic; preserving all semantic ports."
        : "Engineering family graphic; no manufacturer model graphic requested.",
    });
    node.symbol.actual = {
      id: `hvac:${node.graphic.template}`,
      version: node.graphic.version,
      bbox: [0, 0, node.width, node.height],
      primitives: node.graphic.primitives,
      ports: Object.fromEntries(
        node.ports.map((port) => [
          port.name,
          { kind: port.kind, direction: port.direction, x: port.x, y: port.y },
        ]),
      ),
      label_anchor: node.graphic.label_anchor,
    };
  }
  document.presentation = {
    renderer: "react-svg/1",
    symbols: "hvac-engineering/1",
    geometry: "shared-typescript/1",
    number_precision: 3,
  };
  return document;
}

export function renderSvg(document: Diagram): string {
  return renderToStaticMarkup(<DiagramSvg document={document} />) + "\n";
}

const escapeHtml = (text: string) =>
  text.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll('"', "&quot;");

export function renderHtml(
  document: Diagram,
  bundle: string,
  style: string,
  results?: unknown[],
): string {
  const data = JSON.stringify({ document, results }).replaceAll("<", "\\u003c");
  return `<!doctype html>\n<html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${escapeHtml(document.title)} · TexSys</title><style>${style}</style></head><body><div id="root"></div><script id="texsys-data" type="application/json">${data}</script><script>${bundle.replaceAll("</script", "<\\/script")}</script></body></html>\n`;
}
