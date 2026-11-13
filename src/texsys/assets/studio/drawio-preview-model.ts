import type { DrawingPage } from "./DrawingPreview";
import type { DrawingBounds } from "./preview-viewport";

export type DrawioPoint = { x: number; y: number };
type CellStyle = Map<string, string>;
type Cell = {
  id: string;
  parent: string | null;
  node: Element;
  geometry?: Element;
  style: CellStyle;
  label: string;
};
export type DrawioVertex = {
  kind: "vertex";
  id: string;
  bounds: DrawingBounds;
  shape: string;
  image?: string;
  label: string;
  style: CellStyle;
  unsupported: boolean;
};
export type DrawioEdge = {
  kind: "edge";
  id: string;
  points: DrawioPoint[];
  label: string;
  style: CellStyle;
};
export type DrawioPage = DrawingPage & {
  cells: (DrawioVertex | DrawioEdge)[];
  background: string;
  unsupported: number;
};
type PageSource = { id: string; label: string; graph: Element | string };

const imagePrefix = /^data:image\/(svg\+xml|png|jpe?g|gif|webp)(?:;charset=utf-8)?(;base64)?,/i;
const shapes = new Set(["rectangle", "ellipse", "rhombus", "text", "group", "image"]);
const emptyBounds = { x: 0, y: 0, width: 0, height: 0 };
const labelEntities = new Map([
  ["nbsp", "\u00a0"],
  ["amp", "&"],
  ["quot", '"'],
  ["apos", "'"],
  ["lt", "<"],
  ["gt", ">"],
]);

function child(node: Element | undefined, name: string, role?: string) {
  return (
    node &&
    Array.from(node.children).find(
      (element) =>
        element.tagName === name && (role === undefined || element.getAttribute("as") === role),
    )
  );
}

function number(value: string | null | undefined, fallback = 0) {
  const parsed = value?.trim() ? Number(value) : NaN;
  return Number.isFinite(parsed) && Math.abs(parsed) <= 1e9 ? parsed : fallback;
}

function xmlRoot(text: string) {
  if (/<!DOCTYPE\b/i.test(text)) throw new Error("Unexpected XML document type");
  return new DOMParser().parseFromString(text, "application/xml").documentElement;
}

export function drawioPageSources(content: string, label: string): PageSource[] {
  const root = xmlRoot(content);
  if (root.tagName === "mxGraphModel") return [{ id: "0", label, graph: root }];
  if (root.tagName !== "mxfile") throw new Error("Invalid Draw.io document");
  const diagrams = Array.from(root.children).filter((node) => node.tagName === "diagram");
  if (!diagrams.length) throw new Error("Missing Draw.io pages");
  const ids = new Set(diagrams.flatMap((diagram) => diagram.getAttribute("id") || []));
  const seen = new Set<string>();
  let anonymous = 0;
  return diagrams.map((diagram, index) => {
    let id = diagram.getAttribute("id");
    if (!id || seen.has(id)) {
      do id = String(anonymous++);
      while (ids.has(id));
    }
    seen.add(id);
    return {
      id,
      label: diagram.getAttribute("name") || String(index + 1),
      graph: child(diagram, "mxGraphModel") ?? diagram.textContent?.trim() ?? "",
    };
  });
}

export async function decompressDrawioPages(sources: PageSource[], signal: AbortSignal) {
  // One budget across the file also limits highly compressed multi-page documents.
  let remaining = 8 * 1024 * 1024;
  const pages: DrawioPage[] = [];
  for (const source of sources) {
    if (signal.aborted) return pages;
    if (typeof source.graph !== "string") {
      pages.push(drawioPage(source, source.graph));
      continue;
    }
    const bytes = Uint8Array.from(atob(source.graph), (character) => character.charCodeAt(0));
    const reader = new Blob([bytes])
      .stream()
      .pipeThrough(new DecompressionStream("deflate-raw"))
      .getReader();
    const abort = () => void reader.cancel().catch(() => {});
    signal.addEventListener("abort", abort, { once: true });
    try {
      const decoder = new TextDecoder("utf-8", { fatal: true });
      const chunks: string[] = [];
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        remaining -= value.length;
        if (remaining < 0) throw new Error("Draw.io expansion limit exceeded");
        chunks.push(decoder.decode(value, { stream: true }));
      }
      if (signal.aborted) return pages;
      chunks.push(decoder.decode());
      pages.push(drawioPage(source, xmlRoot(decodeURIComponent(chunks.join("")))));
    } finally {
      signal.removeEventListener("abort", abort);
      await reader.cancel().catch(() => {});
    }
  }
  return pages;
}

