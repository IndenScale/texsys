/** File preview geometry and explicit source mappings. */
export type PreviewSource = { file: string; path?: string; line?: number; column?: number };
export type PreviewArtifact = {
  id: string;
  target: string;
  run_id: string;
  path: string;
  digest?: string;
  title?: string;
  kind: string;
  root?: string;
  preview?: { kind?: string; root?: string; title?: string; [key: string]: unknown };
  current: boolean;
  freshness: string;
  partial?: boolean;
  complete?: boolean;
  completeness?: string;
  status?: string;
  execution?: string;
  fallback?: boolean;
  available: boolean;
  error?: string;
  latest_target?: { status: string; diagnostics?: unknown[] };
  diagnostics?: unknown[];
  open_items?: unknown[];
};
/** Unsaved scene text is parsed in memory; clean files are read by their exact path and version. */
export function sceneFileRequest(path: string, version: string, content: string, dirty: boolean) {
  const query = new URLSearchParams({ path, version }).toString();
  return {
    key: JSON.stringify([path, version, dirty ? content : null]),
    url: dirty ? "/api/scene" : `/api/scene?${query}`,
    body: dirty ? { path, version, content } : undefined,
  };
}
export function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}
export function records(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.map(record) : [];
}
export function displayValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}
export function sourceReference(
  data: unknown,
  semanticRef: string,
  id = semanticRef,
): PreviewSource | undefined {
  const sources = record(record(data).source_map);
  const source = record(sources[semanticRef] ?? sources[id] ?? sources[`node:${semanticRef}`]);
  const file = source.file;
  // Only the explicit project-relative file field is navigable; path is a declaration path.
  if (
    typeof file !== "string" ||
    !file ||
    /[\\\0]/.test(file) ||
    file.startsWith("/") ||
    /^[a-z]+:/i.test(file) ||
    file.split("/").includes("..")
  )
    return;
  return {
    file,
    path: typeof source.path === "string" ? source.path : undefined,
    line:
      Number.isInteger(source.line) && Number(source.line) > 0 ? Number(source.line) : undefined,
    column:
      Number.isInteger(source.column) && Number(source.column) > 0
        ? Number(source.column)
        : undefined,
  };
}
const tags = new Set(["path", "line", "rect", "circle", "ellipse", "polyline", "polygon"]);
const attributes = new Set([
  "d",
  "x",
  "y",
  "x1",
  "x2",
  "y1",
  "y2",
  "width",
  "height",
  "rx",
  "ry",
  "cx",
  "cy",
  "r",
  "points",
  "transform",
  "fill",
  "stroke",
  "stroke-width",
  "strokeWidth",
  "stroke-dasharray",
  "strokeDasharray",
  "opacity",
  "fill-opacity",
  "fillOpacity",
  "stroke-linecap",
  "strokeLinecap",
  "stroke-linejoin",
  "strokeLinejoin",
]);
export function safePrimitive(
  value: unknown,
): { tag: string; attrs: Record<string, string | number> } | undefined {
  const primitive = record(value);
  if (typeof primitive.tag !== "string" || !tags.has(primitive.tag)) return;
  const attrs: Record<string, string | number> = {};
  for (const [name, value] of Object.entries(record(primitive.attrs))) {
    if (!attributes.has(name) || !["string", "number"].includes(typeof value)) continue;
    if (typeof value === "number" && !Number.isFinite(value)) continue;
    if (typeof value === "string" && /url\s*\(|javascript:|data:|[<>]/i.test(value)) continue;
    const key = name.replace(/-([a-z])/g, (_, letter: string) => letter.toUpperCase());
    attrs[key] = value as string | number;
  }
  return { tag: primitive.tag, attrs };
}
/** Canvas picking in CSS pixels; caller supplies faces from back to front. */
export function pickedFace<T extends { vertices: number[][] }>(
  faces: T[],
  x: number,
  y: number,
): T | undefined {
  for (let face = faces.length - 1; face >= 0; face--) {
    const vertices = faces[face].vertices;
    let inside = false;
    for (let i = 0, j = vertices.length - 1; i < vertices.length; j = i++) {
      const [xi, yi] = vertices[i],
        [xj, yj] = vertices[j];
      if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
    }
    if (inside) return faces[face];
  }
}