export function drawioColor(value: string | null | undefined, fallback: string) {
  return value && /^(?:#[\da-f]{3,8}|[a-z]+)$/i.test(value) ? value : fallback;
}

function readStyle(value: string) {
  const entries = value.split(/;(?!base64,|charset=utf-8[,;])/i);
  const style = new Map<string, string>();
  for (const entry of entries) {
    const equals = entry.indexOf("=");
    if (equals > 0) style.set(entry.slice(0, equals), entry.slice(equals + 1));
    else if (entry === "text" || entry === "group") style.set("shape", entry);
  }
  return style;
}

function readCell(node: Element): Cell | undefined {
  const wrapped = node.tagName !== "mxCell";
  const cell = wrapped ? child(node, "mxCell") : node;
  const id = node.getAttribute("id") || cell?.getAttribute("id");
  if (!id || !cell) return;
  return {
    id,
    parent: cell.getAttribute("parent"),
    node: cell,
    geometry: child(cell, "mxGeometry"),
    style: readStyle(cell.getAttribute("style") ?? ""),
    label: node.getAttribute(wrapped ? "label" : "value") ?? cell.getAttribute("value") ?? "",
  };
}

function textLabel(cell: Cell) {
  if (cell.style.get("noLabel") === "1") return "";
  if (cell.style.get("html") !== "1") return cell.label;
  // Preserve basic HTML label line breaks, with all label output rendered as SVG text.
  return cell.label
    .replace(/<br\b[^<>]*>|<\/(?:div|p)>/gi, "\n")
    .replace(/<[^<>]*>/g, "")
    .replace(/&(nbsp|amp|quot|apos|lt|gt|#\d+|#x[\da-f]+);/gi, (_, entity: string) => {
      const key = entity.toLowerCase();
      const code = key.startsWith("#x") ? parseInt(key.slice(2), 16) : Number(key.slice(1));
      return (
        labelEntities.get(key) ??
        (code > 0 && code <= 0x10ffff ? String.fromCodePoint(code) : `&${entity};`)
      );
    })
    .trim();
}

function point(node: Element | undefined): DrawioPoint {
  return { x: number(node?.getAttribute("x")), y: number(node?.getAttribute("y")) };
}

function add(left: DrawioPoint, right: DrawioPoint): DrawioPoint {
  return { x: left.x + right.x, y: left.y + right.y };
}

function cellBounds(cells: Map<string, Cell>) {
  const resolved = new Map<string, DrawingBounds | null>();
  return (id: string): DrawingBounds | null => {
    const ancestry: Cell[] = [];
    const visited = new Set<string>();
    let current: string | null = id;
    while (current !== null && !resolved.has(current)) {
      const cell = cells.get(current);
      if (!cell || visited.has(current)) break;
      visited.add(current);
      ancestry.push(cell);
      current = cell.parent;
    }
    let bounds = current === null ? emptyBounds : (resolved.get(current) ?? null);
    for (const cell of ancestry.reverse()) {
      if (
        cell.node.getAttribute("visible") === "0" ||
        (cell.parent && cells.get(cell.parent)?.node.getAttribute("collapsed") === "1")
      )
        bounds = null;
      if (bounds && cell.geometry) {
        const { x, y } = point(cell.geometry);
        const relative = cell.geometry.getAttribute("relative") === "1";
        const offset = point(child(cell.geometry, "mxPoint", "offset"));
        bounds = {
          x: bounds.x + (relative ? x * bounds.width : x) + offset.x,
          y: bounds.y + (relative ? y * bounds.height : y) + offset.y,
          width: Math.max(0, number(cell.geometry.getAttribute("width"))),
          height: Math.max(0, number(cell.geometry.getAttribute("height"))),
        };
      }
      if (bounds && ![bounds.x, bounds.y, bounds.width, bounds.height].every(Number.isFinite))
        bounds = null;
      resolved.set(cell.id, bounds);
    }
    return bounds;
  };
}

function inlineImage(value: string) {
  const prefix = imagePrefix.exec(value);
  if (!prefix) return;
  const data = value.slice(prefix[0].length);
  // mxGraph also writes base64 image payloads without the optional MIME parameter.
  if (!prefix[2] && /^[\da-z+/]+=*$/i.test(data)) return `${prefix[0].slice(0, -1)};base64,${data}`;
  return value;
}

function endpoint(
  bounds: DrawingBounds,
  style: CellStyle,
  key: "entry" | "exit",
  toward: DrawioPoint,
  shape: string | undefined,
): DrawioPoint {
  const x = style.get(`${key}X`);
  const y = style.get(`${key}Y`);
  const center = { x: bounds.x + bounds.width / 2, y: bounds.y + bounds.height / 2 };
  if (x !== undefined || y !== undefined)
    return {
      x: bounds.x + bounds.width * number(x, 0.5) + number(style.get(`${key}Dx`)),
      y: bounds.y + bounds.height * number(y, 0.5) + number(style.get(`${key}Dy`)),
    };
  if (!bounds.width || !bounds.height) return center;
  const dx = (toward.x - center.x) / (bounds.width / 2);
  const dy = (toward.y - center.y) / (bounds.height / 2);
  const radius =
    shape === "ellipse"
      ? Math.hypot(dx, dy)
      : shape === "rhombus"
        ? Math.abs(dx) + Math.abs(dy)
        : Math.max(Math.abs(dx), Math.abs(dy));
  return radius
    ? {
        x: center.x + (toward.x - center.x) / radius,
        y: center.y + (toward.y - center.y) / radius,
      }
    : center;
}

function edgePoints(cell: Cell, cells: Map<string, Cell>, boundsOf: ReturnType<typeof cellBounds>) {
  const origin = cell.parent ? boundsOf(cell.parent) : emptyBounds;
  if (!origin) return;
  const source = cell.node.getAttribute("source");
  const target = cell.node.getAttribute("target");
  const sourceBounds = source ? boundsOf(source) : null;
  const targetBounds = target ? boundsOf(target) : null;
  const sourcePoint = child(cell.geometry, "mxPoint", "sourcePoint");
  const targetPoint = child(cell.geometry, "mxPoint", "targetPoint");
  const from = sourceBounds
    ? add(sourceBounds, { x: sourceBounds.width / 2, y: sourceBounds.height / 2 })
    : sourcePoint && add(origin, point(sourcePoint));
  const to = targetBounds
    ? add(targetBounds, { x: targetBounds.width / 2, y: targetBounds.height / 2 })
    : targetPoint && add(origin, point(targetPoint));
  if (!from || !to) return;
  const waypoints = Array.from(child(cell.geometry, "Array", "points")?.children ?? [])
    .filter((node) => node.tagName === "mxPoint")
    .map((node) => add(origin, point(node)));
  return [
    sourceBounds && source
      ? endpoint(
          sourceBounds,
          cell.style,
          "exit",
          waypoints[0] ?? to,
          cells.get(source)?.style.get("shape"),
        )
      : from,
    ...waypoints,
    targetBounds && target
      ? endpoint(
          targetBounds,
          cell.style,
          "entry",
          waypoints.at(-1) ?? from,
          cells.get(target)?.style.get("shape"),
        )
      : to,
  ];
}

export function drawioPage(source: PageSource, graph: Element): DrawioPage {
  const root = child(graph, "root");
  if (graph.tagName !== "mxGraphModel" || !root) throw new Error("Invalid Draw.io page");
  const cells = new Map<string, Cell>();
  for (const node of Array.from(root.children)) {
    const cell = readCell(node);
    if (!cell) continue;
    if (cells.has(cell.id)) throw new Error("Duplicate Draw.io cell");
    cells.set(cell.id, cell);
  }
  const boundsOf = cellBounds(cells);
  const result: DrawioPage["cells"] = [];
  let unsupported = 0;
  const pageWidth = number(graph.getAttribute("pageWidth"));
  const pageHeight = number(graph.getAttribute("pageHeight"));
  const pageSized = graph.getAttribute("page") !== "0" && pageWidth > 0 && pageHeight > 0;
  let left = pageSized ? 0 : Infinity;
  let top = pageSized ? 0 : Infinity;
  let right = pageSized ? pageWidth : -Infinity;
  let bottom = pageSized ? pageHeight : -Infinity;
  const include = ({ x, y }: DrawioPoint) => {
    left = Math.min(left, x);
    right = Math.max(right, x);
    top = Math.min(top, y);
    bottom = Math.max(bottom, y);
  };
  for (const cell of cells.values()) {
    const bounds = boundsOf(cell.id);
    if (!bounds) continue;
    if (cell.node.getAttribute("vertex") === "1" && cell.geometry) {
      const shape = cell.style.get("shape") ?? "rectangle";
      const image = shape === "image" ? inlineImage(cell.style.get("image") ?? "") : undefined;
      const unsupportedShape = !shapes.has(shape) || (shape === "image" && !image);
      const label = textLabel(cell);
      if (unsupportedShape) unsupported++;
      result.push({
        kind: "vertex",
        id: cell.id,
        bounds,
        shape,
        image,
        label,
        style: cell.style,
        unsupported: unsupportedShape,
      });
      include(bounds);
      include({ x: bounds.x + bounds.width, y: bounds.y + bounds.height });
    } else if (cell.node.getAttribute("edge") === "1") {
      const points = edgePoints(cell, cells, boundsOf);
      if (points) {
        points.forEach(include);
        result.push({
          kind: "edge",
          id: cell.id,
          points,
          label: textLabel(cell),
          style: cell.style,
        });
      } else unsupported++;
    }
  }
  const padding = pageSized ? 0 : 16;
  return {
    id: source.id,
    label: source.label,
    bounds: Number.isFinite(left)
      ? {
          x: left - padding,
          y: top - padding,
          width: Math.max(1, right - left + padding * 2),
          height: Math.max(1, bottom - top + padding * 2),
        }
      : { x: 0, y: 0, width: 300, height: 150 },
    background: drawioColor(graph.getAttribute("background"), "#fff"),
    cells: result,
    unsupported,
  };
}
